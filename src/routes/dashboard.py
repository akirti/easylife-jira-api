"""Dashboard routes — read-only views for Jira issue data.

All endpoints require authenticated user via get_current_user.
Data is served from MongoDB jira_issues collection.
"""
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.auth import CurrentUser, get_current_user
from src.db import COLL_JIRA_ISSUES, get_db
from src.models import (
    BoardSummary,
    CanvasEdge,
    CanvasNode,
    CanvasNodeData,
    CanvasResponse,
    EpicSummary,
    DashboardStats,
    IssueListResponse,
    JiraIssueDoc,
    PriorityBreakdown,
    StatusBreakdown,
    TaskCountByType,
    TimelineEntry,
    TypeBreakdown,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Constants
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
EDGE_TYPE_BLOCKS = "blocks"
EDGE_TYPE_PARENT = "parent_of"
EDGE_TYPE_RELATED = "related"
EDGE_STYLE_BLOCKS = {"stroke": "#ef4444", "strokeDasharray": "5 5"}
EDGE_STYLE_PARENT = {"stroke": "#3b82f6"}
EDGE_STYLE_RELATED = {"stroke": "#9ca3af"}
NODE_SPACING_X = 300
NODE_SPACING_Y = 200


@router.get(
    "/stats",
    response_model=DashboardStats,
    responses={401: {"description": "Unauthorized"}},
)
async def get_stats(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    project_key: str = Query(default="SCEN", description="Jira project key"),
) -> DashboardStats:
    """Get aggregated dashboard statistics for a project."""
    db = get_db()
    collection = db[COLL_JIRA_ISSUES]
    base_filter: Dict[str, Any] = {"project_key": project_key}

    total = await collection.count_documents(base_filter)

    by_status = await _aggregate_counts(collection, base_filter, "status")
    by_type = await _aggregate_counts(collection, base_filter, "issue_type")
    by_priority = await _aggregate_counts(collection, base_filter, "priority")

    blockers = await collection.count_documents({**base_filter, "flagged": True})

    now_iso = datetime.now(timezone.utc).isoformat()
    overdue = await collection.count_documents({
        **base_filter,
        "due_date": {"$ne": None, "$lt": now_iso},
        "resolution_date": None,
    })

    my_mentions = await collection.count_documents({
        **base_filter,
        "comment_mentions": user.user_id,
    })

    return DashboardStats(
        total=total,
        by_status=[StatusBreakdown(status=s["_id"], count=s["count"]) for s in by_status],
        by_type=[TypeBreakdown(issue_type=t["_id"], count=t["count"]) for t in by_type],
        by_priority=[PriorityBreakdown(priority=p["_id"] or "None", count=p["count"]) for p in by_priority],
        blockers=blockers,
        overdue=overdue,
        my_mentions=my_mentions,
    )


@router.get(
    "/issues",
    response_model=IssueListResponse,
    responses={401: {"description": "Unauthorized"}},
)
async def get_issues(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    project_key: str = Query(default="SCEN"),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    issue_type: Optional[str] = Query(default=None),
    assignee: Optional[str] = Query(default=None),
    flagged: Optional[bool] = Query(default=None),
    sprint: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> IssueListResponse:
    """Get paginated issues with optional filters for the board view."""
    db = get_db()
    collection = db[COLL_JIRA_ISSUES]

    query = _build_issue_filter(
        project_key, status_filter, issue_type, assignee, flagged, sprint
    )

    total = await collection.count_documents(query)
    skip = (page - 1) * page_size

    cursor = collection.find(query, {"_id": 0}).sort("updated", -1).skip(skip).limit(page_size)
    items = await cursor.to_list(length=page_size)

    return IssueListResponse(
        items=[JiraIssueDoc(**item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/canvas",
    response_model=CanvasResponse,
    responses={401: {"description": "Unauthorized"}},
)
async def get_canvas(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    project_key: str = Query(default="SCEN"),
    epic_key: Optional[str] = Query(default=None, description="Filter by parent epic"),
) -> CanvasResponse:
    """Get nodes and edges for the ReactFlow canvas view."""
    db = get_db()
    collection = db[COLL_JIRA_ISSUES]

    query: Dict[str, Any] = {"project_key": project_key}
    if epic_key:
        query["$or"] = [
            {"key": epic_key},
            {"parent_key": epic_key},
        ]

    cursor = collection.find(query, {"_id": 0})
    issues = await cursor.to_list(length=1000)

    # Fetch epics for the filter dropdown
    epic_cursor = collection.find(
        {"project_key": project_key, "issue_type": "Epic"},
        {"_id": 0, "key": 1, "summary": 1},
    ).sort("key", 1)
    epic_docs = await epic_cursor.to_list(length=200)
    epics = [{"key": d["key"], "summary": d.get("summary", "")} for d in epic_docs]

    nodes, edges = _build_canvas_graph(issues)
    return CanvasResponse(nodes=nodes, edges=edges, epics=epics)


@router.get(
    "/timeline",
    response_model=List[TimelineEntry],
    responses={401: {"description": "Unauthorized"}},
)
async def get_timeline(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    project_key: str = Query(default="SCEN"),
    assignee: Optional[str] = Query(default=None),
    issue_type: Optional[str] = Query(default=None),
    sprint: Optional[str] = Query(default=None),
) -> List[TimelineEntry]:
    """Get timeline entries for the Gantt view."""
    db = get_db()
    collection = db[COLL_JIRA_ISSUES]

    query: Dict[str, Any] = {"project_key": project_key}
    if assignee:
        query["assignee"] = {"$regex": assignee, "$options": "i"}
    if issue_type:
        query["issue_type"] = issue_type
    if sprint:
        query["sprint"] = sprint

    cursor = collection.find(query, {"_id": 0}).sort("created", 1)
    issues = await cursor.to_list(length=500)

    now_iso = datetime.now(timezone.utc).isoformat()
    entries = []
    for issue in issues:
        is_overdue = (
            issue.get("due_date") is not None
            and issue["due_date"] < now_iso
            and issue.get("resolution_date") is None
        )
        entries.append(TimelineEntry(
            key=issue["key"],
            summary=issue.get("summary", ""),
            start=issue.get("start_date") or issue.get("created"),
            end=issue.get("resolution_date") or issue.get("due_date"),
            due_date=issue.get("due_date"),
            status=issue.get("status", ""),
            status_category=issue.get("status_category", ""),
            issue_type=issue.get("issue_type", ""),
            assignee=issue.get("assignee"),
            overdue=is_overdue,
        ))

    return entries


@router.get(
    "/my-mentions",
    response_model=List[JiraIssueDoc],
    responses={401: {"description": "Unauthorized"}},
)
async def get_my_mentions(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    project_key: str = Query(default="SCEN"),
) -> List[JiraIssueDoc]:
    """Get issues where the current user is mentioned in comments."""
    db = get_db()
    collection = db[COLL_JIRA_ISSUES]

    query = {
        "project_key": project_key,
        "comment_mentions": user.user_id,
    }

    cursor = collection.find(query, {"_id": 0}).sort("updated", -1)
    items = await cursor.to_list(length=100)
    return [JiraIssueDoc(**item) for item in items]


@router.get(
    "/boards",
    response_model=List[BoardSummary],
    responses={401: {"description": "Unauthorized"}},
)
async def get_boards(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    project_key: str = Query(default="SCEN"),
) -> List[BoardSummary]:
    """Get board summaries with task counts by type."""
    db = get_db()
    collection = db[COLL_JIRA_ISSUES]

    pipeline = [
        {"$match": {"project_key": project_key}},
        {"$group": {
            "_id": {"sprint": "$sprint"},
            "total": {"$sum": 1},
            "types": {"$push": "$issue_type"},
        }},
        {"$sort": {"_id.sprint": -1}},
    ]

    results = await collection.aggregate(pipeline).to_list(length=50)
    boards = []
    for result in results:
        sprint_name = result["_id"].get("sprint") or "Backlog"
        type_counts = _count_types(result["types"])
        boards.append(BoardSummary(
            board_id=sprint_name,
            board_name=sprint_name,
            task_counts=[TaskCountByType(issue_type=t, count=c) for t, c in type_counts.items()],
            total=result["total"],
        ))

    return boards


@router.get(
    "/blockers",
    response_model=List[JiraIssueDoc],
    responses={401: {"description": "Unauthorized"}},
)
async def get_blockers(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    project_key: str = Query(default="SCEN"),
) -> List[JiraIssueDoc]:
    """Get flagged/blocked issues with dependency context."""
    db = get_db()
    collection = db[COLL_JIRA_ISSUES]

    query = {
        "project_key": project_key,
        "$or": [
            {"flagged": True},
            {"linked_keys.type": {"$regex": "block", "$options": "i"}},
        ],
    }

    cursor = collection.find(query, {"_id": 0}).sort("updated", -1)
    items = await cursor.to_list(length=100)
    return [JiraIssueDoc(**item) for item in items]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def _aggregate_counts(
    collection: Any,
    base_filter: Dict[str, Any],
    field: str,
) -> List[Dict[str, Any]]:
    """Run a group-by aggregation on a field."""
    pipeline = [
        {"$match": base_filter},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    return await collection.aggregate(pipeline).to_list(length=50)


def _build_issue_filter(
    project_key: str,
    status_filter: Optional[str],
    issue_type: Optional[str],
    assignee: Optional[str],
    flagged: Optional[bool],
    sprint: Optional[str],
) -> Dict[str, Any]:
    """Build a MongoDB filter dict from query parameters."""
    query: Dict[str, Any] = {"project_key": project_key}
    if status_filter:
        query["status"] = status_filter
    if issue_type:
        query["issue_type"] = issue_type
    if assignee:
        query["assignee"] = {"$regex": assignee, "$options": "i"}
    if flagged is not None:
        query["flagged"] = flagged
    if sprint:
        query["sprint"] = sprint
    return query


def _build_canvas_graph(
    issues: List[Dict[str, Any]],
) -> tuple[List[CanvasNode], List[CanvasEdge]]:
    """Build ReactFlow nodes and edges from issue documents."""
    issue_keys = {issue["key"] for issue in issues}
    nodes: List[CanvasNode] = []
    edges: List[CanvasEdge] = []
    edge_ids: set = set()

    for idx, issue in enumerate(issues):
        row = idx // 4
        col = idx % 4
        node = CanvasNode(
            id=issue["key"],
            position={"x": col * NODE_SPACING_X, "y": row * NODE_SPACING_Y},
            data=CanvasNodeData(
                key=issue["key"],
                summary=issue.get("summary", ""),
                status=issue.get("status", ""),
                status_category=issue.get("status_category", ""),
                issue_type=issue.get("issue_type", ""),
                priority=issue.get("priority"),
                assignee=issue.get("assignee"),
                flagged=issue.get("flagged", False),
                days_in_status=issue.get("days_in_status"),
            ),
        )
        nodes.append(node)

        # Parent -> child edges
        parent_key = issue.get("parent_key")
        if parent_key and parent_key in issue_keys:
            edge_id = f"{parent_key}->{issue['key']}"
            if edge_id not in edge_ids:
                edges.append(_create_edge(edge_id, parent_key, issue["key"], EDGE_TYPE_PARENT))
                edge_ids.add(edge_id)

        # Linked issue edges
        for link in issue.get("linked_keys", []):
            linked_key = link.get("key", "")
            link_type = link.get("type", "").lower()
            if linked_key not in issue_keys:
                continue

            edge_id = _canonical_edge_id(issue["key"], linked_key)
            if edge_id in edge_ids:
                continue

            if "block" in link_type:
                edges.append(_create_edge(edge_id, issue["key"], linked_key, EDGE_TYPE_BLOCKS))
            else:
                edges.append(_create_edge(edge_id, issue["key"], linked_key, EDGE_TYPE_RELATED))
            edge_ids.add(edge_id)

    return nodes, edges


def _create_edge(edge_id: str, source: str, target: str, edge_type: str) -> CanvasEdge:
    """Create a CanvasEdge with appropriate styling."""
    style_map = {
        EDGE_TYPE_BLOCKS: EDGE_STYLE_BLOCKS,
        EDGE_TYPE_PARENT: EDGE_STYLE_PARENT,
        EDGE_TYPE_RELATED: EDGE_STYLE_RELATED,
    }
    return CanvasEdge(
        id=edge_id,
        source=source,
        target=target,
        label=edge_type.replace("_", " "),
        edge_type=edge_type,
        animated=edge_type == EDGE_TYPE_BLOCKS,
        style=style_map.get(edge_type, EDGE_STYLE_RELATED),
    )


def _canonical_edge_id(key_a: str, key_b: str) -> str:
    """Create a canonical (sorted) edge ID to avoid duplicates."""
    return f"{min(key_a, key_b)}->{max(key_a, key_b)}"


def _count_types(types: List[str]) -> Dict[str, int]:
    """Count occurrences of each issue type."""
    counts: Dict[str, int] = {}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    return counts
