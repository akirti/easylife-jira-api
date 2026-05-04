"""Sync routes — sync management and archival endpoints.

All endpoints require admin role via require_admin dependency.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.auth import CurrentUser, require_admin
from src.db import COLL_SYNC_CONFIG, COLL_SYNC_PROGRESS, get_db
from src.models import ArchiveRecord, JiraSyncConfig, SyncProgress, SyncTriggerResponse
from src.services.jira_sync import get_sync_progress, clear_sync_progress

# Sync progress older than this is considered stale (server crashed mid-sync)
STALE_PROGRESS_MINUTES = 5

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])

# Module-level service references — set during app startup
_sync_service = None

# Error messages
ERR_SYNC_SERVICE_NOT_INIT = "Sync service not initialized"
ERR_SYNC_CONFIG_NOT_FOUND = "Sync config not found for project"
ERR_SYNC_FAILED = "Sync failed"
ERR_ARCHIVE_FAILED = "Archive operation failed"


async def cleanup_stale_sync_progress() -> None:
    """Clear any sync progress stuck from a previous server crash.

    Called during app startup to ensure no orphaned progress blocks new syncs.
    """
    try:
        db = get_db()
        result = await db[COLL_SYNC_PROGRESS].delete_many(
            {"status": {"$in": ["fetching", "syncing"]}}
        )
        if result.deleted_count > 0:
            logger.warning(
                "Cleaned up %d stale sync progress records from previous server session",
                result.deleted_count,
            )
    except Exception as exc:
        logger.warning("Could not clean up stale sync progress: %s", exc)


def init_sync_routes(sync_service: Any) -> None:
    """Initialize sync routes with the sync service. Called during app startup."""
    global _sync_service  # noqa: PLW0603
    _sync_service = sync_service
    logger.info("Sync routes initialized")


def _get_sync_service():
    """Get the sync service instance."""
    if _sync_service is None:
        raise RuntimeError(ERR_SYNC_SERVICE_NOT_INIT)
    return _sync_service


async def _run_sync_background(project_key: str, days: int, max_retries: int = 3) -> None:
    """Background task that runs the sync with retry and backoff."""
    sync_service = _get_sync_service()
    for attempt in range(max_retries):
        try:
            count = await sync_service.sync_project(project_key, days)
            logger.info("Background sync completed for %s: %d issues", project_key, count)
            return
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "Sync attempt %d/%d failed for %s, retrying in %ds: %s",
                    attempt + 1, max_retries, project_key, wait, exc,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "Sync permanently failed for %s after %d attempts: %s",
                    project_key, max_retries, exc,
                )


@router.post(
    "/trigger",
    response_model=SyncTriggerResponse,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Admin access required"},
        409: {"description": "Sync already in progress"},
    },
)
async def trigger_sync(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    project_key: str = Query(default="SCEN", description="Jira project key"),
    days: int = Query(default=90, ge=1, le=730, description="Days of history to sync"),
) -> SyncTriggerResponse:
    """Trigger a manual Jira sync for a project (runs in background)."""
    # Check if sync is already running for this project
    progress = await get_sync_progress(project_key)
    if progress and progress.get("status") in ("fetching", "syncing"):
        # Check if the progress is stale (server crashed mid-sync)
        updated_at = progress.get("updated_at")
        is_stale = False
        if updated_at:
            try:
                last_update = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                age_minutes = (datetime.now(timezone.utc) - last_update).total_seconds() / 60
                is_stale = age_minutes > STALE_PROGRESS_MINUTES
            except (ValueError, TypeError):
                is_stale = True  # Can't parse timestamp — treat as stale

        if is_stale:
            logger.warning(
                "Clearing stale sync progress for %s (last updated: %s)",
                project_key, updated_at,
            )
            await clear_sync_progress(project_key)
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Sync already in progress for {project_key}",
            )
    else:
        # Clear any completed/failed progress
        await clear_sync_progress(project_key)

    # Launch sync as background task
    asyncio.create_task(_run_sync_background(project_key, days))

    logger.info("Sync triggered by %s for %s (%d days)", admin.email, project_key, days)
    return SyncTriggerResponse(
        status="started",
        project_key=project_key,
        issues_synced=0,
        message=f"Sync started for {project_key} ({days} days). Poll /sync/progress for updates.",
    )


@router.get(
    "/progress",
    response_model=SyncProgress,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Admin access required"},
    },
)
async def sync_progress(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    project_key: str = Query(default="SCEN"),
) -> SyncProgress:
    """Get real-time sync progress for a project."""
    progress = await get_sync_progress(project_key)
    if not progress:
        return SyncProgress(status="idle", project_key=project_key, message="No sync in progress")
    return SyncProgress(**progress)


@router.get(
    "/config",
    response_model=JiraSyncConfig,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Admin access required"},
        404: {"description": "Config not found"},
    },
)
async def get_sync_config(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    project_key: str = Query(default="SCEN"),
) -> JiraSyncConfig:
    """Get the sync configuration for a project."""
    db = get_db()
    config = await db[COLL_SYNC_CONFIG].find_one(
        {"project_key": project_key}, {"_id": 0}
    )
    if not config:
        # Return default config for this project key (not yet persisted)
        return JiraSyncConfig(project_key=project_key)
    return JiraSyncConfig(**config)


@router.put(
    "/config",
    response_model=JiraSyncConfig,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Admin access required"},
    },
)
async def update_sync_config(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    config_update: JiraSyncConfig,
) -> JiraSyncConfig:
    """Update the sync configuration for a project."""
    db = get_db()
    update_data = config_update.model_dump(exclude_none=True)

    await db[COLL_SYNC_CONFIG].update_one(
        {"project_key": config_update.project_key},
        {"$set": update_data},
        upsert=True,
    )

    logger.info(
        "Sync config updated by %s for %s", admin.email, config_update.project_key
    )

    updated = await db[COLL_SYNC_CONFIG].find_one(
        {"project_key": config_update.project_key}, {"_id": 0}
    )
    return JiraSyncConfig(**updated)


@router.post(
    "/archive",
    response_model=ArchiveRecord,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Admin access required"},
        500: {"description": "Archive failed"},
    },
)
async def archive_issues(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    project_key: str = Query(default="SCEN"),
    months: int = Query(default=6, ge=1, le=36, description="Archive issues older than N months"),
) -> ArchiveRecord:
    """Archive old issues to GCS."""
    sync_service = _get_sync_service()
    try:
        result = await sync_service.archive_old_issues(project_key, months)
        logger.info(
            "Archive triggered by %s for %s: %d issues",
            admin.email, project_key, result.get("issue_count", 0),
        )
        return ArchiveRecord(**result)
    except Exception as exc:
        logger.error("%s for %s: %s", ERR_ARCHIVE_FAILED, project_key, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{ERR_ARCHIVE_FAILED}: {exc}",
        ) from exc


@router.get(
    "/archives",
    response_model=List[ArchiveRecord],
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Admin access required"},
    },
)
async def list_archives(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    project_key: Optional[str] = Query(default=None),
) -> List[ArchiveRecord]:
    """List available GCS archives."""
    sync_service = _get_sync_service()
    archives = await sync_service.get_archive_list(project_key)
    return [ArchiveRecord(**a) for a in archives]
