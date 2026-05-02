"""Jira sync service — syncs Jira issues to MongoDB and archives old data.

Main entry points:
- sync_project(): Pull issues from Jira in batches, map, upsert to MongoDB.
- compute_days_in_status(): Calculate days since last status change.
- archive_old_issues(): Archive old issues to GCS, remove from MongoDB.
"""
import gzip
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import UpdateOne

from src.config import Config
from src.db import COLL_ARCHIVES, COLL_JIRA_ISSUES, COLL_SYNC_CONFIG, COLL_SYNC_PROGRESS, get_db
from src.services.attribute_mapper import map_issue
from src.services.gcs import GCSClient
from src.services.jira_client import JiraClient

logger = logging.getLogger(__name__)

# Constants
ARCHIVE_CONTENT_TYPE = "application/gzip"
JSONL_EXTENSION = ".jsonl.gz"
BATCH_SIZE = 100


async def get_sync_progress(project_key: str) -> Optional[Dict[str, Any]]:
    """Get current sync progress for a project."""
    db = get_db()
    doc = await db[COLL_SYNC_PROGRESS].find_one(
        {"project_key": project_key}, {"_id": 0}
    )
    return doc


async def clear_sync_progress(project_key: str) -> None:
    """Clear sync progress for a project."""
    db = get_db()
    await db[COLL_SYNC_PROGRESS].delete_one({"project_key": project_key})


