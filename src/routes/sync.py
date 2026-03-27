"""Sync routes — sync management and archival endpoints.

All endpoints require admin role via require_admin dependency.
"""
import logging
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.auth import CurrentUser, require_admin
from src.db import COLL_SYNC_CONFIG, get_db
from src.models import ArchiveRecord, JiraSyncConfig, SyncTriggerResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])

# Module-level service references — set during app startup
_sync_service = None

# Error messages
ERR_SYNC_SERVICE_NOT_INIT = "Sync service not initialized"
ERR_SYNC_CONFIG_NOT_FOUND = "Sync config not found for project"
ERR_SYNC_FAILED = "Sync failed"
ERR_ARCHIVE_FAILED = "Archive operation failed"


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


@router.post(
    "/trigger",
    response_model=SyncTriggerResponse,
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Admin access required"},
        500: {"description": "Sync failed"},
    },
)
async def trigger_sync(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    project_key: str = Query(default="SCEN", description="Jira project key"),
    months: int = Query(default=3, ge=1, le=24, description="Months of history to sync"),
) -> SyncTriggerResponse:
    """Trigger a manual Jira sync for a project."""
    sync_service = _get_sync_service()
    try:
        count = await sync_service.sync_project(project_key, months)
        logger.info("Sync triggered by %s for %s: %d issues", admin.email, project_key, count)
        return SyncTriggerResponse(
            status="completed",
            project_key=project_key,
            issues_synced=count,
            message=f"Successfully synced {count} issues",
        )
    except Exception as exc:
        logger.error("%s for %s: %s", ERR_SYNC_FAILED, project_key, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{ERR_SYNC_FAILED}: {exc}",
        ) from exc


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
