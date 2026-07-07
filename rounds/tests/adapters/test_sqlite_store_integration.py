"""Integration tests for SQLite signature store row parsing."""

import tempfile
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from rounds.adapters.store.sqlite import SQLiteSignatureStore
from rounds.core.models import Severity, Signature, SignatureStatus


@pytest.fixture
async def temp_db() -> AsyncGenerator[tuple[SQLiteSignatureStore, Path], None]:
    """Create a temporary SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = SQLiteSignatureStore(str(db_path))
        await store._init_schema()
        yield store, db_path
        await store.close_pool()




@pytest.mark.asyncio
async def test_row_parsing_with_invalid_timestamp(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing fails gracefully with invalid timestamp."""
    store, _db_path = temp_db

    # Get a raw connection to insert a malformed row
    conn = await store._get_connection()
    try:
        # Insert a row with invalid timestamp
        await conn.execute(
            """
            INSERT INTO signatures
            (id, fingerprint, error_type, service, message_template, stack_hash,
             first_seen, last_seen, occurrence_count, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test-id",
                "test-fp",
                "TestError",
                "test-service",
                "test message",
                "test-hash",
                "not-a-valid-timestamp",  # Invalid
                "2024-01-01 12:00:00",
                1,
                "new",
            ),
        )
        await conn.commit()
    finally:
        await store._return_connection(conn)

    # Attempt to load the malformed row - should raise ValueError
    with pytest.raises(ValueError, match=r"(?i)invalid|date"):
        await store.get_by_id("test-id")


@pytest.mark.asyncio
async def test_row_parsing_with_invalid_status(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing fails with invalid signature status."""
    store, _db_path = temp_db

    # Get a raw connection to insert a row with invalid status
    conn = await store._get_connection()
    try:
        now_iso = datetime.now(UTC).isoformat()
        await conn.execute(
            """
            INSERT INTO signatures
            (id, fingerprint, error_type, service, message_template, stack_hash,
             first_seen, last_seen, occurrence_count, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test-id",
                "test-fp",
                "TestError",
                "test-service",
                "test message",
                "test-hash",
                now_iso,
                now_iso,
                1,
                "invalid-status",  # Invalid status value
            ),
        )
        await conn.commit()
    finally:
        await store._return_connection(conn)

    # Attempt to load the row - should raise ValueError
    with pytest.raises(ValueError, match=r"Row parsing failed|invalid"):
        await store.get_by_id("test-id")


@pytest.mark.asyncio
async def test_wal_journal_mode_is_enabled(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """SQLite store must use WAL journal mode for concurrent read/write safety."""
    store, _db_path = temp_db

    conn = await store._get_connection()
    try:
        cursor = await conn.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
    finally:
        await store._return_connection(conn)

    assert row is not None
    assert row[0] == "wal", f"Expected WAL journal mode, got: {row[0]}"


@pytest.mark.asyncio
async def test_busy_timeout_is_set_on_new_connections(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """New connections must have a non-zero busy_timeout to handle transient locks."""
    store, _db_path = temp_db

    conn = await store._get_connection()
    try:
        cursor = await conn.execute("PRAGMA busy_timeout")
        row = await cursor.fetchone()
    finally:
        await store._return_connection(conn)

    assert row is not None
    assert int(row[0]) > 0, f"Expected positive busy_timeout, got: {row[0]}"


@pytest.mark.asyncio
async def test_row_parsing_with_negative_occurrence_count(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing fails with negative occurrence count."""
    store, _db_path = temp_db

    # Get a raw connection to insert a row with negative occurrence count
    conn = await store._get_connection()
    try:
        now_iso = datetime.now(UTC).isoformat()
        await conn.execute(
            """
            INSERT INTO signatures
            (id, fingerprint, error_type, service, message_template, stack_hash,
             first_seen, last_seen, occurrence_count, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "test-id",
                "test-fp",
                "TestError",
                "test-service",
                "test message",
                "test-hash",
                now_iso,
                now_iso,
                -1,  # Invalid negative count
                "new",
            ),
        )
        await conn.commit()
    finally:
        await store._return_connection(conn)

    # Attempt to load the row - should raise ValueError
    with pytest.raises(ValueError, match=r"Row parsing failed|occurrence_count"):
        await store.get_by_id("test-id")


@pytest.mark.asyncio
async def test_resolution_and_severity_fields_round_trip(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """resolution_threshold_hours, last_alerted_at, and max_severity persist and reload."""
    store, _db_path = temp_db

    alerted_at = datetime(2024, 1, 1, 14, 0, 0, tzinfo=UTC)
    sig = Signature(
        id="test-id",
        fingerprint="test-fp",
        error_type="NullPointerError",
        service="billing-service",
        message_template="Null reference in handler",
        stack_hash="test-hash",
        first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
        occurrence_count=1,
        status=SignatureStatus.NEW,
        resolution_threshold_hours=12,
        last_alerted_at=alerted_at,
        max_severity=Severity.FATAL,
    )
    await store.save(sig)

    loaded = await store.get_by_id("test-id")

    assert loaded is not None
    assert loaded.resolution_threshold_hours == 12
    assert loaded.last_alerted_at == alerted_at
    assert loaded.max_severity == Severity.FATAL


@pytest.mark.asyncio
async def test_new_nullable_fields_default_when_absent(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """A signature saved without the new fields reloads with sensible defaults."""
    store, _db_path = temp_db

    sig = Signature(
        id="test-id",
        fingerprint="test-fp",
        error_type="TestError",
        service="test-service",
        message_template="test message",
        stack_hash="test-hash",
        first_seen=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        last_seen=datetime(2024, 1, 1, 12, 5, 0, tzinfo=UTC),
        occurrence_count=1,
        status=SignatureStatus.NEW,
    )
    await store.save(sig)

    loaded = await store.get_by_id("test-id")

    assert loaded is not None
    assert loaded.resolution_threshold_hours is None
    assert loaded.last_alerted_at is None
    assert loaded.max_severity == Severity.ERROR


@pytest.mark.asyncio
async def test_schema_migration_adds_new_columns_to_pre_existing_database() -> None:
    """A database created before this phase (12-column schema) must migrate cleanly.

    Simulates an existing deployment's signatures.db by creating the table with
    only the pre-Phase-1 columns, then verifies that opening it with the current
    SQLiteSignatureStore adds the new columns and can read old rows.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "legacy.db"

        # Build the database using the pre-Phase-1 schema (no resolution_threshold_hours,
        # last_alerted_at, or max_severity columns).
        conn = await aiosqlite.connect(str(db_path))
        try:
            await conn.execute(
                """
                CREATE TABLE signatures (
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
                    diagnosis_json TEXT,
                    tags TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            now_iso = datetime.now(UTC).isoformat()
            await conn.execute(
                """
                INSERT INTO signatures
                (id, fingerprint, error_type, service, message_template, stack_hash,
                 first_seen, last_seen, occurrence_count, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-id",
                    "legacy-fp",
                    "LegacyError",
                    "legacy-service",
                    "legacy message",
                    "legacy-hash",
                    now_iso,
                    now_iso,
                    1,
                    "new",
                ),
            )
            await conn.commit()
        finally:
            await conn.close()

        # Opening with the current store must migrate the schema without error.
        store = SQLiteSignatureStore(str(db_path))
        try:
            loaded = await store.get_by_id("legacy-id")

            assert loaded is not None
            assert loaded.resolution_threshold_hours is None
            assert loaded.last_alerted_at is None
            assert loaded.max_severity == Severity.ERROR

            # New writes to the migrated database should also round-trip.
            loaded.resolution_threshold_hours = 8
            await store.update(loaded)
            reloaded = await store.get_by_id("legacy-id")
            assert reloaded is not None
            assert reloaded.resolution_threshold_hours == 8
        finally:
            await store.close_pool()
