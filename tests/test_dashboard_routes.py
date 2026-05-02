"""Tests for src/routes/dashboard.py — Dashboard API endpoints."""
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.routes.dashboard import (
    _build_canvas_graph,
    _build_issue_filter,
    _canonical_edge_id,
    _count_types,
)


class TestBuildIssueFilter:
    """Tests for the _build_issue_filter helper."""

    def test_base_filter_only(self):
        """Only project_key when no filters provided."""
        result = _build_issue_filter("TEST", None, None, None, None, None)
        assert result == {"project_key": "TEST"}

    def test_status_filter(self):
        """Status filter adds status field."""
        result = _build_issue_filter("TEST", "In Progress", None, None, None, None)
        assert result["status"] == "In Progress"

    def test_issue_type_filter(self):
        """Issue type filter adds issue_type field."""
        result = _build_issue_filter("TEST", None, "Bug", None, None, None)
        assert result["issue_type"] == "Bug"

    def test_assignee_filter_uses_regex(self):
        """Assignee filter uses case-insensitive regex."""
        result = _build_issue_filter("TEST", None, None, "John", None, None)
        assert "$regex" in result["assignee"]
        assert result["assignee"]["$options"] == "i"

    def test_flagged_filter(self):
        """Flagged filter adds boolean field."""
        result = _build_issue_filter("TEST", None, None, None, True, None)
        assert result["flagged"] is True

    def test_sprint_filter(self):
        """Sprint filter adds sprint field."""
        result = _build_issue_filter("TEST", None, None, None, None, "Sprint 5")
        assert result["sprint"] == "Sprint 5"

    def test_all_filters(self):
        """All filters combined."""
        result = _build_issue_filter("TEST", "Open", "Task", "Jane", False, "Sprint 1")
        assert result["project_key"] == "TEST"
        assert result["status"] == "Open"
        assert result["issue_type"] == "Task"
        assert result["flagged"] is False
        assert result["sprint"] == "Sprint 1"

    def test_build_issue_filter_escapes_regex_chars(self):
        """Regex special characters in assignee are escaped to prevent ReDoS."""
        result = _build_issue_filter("PROJ", None, None, "user.*+?()", None, None)
        regex_val = result["assignee"]["$regex"]
        # Should be escaped — no raw regex operators
        assert ".*" not in regex_val
        assert re.escape("user.*+?()") == regex_val


