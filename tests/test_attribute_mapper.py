"""Tests for src/services/attribute_mapper.py — Jira field mapping."""
from unittest.mock import MagicMock

import pytest

from src.services.attribute_mapper import (
    DEFAULT_ATTRIBUTE_MAP,
    _extract_linked_keys,
    _extract_parent_key,
    _truncate_description,
    extract_mentions,
    map_issue,
)


def _make_mock_issue(
    key="TEST-1",
    summary="Test summary",
    status="Open",
    issue_type="Task",
    priority="Medium",
    assignee=None,
    reporter=None,
    parent=None,
    subtasks=None,
    issuelinks=None,
    labels=None,
    components=None,
    description=None,
    comment=None,
    custom_fields=None,
):
    """Create a mock Jira issue object for testing."""
    issue = MagicMock()
    issue.key = key
    issue.id = "10001"

    fields = MagicMock()
    fields.summary = summary
    fields.status = MagicMock()
    fields.status.__str__ = lambda self: status
    fields.status.statusCategory = MagicMock()
    fields.status.statusCategory.__str__ = lambda self: "To Do"

    fields.issuetype = MagicMock()
    fields.issuetype.__str__ = lambda self: issue_type

    fields.priority = MagicMock()
    fields.priority.__str__ = lambda self: priority

    if assignee:
        fields.assignee = MagicMock()
        fields.assignee.displayName = assignee
        fields.assignee.emailAddress = f"{assignee.lower().replace(' ', '.')}@example.com"
    else:
        fields.assignee = None

    if reporter:
        fields.reporter = MagicMock()
        fields.reporter.displayName = reporter
        fields.reporter.emailAddress = f"{reporter.lower().replace(' ', '.')}@example.com"
    else:
        fields.reporter = None

    fields.project = MagicMock()
    fields.project.key = "TEST"
    fields.project.name = "Test Project"
    fields.created = "2025-01-01T00:00:00.000+0000"
    fields.updated = "2025-02-01T00:00:00.000+0000"
    fields.duedate = None
    fields.resolutiondate = None
    fields.labels = labels or []
    fields.components = components or []
    fields.description = description
    fields.parent = parent
    fields.subtasks = subtasks or []
    fields.issuelinks = issuelinks or []
    fields.flagged = False
    fields.comment = comment

    # Custom fields
    if custom_fields:
        for cf_name, cf_value in custom_fields.items():
            setattr(fields, cf_name, cf_value)

    issue.fields = fields
    return issue


class TestMapIssue:
    """Tests for the map_issue function."""

    def test_basic_mapping(self):
        """Basic issue fields are mapped correctly."""
        issue = _make_mock_issue(key="TEST-42", summary="Hello world")
        doc = map_issue(issue, {})
        assert doc["key"] == "TEST-42"
        assert doc["summary"] == "Hello world"
        assert doc["project_key"] == "TEST"

    def test_assignee_mapping(self):
        """Assignee name and email are extracted."""
        issue = _make_mock_issue(assignee="John Doe")
        doc = map_issue(issue, {})
        assert doc["assignee"] == "John Doe"
        assert doc["assignee_email"] == "john.doe@example.com"

    def test_no_assignee(self):
        """None assignee returns None values."""
        issue = _make_mock_issue(assignee=None)
        doc = map_issue(issue, {})
        assert doc["assignee"] is None
        assert doc["assignee_email"] is None

    def test_custom_field_mapping(self):
        """Custom fields are mapped via attribute_map."""
        issue = _make_mock_issue(custom_fields={"customfield_10016": 5})
        attr_map = {"customfield_10016": "story_points"}
        doc = map_issue(issue, attr_map)
        assert doc["story_points"] == "5"

    def test_parent_key_extraction(self):
        """Parent key is extracted when present."""
        parent = MagicMock()
        parent.key = "TEST-1"
        issue = _make_mock_issue(parent=parent)
        doc = map_issue(issue, {})
        assert doc["parent_key"] == "TEST-1"

    def test_subtask_keys(self):
        """Subtask keys are collected."""
        sub1 = MagicMock()
        sub1.key = "TEST-10"
        sub2 = MagicMock()
        sub2.key = "TEST-11"
        issue = _make_mock_issue(subtasks=[sub1, sub2])
        doc = map_issue(issue, {})
        assert doc["subtask_keys"] == ["TEST-10", "TEST-11"]

    def test_labels_and_components(self):
        """Labels and components are collected."""
        comp = MagicMock()
        comp.name = "backend"
        issue = _make_mock_issue(labels=["urgent", "bug"], components=[comp])
        doc = map_issue(issue, {})
        assert doc["labels"] == ["urgent", "bug"]
        assert doc["components"] == ["backend"]

    def test_linked_keys_outward(self):
        """Outward issue links are extracted."""
        link = MagicMock()
        link.outwardIssue = MagicMock()
        link.outwardIssue.key = "TEST-99"
        link.type = MagicMock()
        link.type.outward = "blocks"
        link.inwardIssue = None
        # Ensure hasattr checks work
        del link.inwardIssue

        issue = _make_mock_issue(issuelinks=[link])
        doc = map_issue(issue, {})
        assert any(lk["key"] == "TEST-99" for lk in doc["linked_keys"])

    def test_description_truncation(self):
        """Long descriptions are truncated."""
        long_desc = "x" * 3000
        issue = _make_mock_issue(description=long_desc)
        doc = map_issue(issue, {})
        assert len(doc["description_text"]) == 2000

    def test_empty_description(self):
        """None description results in None."""
        issue = _make_mock_issue(description=None)
        doc = map_issue(issue, {})
        assert doc["description_text"] is None

    def test_fix_versions_extracted(self):
        """fixVersions are extracted as a list of version names."""
        v1 = MagicMock()
        v1.name = "v1.0"
        v2 = MagicMock()
        v2.name = "v2.0"
        issue = _make_mock_issue()
        issue.fields.fixVersions = [v1, v2]
        result = map_issue(issue, {})
        assert result["fix_versions"] == ["v1.0", "v2.0"]

    def test_fix_versions_empty(self):
        """Missing fixVersions returns empty list."""
        issue = _make_mock_issue()
        issue.fields.fixVersions = None
        result = map_issue(issue, {})
        assert result["fix_versions"] == []

    def test_issue_links_detail_extracted(self):
        """Issue links are extracted with type, direction, and target key."""
        link1 = MagicMock()
        link1.type.name = "Blocks"
        link1.outwardIssue = MagicMock()
        link1.outwardIssue.key = "PROJ-99"
        link1.inwardIssue = None

        link2 = MagicMock()
        link2.type.name = "Relates"
        link2.outwardIssue = None
        link2.inwardIssue = MagicMock()
        link2.inwardIssue.key = "PROJ-50"

        issue = _make_mock_issue(issuelinks=[link1, link2])
        result = map_issue(issue, {})
        assert len(result["issue_links_detail"]) == 2
        assert result["issue_links_detail"][0] == {
            "link_type": "Blocks", "direction": "outward", "target_key": "PROJ-99"
        }
        assert result["issue_links_detail"][1] == {
            "link_type": "Relates", "direction": "inward", "target_key": "PROJ-50"
        }

    def test_issue_links_detail_empty(self):
        """Missing issuelinks returns empty list."""
        issue = _make_mock_issue()
        issue.fields.issuelinks = None
        result = map_issue(issue, {})
        assert result["issue_links_detail"] == []


