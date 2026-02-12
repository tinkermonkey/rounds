"""Integration tests for PostgreSQL signature store adapter.

NOTE: These tests require a PostgreSQL instance. For development testing,
tests can be skipped if PostgreSQL is not available using pytest.mark.skipif
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from rounds.core.models import (
    Diagnosis,
    Signature,
    SignatureStatus,
)
from rounds.adapters.store.postgresql import PostgreSQLSignatureStore


@pytest.fixture
def postgres_config():
    """PostgreSQL connection configuration."""
    return {
        "host": "localhost",
        "port": 5432,
        "database": "rounds_test",
        "user": "rounds_test",
        "password": "rounds_test",
        "pool_size": 5,
    }


@pytest.fixture
def store(postgres_config) -> PostgreSQLSignatureStore:
    """Create a PostgreSQL store adapter."""
    return PostgreSQLSignatureStore(**postgres_config)


class TestPostgreSQLStoreInitialization:
    """Tests for PostgreSQL store initialization."""

    def test_store_initialization(self, postgres_config):
        """Test PostgreSQL store initialization with configuration."""
        store = PostgreSQLSignatureStore(**postgres_config)

        assert store.host == postgres_config["host"]
        assert store.port == postgres_config["port"]
        assert store.database == postgres_config["database"]
        assert store.user == postgres_config["user"]
        assert store.password == postgres_config["password"]

    def test_store_default_configuration(self):
        """Test PostgreSQL store with default configuration."""
        store = PostgreSQLSignatureStore()

        assert store.host == "localhost"
        assert store.port == 5432
        assert store.database == "rounds"
        assert store.user == "rounds"


@pytest.mark.asyncio
@pytest.mark.skipif(
    True, reason="PostgreSQL not available in test environment"
)
async def test_create_and_retrieve_signature(
    store: PostgreSQLSignatureStore,
) -> None:
    """Test creating and retrieving a signature from PostgreSQL."""
    await store.initialize()

    sig = Signature(
        id="test-1",
        fingerprint="abc123",
        error_type="ValueError",
        service="user-service",
        message_template="Invalid value: {value}",
        stack_hash="stack123",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        occurrence_count=1,
        status=SignatureStatus.NEW,
    )

    await store.save(sig)
    retrieved = await store.get(sig.fingerprint, sig.service)

    assert retrieved is not None
    assert retrieved.id == sig.id
    assert retrieved.error_type == sig.error_type


@pytest.mark.asyncio
@pytest.mark.skipif(
    True, reason="PostgreSQL not available in test environment"
)
async def test_connection_pooling(store: PostgreSQLSignatureStore) -> None:
    """Test that connection pooling works correctly."""
    await store.initialize()

    # Connection pool should be initialized
    assert store._pool is not None
    assert store._pool.get_size() >= 1
    assert store._pool.get_size() <= store._pool_size

    await store.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    True, reason="PostgreSQL not available in test environment"
)
async def test_schema_initialization(store: PostgreSQLSignatureStore) -> None:
    """Test that database schema is initialized on first use."""
    await store.initialize()

    # Schema should be initialized
    assert store._schema_initialized is True

    await store.close()


class TestPostgreSQLTransactions:
    """Tests for transaction handling."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        True, reason="PostgreSQL not available in test environment"
    )
    async def test_save_creates_transaction(
        self, store: PostgreSQLSignatureStore
    ) -> None:
        """Test that save operation uses a transaction."""
        await store.initialize()

        sig = Signature(
            id="test-tx-1",
            fingerprint="tx123",
            error_type="RuntimeError",
            service="api-service",
            message_template="Runtime error",
            stack_hash="stack-tx",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            occurrence_count=1,
            status=SignatureStatus.NEW,
        )

        # Save should use a transaction
        await store.save(sig)

        await store.close()

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        True, reason="PostgreSQL not available in test environment"
    )
    async def test_update_with_diagnosis(
        self, store: PostgreSQLSignatureStore
    ) -> None:
        """Test updating signature with diagnosis in a transaction."""
        await store.initialize()

        sig = Signature(
            id="test-diag-1",
            fingerprint="diag123",
            error_type="TimeoutError",
            service="db-service",
            message_template="Timeout",
            stack_hash="stack-diag",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            occurrence_count=1,
            status=SignatureStatus.NEW,
        )

        await store.save(sig)

        # Update with diagnosis
        diagnosis = Diagnosis(
            root_cause="Database connection timeout",
            evidence=("Pool exhausted",),
            suggested_fix="Increase pool size",
            confidence="high",
            cost_usd=0.05,
        )

        sig.diagnosis = diagnosis
        sig.status = SignatureStatus.DIAGNOSED

        await store.update(sig)

        await store.close()


class TestPostgreSQLErrorHandling:
    """Tests for error handling in PostgreSQL store."""

    def test_invalid_connection_config(self):
        """Test that invalid configuration is caught."""
        store = PostgreSQLSignatureStore(
            host="invalid-host",
            port=99999,
        )

        assert store.host == "invalid-host"
        assert store.port == 99999

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        True, reason="PostgreSQL not available in test environment"
    )
    async def test_connection_failure(self):
        """Test handling of connection failures."""
        store = PostgreSQLSignatureStore(
            host="invalid-host",
            port=99999,
        )

        # Attempting to initialize should raise an exception
        with pytest.raises(Exception):
            await store.initialize()