class TestBuildCanvasGraph:
    """Tests for the _build_canvas_graph helper."""

    def test_empty_issues(self):
        """Empty issues list produces empty graph."""
        nodes, edges = _build_canvas_graph([])
        assert nodes == []
        assert edges == []

    def test_single_issue_no_edges(self):
        """Single issue produces one node, no edges."""
        issues = [{
            "key": "TEST-1",
            "summary": "Task",
            "status": "Open",
            "status_category": "To Do",
            "issue_type": "Task",
            "priority": "Medium",
            "assignee": None,
            "flagged": False,
            "days_in_status": 2.0,
            "parent_key": None,
            "linked_keys": [],
        }]
        nodes, edges = _build_canvas_graph(issues)
        assert len(nodes) == 1
        assert nodes[0].id == "TEST-1"
        assert edges == []

    def test_parent_child_edge(self):
        """Parent-child relationship creates an edge."""
        issues = [
            {"key": "TEST-1", "summary": "Epic", "status": "Open", "status_category": "",
             "issue_type": "Epic", "priority": None, "assignee": None, "flagged": False,
             "days_in_status": None, "parent_key": None, "linked_keys": []},
            {"key": "TEST-2", "summary": "Story", "status": "Open", "status_category": "",
             "issue_type": "Story", "priority": None, "assignee": None, "flagged": False,
             "days_in_status": None, "parent_key": "TEST-1", "linked_keys": []},
        ]
        nodes, edges = _build_canvas_graph(issues)
        assert len(nodes) == 2
        assert len(edges) == 1
        assert edges[0].source == "TEST-1"
        assert edges[0].target == "TEST-2"

    def test_blocker_edge_animated(self):
        """Blocker edges are animated."""
        issues = [
            {"key": "A-1", "summary": "", "status": "", "status_category": "",
             "issue_type": "", "priority": None, "assignee": None, "flagged": False,
             "days_in_status": None, "parent_key": None,
             "linked_keys": [{"key": "A-2", "type": "blocks"}]},
            {"key": "A-2", "summary": "", "status": "", "status_category": "",
             "issue_type": "", "priority": None, "assignee": None, "flagged": False,
             "days_in_status": None, "parent_key": None, "linked_keys": []},
        ]
        nodes, edges = _build_canvas_graph(issues)
        blocker_edges = [e for e in edges if e.animated]
        assert len(blocker_edges) == 1

    def test_no_duplicate_edges(self):
        """Bidirectional links produce only one edge."""
        issues = [
            {"key": "A-1", "summary": "", "status": "", "status_category": "",
             "issue_type": "", "priority": None, "assignee": None, "flagged": False,
             "days_in_status": None, "parent_key": None,
             "linked_keys": [{"key": "A-2", "type": "relates to"}]},
            {"key": "A-2", "summary": "", "status": "", "status_category": "",
             "issue_type": "", "priority": None, "assignee": None, "flagged": False,
             "days_in_status": None, "parent_key": None,
             "linked_keys": [{"key": "A-1", "type": "relates to"}]},
        ]
        nodes, edges = _build_canvas_graph(issues)
        assert len(edges) == 1

    def test_linked_key_outside_graph_ignored(self):
        """Links to issues not in the graph are ignored."""
        issues = [
            {"key": "A-1", "summary": "", "status": "", "status_category": "",
             "issue_type": "", "priority": None, "assignee": None, "flagged": False,
             "days_in_status": None, "parent_key": None,
             "linked_keys": [{"key": "EXTERNAL-99", "type": "relates to"}]},
        ]
        nodes, edges = _build_canvas_graph(issues)
        assert edges == []


class TestCanonicalEdgeId:
    """Tests for _canonical_edge_id helper."""

    def test_sorted_order(self):
        assert _canonical_edge_id("B-1", "A-1") == "A-1->B-1"
        assert _canonical_edge_id("A-1", "B-1") == "A-1->B-1"


class TestCountTypes:
    """Tests for _count_types helper."""

    def test_empty_list(self):
        assert _count_types([]) == {}

    def test_counts(self):
        result = _count_types(["Task", "Bug", "Task", "Story", "Task"])
        assert result["Task"] == 3
        assert result["Bug"] == 1
        assert result["Story"] == 1


# ---------------------------------------------------------------------------
# HTTP-level route tests
# ---------------------------------------------------------------------------

def _make_sample_issue(key="TEST-1", **overrides):
    """Create a sample issue dict for mocking DB results."""
    base = {
        "key": key,
        "issue_id": "10001",
        "summary": f"Sample issue {key}",
        "status": "In Progress",
        "status_category": "In Progress",
        "issue_type": "Task",
        "priority": "Medium",
        "assignee": "Test User",
        "assignee_email": "testuser@example.com",
        "reporter": "Admin User",
        "reporter_email": "admin@example.com",
        "project_key": "TEST",
        "project_name": "Test Project",
        "created": "2025-01-15T10:00:00.000+0000",
        "updated": "2025-03-01T14:30:00.000+0000",
        "due_date": "2025-04-01",
        "resolution_date": None,
        "labels": ["backend"],
        "components": ["api"],
        "description_text": "Test description",
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
        "url": f"https://test.atlassian.net/browse/{key}",
        "synced_at": "2025-03-01T12:00:00+00:00",
    }
    base.update(overrides)
    return base


