"""Weekly snapshot service for portfolio rollup history.

Copies rollups_current to rollups_snapshots, keyed by ISO week start.
Idempotent — re-running for the same week is a no-op (unique index).
"""
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from src.db import COLL_ROLLUPS_CURRENT, COLL_ROLLUPS_SNAPSHOTS, get_db

logger = logging.getLogger(__name__)


class SnapshotService:
    def _iso_week_start(self, d: date) -> date:
        """Return the Monday of the ISO week containing d."""
        return d - timedelta(days=d.weekday())

    async def take_snapshot(
        self, project_key: str, as_of: Optional[date] = None,
    ) -> Dict[str, Any]:
        """Snapshot current rollups for the ISO week.

        Idempotent: duplicate key errors are caught and treated as no-op.
        """
        if as_of is None:
            as_of = date.today()
        week_start = self._iso_week_start(as_of).isoformat()

        db = get_db()
        rollups = await db[COLL_ROLLUPS_CURRENT].find(
            {"project_key": project_key}
        ).to_list(length=100000)

        if not rollups:
            return {"entities_snapshotted": 0, "snapshot_week": week_start}

        docs = [{
            "snapshot_week": week_start,
            "entity_key": r["entity_key"],
            "entity_type": r["entity_type"],
            "project_key": project_key,
            "cumulative_points": r.get("cumulative_points", 0),
            "remaining_points": r.get("remaining_points", 0),
            "tshirt_rollup_points": r.get("tshirt_rollup_points"),
        } for r in rollups]

        try:
            await db[COLL_ROLLUPS_SNAPSHOTS].insert_many(docs, ordered=False)
        except Exception as exc:
            if "duplicate key" in str(exc).lower() or "E11000" in str(exc) or "11000" in str(exc):
                logger.info("Snapshot already exists for week %s", week_start)
                return {"entities_snapshotted": 0, "snapshot_week": week_start,
                        "skipped": True}
            raise

        logger.info("Snapshot: %d entities for week %s", len(docs), week_start)
        return {"entities_snapshotted": len(docs), "snapshot_week": week_start}

    async def get_series(
        self, entity_key: str, metric: str = "remaining",
        from_date: Optional[str] = None, to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get time series for an entity (capability or epic).

        Returns weekly data points sorted chronologically.
        """
        db = get_db()
        query: Dict[str, Any] = {"entity_key": entity_key}
        if from_date or to_date:
            df: Dict[str, str] = {}
            if from_date:
                df["$gte"] = from_date
            if to_date:
                df["$lte"] = to_date
            query["snapshot_week"] = df

        field = f"{metric}_points"
        cursor = db[COLL_ROLLUPS_SNAPSHOTS].find(
            query, {"snapshot_week": 1, field: 1, "_id": 0},
        ).sort("snapshot_week", 1)
        rows = await cursor.to_list(length=1000)
        series = [{"week": r["snapshot_week"], "value": r.get(field, 0)} for r in rows]
        return {"key": entity_key, "metric": metric, "series": series}
