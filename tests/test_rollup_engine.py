"""Tests for the portfolio rollup computation engine.

Covers:
- T-shirt size to story-point conversion
- Epic-level rollup (cumulative + remaining)
- Capability-level rollup (cumulative + remaining + tshirt fallback)
- Full recompute_all integration with mocked DB
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.rollup_engine import RollupEngine


@pytest.fixture
def config():
    cfg = MagicMock()
    def config_get(key, default=None):
        return {
            "portfolio.capability_issue_type": "Capability",
            "portfolio.remaining_statuses": ["Backlog", "In Progress", "On Deck"],
            "portfolio.tshirt_fallback_statuses": ["Backlog", "Discovery"],
            "portfolio.tshirt_size_map": {"XS": 2, "S": 5, "M": 13, "L": 21, "XL": 34},
        }.get(key, default)
    cfg.get = MagicMock(side_effect=config_get)
    return cfg


@pytest.fixture
def engine(config):
    return RollupEngine(config)


class TestTshirtToPoints:
    def test_known_size(self, engine):
        assert engine._tshirt_to_points("M") == 13
        assert engine._tshirt_to_points("XL") == 34

    def test_none(self, engine):
        assert engine._tshirt_to_points(None) == 0

    def test_unknown(self, engine):
        assert engine._tshirt_to_points("XXXL") == 0


class TestComputeEpicRollup:
    def test_basic(self, engine):
        stories = [
            {"story_points": 5, "status": "Done"},
            {"story_points": 8, "status": "In Progress"},
            {"story_points": 3, "status": "Backlog"},
            {"story_points": None, "status": "Done"},
        ]
        cumulative, remaining = engine._compute_epic_rollup(stories)
        assert cumulative == 16  # 5 + 8 + 3 + 0
        assert remaining == 11  # 8 (In Progress) + 3 (Backlog)

    def test_empty(self, engine):
        cumulative, remaining = engine._compute_epic_rollup([])
        assert cumulative == 0
        assert remaining == 0

    def test_all_done(self, engine):
        stories = [
            {"story_points": 10, "status": "Done"},
            {"story_points": 5, "status": "Released"},
        ]
        cumulative, remaining = engine._compute_epic_rollup(stories)
        assert cumulative == 15
        assert remaining == 0


class TestComputeCapabilityRollup:
    def test_with_tshirt_fallback(self, engine):
        epic_rollups = [
            {"epic_key": "E-1", "epic_status": "In Progress", "epic_tshirt": "L",
             "cumulative": 89, "remaining": 34},
            {"epic_key": "E-2", "epic_status": "Discovery", "epic_tshirt": "M",
             "cumulative": 0, "remaining": 0},
        ]
        result = engine._compute_capability_rollup(epic_rollups)
        assert result["cumulative_points"] == 89
        assert result["remaining_points"] == 34
        assert result["tshirt_rollup_points"] == 89 + 13  # E-1 real + E-2 M=13

    def test_no_fallback(self, engine):
        epic_rollups = [
            {"epic_key": "E-1", "epic_status": "In Progress", "epic_tshirt": "L",
             "cumulative": 50, "remaining": 20},
        ]
        result = engine._compute_capability_rollup(epic_rollups)
        assert result["cumulative_points"] == 50
        assert result["tshirt_rollup_points"] == 50

    def test_empty(self, engine):
        result = engine._compute_capability_rollup([])
        assert result["cumulative_points"] == 0
        assert result["remaining_points"] == 0
        assert result["tshirt_rollup_points"] == 0


class TestRecomputeAll:
    @pytest.mark.asyncio
    async def test_full_recompute(self, engine):
        mock_db = MagicMock()
        issues_coll = AsyncMock()
        rollups_coll = AsyncMock()

        # Capabilities
        cap_cursor = AsyncMock()
        cap_cursor.to_list = AsyncMock(return_value=[
            {"key": "CAP-1", "summary": "Cap 1", "status": "Active",
             "issue_type": "Capability", "project_key": "PROJ", "tshirt_size": "XL"},
        ])

        # Epics
        epic_cursor = AsyncMock()
        epic_cursor.to_list = AsyncMock(return_value=[
            {"key": "EPIC-1", "parent_key": "CAP-1", "status": "In Progress",
             "issue_type": "Epic", "tshirt_size": "L"},
        ])

        # Stories
        story_cursor = AsyncMock()
        story_cursor.to_list = AsyncMock(return_value=[
            {"key": "S-1", "epic_link_key": "EPIC-1", "story_points": 5, "status": "Done"},
            {"key": "S-2", "epic_link_key": "EPIC-1", "story_points": 8, "status": "In Progress"},
        ])

        def mock_find(query, projection=None):
            if query.get("issue_type") == "Capability":
                return cap_cursor
            elif query.get("issue_type") == "Epic":
                return epic_cursor
            return story_cursor
        issues_coll.find = MagicMock(side_effect=mock_find)

        mock_db.__getitem__ = MagicMock(side_effect=lambda name: {
            "jira_issues": issues_coll, "rollups_current": rollups_coll,
        }.get(name, AsyncMock()))

        with patch("src.services.rollup_engine.get_db", return_value=mock_db):
            result = await engine.recompute_all("PROJ")

        assert result["capabilities_computed"] == 1
        assert result["epics_computed"] == 1
        assert result["stories_processed"] == 2

        # Verify rollups were upserted (1 epic + 1 capability = 2 calls)
        assert rollups_coll.update_one.call_count == 2

        # Check epic rollup values
        epic_call = rollups_coll.update_one.call_args_list[0]
        epic_set = epic_call[0][1]["$set"]
        assert epic_set["cumulative_points"] == 13  # 5 + 8
        assert epic_set["remaining_points"] == 8    # only In Progress
        assert epic_set["entity_type"] == "epic"

    @pytest.mark.asyncio
    async def test_empty_project(self, engine):
        mock_db = MagicMock()
        issues_coll = AsyncMock()
        rollups_coll = AsyncMock()

        empty_cursor = AsyncMock()
        empty_cursor.to_list = AsyncMock(return_value=[])
        issues_coll.find = MagicMock(return_value=empty_cursor)

        mock_db.__getitem__ = MagicMock(side_effect=lambda name: {
            "jira_issues": issues_coll, "rollups_current": rollups_coll,
        }.get(name, AsyncMock()))

        with patch("src.services.rollup_engine.get_db", return_value=mock_db):
            result = await engine.recompute_all("EMPTY")

        assert result["capabilities_computed"] == 0
        assert result["epics_computed"] == 0
        assert result["stories_processed"] == 0
        rollups_coll.update_one.assert_not_called()