class TestGetStatsEndpoint:
    """Tests for GET /dashboard/stats endpoint."""

    @pytest.mark.asyncio
    async def test_get_stats_basic(self, app_client, auth_headers, mock_db):
        """Returns aggregated stats with all fields."""
        issues_coll = mock_db["jira_issues"]
        issues_coll.count_documents = AsyncMock(return_value=10)

        mock_agg_cursor = AsyncMock()
        mock_agg_cursor.to_list = AsyncMock(return_value=[
            {"_id": "Open", "count": 5},
            {"_id": "In Progress", "count": 3},
        ])
        issues_coll.aggregate = MagicMock(return_value=mock_agg_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/stats?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "by_status" in data
        assert "by_type" in data
        assert "by_priority" in data
        assert "blockers" in data
        assert "overdue" in data
        assert "my_mentions" in data

    @pytest.mark.asyncio
    async def test_get_stats_empty_project(self, app_client, auth_headers, mock_db):
        """Returns zeros when project has no issues."""
        issues_coll = mock_db["jira_issues"]
        issues_coll.count_documents = AsyncMock(return_value=0)

        mock_agg_cursor = AsyncMock()
        mock_agg_cursor.to_list = AsyncMock(return_value=[])
        issues_coll.aggregate = MagicMock(return_value=mock_agg_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/stats?project_key=EMPTY",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["blockers"] == 0
        assert data["overdue"] == 0

    @pytest.mark.asyncio
    async def test_get_stats_unauthorized(self, app_client):
        """Returns 401 without auth header."""
        response = await app_client.get("/api/v1/dashboard/stats")
        assert response.status_code in (401, 403)


class TestGetIssuesEndpoint:
    """Tests for GET /dashboard/issues endpoint."""

    @pytest.mark.asyncio
    async def test_get_issues_success(self, app_client, auth_headers, mock_db):
        """Returns paginated issues."""
        issues_coll = mock_db["jira_issues"]
        issues_coll.count_documents = AsyncMock(return_value=2)

        sample_issues = [_make_sample_issue("TEST-1"), _make_sample_issue("TEST-2")]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=sample_issues)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/issues?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert data["page"] == 1

    @pytest.mark.asyncio
    async def test_get_issues_empty(self, app_client, auth_headers, mock_db):
        """Returns empty list when no issues match."""
        issues_coll = mock_db["jira_issues"]
        issues_coll.count_documents = AsyncMock(return_value=0)

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/issues?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_get_issues_with_filters(self, app_client, auth_headers, mock_db):
        """Filters are passed through to the query."""
        issues_coll = mock_db["jira_issues"]
        issues_coll.count_documents = AsyncMock(return_value=1)

        sample = [_make_sample_issue("TEST-1", status="Open", issue_type="Bug")]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=sample)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/issues?project_key=TEST&status=Open&issue_type=Bug&flagged=true",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_get_issues_pagination(self, app_client, auth_headers, mock_db):
        """Pagination parameters are respected."""
        issues_coll = mock_db["jira_issues"]
        issues_coll.count_documents = AsyncMock(return_value=100)

        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[_make_sample_issue("TEST-51")])
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/issues?project_key=TEST&page=2&page_size=10",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 2
        assert data["page_size"] == 10


class TestGetCanvasEndpoint:
    """Tests for GET /dashboard/canvas endpoint."""

    @pytest.mark.asyncio
    async def test_get_canvas_success(self, app_client, auth_headers, mock_db):
        """Returns nodes and edges for the canvas."""
        issues_coll = mock_db["jira_issues"]
        sample = [
            _make_sample_issue("TEST-1", parent_key=None),
            _make_sample_issue("TEST-2", parent_key="TEST-1"),
        ]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=sample)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/canvas?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 2

    @pytest.mark.asyncio
    async def test_get_canvas_empty(self, app_client, auth_headers, mock_db):
        """Returns empty nodes/edges for empty project."""
        issues_coll = mock_db["jira_issues"]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/canvas?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["nodes"] == []
        assert data["edges"] == []

    @pytest.mark.asyncio
    async def test_get_canvas_with_epic_filter(self, app_client, auth_headers, mock_db):
        """Epic key filter narrows results."""
        issues_coll = mock_db["jira_issues"]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[
            _make_sample_issue("TEST-1"),
        ])
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/canvas?project_key=TEST&epic_key=TEST-1",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 1


