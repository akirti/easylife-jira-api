"""Portfolio routes — capability/epic rollup views, snapshots, recompute.

Viewer endpoints require get_current_user; admin endpoints require require_admin.
"""
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from src.auth import CurrentUser, get_current_user, require_admin
from src.config import Config
import src.db as _db_mod
from src.db import COLL_JIRA_ISSUES, COLL_ROLLUPS_CURRENT, COLL_STATUS_TRANSITIONS
from src.services.export_service import ExportService
from src.services.cycle_time_service import CycleTimeService
from src.models import (
    CapabilitySummary,
    CapabilityTree,
    EpicSummary,
    PortfolioListResponse,
    RollupValues,
    SnapshotSeries,
    StoryItem,
)
from src.services.rollup_engine import RollupEngine
from src.services.snapshot_service import SnapshotService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

# Module-level service references — set during app startup
_engine: Optional[RollupEngine] = None
_snapshot_svc: Optional[SnapshotService] = None
_config: Optional[Config] = None

# Error messages
ERR_NOT_INITIALIZED = "Portfolio routes not initialized"
ERR_CAPABILITY_NOT_FOUND = "Capability not found"


def init_portfolio_routes(
    engine: RollupEngine,
    snapshot_svc: SnapshotService,
    config: Config,
) -> None:
    """Initialize portfolio routes with services. Called during app startup."""
    global _engine, _snapshot_svc, _config  # noqa: PLW0603
    _engine = engine
    _snapshot_svc = snapshot_svc
    _config = config
    logger.info("Portfolio routes initialized")


def _get_engine() -> RollupEngine:
    if _engine is None:
        raise RuntimeError(ERR_NOT_INITIALIZED)
    return _engine


def _get_snapshot_svc() -> SnapshotService:
    if _snapshot_svc is None:
        raise RuntimeError(ERR_NOT_INITIALIZED)
    return _snapshot_svc


# ---- Request models ----

class SnapshotRunRequest(BaseModel):
    """Request body for POST /snapshots/run."""
    project_key: str


# ---- Helper: attach rollup values ----

def _rollup_from_doc(doc: Optional[Dict[str, Any]]) -> RollupValues:
    """Build a RollupValues from a rollups_current document (or None)."""
    if not doc:
        return RollupValues()
    return RollupValues(
        cumulative_points=doc.get("cumulative_points", 0),
        remaining_points=doc.get("remaining_points", 0),
        tshirt_rollup_points=doc.get("tshirt_rollup_points"),
        direct_child_count=doc.get("direct_child_count", 0),
        descendant_count=doc.get("descendant_count", 0),
        computed_at=doc.get("computed_at"),
    )


# ---- Endpoints ----

