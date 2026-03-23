"""Thin wrapper around jira-python library.

Provides a simplified interface for Jira Cloud/Server operations.
All Jira API calls go through this client.
"""
import logging
from typing import Any, Dict, List, Optional

from jira import JIRA, JIRAError

from src.config import Config

logger = logging.getLogger(__name__)

# Error message constants
ERR_JIRA_NOT_CONFIGURED = "Jira client not configured — check jira.base_url in config"
ERR_JIRA_SEARCH_FAILED = "Jira search failed"
ERR_JIRA_GET_FAILED = "Failed to get Jira issue"
ERR_JIRA_CREATE_FAILED = "Failed to create Jira issue"
ERR_JIRA_LINK_FAILED = "Failed to link Jira issues"
ERR_JIRA_TRANSITION_FAILED = "Failed to transition Jira issue"
ERR_JIRA_BOARDS_FAILED = "Failed to get Jira boards"


class JiraClient:
    """Wrapper around jira-python for Jira Cloud/Server interactions."""

    def __init__(self, config: Config):
        self._config = config
        self._client: Optional[JIRA] = None
        self._base_url = config.get("jira.base_url", "")

    def _get_client(self) -> JIRA:
        """Lazy-initialize and return the JIRA client."""
        if self._client is not None:
            return self._client

        base_url = self._base_url
        if not base_url:
            raise RuntimeError(ERR_JIRA_NOT_CONFIGURED)

        email = self._config.get("jira.email", "")
        api_token = self._config.get("jira.api_token", "")
        jira_type = self._config.get("jira.jira_type", "cloud")

        if jira_type == "cloud":
            self._client = JIRA(
                server=base_url,
                basic_auth=(email, api_token),
            )
        else:
            self._client = JIRA(
                server=base_url,
                token_auth=api_token,
            )

        logger.info("Jira client initialized for %s (%s)", base_url, jira_type)
        return self._client

    def search_issues(
        self,
        jql: str,
        max_results: int = 100,
        expand: str = "changelog",
        fields: str = "*all",
    ) -> List[Any]:
        """Search Jira issues using JQL.

        Args:
            jql: JQL query string.
            max_results: Maximum number of results per page.
            expand: Comma-separated list of fields to expand.
            fields: Comma-separated list of fields to return.

        Returns:
            List of jira.Issue objects.
        """
        client = self._get_client()
        all_issues: List[Any] = []
        start_at = 0

        try:
            while True:
                batch = client.search_issues(
                    jql,
                    startAt=start_at,
                    maxResults=max_results,
                    expand=expand,
                    fields=fields,
                )
                if not batch:
                    break
                all_issues.extend(batch)
                if len(batch) < max_results:
                    break
                start_at += len(batch)

            logger.info("JQL search returned %d issues: %s", len(all_issues), jql[:100])
            return all_issues
        except JIRAError as exc:
            logger.error("%s: %s (JQL: %s)", ERR_JIRA_SEARCH_FAILED, exc, jql[:100])
            raise RuntimeError(f"{ERR_JIRA_SEARCH_FAILED}: {exc}") from exc

    def get_issue(self, key: str, expand: str = "changelog") -> Any:
        """Get a single Jira issue by key.

        Args:
            key: Issue key (e.g. 'SCEN-123').
            expand: Comma-separated fields to expand.

        Returns:
            jira.Issue object.
        """
        client = self._get_client()
        try:
            issue = client.issue(key, expand=expand)
            logger.debug("Fetched issue %s", key)
            return issue
        except JIRAError as exc:
            logger.error("%s %s: %s", ERR_JIRA_GET_FAILED, key, exc)
            raise RuntimeError(f"{ERR_JIRA_GET_FAILED} {key}: {exc}") from exc

    def create_issue(self, fields: Dict[str, Any]) -> Any:
        """Create a new Jira issue.

        Args:
            fields: Dict of Jira issue fields.

        Returns:
            The created jira.Issue object.
        """
        client = self._get_client()
        try:
            issue = client.create_issue(fields=fields)
            logger.info("Created Jira issue %s", issue.key)
            return issue
        except JIRAError as exc:
            logger.error("%s: %s (fields: %s)", ERR_JIRA_CREATE_FAILED, exc, list(fields.keys()))
            raise RuntimeError(f"{ERR_JIRA_CREATE_FAILED}: {exc}") from exc

    def add_issue_link(
        self,
        inward_key: str,
        outward_key: str,
        link_type: str = "Relates",
    ) -> None:
        """Create a link between two Jira issues.

        Args:
            inward_key: Source issue key.
            outward_key: Target issue key.
            link_type: Link type name (e.g. 'Relates', 'Blocks').
        """
        client = self._get_client()
        try:
            client.create_issue_link(
                type=link_type,
                inwardIssue=inward_key,
                outwardIssue=outward_key,
            )
            logger.info("Linked %s -> %s (%s)", inward_key, outward_key, link_type)
        except JIRAError as exc:
            logger.error(
                "%s %s -> %s: %s", ERR_JIRA_LINK_FAILED, inward_key, outward_key, exc
            )
            raise RuntimeError(
                f"{ERR_JIRA_LINK_FAILED} {inward_key} -> {outward_key}: {exc}"
            ) from exc

    def get_boards(self, project_key: str) -> List[Any]:
        """List Jira boards for a project.

        Args:
            project_key: Jira project key.

        Returns:
            List of board objects.
        """
        client = self._get_client()
        try:
            boards = client.boards(projectKeyOrID=project_key)
            logger.debug("Found %d boards for %s", len(boards), project_key)
            return boards
        except JIRAError as exc:
            logger.error("%s for %s: %s", ERR_JIRA_BOARDS_FAILED, project_key, exc)
            raise RuntimeError(
                f"{ERR_JIRA_BOARDS_FAILED} for {project_key}: {exc}"
            ) from exc

    def transition_issue(self, key: str, transition_name: str) -> None:
        """Transition a Jira issue to a new status.

        Args:
            key: Issue key.
            transition_name: Name of the target transition.
        """
        client = self._get_client()
        try:
            transitions = client.transitions(key)
            target = None
            for t in transitions:
                if t["name"].lower() == transition_name.lower():
                    target = t
                    break

            if target is None:
                available = [t["name"] for t in transitions]
                raise ValueError(
                    f"Transition '{transition_name}' not found for {key}. "
                    f"Available: {available}"
                )

            client.transition_issue(key, target["id"])
            logger.info("Transitioned %s to '%s'", key, transition_name)
        except JIRAError as exc:
            logger.error("%s %s to '%s': %s", ERR_JIRA_TRANSITION_FAILED, key, transition_name, exc)
            raise RuntimeError(
                f"{ERR_JIRA_TRANSITION_FAILED} {key}: {exc}"
            ) from exc

    def get_issue_changelog(self, key: str) -> List[Dict[str, Any]]:
        """Get the changelog for an issue (status transitions).

        Args:
            key: Issue key.

        Returns:
            List of changelog history entries.
        """
        issue = self.get_issue(key, expand="changelog")
        histories = []
        if hasattr(issue, "changelog") and issue.changelog:
            for history in issue.changelog.histories:
                entry = {
                    "created": str(history.created),
                    "author": str(history.author) if history.author else None,
                    "items": [],
                }
                for item in history.items:
                    entry["items"].append({
                        "field": item.field,
                        "from_string": item.fromString,
                        "to_string": item.toString,
                    })
                histories.append(entry)
        return histories
