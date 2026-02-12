"""PostgreSQL signature store adapter.

Implements SignatureStorePort using PostgreSQL with asyncpg for async access.
Provides ACID guarantees for signature state with scalability for production use.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import asyncpg

from rounds.core.models import Diagnosis, Signature, SignatureStatus, StoreStats
from rounds.core.ports import SignatureStorePort

logger = logging.getLogger(__name__)


class PostgreSQLSignatureStore(SignatureStorePort):
    """PostgreSQL-backed signature store with connection pooling and async access."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "rounds",
        user: str = "rounds",
        password: str = "",
        pool_size: int = 10,
    ):
        """Initialize PostgreSQL store with connection pooling.

        Args:
            host: PostgreSQL server hostname.
            port: PostgreSQL server port.
            database: Database name.
            user: Database user.
            password: Database password.
            pool_size: Number of connections to maintain in the pool.
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self._pool: asyncpg.Pool | None = None
        self._pool_size = pool_size
        self._schema_lock = asyncio.Lock()
        self._schema_initialized = False

    async def _init_pool(self) -> None:
        """Initialize the connection pool on first use."""
        if self._pool is not None:
            return

        self._pool = await asyncpg.create_pool(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
            min_size=1,
            max_size=self._pool_size,
        )

    async def close_pool(self) -> None:
        """Close all pooled connections."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _init_schema(self) -> None:
        """Initialize database schema on first use.

        Only runs once per instance. Subsequent calls are no-ops.
        Uses dedicated _schema_lock to avoid contention with pool operations.
        """
        # Check first without lock to avoid unnecessary locking
        if self._schema_initialized:
            return

        async with self._schema_lock:
            # Check again after acquiring lock to prevent race
            if self._schema_initialized:
                return

            await self._init_pool()
            assert self._pool is not None

            async with self._pool.acquire() as conn:
                # Create table
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS signatures (
                        id TEXT PRIMARY KEY,
                        fingerprint TEXT UNIQUE NOT NULL,
                        error_type TEXT NOT NULL,
                        service TEXT NOT NULL,
                        message_template TEXT NOT NULL,
                        stack_hash TEXT NOT NULL,
                        first_seen TIMESTAMP NOT NULL,
                        last_seen TIMESTAMP NOT NULL,
                        occurrence_count INTEGER NOT NULL DEFAULT 1,
                        status TEXT NOT NULL DEFAULT 'new',
                        diagnosis_json JSONB,
                        tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]
                    )
                    """
                )

                # Create indexes
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_status ON signatures(status)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_service ON signatures(service)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fingerprint ON signatures(fingerprint)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_last_seen ON signatures(last_seen DESC)"
                )

                self._schema_initialized = True

    async def get_by_id(self, signature_id: str) -> Signature | None:
        """Look up a signature by its ID."""
        await self._init_schema()
        await self._init_pool()
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM signatures WHERE id = $1", signature_id
            )
            if row is None:
                return None
            return self._row_to_signature(row)

    async def get_by_fingerprint(self, fingerprint: str) -> Signature | None:
        """Look up a signature by its fingerprint hash."""
        await self._init_schema()
        await self._init_pool()
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM signatures WHERE fingerprint = $1", fingerprint
            )
            if row is None:
                return None
            return self._row_to_signature(row)

    async def save(self, signature: Signature) -> None:
        """Create or update a signature."""
        await self._init_schema()
        await self._init_pool()
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            diagnosis_json = None
            if signature.diagnosis is not None:
                diagnosis_json = self._serialize_diagnosis(signature.diagnosis)

            tags_list = sorted(list(signature.tags))

            # Use INSERT ... ON CONFLICT for upsert
            await conn.execute(
                """
                INSERT INTO signatures
                (id, fingerprint, error_type, service, message_template,
                 stack_hash, first_seen, last_seen, occurrence_count, status,
                 diagnosis_json, tags)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (id) DO UPDATE SET
                    fingerprint = $2,
                    error_type = $3,
                    service = $4,
                    message_template = $5,
                    stack_hash = $6,
                    first_seen = $7,
                    last_seen = $8,
                    occurrence_count = $9,
                    status = $10,
                    diagnosis_json = $11,
                    tags = $12
                """,
                signature.id,
                signature.fingerprint,
                signature.error_type,
                signature.service,
                signature.message_template,
                signature.stack_hash,
                signature.first_seen,
                signature.last_seen,
                signature.occurrence_count,
                signature.status.value,
                diagnosis_json,
                tags_list,
            )

    async def update(self, signature: Signature) -> None:
        """Update an existing signature."""
        # PostgreSQL upsert is idempotent, so save and update are the same
        await self.save(signature)

    async def get_pending_investigation(self) -> list[Signature]:
        """Return signatures with status NEW, ordered by priority."""
        await self._init_schema()
        await self._init_pool()
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM signatures
                WHERE status = $1
                ORDER BY last_seen DESC, occurrence_count DESC
                """,
                SignatureStatus.NEW.value,
            )
            return [self._row_to_signature(row) for row in rows]

    async def get_all(
        self, status: SignatureStatus | None = None
    ) -> list[Signature]:
        """Return all signatures, optionally filtered by status."""
        await self._init_schema()
        await self._init_pool()
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            if status is None:
                rows = await conn.fetch(
                    """
                    SELECT * FROM signatures
                    ORDER BY last_seen DESC, occurrence_count DESC
                    """
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM signatures
                    WHERE status = $1
                    ORDER BY last_seen DESC, occurrence_count DESC
                    """,
                    status.value,
                )
            return [self._row_to_signature(row) for row in rows]

    async def get_similar(
        self, signature: Signature, limit: int = 5
    ) -> list[Signature]:
        """Return signatures with similar characteristics.

        Currently uses simple heuristics (same service + error type).
        Could be enhanced with vector similarity in the future.
        """
        await self._init_schema()
        await self._init_pool()
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM signatures
                WHERE service = $1 AND error_type = $2 AND id != $3
                ORDER BY last_seen DESC
                LIMIT $4
                """,
                signature.service,
                signature.error_type,
                signature.id,
                limit,
            )
            return [self._row_to_signature(row) for row in rows]

    async def get_stats(self) -> StoreStats:
        """Summary statistics for reporting."""
        await self._init_schema()
        await self._init_pool()
        assert self._pool is not None

        async with self._pool.acquire() as conn:
            # Total signatures
            total_row = await conn.fetchrow("SELECT COUNT(*) FROM signatures")
            total = total_row[0] if total_row else 0

            # By status
            status_rows = await conn.fetch(
                """
                SELECT status, COUNT(*) FROM signatures
                GROUP BY status
                """
            )
            status_counts = {row[0]: row[1] for row in status_rows}

            # By service
            service_rows = await conn.fetch(
                """
                SELECT service, COUNT(*) FROM signatures
                GROUP BY service
                """
            )
            service_counts = {row[0]: row[1] for row in service_rows}

            # Oldest signature age and average occurrence count
            stats_row = await conn.fetchrow(
                """
                SELECT
                    CASE WHEN COUNT(*) > 0 THEN
                        EXTRACT(EPOCH FROM (NOW() - MIN(first_seen))) / 3600.0
                    ELSE NULL END as oldest_age_hours,
                    CASE WHEN COUNT(*) > 0 THEN
                        AVG(occurrence_count)
                    ELSE 0 END as avg_occurrence
                FROM signatures
                """
            )
            oldest_age_hours = stats_row[0] if stats_row and stats_row[0] is not None else None
            avg_occurrence = float(stats_row[1]) if stats_row and stats_row[1] is not None else 0.0

            return StoreStats(
                total_signatures=total,
                by_status=status_counts,
                by_service=service_counts,
                oldest_signature_age_hours=oldest_age_hours,
                avg_occurrence_count=avg_occurrence,
            )

    def _row_to_signature(self, row: asyncpg.Record) -> Signature:
        """Convert a database row to a Signature object.

        Raises:
            ValueError: If row is malformed or contains invalid data.
        """
        try:
            sig_id = row["id"]
            fingerprint = row["fingerprint"]
            error_type = row["error_type"]
            service = row["service"]
            message_template = row["message_template"]
            stack_hash = row["stack_hash"]
            first_seen = row["first_seen"]
            last_seen = row["last_seen"]
            occurrence_count = row["occurrence_count"]
            status = row["status"]
            diagnosis_json = row["diagnosis_json"]
            tags = row["tags"]

            # Validate required fields
            if not sig_id or not fingerprint:
                raise ValueError("Missing required fields: id or fingerprint")

            # Validate occurrence_count
            if not isinstance(occurrence_count, int) or occurrence_count < 1:
                raise ValueError(f"Invalid occurrence_count: {occurrence_count}")

            # Parse diagnosis
            diagnosis = None
            if diagnosis_json:
                try:
                    diagnosis = self._deserialize_diagnosis(diagnosis_json)
                except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                    logger.warning(
                        f"Failed to parse diagnosis for signature {sig_id}: {e}. "
                        f"Diagnosis will be discarded."
                    )
                    diagnosis = None

            # Convert tags array
            tags_set = frozenset(tags) if tags else frozenset()

            return Signature(
                id=sig_id,
                fingerprint=fingerprint,
                error_type=error_type,
                service=service,
                message_template=message_template,
                stack_hash=stack_hash,
                first_seen=first_seen,
                last_seen=last_seen,
                occurrence_count=occurrence_count,
                status=SignatureStatus(status),
                diagnosis=diagnosis,
                tags=tags_set,
            )

        except ValueError as e:
            logger.error(f"Failed to parse database row: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Unexpected error parsing database row: {e}", exc_info=True)
            raise ValueError(f"Row parsing failed: {e}") from e

    @staticmethod
    def _serialize_diagnosis(diagnosis: Diagnosis) -> dict[str, Any]:
        """Serialize a Diagnosis to a dictionary for JSONB storage."""
        return {
            "root_cause": diagnosis.root_cause,
            "evidence": list(diagnosis.evidence),
            "suggested_fix": diagnosis.suggested_fix,
            "confidence": diagnosis.confidence,
            "diagnosed_at": diagnosis.diagnosed_at.isoformat(),
            "model": diagnosis.model,
            "cost_usd": diagnosis.cost_usd,
        }

    @staticmethod
    def _deserialize_diagnosis(diagnosis_dict: dict[str, Any]) -> Diagnosis:
        """Deserialize a Diagnosis from a dictionary."""
        return Diagnosis(
            root_cause=diagnosis_dict["root_cause"],
            evidence=tuple(diagnosis_dict["evidence"]),
            suggested_fix=diagnosis_dict["suggested_fix"],
            confidence=diagnosis_dict["confidence"],
            diagnosed_at=datetime.fromisoformat(diagnosis_dict["diagnosed_at"]),
            model=diagnosis_dict["model"],
            cost_usd=diagnosis_dict["cost_usd"],
        )
