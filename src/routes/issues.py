"""Issue routes — create, link, transition, and detail endpoints.

Proxies operations to Jira Cloud/Server via JiraClient,
then syncs results back to MongoDB.
"""
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Path, status

from src.auth import CurrentUser, get_current_user
from src.db import COLL_JIRA_ISSUES, get_db
from src.models import (
    CreateIssueRequest,
    JiraIssueDoc,
    LinkIssueRequest,
    TransitionRequest,
)
from src.services.attribute_mapper import map_issue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/issues", tags=["issues"])

# Module-level service references — set during app startup
_jira_client = None
_config = None

# Error messages
ERR_JIRA_CLIENT_NOT_INIT = "Jira client not initialized"
ERR_ISSUE_NOT_FOUND = "Issue not found"
ERR_CREATE_FAILED = "Failed to create issue"
ERR_LINK_FAILED = "Failed to link issues"
ERR_TRANSITION_FAILED = "Failed to transition issue"

# Path parameter description
ISSUE_KEY_DESCRIPTION = "Jira issue key (e.g. SCEN-123)"


def init_issue_routes(jira_client: Any, config: Any) -> None:
    """Initialize issue routes with service dependencies. Called during app startup."""
    global _jira_client, _config  # noqa: PLW0603
    _jira_client = jira_client
    _config = config
    logger.info("Issue routes initialized")


def _get_jira_client():
    """Get the Jira client instance."""
    if _jira_client is None:
        raise RuntimeError(ERR_JIRA_CLIENT_NOT_INIT)
    return _jira_client


@router.post(
    "/create",
    response_model=JiraIssueDoc,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"description": "Unauthorized"},
        500: {"description": "Creation failed"},
    },
)
async def create_issue(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    request: CreateIssueRequest,
) -> JiraIssueDoc:
    """Create a new Jira issue and sync it to MongoDB."""
    jira = _get_jira_client()

    fields: Dict[str, Any] = {
        "project": {"key": request.project_key},
        "summary": request.summary,
        "issuetype": {"name": request.issue_type},
        "priority": {"name": request.priority},
    }

    if request.description:
        fields["description"] = request.description
    if request.assignee_email:
        fields["assignee"] = {"emailAddress": request.assignee_email}
    if request.parent_key:
        fields["parent"] = {"key": request.parent_key}
    if request.labels:
        fields["labels"] = request.labels

    try:
        raw_issue = jira.create_issue(fields)
    except Exception as exc:
        logger.error("%s: %s", ERR_CREATE_FAILED, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{ERR_CREATE_FAILED}: {exc}",
        ) from exc

    # Sync created issue to MongoDB
    attribute_map = _config.get("attribute_map", {}) if _config else {}
    doc = map_issue(raw_issue, attribute_map)
    doc["synced_at"] = datetime.now(timezone.utc)

    base_url = _config.get("jira.base_url", "") if _config else ""
    if base_url:
        doc["url"] = f"{base_url.rstrip('/')}/browse/{doc['key']}"

    db = get_db()
    await db[COLL_JIRA_ISSUES].update_one(
        {"key": doc["key"]},
        {"$set": doc},
        upsert=True,
    )

    logger.info("Issue %s created by %s", doc["key"], user.email)
    return JiraIssueDoc(**doc)


@router.post(
    "/{key}/link",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Issue not found"},
        500: {"description": "Link failed"},
    },
)
async def link_issue(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    key: Annotated[str, Path(description=ISSUE_KEY_DESCRIPTION)],
    request: LinkIssueRequest,
) -> None:
    """Link two Jira issues together."""
    jira = _get_jira_client()

    try:
        jira.add_issue_link(key, request.target_key, request.link_type)
    except Exception as exc:
        logger.error("%s %s -> %s: %s", ERR_LINK_FAILED, key, request.target_key, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{ERR_LINK_FAILED}: {exc}",
        ) from exc

    # Update linked_keys in MongoDB for both issues
    db = get_db()
    await db[COLL_JIRA_ISSUES].update_one(
        {"key": key},
        {"$addToSet": {"linked_keys": {"key": request.target_key, "type": request.link_type}}},
    )
    await db[COLL_JIRA_ISSUES].update_one(
        {"key": request.target_key},
        {"$addToSet": {"linked_keys": {"key": key, "type": request.link_type}}},
    )

    logger.info("Linked %s -> %s (%s) by %s", key, request.target_key, request.link_type, user.email)


@router.post(
    "/{key}/transition",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Issue not found"},
        500: {"description": "Transition failed"},
    },
)
async def transition_issue(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    key: Annotated[str, Path(description=ISSUE_KEY_DESCRIPTION)],
    request: TransitionRequest,
) -> None:
    """Transition a Jira issue to a new status."""
    jira = _get_jira_client()

    try:
        jira.transition_issue(key, request.transition_name)
    except Exception as exc:
        logger.error("%s %s to '%s': %s", ERR_TRANSITION_FAILED, key, request.transition_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{ERR_TRANSITION_FAILED}: {exc}",
        ) from exc

    # Update status in MongoDB
    db = get_db()
    await db[COLL_JIRA_ISSUES].update_one(
        {"key": key},
        {"$set": {"status": request.transition_name, "synced_at": datetime.now(timezone.utc)}},
    )

    logger.info("Transitioned %s to '%s' by %s", key, request.transition_name, user.email)


@router.get(
    "/{key}",
    response_model=JiraIssueDoc,
    responses={
        401: {"description": "Unauthorized"},
        404: {"description": "Issue not found"},
    },
)
async def get_issue_detail(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    key: Annotated[str, Path(description=ISSUE_KEY_DESCRIPTION)],
) -> JiraIssueDoc:
    """Get full issue detail from MongoDB."""
    db = get_db()
    issue = await db[COLL_JIRA_ISSUES].find_one({"key": key}, {"_id": 0})

    if not issue:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{ERR_ISSUE_NOT_FOUND}: {key}",
        )

    return JiraIssueDoc(**issue)