class TestExtractMentions:
    """Tests for the extract_mentions function."""

    def test_account_id_mentions(self):
        """Extracts [~accountId:xxx] mentions from comments."""
        comment = MagicMock()
        comment.body = "Hey [~accountId:abc123] check this"
        comment_field = MagicMock()
        comment_field.comments = [comment]

        issue = _make_mock_issue()
        issue.fields.comment = comment_field

        mentions = extract_mentions(issue)
        assert "abc123" in mentions

    def test_username_mentions(self):
        """Extracts [~username] mentions from comments."""
        comment = MagicMock()
        comment.body = "CC [~jdoe] and [~asmith]"
        comment_field = MagicMock()
        comment_field.comments = [comment]

        issue = _make_mock_issue()
        issue.fields.comment = comment_field

        mentions = extract_mentions(issue)
        assert "jdoe" in mentions
        assert "asmith" in mentions

    def test_no_comments(self):
        """No comments returns empty list."""
        issue = _make_mock_issue(comment=None)
        mentions = extract_mentions(issue)
        assert mentions == []

    def test_no_mentions_in_comments(self):
        """Comments without mentions return empty list."""
        comment = MagicMock()
        comment.body = "Just a regular comment with no mentions"
        comment_field = MagicMock()
        comment_field.comments = [comment]

        issue = _make_mock_issue()
        issue.fields.comment = comment_field

        mentions = extract_mentions(issue)
        assert mentions == []

    def test_deduplicated_mentions(self):
        """Duplicate mentions are deduplicated."""
        c1 = MagicMock()
        c1.body = "[~accountId:abc] mentioned"
        c2 = MagicMock()
        c2.body = "[~accountId:abc] again"
        comment_field = MagicMock()
        comment_field.comments = [c1, c2]

        issue = _make_mock_issue()
        issue.fields.comment = comment_field

        mentions = extract_mentions(issue)
        assert mentions.count("abc") == 1


class TestHelperFunctions:
    """Tests for internal helper functions."""

    def test_truncate_description_none(self):
        assert _truncate_description(None) is None

    def test_truncate_description_short(self):
        assert _truncate_description("short") == "short"

    def test_truncate_description_long(self):
        assert len(_truncate_description("x" * 3000)) == 2000

    def test_extract_parent_key_none(self):
        fields = MagicMock()
        fields.parent = None
        assert _extract_parent_key(fields) is None

    def test_extract_linked_keys_empty(self):
        fields = MagicMock()
        fields.issuelinks = []
        assert _extract_linked_keys(fields) == []
