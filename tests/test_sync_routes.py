"""Tests for src/routes/sync.py — Sync API endpoints."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.routes.sync import init_sync_routes


class TestSyncRouteHelpers:
    """Tests for sync route initialization."""

    def test_init_sync_routes_sets_service(self):
        """init_sync_routes stores the service reference."""
        mock_service = MagicMock()
        init_sync_routes(mock_service)
        from src.routes.sync import _sync_service
        assert _sync_service is mock_service


class TestTriggerSync:
    """Tests for POST /sync/trigger endpoint."""

    @pytest.mark.asyncio
    async def test_trigger_sync_success(self, app_client, admin_headers, mock_db):
        """Admin can trigger sync successfully."""
        mock_service = AsyncMock()
        mock_service.sync_project = AsyncMock(return_value=42)
        init_sync_routes(mock_service)

        response = await app_client.post(
            "/api/v1/sync/trigger?project_key=TEST",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["issues_synced"] == 42
        assert data["status"] == "completed"

    @pytest.mark.asyncio
    async def test_trigger_sync_unauthorized(self, app_client):
        """No auth header returns 401 (no bearer)."""
        response = await app_client.post("/api/v1/sync/trigger")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_trigger_sync_non_admin(self, app_client, auth_headers):
        """Non-admin user gets 403 Forbidden."""
        mock_service = AsyncMock()
        init_sync_routes(mock_service)

        response = await app_client.post(
            "/api/v1/sync/trigger?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_trigger_sync_failure(self, app_client, admin_headers):
        """Sync failure returns 500."""
        mock_service = AsyncMock()
        mock_service.sync_project = AsyncMock(side_effect=RuntimeError("Jira down"))
        init_sync_routes(mock_service)

        response = await app_client.post(
            "/api/v1/sync/trigger?project_key=TEST",
            headers=admin_headers,
        )
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_trigger_sync_custom_months(self, app_client, admin_headers):
        """Custom months parameter is passed through."""
        mock_service = AsyncMock()
        mock_service.sync_project = AsyncMock(return_value=10)
        init_sync_routes(mock_service)

        response = await app_client.post(
            "/api/v1/sync/trigger?project_key=TEST&months=6",
            headers=admin_headers,
        )
        assert response.status_code == 200
        mock_service.sync_project.assert_called_once_with("TEST", 6)


class TestGetSyncConfig:
    """Tests for GET /sync/config endpoint."""

    @pytest.mark.asyncio
    async def test_get_config_found(self, app_client, admin_headers, mock_db):
        """Returns sync config when found."""
        mock_db["jira_sync_config"].find_one = AsyncMock(return_value={
            "project_key": "TEST",
            "sync_period_months": 3,
            "archive_after_months": 6,
            "interval_minutes": 30,
            "attribute_map": {},
            "last_sync": None,
            "last_sync_count": 0,
            "last_sync_status": "",
        })

        response = await app_client.get(
            "/api/v1/sync/config?project_key=TEST",
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["project_key"] == "TEST"

    @pytest.mark.asyncio
    async def test_get_config_not_found(self, app_client, admin_headers, mock_db):
        """Returns 404 when config not found."""
        mock_db["jira_sync_config"].find_one = AsyncMock(return_value=None)

        response = await app_client.get(
            "/api/v1/sync/config?project_key=UNKNOWN",
            headers=admin_headers,
        )
        assert response.status_code == 404


class TestUpdateSyncConfig:
    """Tests for PUT /sync/config endpoint."""

    @pytest.mark.asyncio
    async def test_update_config_success(self, app_client, admin_headers, mock_db):
        """Admin can update sync configuration."""
        updated_config = {
            "project_key": "TEST",
            "sync_period_months": 6,
            "archive_after_months": 12,
            "interval_minutes": 60,
            "attribute_map": {"customfield_10015": "start_date"},
            "last_sync": None,
            "last_sync_count": 0,
            "last_sync_status": "",
        }
        mock_db["jira_sync_config"].find_one = AsyncMock(return_value=updated_config)

        response = await app_client.put(
            "/api/v1/sync/config",
            headers=admin_headers,
            json={
                "project_key": "TEST",
                "sync_period_months": 6,
                "archive_after_months": 12,
                "interval_minutes": 60,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["project_key"] == "TEST"
        assert data["sync_period_months"] == 6
        mock_db["jira_sync_config"].update_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_config_non_admin(self, app_client, auth_headers):
        """Non-admin user gets 403."""
        response = await app_client.put(
            "/api/v1/sync/config",
            headers=auth_headers,
            json={"project_key": "TEST"},
        )
        assert response.status_code == 403


class TestArchiveIssues:
    """Tests for POST /sync/archive endpoint."""

    @pytest.mark.asyncio
    async def test_archive_success(self, app_client, admin_headers):
        """Admin can trigger archival successfully."""
        mock_service = AsyncMock()
        mock_service.archive_old_issues = AsyncMock(return_value={
            "archive_id": "TEST_20250301",
            "project_key": "TEST",
            "gcs_path": "archives/TEST_20250301.jsonl.gz",
            "issue_count": 25,
            "archived_at": "2025-03-01T00:00:00Z",
            "size_bytes": 5000,
        })
        init_sync_routes(mock_service)

        response = await app_client.post(
            "/api/v1/sync/archive?project_key=TEST&months=6",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["issue_count"] == 25
        assert data["archive_id"] == "TEST_20250301"

    @pytest.mark.asyncio
    async def test_archive_failure_returns_500(self, app_client, admin_headers):
        """Archive failure returns 500 error."""
        mock_service = AsyncMock()
        mock_service.archive_old_issues = AsyncMock(
            side_effect=RuntimeError("GCS upload failed")
        )
        init_sync_routes(mock_service)

        response = await app_client.post(
            "/api/v1/sync/archive?project_key=TEST",
            headers=admin_headers,
        )
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_archive_non_admin(self, app_client, auth_headers):
        """Non-admin user gets 403."""
        mock_service = AsyncMock()
        init_sync_routes(mock_service)

        response = await app_client.post(
            "/api/v1/sync/archive?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestListArchives:
    """Tests for GET /sync/archives endpoint."""

    @pytest.mark.asyncio
    async def test_list_archives_empty(self, app_client, admin_headers):
        """Returns empty list when no archives exist."""
        mock_service = AsyncMock()
        mock_service.get_archive_list = AsyncMock(return_value=[])
        init_sync_routes(mock_service)

        response = await app_client.get(
            "/api/v1/sync/archives",
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_archives_with_data(self, app_client, admin_headers):
        """Returns archive records."""
        mock_service = AsyncMock()
        mock_service.get_archive_list = AsyncMock(return_value=[
            {
                "archive_id": "TEST_20250301",
                "project_key": "TEST",
                "gcs_path": "archives/TEST_20250301.jsonl.gz",
                "issue_count": 50,
                "archived_at": "2025-03-01T00:00:00Z",
                "size_bytes": 12345,
            }
        ])
        init_sync_routes(mock_service)

        response = await app_client.get(
            "/api/v1/sync/archives",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["archive_id"] == "TEST_20250301"

    @pytest.mark.asyncio
    async def test_list_archives_with_project_filter(self, app_client, admin_headers):
        """Project key filter is passed through."""
        mock_service = AsyncMock()
        mock_service.get_archive_list = AsyncMock(return_value=[])
        init_sync_routes(mock_service)

        response = await app_client.get(
            "/api/v1/sync/archives?project_key=SPECIFIC",
            headers=admin_headers,
        )
        assert response.status_code == 200
        mock_service.get_archive_list.assert_called_once_with("SPECIFIC")
