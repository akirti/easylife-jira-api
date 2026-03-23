"""MongoDB connection for jira-api service.

Uses Motor async driver. Creates indexes on startup.
"""
import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from src.config import Config

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None

# Collection name constants
COLL_JIRA_ISSUES = "jira_issues"
COLL_SYNC_CONFIG = "jira_sync_config"
COLL_ARCHIVES = "jira_issue_archives"


async def connect_db(config: Config) -> AsyncIOMotorDatabase:
    """Connect to MongoDB and create indexes.

    Args:
        config: Application config with database.uri and database.name.

    Returns:
        The Motor database instance.
    """
    global _client, _db  # noqa: PLW0603

    mongo_uri = config.get("database.uri", "mongodb://localhost:27017")
    db_name = config.get("database.name", "easylife_jira")

    logger.info("Connecting to MongoDB at %s, database=%s", mongo_uri, db_name)
    _client = AsyncIOMotorClient(mongo_uri)
    _db = _client[db_name]

    await _create_indexes(_db)
    logger.info("MongoDB connected and indexes created")
    return _db


async def _create_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create indexes on jira_issues collection for query performance."""
    try:
        issues = db[COLL_JIRA_ISSUES]
        await issues.create_index("key", unique=True)
        await issues.create_index("project_key")
        await issues.create_index("status")
        await issues.create_index("assignee_email")
        await issues.create_index("issue_type")
        await issues.create_index("parent_key")
        await issues.create_index("synced_at")
        await issues.create_index("comment_mentions")

        sync_cfg = db[COLL_SYNC_CONFIG]
        await sync_cfg.create_index("project_key", unique=True)

        archives = db[COLL_ARCHIVES]
        await archives.create_index("project_key")
        await archives.create_index("archived_at")
    except Exception as exc:
        logger.warning("Index creation failed (may require auth): %s", exc)


async def close_db() -> None:
    """Close MongoDB connection."""
    global _client, _db  # noqa: PLW0603
    if _client:
        _client.close()
        logger.info("MongoDB connection closed")
    _client = None
    _db = None


def get_db() -> AsyncIOMotorDatabase:
    """Get the current database instance.

    Raises:
        RuntimeError: If database is not initialized.
    """
    if _db is None:
        raise RuntimeError("Database not initialized — call connect_db() first")
    return _db