async def _update_progress(project_key: str, updates: Dict[str, Any]) -> None:
    """Upsert sync progress for a project in MongoDB."""
    db = get_db()
    await db[COLL_SYNC_PROGRESS].update_one(
        {"project_key": project_key},
        {"$set": {**updates, "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


class JiraSyncService:
    """Synchronizes Jira issues to MongoDB and manages archival."""

    def __init__(self, config: Config, jira_client: JiraClient, gcs_client: GCSClient, rollup_engine=None):
        self._config = config
        self._jira = jira_client
        self._gcs = gcs_client
        self._rollup_engine = rollup_engine

    async def sync_project(
        self,
        project_key: str,
        days_back: int = 90,
        attribute_map: Optional[Dict[str, str]] = None,
    ) -> int:
        """Sync Jira issues for a project into MongoDB in batches.

        Fetches BATCH_SIZE issues at a time from Jira, upserts each batch
        to MongoDB immediately, and updates progress for UI polling.

        Args:
            project_key: Jira project key (e.g. 'SCEN').
            days_back: How many days of history to sync.
            attribute_map: Custom field mapping overrides.

        Returns:
            Number of issues synced.
        """
        if attribute_map is None:
            attribute_map = self._config.get("attribute_map", {})

        jql = f"project = {project_key} AND updated >= -{days_back}d ORDER BY updated DESC"
        logger.info("Starting sync for %s (JQL: %s)", project_key, jql)

        # Initialize progress
        await _update_progress(project_key, {
            "status": "fetching",
            "project_key": project_key,
            "fetched": 0,
            "synced": 0,
            "total_estimated": 0,
            "current_batch": 0,
            "message": "Fetching issues from Jira...",
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

        db = get_db()
        collection = db[COLL_JIRA_ISSUES]
        synced_count = 0
        total_estimated = 0
        now = datetime.now(timezone.utc)

        try:
            # Fetch and process in batches
            client = self._jira._get_client()
            start_at = 0
            batch_num = 0

            while True:
                # Fetch one batch from Jira
                batch = client.search_issues(
                    jql,
                    startAt=start_at,
                    maxResults=BATCH_SIZE,
                    expand="changelog",
                    fields="*all",
                )

                if not batch:
                    break

                batch_num += 1
                fetched_so_far = start_at + len(batch)

                # Update progress — fetching
                total_estimated = max(fetched_so_far, total_estimated)
                await _update_progress(project_key, {
                    "status": "syncing",
                    "fetched": fetched_so_far,
                    "total_estimated": total_estimated,
                    "current_batch": batch_num,
                    "message": f"Processing batch {batch_num} ({fetched_so_far} issues fetched)...",
                })

                # Map and bulk-upsert this batch
                bulk_ops = []
                for raw_issue in batch:
                    doc = map_issue(raw_issue, attribute_map)
                    doc["synced_at"] = now
                    doc["url"] = self._build_issue_url(raw_issue.key)
                    doc["days_in_status"] = self._compute_days_from_changelog(raw_issue)
                    bulk_ops.append(UpdateOne({"key": doc["key"]}, {"$set": doc}, upsert=True))

                if bulk_ops:
                    await collection.bulk_write(bulk_ops, ordered=False)
                synced_count += len(batch)

                # Update progress — batch done
                await _update_progress(project_key, {
                    "synced": synced_count,
                    "message": f"Synced {synced_count} issues ({batch_num} batches)...",
                })

                logger.info(
                    "Batch %d: fetched %d, synced %d total for %s",
                    batch_num, len(batch), synced_count, project_key,
                )

                # Check if we've exhausted results
                if len(batch) < BATCH_SIZE:
                    break
                start_at += len(batch)

            # Update sync config in DB
            await self._update_sync_config(project_key, now, synced_count)

            # Final progress
            await _update_progress(project_key, {
                "status": "completed",
                "synced": synced_count,
                "total_estimated": synced_count,
                "message": f"Sync complete. {synced_count} issues synced.",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })

            logger.info("Synced %d issues for %s", synced_count, project_key)

            # Post-sync: recompute portfolio rollups
            if self._rollup_engine:
                try:
                    await self._rollup_engine.recompute_all(project_key)
                    logger.info("Post-sync rollup recompute completed for %s", project_key)
                except Exception as exc:
                    logger.error("Rollup recompute failed for %s: %s", project_key, exc)

            return synced_count

        except Exception as exc:
            await _update_progress(project_key, {
                "status": "error",
                "synced": synced_count,
                "message": f"Sync failed after {synced_count} issues: {exc}",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            logger.error("Sync failed for %s after %d issues: %s", project_key, synced_count, exc)
            raise

    def _build_issue_url(self, key: str) -> str:
        """Build the browser URL for a Jira issue."""
        base_url = self._config.get("jira.base_url", "").rstrip("/")
        if not base_url:
            return ""
        return f"{base_url}/browse/{key}"

    def _compute_days_from_changelog(self, raw_issue: Any) -> Optional[float]:
        """Compute days in current status from the issue's changelog."""
        changelog = getattr(raw_issue, "changelog", None)
        if changelog is None:
            return None
        return compute_days_in_status(changelog)

    async def _update_sync_config(
        self, project_key: str, sync_time: datetime, count: int
    ) -> None:
        """Update the sync config record after a successful sync."""
        db = get_db()
        await db[COLL_SYNC_CONFIG].update_one(
            {"project_key": project_key},
            {
                "$set": {
                    "last_sync": sync_time,
                    "last_sync_count": count,
                    "last_sync_status": "success",
                },
                "$setOnInsert": {
                    "project_key": project_key,
                    "sync_period_months": self._config.get("sync.period_months", 3),
                    "sync_period_days": self._config.get("sync.period_days", 90),
                    "archive_after_months": self._config.get("sync.archive_after_months", 6),
                    "interval_minutes": self._config.get("sync.interval_minutes", 30),
                    "attribute_map": self._config.get("attribute_map", {}),
                },
            },
            upsert=True,
        )

    async def archive_old_issues(
        self,
        project_key: str,
        months_cutoff: int = 6,
    ) -> Dict[str, Any]:
        """Archive issues older than cutoff to GCS and remove from MongoDB.

        Args:
            project_key: Jira project key.
            months_cutoff: Issues updated before this many months ago are archived.

        Returns:
            Dict with archive_id, issue_count, gcs_path.
        """
        db = get_db()
        collection = db[COLL_JIRA_ISSUES]
        cutoff = _compute_cutoff_date(months_cutoff)

        cursor = collection.find({
            "project_key": project_key,
            "updated": {"$lt": cutoff.isoformat()},
        })

        issues = await cursor.to_list(length=None)
        if not issues:
            logger.info("No issues to archive for %s", project_key)
            return {"archive_id": "", "issue_count": 0, "gcs_path": ""}

        archive_data = _serialize_to_jsonl_gz(issues)
        now = datetime.now(timezone.utc)
        archive_id = f"{project_key}_{now.strftime('%Y%m%d_%H%M%S')}"
        gcs_prefix = self._config.get("gcs.archive_prefix", "jira_archives")
        gcs_path = f"{gcs_prefix}/{archive_id}{JSONL_EXTENSION}"

        self._gcs.upload_file(archive_data, gcs_path, ARCHIVE_CONTENT_TYPE)

        # Save archive metadata
        archive_record = {
            "archive_id": archive_id,
            "project_key": project_key,
            "gcs_path": gcs_path,
            "issue_count": len(issues),
            "archived_at": now,
            "size_bytes": len(archive_data),
        }
        await db[COLL_ARCHIVES].insert_one(archive_record)

        # Remove archived issues from main collection
        archived_keys = [issue["key"] for issue in issues]
        await collection.delete_many({"key": {"$in": archived_keys}})

        logger.info(
            "Archived %d issues for %s to %s", len(issues), project_key, gcs_path
        )
        return archive_record

    async def get_archive_list(self, project_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """List available archives, optionally filtered by project."""
        db = get_db()
        query: Dict[str, Any] = {}
        if project_key:
            query["project_key"] = project_key

        cursor = db[COLL_ARCHIVES].find(query).sort("archived_at", -1)
        archives = await cursor.to_list(length=100)

        for archive in archives:
            archive.pop("_id", None)

        return archives

    async def get_archive_download_url(
        self, archive_id: str, expiry_minutes: int = 60
    ) -> Optional[str]:
        """Get a signed download URL for an archive."""
        db = get_db()
        record = await db[COLL_ARCHIVES].find_one({"archive_id": archive_id})
        if not record:
            return None
        return self._gcs.get_signed_url(record["gcs_path"], expiry_minutes)


def compute_days_in_status(changelog: Any) -> Optional[float]:
    """Find the last status change in changelog and compute days since then."""
    last_status_change: Optional[datetime] = None
    histories = getattr(changelog, "histories", []) or []

    for history in reversed(histories):
        for item in history.items:
            if item.field == "status":
                try:
                    last_status_change = datetime.fromisoformat(
                        str(history.created).replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    continue
                break
        if last_status_change is not None:
            break

    if last_status_change is None:
        return None

    now = datetime.now(timezone.utc)
    if last_status_change.tzinfo is None:
        last_status_change = last_status_change.replace(tzinfo=timezone.utc)

    delta = now - last_status_change
    return round(delta.total_seconds() / 86400, 1)


def _compute_cutoff_date(months: int) -> datetime:
    """Compute a cutoff datetime N months ago from now."""
    now = datetime.now(timezone.utc)
    month = now.month - months
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    return now.replace(year=year, month=month)


def _serialize_to_jsonl_gz(issues: List[Dict[str, Any]]) -> bytes:
    """Serialize a list of issue dicts to gzipped JSONL format."""
    lines = []
    for issue in issues:
        cleaned = {k: v for k, v in issue.items() if k != "_id"}
        for key, val in cleaned.items():
            if isinstance(val, datetime):
                cleaned[key] = val.isoformat()
        lines.append(json.dumps(cleaned, default=str))

    content = "\n".join(lines).encode("utf-8")
    return gzip.compress(content)
