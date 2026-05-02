"""Portfolio rollup computation engine.

Reads from jira_issues, computes cumulative/remaining story-point rollups
per epic and capability, writes to rollups_current collection.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config
from src.db import COLL_JIRA_ISSUES, COLL_ROLLUPS_CURRENT, get_db

logger = logging.getLogger(__name__)

LEVEL0_TYPES = {"Story", "Bug", "Task", "Technical Story", "Technical Task",
                "Enhancement", "Tech Debt", "Spike"}


class RollupEngine:
    """Computes story-point rollups for epics and capabilities.

    Epic rollup:
        cumulative = sum of all child story points
        remaining  = sum of story points WHERE status IN remaining_statuses

    Capability rollup:
        cumulative    = sum of epic cumulatives
        remaining     = sum of epic remainings
        tshirt_rollup = sum where epics in fallback_statuses use
                        tshirt_to_points(size) instead of their cumulative
    """

    def __init__(self, config: Config):
        self._config = config
        self._remaining_statuses: List[str] = config.get(
            "portfolio.remaining_statuses", [])
        self._fallback_statuses: List[str] = config.get(
            "portfolio.tshirt_fallback_statuses", [])
        self._tshirt_map: Dict[str, int] = config.get(
            "portfolio.tshirt_size_map", {})
        self._cap_type: str = config.get(
            "portfolio.capability_issue_type", "Capability")

    def _tshirt_to_points(self, size: Optional[str]) -> float:
        """Convert t-shirt size to story points using configured map."""
        if not size:
            return 0
        return self._tshirt_map.get(size, 0)

    def _compute_epic_rollup(
        self, stories: List[Dict[str, Any]]
    ) -> Tuple[float, float]:
        """Compute cumulative and remaining points for an epic's children.

        Args:
            stories: List of child issue dicts with story_points and status.

        Returns:
            (cumulative, remaining) tuple of floats.
        """
        cumulative = 0.0
        remaining = 0.0
        for s in stories:
            pts = s.get("story_points") or 0
            cumulative += pts
            if s.get("status") in self._remaining_statuses:
                remaining += pts
        return cumulative, remaining

    def _compute_capability_rollup(
        self, epic_rollups: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Compute capability rollup from its epics' rollups.

        T-shirt fallback: epics in fallback statuses contribute their
        t-shirt size points instead of their actual cumulative.

        Args:
            epic_rollups: List of dicts with epic_key, epic_status,
                epic_tshirt, cumulative, remaining.

        Returns:
            Dict with cumulative_points, remaining_points, tshirt_rollup_points.
        """
        cumulative = 0.0
        remaining = 0.0
        tshirt_rollup = 0.0
        for er in epic_rollups:
            cumulative += er["cumulative"]
            remaining += er["remaining"]
            if er["epic_status"] in self._fallback_statuses:
                tshirt_rollup += self._tshirt_to_points(er.get("epic_tshirt"))
            else:
                tshirt_rollup += er["cumulative"]
        return {
            "cumulative_points": cumulative,
            "remaining_points": remaining,
            "tshirt_rollup_points": tshirt_rollup,
        }

    async def recompute_all(self, project_key: str) -> Dict[str, Any]:
        """Recompute all rollups for a project.

        1. Fetch all capabilities, epics, and level-0 issues
        2. Group stories by epic, epics by capability
        3. Compute epic rollups, upsert to rollups_current
        4. Compute capability rollups, upsert to rollups_current

        Args:
            project_key: The Jira project key (e.g. "PROJ").

        Returns:
            Summary dict with counts of capabilities, epics, stories processed.
        """
        db = get_db()
        issues_coll = db[COLL_JIRA_ISSUES]
        rollups_coll = db[COLL_ROLLUPS_CURRENT]
        now = datetime.now(timezone.utc).isoformat()

        # Fetch all issue types
        caps = await issues_coll.find(
            {"project_key": project_key, "issue_type": self._cap_type},
            {"key": 1, "summary": 1, "status": 1, "tshirt_size": 1, "_id": 0},
        ).to_list(length=1000)

        epics = await issues_coll.find(
            {"project_key": project_key, "issue_type": "Epic"},
            {"key": 1, "summary": 1, "status": 1, "tshirt_size": 1,
             "parent_key": 1, "_id": 0},
        ).to_list(length=10000)

        stories = await issues_coll.find(
            {"project_key": project_key,
             "issue_type": {"$in": list(LEVEL0_TYPES)}},
            {"key": 1, "epic_link_key": 1, "story_points": 1, "status": 1,
             "_id": 0},
        ).to_list(length=100000)

        # Group stories by epic
        stories_by_epic: Dict[str, List[Dict]] = {}
        for s in stories:
            ek = s.get("epic_link_key", "")
            if ek:
                stories_by_epic.setdefault(ek, []).append(s)

        # Group epics by capability (parent_key)
        epics_by_cap: Dict[str, List[Dict]] = {}
        for e in epics:
            pk = e.get("parent_key", "")
            if pk:
                epics_by_cap.setdefault(pk, []).append(e)

        # Compute epic rollups
        epic_rollup_data: Dict[str, Dict] = {}
        for epic in epics:
            epic_stories = stories_by_epic.get(epic["key"], [])
            cumulative, remaining = self._compute_epic_rollup(epic_stories)
            rollup_doc = {
                "entity_key": epic["key"],
                "entity_type": "epic",
                "project_key": project_key,
                "cumulative_points": cumulative,
                "remaining_points": remaining,
                "direct_child_count": len(epic_stories),
                "descendant_count": len(epic_stories),
                "computed_at": now,
            }
            await rollups_coll.update_one(
                {"entity_key": epic["key"]},
                {"$set": rollup_doc},
                upsert=True,
            )
            epic_rollup_data[epic["key"]] = {
                "epic_key": epic["key"],
                "epic_status": epic.get("status", ""),
                "epic_tshirt": epic.get("tshirt_size"),
                "cumulative": cumulative,
                "remaining": remaining,
            }

        # Compute capability rollups
        for cap in caps:
            cap_epics = epics_by_cap.get(cap["key"], [])
            cap_epic_rollups = [
                epic_rollup_data[e["key"]]
                for e in cap_epics
                if e["key"] in epic_rollup_data
            ]
            cap_result = self._compute_capability_rollup(cap_epic_rollups)
            rollup_doc = {
                "entity_key": cap["key"],
                "entity_type": "capability",
                "project_key": project_key,
                "cumulative_points": cap_result["cumulative_points"],
                "remaining_points": cap_result["remaining_points"],
                "tshirt_rollup_points": cap_result["tshirt_rollup_points"],
                "direct_child_count": len(cap_epics),
                "descendant_count": sum(
                    len(stories_by_epic.get(e["key"], []))
                    for e in cap_epics
                ),
                "computed_at": now,
            }
            await rollups_coll.update_one(
                {"entity_key": cap["key"]},
                {"$set": rollup_doc},
                upsert=True,
            )

        logger.info(
            "Rollup recompute: %d caps, %d epics, %d stories for project %s",
            len(caps), len(epics), len(stories), project_key,
        )
        return {
            "capabilities_computed": len(caps),
            "epics_computed": len(epics),
            "stories_processed": len(stories),
        }
