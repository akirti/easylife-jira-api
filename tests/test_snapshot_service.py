import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.snapshot_service import SnapshotService


@pytest.fixture
def service():
    return SnapshotService()


class TestIsoWeekStart:
    def test_thursday(self, service):
        # 2026-04-30 is Thursday -> week starts Monday 2026-04-27
        assert service._iso_week_start(date(2026, 4, 30)) == date(2026, 4, 27)

    def test_monday(self, service):
        assert service._iso_week_start(date(2026, 4, 27)) == date(2026, 4, 27)

    def test_sunday(self, service):
        # 2026-05-03 is Sunday -> week starts Monday 2026-04-27
        assert service._iso_week_start(date(2026, 5, 3)) == date(2026, 4, 27)

    def test_saturday(self, service):
        assert service._iso_week_start(date(2026, 5, 2)) == date(2026, 4, 27)


class TestTakeSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_creates_docs(self, service):
        mock_db = MagicMock()
        rollups_coll = AsyncMock()
        snapshots_coll = AsyncMock()

        rollups_cursor = AsyncMock()
        rollups_cursor.to_list = AsyncMock(return_value=[
            {"entity_key": "CAP-1", "entity_type": "capability", "project_key": "PROJ",
             "cumulative_points": 100, "remaining_points": 40, "tshirt_rollup_points": 120},
            {"entity_key": "EPIC-1", "entity_type": "epic", "project_key": "PROJ",
             "cumulative_points": 50, "remaining_points": 20, "tshirt_rollup_points": None},
        ])
        rollups_coll.find = MagicMock(return_value=rollups_cursor)
        snapshots_coll.insert_many = AsyncMock()

        mock_db.__getitem__ = MagicMock(side_effect=lambda name: {
            "rollups_current": rollups_coll,
            "rollups_snapshots": snapshots_coll,
        }.get(name, AsyncMock()))

        with patch("src.services.snapshot_service.get_db", return_value=mock_db):
            result = await service.take_snapshot("PROJ", as_of=date(2026, 4, 30))

        assert result["entities_snapshotted"] == 2
        assert result["snapshot_week"] == "2026-04-27"
        snapshots_coll.insert_many.assert_called_once()
        docs = snapshots_coll.insert_many.call_args[0][0]
        assert len(docs) == 2
        assert docs[0]["snapshot_week"] == "2026-04-27"
        assert docs[0]["entity_key"] == "CAP-1"

    @pytest.mark.asyncio
    async def test_snapshot_empty_rollups(self, service):
        mock_db = MagicMock()
        rollups_coll = AsyncMock()
        rollups_cursor = AsyncMock()
        rollups_cursor.to_list = AsyncMock(return_value=[])
        rollups_coll.find = MagicMock(return_value=rollups_cursor)

        mock_db.__getitem__ = MagicMock(side_effect=lambda name: {
            "rollups_current": rollups_coll,
        }.get(name, AsyncMock()))

        with patch("src.services.snapshot_service.get_db", return_value=mock_db):
            result = await service.take_snapshot("PROJ", as_of=date(2026, 4, 30))

        assert result["entities_snapshotted"] == 0

    @pytest.mark.asyncio
    async def test_snapshot_idempotent(self, service):
        """Duplicate key error (same week) is handled gracefully."""
        mock_db = MagicMock()
        rollups_coll = AsyncMock()
        snapshots_coll = AsyncMock()

        rollups_cursor = AsyncMock()
        rollups_cursor.to_list = AsyncMock(return_value=[
            {"entity_key": "CAP-1", "entity_type": "capability", "project_key": "PROJ",
             "cumulative_points": 100, "remaining_points": 40},
        ])
        rollups_coll.find = MagicMock(return_value=rollups_cursor)

        # Simulate duplicate key error
        from pymongo.errors import BulkWriteError
        snapshots_coll.insert_many = AsyncMock(
            side_effect=BulkWriteError({"writeErrors": [{"code": 11000}]})
        )

        mock_db.__getitem__ = MagicMock(side_effect=lambda name: {
            "rollups_current": rollups_coll,
            "rollups_snapshots": snapshots_coll,
        }.get(name, AsyncMock()))

        with patch("src.services.snapshot_service.get_db", return_value=mock_db):
            result = await service.take_snapshot("PROJ", as_of=date(2026, 4, 30))

        assert result["entities_snapshotted"] == 0
        assert result.get("skipped") is True


class TestGetSeries:
    @pytest.mark.asyncio
    async def test_returns_series(self, service):
        mock_db = MagicMock()
        snapshots_coll = AsyncMock()

        cursor = AsyncMock()
        cursor.to_list = AsyncMock(return_value=[
            {"snapshot_week": "2026-04-13", "remaining_points": 50},
            {"snapshot_week": "2026-04-20", "remaining_points": 40},
            {"snapshot_week": "2026-04-27", "remaining_points": 35},
        ])
        mock_sort = MagicMock(return_value=cursor)
        snapshots_coll.find = MagicMock(return_value=MagicMock(sort=mock_sort))

        mock_db.__getitem__ = MagicMock(return_value=snapshots_coll)

        with patch("src.services.snapshot_service.get_db", return_value=mock_db):
            result = await service.get_series("CAP-1", metric="remaining")

        assert result["key"] == "CAP-1"
        assert result["metric"] == "remaining"
        assert len(result["series"]) == 3
        assert result["series"][0] == {"week": "2026-04-13", "value": 50}

    @pytest.mark.asyncio
    async def test_with_date_range(self, service):
        mock_db = MagicMock()
        snapshots_coll = AsyncMock()

        cursor = AsyncMock()
        cursor.to_list = AsyncMock(return_value=[])
        mock_sort = MagicMock(return_value=cursor)
        snapshots_coll.find = MagicMock(return_value=MagicMock(sort=mock_sort))

        mock_db.__getitem__ = MagicMock(return_value=snapshots_coll)

        with patch("src.services.snapshot_service.get_db", return_value=mock_db):
            result = await service.get_series(
                "CAP-1", metric="cumulative",
                from_date="2026-04-01", to_date="2026-04-30"
            )

        # Verify the query included date range filter
        call_args = snapshots_coll.find.call_args[0][0]
        assert call_args["snapshot_week"]["$gte"] == "2026-04-01"
        assert call_args["snapshot_week"]["$lte"] == "2026-04-30"
