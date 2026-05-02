"""Tests for src/services/jira_sync.py — Sync service (mocked)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.jira_sync import (
    JiraSyncService,
    _compute_cutoff_date,
    _serialize_to_jsonl_gz,
    compute_days_in_status,
)


class TestComputeDaysInStatus:
    """Tests for compute_days_in_status function."""

    def test_no_histories(self):
        """Returns None when changelog has no histories."""
        changelog = MagicMock()
        changelog.histories = []
        assert compute_days_in_status(changelog) is None

    def test_no_status_changes(self):
        """Returns None when no status field changes exist."""
        item = MagicMock()
        item.field = "priority"
        history = MagicMock()
        history.items = [item]
        history.created = "2025-01-01T00:00:00.000+0000"
        changelog = MagicMock()
        changelog.histories = [history]
        assert compute_days_in_status(changelog) is None

    def test_single_status_change(self):
        """Computes days since a single status change."""
        item = MagicMock()
        item.field = "status"
        history = MagicMock()
        history.items = [item]
        history.created = "2025-03-01T00:00:00+00:00"
        changelog = MagicMock()
        changelog.histories = [history]

        days = compute_days_in_status(changelog)
        assert days is not None
        assert days > 0

    def test_multiple_status_changes_uses_last(self):
        """Uses the most recent status change (last in reversed order)."""
        item1 = MagicMock()
        item1.field = "status"
        h1 = MagicMock()
        h1.items = [item1]
        h1.created = "2025-01-01T00:00:00+00:00"

        item2 = MagicMock()
        item2.field = "status"
        h2 = MagicMock()
        h2.items = [item2]
        h2.created = "2025-03-15T00:00:00+00:00"

        changelog = MagicMock()
        changelog.histories = [h1, h2]

        days = compute_days_in_status(changelog)
        assert days is not None
        # h2 is more recent, should have fewer days
        assert days >= 0

    def test_none_changelog(self):
        """Returns None for None changelog."""
        changelog = MagicMock()
        changelog.histories = None
        assert compute_days_in_status(changelog) is None


class TestSerializeToJsonlGz:
    """Tests for _serialize_to_jsonl_gz function."""

    def test_empty_list(self):
        """Empty list produces valid gzip."""
        import gzip
        result = _serialize_to_jsonl_gz([])
        decompressed = gzip.decompress(result)
        assert decompressed == b""

    def test_single_issue(self):
        """Single issue serializes to one line."""
        import gzip
        import json
        issues = [{"key": "TEST-1", "summary": "Hello"}]
        result = _serialize_to_jsonl_gz(issues)
        lines = gzip.decompress(result).decode("utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["key"] == "TEST-1"

    def test_strips_mongo_id(self):
        """_id field is removed from serialized output."""
        import gzip
        import json
        issues = [{"_id": "mongo-id-123", "key": "TEST-1"}]
        result = _serialize_to_jsonl_gz(issues)
        lines = gzip.decompress(result).decode("utf-8").strip().split("\n")
        parsed = json.loads(lines[0])
        assert "_id" not in parsed

    def test_datetime_conversion(self):
        """Datetime values are serialized to ISO strings."""
        import gzip
        import json
        now = datetime(2025, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        issues = [{"key": "TEST-1", "synced_at": now}]
        result = _serialize_to_jsonl_gz(issues)
        lines = gzip.decompress(result).decode("utf-8").strip().split("\n")
        parsed = json.loads(lines[0])
        assert "2025-03-01" in parsed["synced_at"]

    def test_multiple_issues(self):
        """Multiple issues produce multiple lines."""
        import gzip
        issues = [{"key": f"TEST-{i}"} for i in range(5)]
        result = _serialize_to_jsonl_gz(issues)
        lines = gzip.decompress(result).decode("utf-8").strip().split("\n")
        assert len(lines) == 5


class TestComputeCutoffDate:
    """Tests for _compute_cutoff_date function."""

    def test_three_months_ago(self):
        """Cutoff 3 months ago is in the past."""
        cutoff = _compute_cutoff_date(3)
        now = datetime.now(timezone.utc)
        assert cutoff < now

    def test_twelve_months_ago(self):
        """Cutoff 12 months ago is approximately a year ago."""
        cutoff = _compute_cutoff_date(12)
        now = datetime.now(timezone.utc)
        assert cutoff.year <= now.year - 1 or cutoff.month <= now.month

    def test_zero_months(self):
        """Cutoff 0 months is roughly now."""
        cutoff = _compute_cutoff_date(0)
        now = datetime.now(timezone.utc)
        assert cutoff.year == now.year
        assert cutoff.month == now.month


class TestJiraSyncServiceSync:
    """Tests for JiraSyncService.sync_project method."""

    @pytest.mark.asyncio
    async def test_sync_empty_results(self, test_config, mock_jira_client, mock_db):
        """Sync with no Jira issues results in 0 synced."""
        inner_client = MagicMock()
        inner_client.search_issues = MagicMock(return_value=[])
        mock_jira_client._get_client = MagicMock(return_value=inner_client)
        gcs_client = MagicMock()

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            count = await service.sync_project("TEST", days_back=90)

        assert count == 0

    @pytest.mark.asyncio
    async def test_sync_upserts_issues(self, test_config, mock_jira_client, mock_db):
        """Sync maps and upserts issues to MongoDB."""
        mock_issue = MagicMock()
        mock_issue.key = "TEST-1"
        mock_issue.id = "10001"
        mock_issue.fields = MagicMock()
        mock_issue.fields.summary = "Test"
        mock_issue.fields.status = MagicMock()
        mock_issue.fields.status.__str__ = lambda self: "Open"
        mock_issue.fields.status.statusCategory = MagicMock()
        mock_issue.fields.status.statusCategory.__str__ = lambda self: "To Do"
        mock_issue.fields.issuetype = MagicMock()
        mock_issue.fields.issuetype.__str__ = lambda self: "Task"
        mock_issue.fields.priority = MagicMock()
        mock_issue.fields.priority.__str__ = lambda self: "Medium"
        mock_issue.fields.assignee = None
        mock_issue.fields.reporter = None
        mock_issue.fields.project = MagicMock()
        mock_issue.fields.project.key = "TEST"
        mock_issue.fields.project.name = "Test"
        mock_issue.fields.created = "2025-01-01"
        mock_issue.fields.updated = "2025-02-01"
        mock_issue.fields.duedate = None
        mock_issue.fields.resolutiondate = None
        mock_issue.fields.labels = []
        mock_issue.fields.components = []
        mock_issue.fields.description = None
        mock_issue.fields.parent = None
        mock_issue.fields.subtasks = []
        mock_issue.fields.issuelinks = []
        mock_issue.fields.flagged = False
        mock_issue.fields.comment = None
        mock_issue.changelog = MagicMock()
        mock_issue.changelog.histories = []

        inner_client = MagicMock()
        inner_client.search_issues = MagicMock(return_value=[mock_issue])
        mock_jira_client._get_client = MagicMock(return_value=inner_client)
        gcs_client = MagicMock()

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            count = await service.sync_project("TEST")

        assert count == 1
        mock_db["jira_issues"].bulk_write.assert_called_once()


class TestJiraSyncServiceArchive:
    """Tests for JiraSyncService.archive_old_issues method."""

    @pytest.mark.asyncio
    async def test_archive_no_old_issues(self, test_config, mock_jira_client, mock_db):
        """Archive with no old issues returns empty result."""
        gcs_client = MagicMock()

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            result = await service.archive_old_issues("TEST", 6)

        assert result["issue_count"] == 0

    @pytest.mark.asyncio
    async def test_archive_with_old_issues(self, test_config, mock_jira_client, mock_db):
        """Archive uploads to GCS, saves metadata, and removes from MongoDB."""
        gcs_client = MagicMock()
        gcs_client.upload_file = MagicMock(return_value="gs://bucket/path.jsonl.gz")

        old_issues = [
            {"key": "TEST-1", "summary": "Old issue 1", "updated": "2020-01-01T00:00:00"},
            {"key": "TEST-2", "summary": "Old issue 2", "updated": "2020-02-01T00:00:00"},
        ]

        # Mock the find cursor for jira_issues to return old issues
        issues_cursor = AsyncMock()
        issues_cursor.to_list = AsyncMock(return_value=old_issues)
        mock_db["jira_issues"].find = MagicMock(return_value=issues_cursor)

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            result = await service.archive_old_issues("TEST", 6)

        assert result["issue_count"] == 2
        assert result["project_key"] == "TEST"
        gcs_client.upload_file.assert_called_once()
        mock_db["jira_issue_archives"].insert_one.assert_called_once()
        mock_db["jira_issues"].delete_many.assert_called_once()

    @pytest.mark.asyncio
    async def test_archive_gcs_failure_raises(self, test_config, mock_jira_client, mock_db):
        """Archive raises when GCS upload fails."""
        gcs_client = MagicMock()
        gcs_client.upload_file = MagicMock(side_effect=RuntimeError("GCS down"))

        old_issues = [{"key": "TEST-1", "summary": "Old", "updated": "2020-01-01T00:00:00"}]
        issues_cursor = AsyncMock()
        issues_cursor.to_list = AsyncMock(return_value=old_issues)
        mock_db["jira_issues"].find = MagicMock(return_value=issues_cursor)

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            with pytest.raises(RuntimeError, match="GCS down"):
                await service.archive_old_issues("TEST", 6)

    @pytest.mark.asyncio
    async def test_get_archive_list_empty(self, test_config, mock_jira_client, mock_db):
        """get_archive_list returns empty list when no archives exist."""
        gcs_client = MagicMock()

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            archives = await service.get_archive_list("TEST")

        assert archives == []

    @pytest.mark.asyncio
    async def test_get_archive_list_with_data(self, test_config, mock_jira_client, mock_db):
        """get_archive_list returns archive records with _id stripped."""
        gcs_client = MagicMock()

        archive_records = [
            {
                "_id": "mongo-id-1",
                "archive_id": "TEST_20250301",
                "project_key": "TEST",
                "gcs_path": "archives/TEST_20250301.jsonl.gz",
                "issue_count": 50,
                "archived_at": "2025-03-01T00:00:00Z",
                "size_bytes": 12345,
            }
        ]
        archive_cursor = AsyncMock()
        archive_cursor.to_list = AsyncMock(return_value=archive_records)
        archive_cursor.sort = MagicMock(return_value=archive_cursor)
        mock_db["jira_issue_archives"].find = MagicMock(return_value=archive_cursor)

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            archives = await service.get_archive_list("TEST")

        assert len(archives) == 1
        assert archives[0]["archive_id"] == "TEST_20250301"
        assert "_id" not in archives[0]

    @pytest.mark.asyncio
    async def test_get_archive_list_no_filter(self, test_config, mock_jira_client, mock_db):
        """get_archive_list without project_key queries all archives."""
        gcs_client = MagicMock()

        archive_cursor = AsyncMock()
        archive_cursor.to_list = AsyncMock(return_value=[])
        archive_cursor.sort = MagicMock(return_value=archive_cursor)
        mock_db["jira_issue_archives"].find = MagicMock(return_value=archive_cursor)

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            await service.get_archive_list(None)

        # find() should be called with empty query
        mock_db["jira_issue_archives"].find.assert_called_once_with({})


class TestJiraSyncServiceGetArchiveDownloadUrl:
    """Tests for JiraSyncService.get_archive_download_url method."""

    @pytest.mark.asyncio
    async def test_download_url_not_found(self, test_config, mock_jira_client, mock_db):
        """Returns None when archive record not found."""
        gcs_client = MagicMock()
        mock_db["jira_issue_archives"].find_one = AsyncMock(return_value=None)

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            result = await service.get_archive_download_url("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_download_url_success(self, test_config, mock_jira_client, mock_db):
        """Returns signed URL when archive record exists."""
        gcs_client = MagicMock()
        gcs_client.get_signed_url = MagicMock(return_value="https://signed-url.example.com")
        mock_db["jira_issue_archives"].find_one = AsyncMock(return_value={
            "archive_id": "TEST_20250301",
            "gcs_path": "archives/TEST_20250301.jsonl.gz",
        })

        with patch("src.services.jira_sync.get_db", return_value=mock_db):
            service = JiraSyncService(test_config, mock_jira_client, gcs_client)
            result = await service.get_archive_download_url("TEST_20250301")

        assert result == "https://signed-url.example.com"
        gcs_client.get_signed_url.assert_called_once_with("archives/TEST_20250301.jsonl.gz", 60)


class TestJiraSyncServiceBuildIssueUrl:
    """Tests for JiraSyncService._build_issue_url method."""

    def test_build_issue_url_with_base(self, test_config, mock_jira_client):
        """Returns full browse URL when base_url is configured."""
        gcs_client = MagicMock()
        service = JiraSyncService(test_config, mock_jira_client, gcs_client)
        url = service._build_issue_url("TEST-1")
        assert url == "https://test.atlassian.net/browse/TEST-1"

    def test_build_issue_url_no_base(self, test_config_data, mock_jira_client):
        """Returns empty string when base_url is not configured."""
        from src.config import Config
        import json, tempfile, os

        test_config_data["jira"]["base_url"] = ""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()
            config = Config(f.name)
        os.unlink(f.name)

        gcs_client = MagicMock()
        service = JiraSyncService(config, mock_jira_client, gcs_client)
        url = service._build_issue_url("TEST-1")
        assert url == ""


class TestComputeDaysFromChangelog:
    """Tests for JiraSyncService._compute_days_from_changelog method."""

    def test_no_changelog(self, test_config, mock_jira_client):
        """Returns None when issue has no changelog attribute."""
        gcs_client = MagicMock()
        service = JiraSyncService(test_config, mock_jira_client, gcs_client)

        mock_issue = MagicMock(spec=[])  # No changelog attribute
        result = service._compute_days_from_changelog(mock_issue)
        assert result is None

    def test_with_changelog(self, test_config, mock_jira_client):
        """Delegates to compute_days_in_status when changelog exists."""
        gcs_client = MagicMock()
        service = JiraSyncService(test_config, mock_jira_client, gcs_client)

        item = MagicMock()
        item.field = "status"
        history = MagicMock()
        history.items = [item]
        history.created = "2025-03-01T00:00:00+00:00"
        changelog = MagicMock()
        changelog.histories = [history]

        mock_issue = MagicMock()
        mock_issue.changelog = changelog

        result = service._compute_days_from_changelog(mock_issue)
        assert result is not None
        assert result > 0
