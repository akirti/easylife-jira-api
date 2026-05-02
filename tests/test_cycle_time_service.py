import pytest
from datetime import datetime, timezone
from src.services.cycle_time_service import CycleTimeService


@pytest.fixture
def service():
    buckets = {
        "dev": ["In Progress", "Test in Dev"],
        "qa": ["Ready for Test", "In QA"],
        "stage": ["In Stage"],
        "prod": ["Released", "In Production"],
    }
    return CycleTimeService(buckets)


class TestComputeCycleMetrics:
    def test_basic_flow(self, service):
        """Issue goes through Dev -> QA -> Done."""
        transitions = [
            {"from_status": "To Do", "to_status": "In Progress",
             "changed_at": "2026-04-01T10:00:00Z"},
            {"from_status": "In Progress", "to_status": "Ready for Test",
             "changed_at": "2026-04-04T10:00:00Z"},
            {"from_status": "Ready for Test", "to_status": "Done",
             "changed_at": "2026-04-06T10:00:00Z"},
        ]
        result = service.compute_cycle_metrics(transitions)
        assert result["dev_days"] == pytest.approx(3.0, abs=0.1)
        assert result["qa_days"] == pytest.approx(2.0, abs=0.1)
        assert result["stage_days"] == 0
        assert result["prod_days"] == 0
        assert result["total_days"] == pytest.approx(5.0, abs=0.1)

    def test_empty_transitions(self, service):
        result = service.compute_cycle_metrics([])
        assert result["dev_days"] == 0
        assert result["total_days"] == 0

    def test_multiple_dev_stints(self, service):
        """Issue bounces back to dev after QA."""
        transitions = [
            {"from_status": "To Do", "to_status": "In Progress",
             "changed_at": "2026-04-01T10:00:00Z"},
            {"from_status": "In Progress", "to_status": "Ready for Test",
             "changed_at": "2026-04-03T10:00:00Z"},
            {"from_status": "Ready for Test", "to_status": "In Progress",
             "changed_at": "2026-04-04T10:00:00Z"},
            {"from_status": "In Progress", "to_status": "Done",
             "changed_at": "2026-04-06T10:00:00Z"},
        ]
        result = service.compute_cycle_metrics(transitions)
        # 2 days first dev + 2 days second dev = 4 dev days
        assert result["dev_days"] == pytest.approx(4.0, abs=0.1)
        # 1 day in QA
        assert result["qa_days"] == pytest.approx(1.0, abs=0.1)

    def test_unknown_statuses_ignored(self, service):
        """Statuses not in any bucket don't contribute to any phase."""
        transitions = [
            {"from_status": "To Do", "to_status": "Backlog",
             "changed_at": "2026-04-01T10:00:00Z"},
            {"from_status": "Backlog", "to_status": "In Progress",
             "changed_at": "2026-04-05T10:00:00Z"},
            {"from_status": "In Progress", "to_status": "Done",
             "changed_at": "2026-04-08T10:00:00Z"},
        ]
        result = service.compute_cycle_metrics(transitions)
        assert result["dev_days"] == pytest.approx(3.0, abs=0.1)
        assert result["total_days"] == pytest.approx(3.0, abs=0.1)
