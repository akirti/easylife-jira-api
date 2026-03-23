"""Tests for src/services/jira_client.py — Jira client wrapper (mocked)."""
from unittest.mock import MagicMock, patch

import pytest

from src.services.jira_client import (
    ERR_JIRA_NOT_CONFIGURED,
    JiraClient,
)


class TestJiraClientInit:
    """Tests for JiraClient initialization."""

    def test_init_stores_config(self, test_config):
        """Client stores config reference."""
        client = JiraClient(test_config)
        assert client._base_url == "https://test.atlassian.net"

    def test_lazy_init_not_called_on_construct(self, test_config):
        """JIRA client is not created on __init__."""
        client = JiraClient(test_config)
        assert client._client is None

    def test_no_base_url_raises(self):
        """Missing base_url raises RuntimeError."""
        from src.config import Config
        import tempfile, json, os

        cfg_data = {"jira": {"base_url": "", "email": "", "api_token": ""}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(cfg_data, f)
            f.flush()
            config = Config(f.name)
        os.unlink(f.name)

        client = JiraClient(config)
        with pytest.raises(RuntimeError, match=ERR_JIRA_NOT_CONFIGURED):
            client._get_client()


class TestJiraClientSearch:
    """Tests for search_issues method."""

    @patch("src.services.jira_client.JIRA")
    def test_search_returns_issues(self, mock_jira_cls, test_config):
        """search_issues returns list of issues from Jira."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client

        issue1 = MagicMock()
        issue1.key = "TEST-1"
        mock_client.search_issues.return_value = [issue1]

        client = JiraClient(test_config)
        results = client.search_issues("project = TEST")

        assert len(results) == 1
        assert results[0].key == "TEST-1"

    @patch("src.services.jira_client.JIRA")
    def test_search_empty_results(self, mock_jira_cls, test_config):
        """search_issues handles empty results."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client
        mock_client.search_issues.return_value = []

        client = JiraClient(test_config)
        results = client.search_issues("project = EMPTY")
        assert results == []

    @patch("src.services.jira_client.JIRA")
    def test_search_jira_error(self, mock_jira_cls, test_config):
        """search_issues raises RuntimeError on JIRAError."""
        from jira import JIRAError

        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client
        mock_client.search_issues.side_effect = JIRAError("Connection failed")

        client = JiraClient(test_config)
        with pytest.raises(RuntimeError, match="search failed"):
            client.search_issues("project = TEST")

    @patch("src.services.jira_client.JIRA")
    def test_search_pagination(self, mock_jira_cls, test_config):
        """search_issues paginates through results."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client

        batch1 = [MagicMock() for _ in range(100)]
        batch2 = [MagicMock() for _ in range(50)]
        mock_client.search_issues.side_effect = [batch1, batch2]

        client = JiraClient(test_config)
        results = client.search_issues("project = TEST", max_results=100)
        assert len(results) == 150


class TestJiraClientOperations:
    """Tests for create, link, transition, and get operations."""

    @patch("src.services.jira_client.JIRA")
    def test_get_issue(self, mock_jira_cls, test_config):
        """get_issue returns a single issue."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client
        mock_issue = MagicMock()
        mock_issue.key = "TEST-5"
        mock_client.issue.return_value = mock_issue

        client = JiraClient(test_config)
        result = client.get_issue("TEST-5")
        assert result.key == "TEST-5"

    @patch("src.services.jira_client.JIRA")
    def test_create_issue(self, mock_jira_cls, test_config):
        """create_issue returns the created issue."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client
        created = MagicMock()
        created.key = "TEST-100"
        mock_client.create_issue.return_value = created

        client = JiraClient(test_config)
        result = client.create_issue({"summary": "New"})
        assert result.key == "TEST-100"

    @patch("src.services.jira_client.JIRA")
    def test_add_issue_link(self, mock_jira_cls, test_config):
        """add_issue_link calls Jira API correctly."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client

        client = JiraClient(test_config)
        client.add_issue_link("TEST-1", "TEST-2", "Blocks")

        mock_client.create_issue_link.assert_called_once_with(
            type="Blocks", inwardIssue="TEST-1", outwardIssue="TEST-2"
        )

    @patch("src.services.jira_client.JIRA")
    def test_transition_issue_success(self, mock_jira_cls, test_config):
        """transition_issue finds and applies the transition."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client
        mock_client.transitions.return_value = [
            {"id": "21", "name": "Done"},
            {"id": "31", "name": "In Progress"},
        ]

        client = JiraClient(test_config)
        client.transition_issue("TEST-1", "Done")

        mock_client.transition_issue.assert_called_once_with("TEST-1", "21")

    @patch("src.services.jira_client.JIRA")
    def test_transition_issue_not_found(self, mock_jira_cls, test_config):
        """transition_issue raises ValueError for invalid transition name."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client
        mock_client.transitions.return_value = [{"id": "21", "name": "Done"}]

        client = JiraClient(test_config)
        with pytest.raises(ValueError, match="not found"):
            client.transition_issue("TEST-1", "Nonexistent")

    @patch("src.services.jira_client.JIRA")
    def test_get_boards(self, mock_jira_cls, test_config):
        """get_boards returns board list."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client
        mock_client.boards.return_value = [MagicMock(), MagicMock()]

        client = JiraClient(test_config)
        boards = client.get_boards("TEST")
        assert len(boards) == 2

    @patch("src.services.jira_client.JIRA")
    def test_get_issue_changelog(self, mock_jira_cls, test_config):
        """get_issue_changelog returns parsed changelog."""
        mock_client = MagicMock()
        mock_jira_cls.return_value = mock_client

        history = MagicMock()
        history.created = "2025-01-15T10:00:00.000+0000"
        history.author = MagicMock()
        history.author.__str__ = lambda self: "Test User"

        item = MagicMock()
        item.field = "status"
        item.fromString = "Open"
        item.toString = "In Progress"
        history.items = [item]

        issue = MagicMock()
        issue.changelog = MagicMock()
        issue.changelog.histories = [history]
        mock_client.issue.return_value = issue

        client = JiraClient(test_config)
        result = client.get_issue_changelog("TEST-1")
        assert len(result) == 1
        assert result[0]["items"][0]["field"] == "status"
