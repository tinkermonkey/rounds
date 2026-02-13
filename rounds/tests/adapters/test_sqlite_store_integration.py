"""Integration tests for SQLite signature store row parsing."""

import pytest
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from rounds.core.models import Diagnosis, Signature, SignatureStatus
from rounds.adapters.store.sqlite import SQLiteSignatureStore


@pytest.fixture
async def temp_db() -> tuple[SQLiteSignatureStore, Path]:
    """Create a temporary SQLite database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = SQLiteSignatureStore(str(db_path))
        await store._init_schema()
        yield store, db_path
        await store.close_pool()


@pytest.mark.asyncio
async def test_row_parsing_with_invalid_row_length(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing fails gracefully with invalid row length."""
    store, db_path = temp_db

    # Get a raw connection to insert a malformed row
    conn = await store._get_connection()
    try:
        # Insert a row with missing columns (simulating data corruption)
        await conn.execute(
            """
            INSERT INTO signatures (id, fingerprint)
            VALUES ('bad-id', 'bad-fp')
            """
        )
        await conn.commit()
    finally:
        await store._return_connection(conn)

    # Attempt to load the malformed row - should raise ValueError
    with pytest.raises(ValueError, match="Row parsing failed"):
        await store.get_by_id("bad-id")


@pytest.mark.asyncio
async def test_row_parsing_with_invalid_timestamp(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing fails gracefully with invalid timestamp."""
    store, db_path = temp_db

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
    with pytest.raises(ValueError, match="Row parsing failed|invalid"):
        await store.get_by_id("test-id")


@pytest.mark.asyncio
async def test_row_parsing_with_invalid_diagnosis_json(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing recovers from invalid diagnosis JSON."""
    store, db_path = temp_db

    # Get a raw connection to insert a row with bad diagnosis JSON
    conn = await store._get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            """
            INSERT INTO signatures
            (id, fingerprint, error_type, service, message_template, stack_hash,
             first_seen, last_seen, occurrence_count, status, diagnosis_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "new",
                "{invalid json",  # Malformed JSON
            ),
        )
        await conn.commit()
    finally:
        await store._return_connection(conn)

    # Attempt to load the row - should succeed but diagnosis will be None
    signature = await store.get_by_id("test-id")
    assert signature is not None
    assert signature.diagnosis is None  # Diagnosis should be cleared due to JSON parse error


@pytest.mark.asyncio
async def test_row_parsing_with_invalid_tags_json(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing recovers from invalid tags JSON."""
    store, db_path = temp_db

    # Get a raw connection to insert a row with bad tags JSON
    conn = await store._get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            """
            INSERT INTO signatures
            (id, fingerprint, error_type, service, message_template, stack_hash,
             first_seen, last_seen, occurrence_count, status, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "new",
                "{invalid json",  # Malformed JSON
            ),
        )
        await conn.commit()
    finally:
        await store._return_connection(conn)

    # Attempt to load the row - should succeed but tags will be empty
    signature = await store.get_by_id("test-id")
    assert signature is not None
    assert signature.tags == frozenset()  # Tags should be empty due to JSON parse error


@pytest.mark.asyncio
async def test_row_parsing_with_invalid_status(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing fails with invalid signature status."""
    store, db_path = temp_db

    # Get a raw connection to insert a row with invalid status
    conn = await store._get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
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
    with pytest.raises(ValueError, match="Row parsing failed|invalid"):
        await store.get_by_id("test-id")


@pytest.mark.asyncio
async def test_row_parsing_with_negative_occurrence_count(
    temp_db: tuple[SQLiteSignatureStore, Path],
) -> None:
    """Test that row parsing fails with negative occurrence count."""
    store, db_path = temp_db

    # Get a raw connection to insert a row with negative occurrence count
    conn = await store._get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
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
    with pytest.raises(ValueError, match="Row parsing failed|occurrence_count"):
        await store.get_by_id("test-id")
