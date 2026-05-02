"""Maps Jira custom fields to domain fields via JSON config.

This is the isolation layer — when Jira custom field IDs change,
update the attribute_map config, not the code.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Default attribute mapping (can be overridden in config)
DEFAULT_ATTRIBUTE_MAP: Dict[str, str] = {
    "customfield_10015": "start_date",
    "customfield_10016": "story_points",
    "customfield_10024": "sprint",
    "customfield_10028": "team",
}

# Regex patterns for extracting mentions from Jira comment markup
_MENTION_ACCOUNT_ID_PATTERN = re.compile(r"\[~accountId:([^\]]+)\]")
_MENTION_USERNAME_PATTERN = re.compile(r"\[~([^\]]+)\]")

# Maximum description length stored in MongoDB
_MAX_DESCRIPTION_LENGTH = 2000


def map_issue(raw_issue: Any, attribute_map: Dict[str, str]) -> Dict[str, Any]:
    """Convert a jira-python Issue object to a JiraIssueDoc-shaped dict.

    Args:
        raw_issue: A jira.Issue object from jira-python.
        attribute_map: Mapping of Jira custom field IDs to domain field names.

    Returns:
        Dict matching JiraIssueDoc fields.
    """
    fields = raw_issue.fields

    doc: Dict[str, Any] = {
        "key": raw_issue.key,
        "issue_id": raw_issue.id,
        "summary": fields.summary or "",
        "status": str(fields.status) if fields.status else "",
        "status_category": _extract_status_category(fields),
        "issue_type": str(fields.issuetype) if fields.issuetype else "",
        "priority": str(fields.priority) if fields.priority else None,
        "assignee": _extract_display_name(fields.assignee),
        "assignee_email": _extract_email(fields.assignee),
        "reporter": _extract_display_name(fields.reporter),
        "reporter_email": _extract_email(fields.reporter),
        "project_key": fields.project.key if fields.project else "",
        "project_name": fields.project.name if fields.project else "",
        "created": str(fields.created) if fields.created else None,
        "updated": str(fields.updated) if fields.updated else None,
        "due_date": str(fields.duedate) if fields.duedate else None,
        "resolution_date": _extract_resolution_date(fields),
        "labels": fields.labels or [],
        "components": [c.name for c in (fields.components or [])],
        "description_text": _truncate_description(fields.description),
        "parent_key": _extract_parent_key(fields),
        "subtask_keys": [s.key for s in (fields.subtasks or [])],
        "flagged": getattr(fields, "flagged", False) or False,
    }

    # Fix versions
    fix_versions_raw = getattr(fields, "fixVersions", None) or []
    doc["fix_versions"] = [
        getattr(v, "name", str(v)) for v in fix_versions_raw
    ]

    # Issue links (summary keys — existing)
    doc["linked_keys"] = _extract_linked_keys(fields)

    # Issue links (detailed, for related items)
    links_raw = getattr(fields, "issuelinks", None) or []
    issue_links_detail = []
    for link in links_raw:
        link_type = getattr(link.type, "name", str(link.type)) if hasattr(link, "type") and link.type else ""
        outward = getattr(link, "outwardIssue", None)
        inward = getattr(link, "inwardIssue", None)
        if outward:
            issue_links_detail.append({
                "link_type": link_type,
                "direction": "outward",
                "target_key": outward.key,
            })
        elif inward:
            issue_links_detail.append({
                "link_type": link_type,
                "direction": "inward",
                "target_key": inward.key,
            })
    doc["issue_links_detail"] = issue_links_detail

    # Custom field mapping from config
    for jira_field, domain_field in attribute_map.items():
        val = getattr(fields, jira_field, None)
        if val is not None:
            doc[domain_field] = str(val)

    # Comment mentions
    doc["comment_mentions"] = extract_mentions(raw_issue)

    return doc


def _extract_status_category(fields: Any) -> str:
    """Extract status category name from Jira fields."""
    if not fields.status:
        return ""
    status_cat = getattr(fields.status, "statusCategory", None)
    if status_cat is None:
        return ""
    return str(status_cat)


def _extract_display_name(user: Any) -> Optional[str]:
    """Extract display name from a Jira user object."""
    if user is None:
        return None
    return user.displayName if hasattr(user, "displayName") else str(user)


def _extract_email(user: Any) -> Optional[str]:
    """Extract email address from a Jira user object."""
    if user is None:
        return None
    return getattr(user, "emailAddress", None)


def _extract_resolution_date(fields: Any) -> Optional[str]:
    """Extract resolution date string."""
    rd = getattr(fields, "resolutiondate", None)
    return str(rd) if rd else None


def _truncate_description(description: Optional[str]) -> Optional[str]:
    """Truncate description to max length."""
    if not description:
        return None
    return description[:_MAX_DESCRIPTION_LENGTH]


def _extract_parent_key(fields: Any) -> Optional[str]:
    """Extract parent issue key if present."""
    parent = getattr(fields, "parent", None)
    if parent is None:
        return None
    return parent.key if hasattr(parent, "key") else None


def _extract_linked_keys(fields: Any) -> List[Dict[str, str]]:
    """Extract linked issue keys and relationship types."""
    links: List[Dict[str, str]] = []
    issue_links = getattr(fields, "issuelinks", None) or []

    for link in issue_links:
        outward = getattr(link, "outwardIssue", None)
        if outward is not None:
            links.append({
                "key": outward.key,
                "type": link.type.outward if hasattr(link.type, "outward") else str(link.type),
            })
        inward = getattr(link, "inwardIssue", None)
        if inward is not None:
            links.append({
                "key": inward.key,
                "type": link.type.inward if hasattr(link.type, "inward") else str(link.type),
            })

    return links


def extract_mentions(issue: Any) -> List[str]:
    """Extract @mentioned user IDs from issue comments.

    Handles both Jira Cloud format [~accountId:xxx] and
    Server format [~username].

    Args:
        issue: A jira.Issue object.

    Returns:
        De-duplicated list of mentioned user identifiers.
    """
    mentions: Set[str] = set()
    comment_field = getattr(issue.fields, "comment", None)
    if not comment_field:
        return []

    comments = getattr(comment_field, "comments", []) or []
    for comment in comments:
        body = getattr(comment, "body", "") or ""
        for match in _MENTION_ACCOUNT_ID_PATTERN.findall(body):
            mentions.add(match)
        for match in _MENTION_USERNAME_PATTERN.findall(body):
            mentions.add(match)

    return sorted(mentions)
