"""Cycle time computation from status transitions.

Computes how many days an issue spent in each development phase
(Dev, QA, Stage, Prod) based on its changelog transitions.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class CycleTimeService:
    def __init__(self, buckets: Dict[str, List[str]]):
        """Initialize with status-to-bucket mapping.

        Args:
            buckets: e.g. {"dev": ["In Progress"], "qa": ["In QA"], ...}
        """
        self._buckets = buckets
        # Build reverse map: status -> bucket name
        self._status_to_bucket: Dict[str, str] = {}
        for bucket_name, statuses in buckets.items():
            for status in statuses:
                self._status_to_bucket[status] = bucket_name

    def compute_cycle_metrics(
        self, transitions: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Compute cycle time metrics from a list of status transitions.

        Each transition: {"from_status": str, "to_status": str, "changed_at": ISO str}
        Transitions must be sorted chronologically.
        """
        result = {"dev_days": 0.0, "qa_days": 0.0, "stage_days": 0.0, "prod_days": 0.0}

        if not transitions:
            result["total_days"] = 0.0
            return result

        # Walk through transitions chronologically
        for i, t in enumerate(transitions):
            to_status = t["to_status"]
            bucket = self._status_to_bucket.get(to_status)
            if not bucket:
                continue

            # Time in this status = time until next transition (or now)
            entered = datetime.fromisoformat(t["changed_at"].replace("Z", "+00:00"))
            if i + 1 < len(transitions):
                exited = datetime.fromisoformat(
                    transitions[i + 1]["changed_at"].replace("Z", "+00:00")
                )
            else:
                exited = entered  # still in this status, count as 0 for now

            days = (exited - entered).total_seconds() / 86400.0
            key = f"{bucket}_days"
            if key in result:
                result[key] += days

        result["total_days"] = sum(result.values())
        return result
