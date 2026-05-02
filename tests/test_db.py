"""Tests for src/db.py — MongoDB connection and index management."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.db as db_module
from src.db import COLL_ARCHIVES, COLL_JIRA_ISSUES, COLL_SYNC_CONFIG


class TestConnectDB:
    """Tests for connect_db function."""

    @pytest.mark.asyncio
    async def test_connect_db_success(self, test_config):
        """Connects to MongoDB and creates indexes."""
        mock_client = MagicMock()
        mock_database = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_database)

        mock_collection = MagicMock()
        mock_collection.create_index = AsyncMock()
        mock_database.__getitem__ = MagicMock(return_value=mock_collection)

        with patch("src.db.AsyncIOMotorClient", return_value=mock_client):
            # Reset module state
            db_module._client = None
            db_module._db = None

            result = await db_module.connect_db(test_config)

            assert result is mock_database
            assert db_module._client is mock_client
            assert db_module._db is mock_database
            # Verify indexes were created (8 on jira_issues, 1 on sync_config, 2 on archives)
            assert mock_collection.create_index.await_count >= 8

    @pytest.mark.asyncio
    async def test_connect_db_uses_config_values(self, test_config):
        """Uses URI and database name from config."""
        mock_client = MagicMock()
        mock_database = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_database)

        mock_collection = MagicMock()
        mock_collection.create_index = AsyncMock()
        mock_database.__getitem__ = MagicMock(return_value=mock_collection)

        with patch("src.db.AsyncIOMotorClient") as mock_motor:
            mock_motor.return_value = mock_client
            db_module._client = None
            db_module._db = None

            await db_module.connect_db(test_config)

            mock_motor.assert_called_once()
            call_args = mock_motor.call_args
            assert call_args[0][0] == "mongodb://localhost:27017"
            mock_client.__getitem__.assert_called_with("easylife_jira_test")


    @pytest.mark.asyncio
    async def test_connect_db_sets_pool_parameters(self, test_config):
        """AsyncIOMotorClient is created with connection pool and timeout settings."""
        mock_client = MagicMock()
        mock_database = MagicMock()
        mock_client.__getitem__ = MagicMock(return_value=mock_database)

        mock_collection = MagicMock()
        mock_collection.create_index = AsyncMock()
        mock_database.__getitem__ = MagicMock(return_value=mock_collection)

        with patch("src.db.AsyncIOMotorClient") as mock_motor:
            mock_motor.return_value = mock_client
            db_module._client = None
            db_module._db = None

            await db_module.connect_db(test_config)

            call_kwargs = mock_motor.call_args[1]
            assert call_kwargs["maxPoolSize"] == 200
            assert call_kwargs["minPoolSize"] == 25
            assert call_kwargs["maxIdleTimeMS"] == 45000
            assert call_kwargs["serverSelectionTimeoutMS"] == 5000
            assert call_kwargs["connectTimeoutMS"] == 10000
            assert call_kwargs["socketTimeoutMS"] == 20000
            assert call_kwargs["retryWrites"] is True
            assert call_kwargs["retryReads"] is True

            # Reset module state
            db_module._client = None
            db_module._db = None


class TestCreateIndexes:
    """Tests for _create_indexes function."""

    @pytest.mark.asyncio
    async def test_create_indexes_success(self):
        """Creates all expected indexes without error."""
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_collection.create_index = AsyncMock()
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)

        await db_module._create_indexes(mock_db)

        # Should call create_index multiple times for jira_issues, sync_config, archives
        assert mock_collection.create_index.await_count >= 8

    @pytest.mark.asyncio
    async def test_create_indexes_failure_logs_warning(self):
        """Index creation failure is caught and logged as warning."""
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_collection.create_index = AsyncMock(side_effect=Exception("Auth required"))
        mock_db.__getitem__ = MagicMock(return_value=mock_collection)

        # Should not raise
        await db_module._create_indexes(mock_db)


class TestCloseDB:
    """Tests for close_db function."""

    @pytest.mark.asyncio
    async def test_close_db_with_client(self):
        """Closes client and resets module state."""
        mock_client = MagicMock()
        mock_client.close = MagicMock()
        db_module._client = mock_client
        db_module._db = MagicMock()

        await db_module.close_db()

        mock_client.close.assert_called_once()
        assert db_module._client is None
        assert db_module._db is None

    @pytest.mark.asyncio
    async def test_close_db_without_client(self):
        """No error when closing without an active connection."""
        db_module._client = None
        db_module._db = None

        await db_module.close_db()

        assert db_module._client is None
        assert db_module._db is None


class TestGetDB:
    """Tests for get_db function."""

    def test_get_db_when_initialized(self):
        """Returns the database instance."""
        mock_database = MagicMock()
        db_module._db = mock_database

        result = db_module.get_db()
        assert result is mock_database

    def test_get_db_not_initialized(self):
        """Raises RuntimeError when DB is not initialized."""
        db_module._db = None

        with pytest.raises(RuntimeError, match="Database not initialized"):
            db_module.get_db()
