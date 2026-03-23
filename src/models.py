"""Pydantic models for the Jira Dashboard API.

Covers MongoDB document shapes, API request/response models,
and frontend-facing data structures (canvas, timeline, board).
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# MongoDB document models
# ---------------------------------------------------------------------------

class LinkedIssue(BaseModel):
    """A linked issue reference with relationship type."""
    key: str
    link_type: str = Field(alias="type", default="")


class JiraIssueDoc(BaseModel):
    """MongoDB document shape for a synced Jira issue."""
    key: str
    issue_id: str = ""
    summary: str = ""
    status: str = ""
    status_category: str = ""
    issue_type: str = ""
    priority: Optional[str] = None
    assignee: Optional[str] = None
    assignee_email: Optional[str] = None
    reporter: Optional[str] = None
    reporter_email: Optional[str] = None
    project_key: str = ""
    project_name: str = ""
    created: Optional[str] = None
    updated: Optional[str] = None
    due_date: Optional[str] = None
    resolution_date: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    components: List[str] = Field(default_factory=list)
    description_text: Optional[str] = None
    parent_key: Optional[str] = None
    subtask_keys: List[str] = Field(default_factory=list)
    linked_keys: List[Dict[str, str]] = Field(default_factory=list)
    flagged: bool = False
    blocker_reason: Optional[str] = None
    sprint: Optional[str] = None
    story_points: Optional[float] = None
    start_date: Optional[str] = None
    team: Optional[str] = None
    comment_mentions: List[str] = Field(default_factory=list)
    days_in_status: Optional[float] = None
    url: Optional[str] = None
    synced_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True)


class JiraSyncConfig(BaseModel):
    """Sync configuration stored in MongoDB."""
    project_key: str
    sync_period_months: int = 3
    archive_after_months: int = 6
    interval_minutes: int = 30
    attribute_map: Dict[str, str] = Field(default_factory=dict)
    last_sync: Optional[datetime] = None
    last_sync_count: int = 0
    last_sync_status: str = ""


class ArchiveRecord(BaseModel):
    """Metadata for an archived batch of issues."""
    archive_id: str = ""
    project_key: str = ""
    gcs_path: str = ""
    issue_count: int = 0
    archived_at: Optional[datetime] = None
    size_bytes: int = 0


# ---------------------------------------------------------------------------
# Canvas (ReactFlow) models
# ---------------------------------------------------------------------------

class CanvasNodeData(BaseModel):
    """Data payload for a ReactFlow node."""
    key: str
    summary: str = ""
    status: str = ""
    status_category: str = ""
    issue_type: str = ""
    priority: Optional[str] = None
    assignee: Optional[str] = None
    flagged: bool = False
    days_in_status: Optional[float] = None


class CanvasNode(BaseModel):
    """ReactFlow node shape."""
    id: str
    type: str = "issueCard"
    position: Dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})
    data: CanvasNodeData


class CanvasEdge(BaseModel):
    """ReactFlow edge shape."""
    id: str
    source: str
    target: str
    label: str = ""
    edge_type: str = "default"
    animated: bool = False
    style: Dict[str, Any] = Field(default_factory=dict)


class CanvasResponse(BaseModel):
    """Combined canvas data for the frontend."""
    nodes: List[CanvasNode] = Field(default_factory=list)
    edges: List[CanvasEdge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Timeline models
# ---------------------------------------------------------------------------

class TimelineEntry(BaseModel):
    """Gantt data for a single issue."""
    key: str
    summary: str = ""
    start: Optional[str] = None
    end: Optional[str] = None
    due_date: Optional[str] = None
    status: str = ""
    status_category: str = ""
    issue_type: str = ""
    assignee: Optional[str] = None
    overdue: bool = False


# ---------------------------------------------------------------------------
# Board / stats models
# ---------------------------------------------------------------------------

class TaskCountByType(BaseModel):
    """Count of tasks grouped by issue type."""
    issue_type: str
    count: int


class BoardSummary(BaseModel):
    """A board's metadata and task counts."""
    board_id: str = ""
    board_name: str = ""
    task_counts: List[TaskCountByType] = Field(default_factory=list)
    total: int = 0


class StatusBreakdown(BaseModel):
    """Count of issues per status."""
    status: str
    count: int


class TypeBreakdown(BaseModel):
    """Count of issues per issue type."""
    issue_type: str
    count: int


class PriorityBreakdown(BaseModel):
    """Count of issues per priority."""
    priority: str
    count: int


class DashboardStats(BaseModel):
    """Aggregated stats for the dashboard header."""
    total: int = 0
    by_status: List[StatusBreakdown] = Field(default_factory=list)
    by_type: List[TypeBreakdown] = Field(default_factory=list)
    by_priority: List[PriorityBreakdown] = Field(default_factory=list)
    blockers: int = 0
    overdue: int = 0
    my_mentions: int = 0


# ---------------------------------------------------------------------------
# API request models
# ---------------------------------------------------------------------------

class CreateIssueRequest(BaseModel):
    """Request to create a new Jira issue."""
    project_key: str
    summary: str
    description: str = ""
    issue_type: str = "Task"
    priority: str = "Medium"
    assignee_email: Optional[str] = None
    parent_key: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    sprint: Optional[str] = None


class LinkIssueRequest(BaseModel):
    """Request to link two Jira issues."""
    target_key: str
    link_type: str = "Relates"


class TransitionRequest(BaseModel):
    """Request to transition a Jira issue."""
    transition_name: str


# ---------------------------------------------------------------------------
# API response wrappers
# ---------------------------------------------------------------------------

class IssueListResponse(BaseModel):
    """Paginated issue list."""
    items: List[JiraIssueDoc] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50


class SyncTriggerResponse(BaseModel):
    """Response after triggering a sync."""
    status: str = "started"
    project_key: str = ""
    issues_synced: int = 0
    message: str = ""
