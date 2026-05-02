"""Tests for portfolio rollup Pydantic models."""
import pytest
from src.models import (
    RollupValues, CapabilitySummary, EpicSummary, CapabilityTree,
    StoryItem, SnapshotPoint, SnapshotSeries, PortfolioListResponse,
    CycleMetrics, StatusTransition, RelatedLink, RelatedItems, ExportRequest,
)


class TestRollupValues:
    def test_defaults(self):
        r = RollupValues()
        assert r.cumulative_points == 0
        assert r.remaining_points == 0
        assert r.tshirt_rollup_points is None
        assert r.direct_child_count == 0
        assert r.descendant_count == 0
        assert r.computed_at is None

    def test_with_values(self):
        r = RollupValues(cumulative_points=100, remaining_points=40, tshirt_rollup_points=120)
        assert r.cumulative_points == 100
        assert r.tshirt_rollup_points == 120


class TestCapabilitySummary:
    def test_with_rollups(self):
        c = CapabilitySummary(
            key="CAP-1", summary="Test cap", status="In Progress",
            project_key="PROJ",
            rollups=RollupValues(cumulative_points=100, remaining_points=40),
        )
        assert c.key == "CAP-1"
        assert c.rollups.cumulative_points == 100
        assert c.issue_type == "Capability"

    def test_default_rollups(self):
        c = CapabilitySummary(key="CAP-2", summary="T", status="Active", project_key="P")
        assert c.rollups.cumulative_points == 0


class TestEpicSummary:
    def test_tshirt_fallback(self):
        e = EpicSummary(
            key="EPIC-1", summary="Test", status="Discovery",
            tshirt_size="M", uses_tshirt_fallback=True,
            tshirt_contribution_points=13,
            rollups=RollupValues(),
        )
        assert e.uses_tshirt_fallback is True
        assert e.tshirt_contribution_points == 13
        assert e.issue_type == "Epic"

    def test_no_fallback(self):
        e = EpicSummary(key="E-2", summary="T", status="Active", rollups=RollupValues())
        assert e.uses_tshirt_fallback is False
        assert e.tshirt_contribution_points is None


class TestCapabilityTree:
    def test_with_epics(self):
        tree = CapabilityTree(
            key="CAP-1", summary="Test", status="Active", project_key="PROJ",
            rollups=RollupValues(cumulative_points=200),
            epics=[EpicSummary(key="E-1", summary="E", status="Active", rollups=RollupValues())],
        )
        assert len(tree.epics) == 1
        assert tree.epics[0].key == "E-1"

    def test_empty_epics(self):
        tree = CapabilityTree(key="C-1", summary="T", status="A", project_key="P")
        assert tree.epics == []


class TestStoryItem:
    def test_with_schedule(self):
        s = StoryItem(
            key="S-1", summary="Story", status="In Progress", issue_type="Story",
            story_points=5, target_start="2026-05-01", target_end="2026-05-15",
            in_remaining=True,
        )
        assert s.target_start == "2026-05-01"
        assert s.in_remaining is True
        assert s.days_to_done is None

    def test_minimal(self):
        s = StoryItem(key="S-2", summary="S", status="Done", issue_type="Bug")
        assert s.story_points is None
        assert s.assignee == ""


class TestSnapshotSeries:
    def test_series(self):
        s = SnapshotSeries(
            key="CAP-1", metric="remaining",
            series=[SnapshotPoint(week="2026-04-07", value=100),
                    SnapshotPoint(week="2026-04-14", value=90)],
        )
        assert len(s.series) == 2
        assert s.series[1].value == 90

    def test_empty(self):
        s = SnapshotSeries(key="C-1", metric="cumulative")
        assert s.series == []


class TestPortfolioListResponse:
    def test_pagination(self):
        resp = PortfolioListResponse(data=[], total=47, page=1, page_size=20, has_more=True)
        assert resp.has_more is True
        assert resp.total == 47


class TestCycleMetrics:
    def test_values(self):
        cm = CycleMetrics(issue_key="S-1", dev_days=3.5, qa_days=2.0, stage_days=0.5, prod_days=0, total_days=6.0)
        assert cm.total_days == 6.0

    def test_defaults(self):
        cm = CycleMetrics(issue_key="S-2")
        assert cm.dev_days == 0
        assert cm.total_days == 0


class TestRelatedItems:
    def test_with_links(self):
        ri = RelatedItems(
            subtasks=[StoryItem(key="SUB-1", summary="S", status="Done", issue_type="Sub-task")],
            links=[RelatedLink(key="PROJ-99", summary="Linked", status="Open",
                               link_type="Blocks", direction="outward")],
        )
        assert len(ri.subtasks) == 1
        assert ri.links[0].link_type == "Blocks"
        assert ri.tests == []

    def test_empty(self):
        ri = RelatedItems()
        assert ri.subtasks == []
        assert ri.links == []
        assert ri.tests == []


class TestStatusTransition:
    def test_values(self):
        st = StatusTransition(
            issue_key="S-1", from_status="Open",
            to_status="In Progress", changed_at="2026-05-01T10:00:00Z",
        )
        assert st.from_status == "Open"
        assert st.to_status == "In Progress"


class TestExportRequest:
    def test_values(self):
        er = ExportRequest(
            project_key="PROJ", view="progress", filter="active",
            expanded=["CAP-1", "EPIC-1"], format="docx",
        )
        assert er.view == "progress"
        assert len(er.expanded) == 2

    def test_defaults(self):
        er = ExportRequest(project_key="P")
        assert er.view == "progress"
        assert er.filter == "all"
        assert er.format == "docx"
        assert er.expanded == []