@router.get("/capabilities", response_model=PortfolioListResponse)
async def list_capabilities(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    project_key: str = Query(..., description="Jira project key"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List portfolio capabilities with rollup data."""
    db = _db_mod.get_db()
    cap_type = _config.get("portfolio.capability_issue_type", "Capability") if _config else "Capability"

    query = {"project_key": project_key, "issue_type": cap_type}
    total = await db[COLL_JIRA_ISSUES].count_documents(query)

    skip = (page - 1) * page_size
    caps = await db[COLL_JIRA_ISSUES].find(
        query, {"_id": 0}
    ).sort("key", 1).skip(skip).limit(page_size).to_list(length=page_size)

    # Attach rollups
    data: List[CapabilitySummary] = []
    for cap in caps:
        rollup_doc = await db[COLL_ROLLUPS_CURRENT].find_one(
            {"entity_key": cap["key"]}, {"_id": 0}
        )
        data.append(CapabilitySummary(
            key=cap["key"],
            summary=cap.get("summary", ""),
            status=cap.get("status", ""),
            issue_type=cap.get("issue_type", cap_type),
            tshirt_size=cap.get("tshirt_size"),
            project_key=cap.get("project_key", project_key),
            rollups=_rollup_from_doc(rollup_doc),
        ))

    return PortfolioListResponse(
        data=data,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(skip + page_size) < total,
    )


@router.get("/capabilities/{key}/tree", response_model=CapabilityTree)
async def capability_tree(
    key: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    """Get a capability with its child epics and rollup data."""
    db = _db_mod.get_db()

    cap = await db[COLL_JIRA_ISSUES].find_one({"key": key}, {"_id": 0})
    if not cap:
        raise HTTPException(status_code=404, detail=ERR_CAPABILITY_NOT_FOUND)

    # Capability rollup
    cap_rollup_doc = await db[COLL_ROLLUPS_CURRENT].find_one(
        {"entity_key": key}, {"_id": 0}
    )

    # Child epics
    epic_docs = await db[COLL_JIRA_ISSUES].find(
        {"parent_key": key, "issue_type": "Epic"}, {"_id": 0}
    ).to_list(length=1000)

    # Epic rollups in bulk
    epic_keys = [e["key"] for e in epic_docs]
    epic_rollup_docs = await db[COLL_ROLLUPS_CURRENT].find(
        {"entity_key": {"$in": epic_keys}}, {"_id": 0}
    ).to_list(length=1000) if epic_keys else []
    epic_rollup_map = {r["entity_key"]: r for r in epic_rollup_docs}

    epics: List[EpicSummary] = []
    for e in epic_docs:
        rollup_doc = epic_rollup_map.get(e["key"])
        epics.append(EpicSummary(
            key=e["key"],
            summary=e.get("summary", ""),
            status=e.get("status", ""),
            issue_type=e.get("issue_type", "Epic"),
            tshirt_size=e.get("tshirt_size"),
            rollups=_rollup_from_doc(rollup_doc),
        ))

    return CapabilityTree(
        key=cap["key"],
        summary=cap.get("summary", ""),
        status=cap.get("status", ""),
        issue_type=cap.get("issue_type", "Capability"),
        tshirt_size=cap.get("tshirt_size"),
        project_key=cap.get("project_key", ""),
        rollups=_rollup_from_doc(cap_rollup_doc),
        epics=epics,
    )


@router.get("/epics/{key}/children")
async def epic_children(
    key: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """List stories/tasks under an epic with pagination."""
    db = _db_mod.get_db()
    remaining_statuses = _config.get("portfolio.remaining_statuses", []) if _config else []

    query = {"epic_link_key": key}
    total = await db[COLL_JIRA_ISSUES].count_documents(query)

    skip = (page - 1) * page_size
    docs = await db[COLL_JIRA_ISSUES].find(
        query, {"_id": 0}
    ).sort("key", 1).skip(skip).limit(page_size).to_list(length=page_size)

    data = [
        StoryItem(
            key=d["key"],
            summary=d.get("summary", ""),
            status=d.get("status", ""),
            status_category=d.get("status_category", ""),
            issue_type=d.get("issue_type", ""),
            story_points=d.get("story_points"),
            assignee=d.get("assignee") or "",
            sprint=d.get("sprint") or "",
            priority=d.get("priority") or "",
            in_remaining=d.get("status", "") in remaining_statuses,
        )
        for d in docs
    ]

    return {
        "data": [item.model_dump() for item in data],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": (skip + page_size) < total,
    }


@router.get("/snapshots/{key}", response_model=SnapshotSeries)
async def get_snapshot_series(
    key: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    metric: str = Query("remaining", description="Metric: remaining, cumulative, tshirt_rollup"),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    """Get time-series snapshot data for a capability or epic."""
    svc = _get_snapshot_svc()
    result = await svc.get_series(key, metric=metric, from_date=from_date, to_date=to_date)
    return SnapshotSeries(**result)


@router.post("/snapshots/run")
async def run_snapshot(
    body: SnapshotRunRequest,
    user: Annotated[CurrentUser, Depends(require_admin)],
):
    """Take a weekly snapshot of current rollups (admin only)."""
    svc = _get_snapshot_svc()
    result = await svc.take_snapshot(body.project_key)
    return result


@router.post("/recompute")
async def recompute_rollups(
    user: Annotated[CurrentUser, Depends(require_admin)],
    project_key: str = Query(..., description="Jira project key"),
):
    """Recompute all rollups for a project (admin only)."""
    engine = _get_engine()
    result = await engine.recompute_all(project_key)
    return result


@router.get("/issues/{key}/cycle")
async def get_issue_cycle_metrics(
    key: str,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> Dict[str, Any]:
    """Get cycle time metrics for a specific issue."""
    db = _db_mod.get_db()

    # Fetch transitions for this issue
    transitions_coll = db[COLL_STATUS_TRANSITIONS]
    cursor = transitions_coll.find(
        {"issue_key": key}, {"_id": 0}
    ).sort("changed_at", 1)
    transitions = await cursor.to_list(length=1000)

    if not transitions:
        return {"issue_key": key, "dev_days": 0, "qa_days": 0,
                "stage_days": 0, "prod_days": 0, "total_days": 0}

    buckets = _config.get("portfolio.cycle_time_buckets", {}) if _config else {}
    service = CycleTimeService(buckets)
    metrics = service.compute_cycle_metrics(transitions)
    metrics["issue_key"] = key
    return metrics


@router.get("/issues/{key}/related")
async def get_related_items(
    key: str,
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> Dict[str, Any]:
    """Get subtasks, linked issues, and test cases for a specific issue."""
    db = _db_mod.get_db()
    issues_coll = db[COLL_JIRA_ISSUES]

    # Subtasks: issues where parent_key == this key and type is Sub-task
    subtask_cursor = issues_coll.find(
        {"parent_key": key, "issue_type": "Sub-task"},
        {"_id": 0, "key": 1, "summary": 1, "status": 1, "status_category": 1,
         "issue_type": 1, "story_points": 1, "assignee": 1}
    )
    subtasks = await subtask_cursor.to_list(length=50)

    # Issue links from the issue's own document
    issue_doc = await issues_coll.find_one({"key": key}, {"issue_links_detail": 1, "_id": 0})
    links_raw = (issue_doc or {}).get("issue_links_detail", [])

    # Separate test links from regular links
    test_link_types = {"tested by", "is tested by", "tests", "xray", "zephyr"}
    tests = []
    links = []
    for link in links_raw:
        lt = (link.get("link_type") or "").lower()
        entry = {
            "key": link.get("target_key", ""),
            "link_type": link.get("link_type", ""),
            "direction": link.get("direction", ""),
            "summary": "", "status": "", "issue_type": "",
        }
        # Try to enrich with issue data
        linked_doc = await issues_coll.find_one(
            {"key": entry["key"]},
            {"summary": 1, "status": 1, "issue_type": 1, "_id": 0}
        )
        if linked_doc:
            entry["summary"] = linked_doc.get("summary", "")
            entry["status"] = linked_doc.get("status", "")
            entry["issue_type"] = linked_doc.get("issue_type", "")

        if any(t in lt for t in test_link_types):
            tests.append(entry)
        else:
            links.append(entry)

    return {
        "subtasks": subtasks,
        "links": links,
        "tests": tests,
    }


@router.post("/exports/portfolio")
async def export_portfolio(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    """Export portfolio data as DOCX document."""
    body = await request.json()

    project_key = body.get("project_key", "SCEN")
    view = body.get("view", "progress")
    filter_name = body.get("filter", "all")

    db = _db_mod.get_db()
    issues_coll = db[COLL_JIRA_ISSUES]
    rollups_coll = db[COLL_ROLLUPS_CURRENT]

    cap_type = _engine._cap_type if _engine else "Capability"

    # Fetch capabilities
    caps = await issues_coll.find(
        {"project_key": project_key, "issue_type": cap_type},
        {"_id": 0}
    ).sort("key", 1).to_list(length=100)

    # Attach rollups and epics
    for cap in caps:
        rollup = await rollups_coll.find_one(
            {"entity_key": cap["key"]}, {"_id": 0}
        )
        cap["rollups"] = rollup or {}

        # Fetch epics
        epics = await issues_coll.find(
            {"parent_key": cap["key"], "issue_type": "Epic"}, {"_id": 0}
        ).to_list(length=200)

        for epic in epics:
            er = await rollups_coll.find_one(
                {"entity_key": epic["key"]}, {"_id": 0}
            )
            epic["rollups"] = er or {}

        cap["epics"] = epics

    # Generate DOCX
    service = ExportService()
    docx_bytes = service.generate(
        caps, view=view, filter_name=filter_name, project_key=project_key,
    )

    filename = (
        f"portfolio-rollup-{view}-"
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.docx"
    )
    return Response(
        content=docx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