class TestGetTimelineEndpoint:
    """Tests for GET /dashboard/timeline endpoint."""

    @pytest.mark.asyncio
    async def test_get_timeline_success(self, app_client, auth_headers, mock_db):
        """Returns timeline entries."""
        issues_coll = mock_db["jira_issues"]
        sample = [_make_sample_issue("TEST-1"), _make_sample_issue("TEST-2")]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=sample)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/timeline?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["key"] == "TEST-1"

    @pytest.mark.asyncio
    async def test_get_timeline_empty(self, app_client, auth_headers, mock_db):
        """Returns empty list for empty project."""
        issues_coll = mock_db["jira_issues"]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/timeline?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_get_timeline_with_filters(self, app_client, auth_headers, mock_db):
        """Timeline respects assignee, issue_type, and sprint filters."""
        issues_coll = mock_db["jira_issues"]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[_make_sample_issue("TEST-1")])
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/timeline?project_key=TEST&assignee=John&issue_type=Task&sprint=Sprint+5",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_get_timeline_overdue_issue(self, app_client, auth_headers, mock_db):
        """Overdue issues have overdue=true in the response."""
        issues_coll = mock_db["jira_issues"]
        overdue_issue = _make_sample_issue(
            "TEST-1",
            due_date="2020-01-01",
            resolution_date=None,
        )
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[overdue_issue])
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/timeline?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data[0]["overdue"] is True


class TestGetMyMentionsEndpoint:
    """Tests for GET /dashboard/my-mentions endpoint."""

    @pytest.mark.asyncio
    async def test_get_my_mentions_success(self, app_client, auth_headers, mock_db):
        """Returns issues where user is mentioned."""
        issues_coll = mock_db["jira_issues"]
        sample = [_make_sample_issue("TEST-1", comment_mentions=["user-123"])]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=sample)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/my-mentions?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["key"] == "TEST-1"

    @pytest.mark.asyncio
    async def test_get_my_mentions_empty(self, app_client, auth_headers, mock_db):
        """Returns empty list when user has no mentions."""
        issues_coll = mock_db["jira_issues"]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/my-mentions?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == []


class TestGetBoardsEndpoint:
    """Tests for GET /dashboard/boards endpoint."""

    @pytest.mark.asyncio
    async def test_get_boards_success(self, app_client, auth_headers, mock_db):
        """Returns board summaries grouped by sprint."""
        issues_coll = mock_db["jira_issues"]
        mock_agg_cursor = AsyncMock()
        mock_agg_cursor.to_list = AsyncMock(return_value=[
            {
                "_id": {"sprint": "Sprint 5"},
                "total": 10,
                "types": ["Task", "Task", "Bug", "Story"],
            },
            {
                "_id": {"sprint": None},
                "total": 3,
                "types": ["Task", "Bug", "Task"],
            },
        ])
        issues_coll.aggregate = MagicMock(return_value=mock_agg_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/boards?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # First board is Sprint 5
        assert data[0]["board_name"] == "Sprint 5"
        assert data[0]["total"] == 10
        # Second board has no sprint, so it's named "Backlog"
        assert data[1]["board_name"] == "Backlog"

    @pytest.mark.asyncio
    async def test_get_boards_empty(self, app_client, auth_headers, mock_db):
        """Returns empty list for project with no issues."""
        issues_coll = mock_db["jira_issues"]
        mock_agg_cursor = AsyncMock()
        mock_agg_cursor.to_list = AsyncMock(return_value=[])
        issues_coll.aggregate = MagicMock(return_value=mock_agg_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/boards?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == []


class TestGetBlockersEndpoint:
    """Tests for GET /dashboard/blockers endpoint."""

    @pytest.mark.asyncio
    async def test_get_blockers_success(self, app_client, auth_headers, mock_db):
        """Returns flagged/blocked issues."""
        issues_coll = mock_db["jira_issues"]
        sample = [_make_sample_issue("TEST-1", flagged=True)]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=sample)
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/blockers?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["key"] == "TEST-1"

    @pytest.mark.asyncio
    async def test_get_blockers_empty(self, app_client, auth_headers, mock_db):
        """Returns empty list when no blockers exist."""
        issues_coll = mock_db["jira_issues"]
        mock_cursor = AsyncMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        issues_coll.find = MagicMock(return_value=mock_cursor)

        response = await app_client.get(
            "/api/v1/dashboard/blockers?project_key=TEST",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == []
