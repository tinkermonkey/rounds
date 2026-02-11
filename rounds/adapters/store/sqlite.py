"""SQLite signature store adapter.

Implements SignatureStorePort using SQLite with aiosqlite for async access.
Provides ACID guarantees for signature state with zero operational overhead.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from rounds.core.models import Confidence, Diagnosis, Signature, SignatureStatus
from rounds.core.ports import SignatureStorePort

logger = logging.getLogger(__name__)


class SQLiteSignatureStore(SignatureStorePort):
    """SQLite-backed signature store with async access."""

    def __init__(self, db_path: str):
        """Initialize SQLite store.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_initialized = False

    async def _get_connection(self) -> aiosqlite.Connection:
        """Get a database connection."""
        conn = await aiosqlite.connect(str(self.db_path))
        # Enable foreign keys
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def _init_schema(self) -> None:
        """Initialize database schema on first use.

        Only runs once per instance. Subsequent calls are no-ops.
        """
        if self._schema_initialized:
            return

        conn = await self._get_connection()
        try:
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
                    occurrence_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'new',
                    diagnosis_json TEXT,
                    tags TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            # Index for common queries
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_status ON signatures(status)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_service ON signatures(service)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fingerprint ON signatures(fingerprint)"
            )
            await conn.commit()
            self._schema_initialized = True
        finally:
            await conn.close()

    async def get_by_fingerprint(self, fingerprint: str) -> Signature | None:
        """Look up a signature by its fingerprint hash."""
        await self._init_schema()

        conn = await self._get_connection()
        try:
            cursor = await conn.execute(
                "SELECT * FROM signatures WHERE fingerprint = ?", (fingerprint,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return self._row_to_signature(row)
        finally:
            await conn.close()

    async def save(self, signature: Signature) -> None:
        """Create or update a signature."""
        await self._init_schema()

        conn = await self._get_connection()
        try:
            diagnosis_json = None
            if signature.diagnosis is not None:
                diagnosis_json = self._serialize_diagnosis(signature.diagnosis)

            tags_json = json.dumps(sorted(signature.tags))

            # Try insert, fall back to update if exists
            await conn.execute(
                """
                INSERT OR REPLACE INTO signatures
                (id, fingerprint, error_type, service, message_template,
                 stack_hash, first_seen, last_seen, occurrence_count, status,
                 diagnosis_json, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signature.id,
                    signature.fingerprint,
                    signature.error_type,
                    signature.service,
                    signature.message_template,
                    signature.stack_hash,
                    signature.first_seen.isoformat(),
                    signature.last_seen.isoformat(),
                    signature.occurrence_count,
                    signature.status.value,
                    diagnosis_json,
                    tags_json,
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def update(self, signature: Signature) -> None:
        """Update an existing signature."""
        # SQLite doesn't distinguish between insert and update with INSERT OR REPLACE
        await self.save(signature)

    async def get_pending_investigation(self) -> list[Signature]:
        """Return signatures with status NEW, ordered by priority."""
        await self._init_schema()

        conn = await self._get_connection()
        try:
            cursor = await conn.execute(
                """
                SELECT * FROM signatures
                WHERE status = ?
                ORDER BY last_seen DESC, occurrence_count DESC
                """,
                (SignatureStatus.NEW.value,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_signature(row) for row in rows]
        finally:
            await conn.close()

    async def get_similar(
        self, signature: Signature, limit: int = 5
    ) -> list[Signature]:
        """Return signatures with similar characteristics.

        Currently uses simple heuristics (same service + error type).
        Could be enhanced with vector similarity in the future.
        """
        await self._init_schema()

        conn = await self._get_connection()
        try:
            cursor = await conn.execute(
                """
                SELECT * FROM signatures
                WHERE service = ? AND error_type = ? AND id != ?
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (signature.service, signature.error_type, signature.id, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_signature(row) for row in rows]
        finally:
            await conn.close()

    async def get_stats(self) -> dict[str, Any]:
        """Summary statistics for reporting."""
        await self._init_schema()

        conn = await self._get_connection()
        try:
            # Total signatures
            cursor = await conn.execute("SELECT COUNT(*) FROM signatures")
            total = (await cursor.fetchone())[0]

            # By status
            cursor = await conn.execute(
                """
                SELECT status, COUNT(*) FROM signatures
                GROUP BY status
                """
            )
            status_counts = {row[0]: row[1] for row in await cursor.fetchall()}

            # By service
            cursor = await conn.execute(
                """
                SELECT service, COUNT(*) FROM signatures
                GROUP BY service
                """
            )
            service_counts = {row[0]: row[1] for row in await cursor.fetchall()}

            # Total errors seen
            cursor = await conn.execute("SELECT SUM(occurrence_count) FROM signatures")
            total_errors = (await cursor.fetchone())[0] or 0

            return {
                "total_signatures": total,
                "by_status": status_counts,
                "by_service": service_counts,
                "total_errors_seen": total_errors,
            }
        finally:
            await conn.close()

    def _row_to_signature(self, row: tuple[Any, ...]) -> Signature:
        """Convert a database row to a Signature object."""
        (
            sig_id,
            fingerprint,
            error_type,
            service,
            message_template,
            stack_hash,
            first_seen,
            last_seen,
            occurrence_count,
            status,
            diagnosis_json,
            tags_json,
        ) = row

        # Parse dates
        first_seen_dt = datetime.fromisoformat(first_seen)
        last_seen_dt = datetime.fromisoformat(last_seen)

        # Parse diagnosis
        diagnosis = None
        if diagnosis_json:
            diagnosis = self._deserialize_diagnosis(diagnosis_json)

        # Parse tags
        tags = frozenset(json.loads(tags_json))

        return Signature(
            id=sig_id,
            fingerprint=fingerprint,
            error_type=error_type,
            service=service,
            message_template=message_template,
            stack_hash=stack_hash,
            first_seen=first_seen_dt,
            last_seen=last_seen_dt,
            occurrence_count=occurrence_count,
            status=SignatureStatus(status),
            diagnosis=diagnosis,
            tags=tags,
        )

    @staticmethod
    def _serialize_diagnosis(diagnosis: Diagnosis) -> str:
        """Serialize a Diagnosis to JSON."""
        return json.dumps(
            {
                "root_cause": diagnosis.root_cause,
                "evidence": list(diagnosis.evidence),
                "suggested_fix": diagnosis.suggested_fix,
                "confidence": diagnosis.confidence.value,
                "diagnosed_at": diagnosis.diagnosed_at.isoformat(),
                "model": diagnosis.model,
                "cost_usd": diagnosis.cost_usd,
            }
        )

    @staticmethod
    def _deserialize_diagnosis(diagnosis_json: str) -> Diagnosis:
        """Deserialize a Diagnosis from JSON."""
        data = json.loads(diagnosis_json)
        return Diagnosis(
            root_cause=data["root_cause"],
            evidence=tuple(data["evidence"]),
            suggested_fix=data["suggested_fix"],
            confidence=Confidence(data["confidence"]),
            diagnosed_at=datetime.fromisoformat(data["diagnosed_at"]),
            model=data["model"],
            cost_usd=data["cost_usd"],
        )
