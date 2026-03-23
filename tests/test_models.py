"""Tests for src/models.py — Pydantic model validation."""
from datetime import datetime, timezone

import pytest

from src.models import (
    ArchiveRecord,
    BoardSummary,
    CanvasEdge,
    CanvasNode,
    CanvasNodeData,
    CanvasResponse,
    CreateIssueRequest,
    DashboardStats,
    IssueListResponse,
    JiraIssueDoc,
    JiraSyncConfig,
    LinkIssueRequest,
    PriorityBreakdown,
    StatusBreakdown,
    TaskCountByType,
    TimelineEntry,
    TransitionRequest,
    TypeBreakdown,
)


class TestJiraIssueDoc:
    """Tests for JiraIssueDoc model."""

    def test_minimal_issue(self):
        """Issue with only key field is valid."""
        doc = JiraIssueDoc(key="TEST-1")
        assert doc.key == "TEST-1"
        assert doc.summary == ""
        assert doc.labels == []
        assert doc.flagged is False

    def test_full_issue(self, sample_issue_doc):
        """All fields populate correctly."""
        doc = JiraIssueDoc(**sample_issue_doc)
        assert doc.key == "TEST-1"
        assert doc.status == "In Progress"
        assert doc.story_points == 3.0
        assert "backend" in doc.labels

    def test_optional_fields_default_none(self):
        """Optional fields default to None."""
        doc = JiraIssueDoc(key="X-1")
        assert doc.assignee is None
        assert doc.due_date is None
        assert doc.days_in_status is None

    def test_linked_keys_structure(self):
        """linked_keys accepts list of dicts."""
        doc = JiraIssueDoc(
            key="TEST-1",
            linked_keys=[{"key": "TEST-2", "type": "Blocks"}],
        )
        assert len(doc.linked_keys) == 1
        assert doc.linked_keys[0]["key"] == "TEST-2"

    def test_empty_lists_default(self):
        """List fields default to empty lists."""
        doc = JiraIssueDoc(key="X-1")
        assert doc.subtask_keys == []
        assert doc.components == []
        assert doc.comment_mentions == []


class TestJiraSyncConfig:
    """Tests for JiraSyncConfig model."""

    def test_defaults(self):
        """Sync config has sensible defaults."""
        cfg = JiraSyncConfig(project_key="SCEN")
        assert cfg.sync_period_months == 3
        assert cfg.archive_after_months == 6
        assert cfg.last_sync is None

    def test_custom_values(self):
        """Custom values override defaults."""
        cfg = JiraSyncConfig(
            project_key="PROJ",
            sync_period_months=6,
            attribute_map={"cf_100": "custom_field"},
        )
        assert cfg.sync_period_months == 6
        assert cfg.attribute_map["cf_100"] == "custom_field"

    def test_last_sync_datetime(self):
        """last_sync accepts datetime."""
        now = datetime.now(timezone.utc)
        cfg = JiraSyncConfig(project_key="X", last_sync=now)
        assert cfg.last_sync == now


class TestCanvasModels:
    """Tests for CanvasNode, CanvasEdge, CanvasResponse."""

    def test_canvas_node(self):
        """CanvasNode with data creates correctly."""
        node = CanvasNode(
            id="TEST-1",
            data=CanvasNodeData(key="TEST-1", summary="A task"),
        )
        assert node.id == "TEST-1"
        assert node.data.key == "TEST-1"
        assert node.position == {"x": 0, "y": 0}

    def test_canvas_edge(self):
        """CanvasEdge creates with all fields."""
        edge = CanvasEdge(id="e1", source="A", target="B", label="blocks")
        assert edge.source == "A"
        assert edge.target == "B"
        assert edge.animated is False

    def test_canvas_response_empty(self):
        """Empty CanvasResponse is valid."""
        resp = CanvasResponse()
        assert resp.nodes == []
        assert resp.edges == []


class TestTimelineEntry:
    """Tests for TimelineEntry model."""

    def test_basic_entry(self):
        """Basic timeline entry with dates."""
        entry = TimelineEntry(
            key="TEST-1",
            summary="Task",
            start="2025-01-01",
            end="2025-02-01",
            status="Done",
        )
        assert entry.key == "TEST-1"
        assert entry.overdue is False

    def test_overdue_flag(self):
        """Overdue flag can be set."""
        entry = TimelineEntry(key="X-1", overdue=True, status="In Progress")
        assert entry.overdue is True

    def test_optional_dates(self):
        """Dates are optional."""
        entry = TimelineEntry(key="X-1", status="Open")
        assert entry.start is None
        assert entry.end is None


class TestDashboardStats:
    """Tests for DashboardStats model."""

    def test_default_stats(self):
        """Default stats are all zero/empty."""
        stats = DashboardStats()
        assert stats.total == 0
        assert stats.blockers == 0
        assert stats.by_status == []

    def test_with_breakdowns(self):
        """Stats with breakdown lists."""
        stats = DashboardStats(
            total=10,
            by_status=[StatusBreakdown(status="Open", count=5)],
            by_type=[TypeBreakdown(issue_type="Bug", count=3)],
            by_priority=[PriorityBreakdown(priority="High", count=2)],
            blockers=1,
            overdue=2,
            my_mentions=3,
        )
        assert stats.total == 10
        assert stats.by_status[0].count == 5


class TestRequestModels:
    """Tests for API request models."""

    def test_create_issue_minimal(self):
        """Minimal create request."""
        req = CreateIssueRequest(project_key="TEST", summary="New task")
        assert req.issue_type == "Task"
        assert req.priority == "Medium"
        assert req.labels == []

    def test_create_issue_full(self):
        """Full create request with all fields."""
        req = CreateIssueRequest(
            project_key="PROJ",
            summary="Bug report",
            description="Steps to reproduce...",
            issue_type="Bug",
            priority="High",
            assignee_email="dev@example.com",
            parent_key="PROJ-100",
            labels=["bug", "urgent"],
        )
        assert req.issue_type == "Bug"
        assert req.assignee_email == "dev@example.com"

    def test_link_issue_default_type(self):
        """Link request defaults to 'Relates'."""
        req = LinkIssueRequest(target_key="TEST-2")
        assert req.link_type == "Relates"

    def test_transition_request(self):
        """Transition request captures target name."""
        req = TransitionRequest(transition_name="Done")
        assert req.transition_name == "Done"


class TestIssueListResponse:
    """Tests for IssueListResponse model."""

    def test_empty_response(self):
        """Empty response with defaults."""
        resp = IssueListResponse()
        assert resp.items == []
        assert resp.total == 0
        assert resp.page == 1
        assert resp.page_size == 50

    def test_with_items(self, sample_issue_doc):
        """Response with issue items."""
        resp = IssueListResponse(
            items=[JiraIssueDoc(**sample_issue_doc)],
            total=1,
            page=1,
            page_size=50,
        )
        assert len(resp.items) == 1
        assert resp.items[0].key == "TEST-1"
