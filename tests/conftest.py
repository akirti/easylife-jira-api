"""Shared test fixtures for jira-api tests.

Provides mock DB, mock Jira client, test config, auth headers,
and httpx AsyncClient for route testing.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# JWT settings for test tokens
TEST_JWT_SECRET = "test-secret-key-for-testing"
TEST_JWT_ALGORITHM = "HS256"
TEST_JWT_ISSUER = "easylife-auth"
TEST_JWT_AUDIENCE = "easylife-api"

# Test user data
TEST_USER_ID = "user-123"
TEST_USER_EMAIL = "testuser@example.com"
TEST_USER_NAME = "Test User"
TEST_USER_ROLES = ["viewer"]

TEST_ADMIN_ID = "admin-456"
TEST_ADMIN_EMAIL = "admin@example.com"
TEST_ADMIN_NAME = "Admin User"
TEST_ADMIN_ROLES = ["administrator"]


def _make_token(payload: Dict[str, Any]) -> str:
    """Generate a JWT token for testing."""
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGORITHM)


def _make_user_token() -> str:
    """Generate a regular user JWT token."""
    return _make_token({
        "sub": TEST_USER_ID,
        "email": TEST_USER_EMAIL,
        "username": TEST_USER_NAME,
        "roles": TEST_USER_ROLES,
        "groups": ["team-a"],
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "exp": int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp()),
    })


def _make_admin_token() -> str:
    """Generate an admin JWT token."""
    return _make_token({
        "sub": TEST_ADMIN_ID,
        "email": TEST_ADMIN_EMAIL,
        "username": TEST_ADMIN_NAME,
        "roles": TEST_ADMIN_ROLES,
        "groups": ["team-a", "admins"],
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "exp": int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp()),
    })


def _make_expired_token() -> str:
    """Generate an expired JWT token."""
    return _make_token({
        "sub": TEST_USER_ID,
        "email": TEST_USER_EMAIL,
        "username": TEST_USER_NAME,
        "roles": TEST_USER_ROLES,
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "exp": int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()),
    })


@pytest.fixture
def test_config_data() -> Dict[str, Any]:
    """Raw config data for tests."""
    return {
        "server": {
            "host": "0.0.0.0",
            "port": 8001,
            "cors_origins": ["http://localhost:3000"],
        },
        "jwt": {
            "secret_key": TEST_JWT_SECRET,
            "algorithm": TEST_JWT_ALGORITHM,
            "issuer": TEST_JWT_ISSUER,
            "audience": TEST_JWT_AUDIENCE,
        },
        "database": {
            "uri": "mongodb://localhost:27017",
            "name": "easylife_jira_test",
        },
        "jira": {
            "base_url": "https://test.atlassian.net",
            "email": "jira@example.com",
            "api_token": "fake-token",
            "project_key": "TEST",
            "jira_type": "cloud",
            "default_issue_type": "Task",
        },
        "sync": {
            "period_months": 3,
            "archive_after_months": 6,
            "interval_minutes": 30,
        },
        "gcs": {
            "bucket_name": "test-bucket",
            "credentials_json": "",
            "archive_prefix": "test_archives",
        },
        "attribute_map": {
            "customfield_10015": "start_date",
            "customfield_10016": "story_points",
        },
    }


@pytest.fixture
def test_config(test_config_data):
    """Create a Config instance from test data."""
    from src.config import Config

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(test_config_data, f)
        f.flush()
        config = Config(f.name)

    os.unlink(f.name)
    return config


@pytest.fixture
def auth_headers() -> Dict[str, str]:
    """Authorization headers with a valid user JWT token."""
    return {"Authorization": f"Bearer {_make_user_token()}"}


@pytest.fixture
def admin_headers() -> Dict[str, str]:
    """Authorization headers with a valid admin JWT token."""
    return {"Authorization": f"Bearer {_make_admin_token()}"}


@pytest.fixture
def expired_headers() -> Dict[str, str]:
    """Authorization headers with an expired JWT token."""
    return {"Authorization": f"Bearer {_make_expired_token()}"}


@pytest.fixture
def mock_db():
    """Mock MongoDB database with async collections."""
    db = MagicMock()

    # jira_issues collection
    issues_coll = AsyncMock()
    issues_coll.count_documents = AsyncMock(return_value=0)
    issues_coll.find_one = AsyncMock(return_value=None)
    issues_coll.update_one = AsyncMock()
    issues_coll.delete_many = AsyncMock()
    issues_coll.create_index = AsyncMock()

    # Mock find() to return a chainable cursor
    mock_cursor = AsyncMock()
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_cursor.sort = MagicMock(return_value=mock_cursor)
    mock_cursor.skip = MagicMock(return_value=mock_cursor)
    mock_cursor.limit = MagicMock(return_value=mock_cursor)
    issues_coll.find = MagicMock(return_value=mock_cursor)

    # Mock aggregate
    mock_agg_cursor = AsyncMock()
    mock_agg_cursor.to_list = AsyncMock(return_value=[])
    issues_coll.aggregate = MagicMock(return_value=mock_agg_cursor)

    # jira_sync_config collection
    sync_coll = AsyncMock()
    sync_coll.find_one = AsyncMock(return_value=None)
    sync_coll.update_one = AsyncMock()
    sync_coll.create_index = AsyncMock()

    # jira_issue_archives collection
    archives_coll = AsyncMock()
    archives_coll.find_one = AsyncMock(return_value=None)
    archives_coll.insert_one = AsyncMock()
    archives_coll.create_index = AsyncMock()
    archive_cursor = AsyncMock()
    archive_cursor.to_list = AsyncMock(return_value=[])
    archive_cursor.sort = MagicMock(return_value=archive_cursor)
    archives_coll.find = MagicMock(return_value=archive_cursor)

    db.__getitem__ = MagicMock(side_effect=lambda name: {
        "jira_issues": issues_coll,
        "jira_sync_config": sync_coll,
        "jira_issue_archives": archives_coll,
    }.get(name, AsyncMock()))

    db.command = AsyncMock(return_value={"ok": 1})

    return db


@pytest.fixture
def mock_jira_client():
    """Mock JiraClient with common methods."""
    client = MagicMock()
    client.search_issues = MagicMock(return_value=[])
    client.get_issue = MagicMock(return_value=None)
    client.create_issue = MagicMock(return_value=None)
    client.add_issue_link = MagicMock()
    client.get_boards = MagicMock(return_value=[])
    client.transition_issue = MagicMock()
    client.get_issue_changelog = MagicMock(return_value=[])
    return client


@pytest.fixture
def sample_issue_doc() -> Dict[str, Any]:
    """A sample JiraIssueDoc-shaped dict for testing."""
    return {
        "key": "TEST-1",
        "issue_id": "10001",
        "summary": "Sample issue for testing",
        "status": "In Progress",
        "status_category": "In Progress",
        "issue_type": "Task",
        "priority": "Medium",
        "assignee": "Test User",
        "assignee_email": TEST_USER_EMAIL,
        "reporter": "Admin User",
        "reporter_email": TEST_ADMIN_EMAIL,
        "project_key": "TEST",
        "project_name": "Test Project",
        "created": "2025-01-15T10:00:00.000+0000",
        "updated": "2025-03-01T14:30:00.000+0000",
        "due_date": "2025-04-01",
        "resolution_date": None,
        "labels": ["backend", "priority"],
        "components": ["api"],
        "description_text": "This is a test issue description",
        "parent_key": None,
        "subtask_keys": [],
        "linked_keys": [],
        "flagged": False,
        "blocker_reason": None,
        "sprint": "Sprint 5",
        "story_points": 3.0,
        "start_date": "2025-01-15",
        "team": "Backend",
        "comment_mentions": [],
        "days_in_status": 5.2,
        "url": "https://test.atlassian.net/browse/TEST-1",
        "synced_at": datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc),
    }


@pytest_asyncio.fixture
async def app_client(test_config, mock_db):
    """httpx AsyncClient with mocked DB and auth for route testing."""
    with patch("src.db._db", mock_db), \
         patch("src.db.get_db", return_value=mock_db):

        from src.auth import init_auth
        init_auth(test_config)

        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
