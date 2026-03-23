"""Tests for src/routes/issues.py — Issue API endpoints."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.routes.issues import init_issue_routes


class TestIssueRouteInit:
    """Tests for issue route initialization."""

    def test_init_issue_routes_sets_deps(self, test_config):
        """init_issue_routes stores client and config."""
        mock_client = MagicMock()
        init_issue_routes(mock_client, test_config)
        from src.routes.issues import _jira_client, _config
        assert _jira_client is mock_client
        assert _config is test_config


class TestGetIssueDetail:
    """Tests for GET /issues/{key} endpoint."""

    @pytest.mark.asyncio
    async def test_get_issue_found(self, app_client, auth_headers, mock_db, sample_issue_doc):
        """Returns issue when found in MongoDB."""
        mock_db["jira_issues"].find_one = AsyncMock(return_value=sample_issue_doc)

        response = await app_client.get(
            "/api/v1/issues/TEST-1",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["key"] == "TEST-1"

    @pytest.mark.asyncio
    async def test_get_issue_not_found(self, app_client, auth_headers, mock_db):
        """Returns 404 when issue not found."""
        mock_db["jira_issues"].find_one = AsyncMock(return_value=None)

        response = await app_client.get(
            "/api/v1/issues/MISSING-1",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_issue_unauthorized(self, app_client):
        """Returns 403 without auth."""
        response = await app_client.get("/api/v1/issues/TEST-1")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_issue_expired_token(self, app_client, expired_headers, mock_db):
        """Returns 401 with expired token."""
        response = await app_client.get(
            "/api/v1/issues/TEST-1",
            headers=expired_headers,
        )
        assert response.status_code == 401


class TestCreateIssue:
    """Tests for POST /issues/create endpoint."""

    @pytest.mark.asyncio
    async def test_create_issue_success(self, app_client, auth_headers, mock_db, test_config):
        """Successfully creates an issue via Jira and syncs to MongoDB."""
        mock_client = MagicMock()
        created_issue = MagicMock()
        created_issue.key = "TEST-100"
        created_issue.id = "10100"
        created_issue.fields = MagicMock(spec=[])
        created_issue.fields.summary = "New issue"
        created_issue.fields.status = MagicMock()
        created_issue.fields.status.__str__ = lambda self: "Open"
        created_issue.fields.status.statusCategory = MagicMock()
        created_issue.fields.status.statusCategory.__str__ = lambda self: "To Do"
        created_issue.fields.issuetype = MagicMock()
        created_issue.fields.issuetype.__str__ = lambda self: "Task"
        created_issue.fields.priority = MagicMock()
        created_issue.fields.priority.__str__ = lambda self: "Medium"
        created_issue.fields.assignee = None
        created_issue.fields.reporter = None
        created_issue.fields.project = MagicMock()
        created_issue.fields.project.key = "TEST"
        created_issue.fields.project.name = "Test Project"
        created_issue.fields.created = "2025-03-01T00:00:00"
        created_issue.fields.updated = "2025-03-01T00:00:00"
        created_issue.fields.duedate = None
        created_issue.fields.resolutiondate = None
        created_issue.fields.labels = []
        created_issue.fields.components = []
        created_issue.fields.description = None
        created_issue.fields.parent = None
        created_issue.fields.subtasks = []
        created_issue.fields.issuelinks = []
        created_issue.fields.flagged = False
        created_issue.fields.comment = None
        mock_client.create_issue.return_value = created_issue

        init_issue_routes(mock_client, test_config)

        response = await app_client.post(
            "/api/v1/issues/create",
            headers=auth_headers,
            json={"project_key": "TEST", "summary": "New issue"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["key"] == "TEST-100"

    @pytest.mark.asyncio
    async def test_create_issue_jira_error(self, app_client, auth_headers, test_config):
        """Jira error during creation returns 500."""
        mock_client = MagicMock()
        mock_client.create_issue.side_effect = RuntimeError("Jira unavailable")
        init_issue_routes(mock_client, test_config)

        response = await app_client.post(
            "/api/v1/issues/create",
            headers=auth_headers,
            json={"project_key": "TEST", "summary": "Fail"},
        )
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_create_issue_missing_summary(self, app_client, auth_headers, test_config):
        """Missing required summary returns 422."""
        mock_client = MagicMock()
        init_issue_routes(mock_client, test_config)

        response = await app_client.post(
            "/api/v1/issues/create",
            headers=auth_headers,
            json={"project_key": "TEST"},
        )
        assert response.status_code == 422


class TestLinkIssue:
    """Tests for POST /issues/{key}/link endpoint."""

    @pytest.mark.asyncio
    async def test_link_issue_success(self, app_client, auth_headers, mock_db, test_config):
        """Successfully links two issues."""
        mock_client = MagicMock()
        init_issue_routes(mock_client, test_config)

        response = await app_client.post(
            "/api/v1/issues/TEST-1/link",
            headers=auth_headers,
            json={"target_key": "TEST-2", "link_type": "Blocks"},
        )
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_link_issue_jira_error(self, app_client, auth_headers, test_config):
        """Jira error during linking returns 500."""
        mock_client = MagicMock()
        mock_client.add_issue_link.side_effect = RuntimeError("Link failed")
        init_issue_routes(mock_client, test_config)

        response = await app_client.post(
            "/api/v1/issues/TEST-1/link",
            headers=auth_headers,
            json={"target_key": "TEST-2"},
        )
        assert response.status_code == 500


class TestTransitionIssue:
    """Tests for POST /issues/{key}/transition endpoint."""

    @pytest.mark.asyncio
    async def test_transition_success(self, app_client, auth_headers, mock_db, test_config):
        """Successfully transitions an issue."""
        mock_client = MagicMock()
        init_issue_routes(mock_client, test_config)

        response = await app_client.post(
            "/api/v1/issues/TEST-1/transition",
            headers=auth_headers,
            json={"transition_name": "Done"},
        )
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_transition_jira_error(self, app_client, auth_headers, test_config):
        """Jira error during transition returns 500."""
        mock_client = MagicMock()
        mock_client.transition_issue.side_effect = RuntimeError("Transition denied")
        init_issue_routes(mock_client, test_config)

        response = await app_client.post(
            "/api/v1/issues/TEST-1/transition",
            headers=auth_headers,
            json={"transition_name": "Done"},
        )
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_transition_unauthorized(self, app_client):
        """No auth returns 403."""
        response = await app_client.post(
            "/api/v1/issues/TEST-1/transition",
            json={"transition_name": "Done"},
        )
        assert response.status_code == 401
