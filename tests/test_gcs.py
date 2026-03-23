"""Tests for src/services/gcs.py — Google Cloud Storage client."""
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.services.gcs import (
    ERR_GCS_DELETE_FAILED,
    ERR_GCS_NOT_CONFIGURED,
    ERR_GCS_SIGNED_URL_FAILED,
    ERR_GCS_UPLOAD_FAILED,
    GCSClient,
)


def _make_config_with(test_config_data, overrides=None):
    """Create a Config from test_config_data with optional overrides."""
    from src.config import Config

    data = {**test_config_data}
    if overrides:
        for k, v in overrides.items():
            keys = k.split(".")
            node = data
            for seg in keys[:-1]:
                node = node.setdefault(seg, {})
            node[keys[-1]] = v

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        f.flush()
        config = Config(f.name)
    os.unlink(f.name)
    return config


class TestGCSClientInit:
    """Tests for GCSClient initialization."""

    def test_init_stores_config(self, test_config):
        """Client stores bucket name and credentials from config."""
        client = GCSClient(test_config)
        assert client._bucket_name == "test-bucket"
        assert client._client is None
        assert client._bucket is None


class TestGetBucket:
    """Tests for _get_bucket lazy initialization."""

    def test_raises_when_no_bucket_configured(self, test_config_data):
        """Raises RuntimeError when bucket_name is empty."""
        config = _make_config_with(test_config_data, {"gcs.bucket_name": ""})
        client = GCSClient(config)
        with pytest.raises(RuntimeError, match=ERR_GCS_NOT_CONFIGURED):
            client._get_bucket()

    def test_initializes_with_credentials_json(self, test_config_data):
        """Uses from_service_account_info when credentials_json is set."""
        config = _make_config_with(
            test_config_data,
            {"gcs.credentials_json": '{"type": "service_account", "project_id": "test"}'},
        )

        mock_storage_module = MagicMock()
        mock_client_instance = MagicMock()
        mock_storage_module.Client.from_service_account_info.return_value = mock_client_instance
        mock_bucket = MagicMock()
        mock_client_instance.bucket.return_value = mock_bucket

        with patch("google.cloud.storage.Client", mock_storage_module.Client):
            client = GCSClient(config)
            result = client._get_bucket()

            mock_storage_module.Client.from_service_account_info.assert_called_once()
            assert result is mock_bucket

    def test_initializes_with_default_credentials(self, test_config):
        """Uses default Client() when no credentials_json."""
        mock_storage_module = MagicMock()
        mock_client_instance = MagicMock()
        mock_storage_module.Client.return_value = mock_client_instance
        mock_bucket = MagicMock()
        mock_client_instance.bucket.return_value = mock_bucket

        with patch("google.cloud.storage.Client", mock_storage_module.Client):
            client = GCSClient(test_config)
            result = client._get_bucket()

            mock_storage_module.Client.assert_called_once()
            assert result is mock_bucket

    def test_returns_cached_bucket(self, test_config):
        """Returns cached bucket on second call without reinitializing."""
        client = GCSClient(test_config)
        mock_bucket = MagicMock()
        client._bucket = mock_bucket

        result = client._get_bucket()
        assert result is mock_bucket


class TestUploadFile:
    """Tests for GCSClient.upload_file method."""

    def test_upload_success(self, test_config):
        """Upload returns GCS URI on success."""
        client = GCSClient(test_config)
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        client._bucket = mock_bucket

        result = client.upload_file(b"hello world", "test/path.txt", "text/plain")

        mock_blob.upload_from_string.assert_called_once_with(b"hello world", content_type="text/plain")
        assert result == "gs://test-bucket/test/path.txt"

    def test_upload_failure_raises(self, test_config):
        """Upload wraps exceptions in RuntimeError."""
        client = GCSClient(test_config)
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.upload_from_string.side_effect = Exception("Network error")
        mock_bucket.blob.return_value = mock_blob
        client._bucket = mock_bucket

        with pytest.raises(RuntimeError, match=ERR_GCS_UPLOAD_FAILED):
            client.upload_file(b"data", "path.txt")


class TestGetSignedUrl:
    """Tests for GCSClient.get_signed_url method."""

    def test_signed_url_success(self, test_config):
        """Returns a signed URL on success."""
        client = GCSClient(test_config)
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.return_value = "https://storage.googleapis.com/signed-url"
        mock_bucket.blob.return_value = mock_blob
        client._bucket = mock_bucket

        result = client.get_signed_url("test/path.txt", expiry_minutes=30)

        assert result == "https://storage.googleapis.com/signed-url"
        mock_blob.generate_signed_url.assert_called_once()

    def test_signed_url_failure_raises(self, test_config):
        """Wraps exceptions in RuntimeError."""
        client = GCSClient(test_config)
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.generate_signed_url.side_effect = Exception("Auth error")
        mock_bucket.blob.return_value = mock_blob
        client._bucket = mock_bucket

        with pytest.raises(RuntimeError, match=ERR_GCS_SIGNED_URL_FAILED):
            client.get_signed_url("path.txt")


class TestDeleteFile:
    """Tests for GCSClient.delete_file method."""

    def test_delete_success(self, test_config):
        """Returns True on successful deletion."""
        client = GCSClient(test_config)
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        client._bucket = mock_bucket

        result = client.delete_file("test/path.txt")

        assert result is True
        mock_blob.delete.assert_called_once()

    def test_delete_failure_raises(self, test_config):
        """Wraps exceptions in RuntimeError."""
        client = GCSClient(test_config)
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.delete.side_effect = Exception("Not found")
        mock_bucket.blob.return_value = mock_blob
        client._bucket = mock_bucket

        with pytest.raises(RuntimeError, match=ERR_GCS_DELETE_FAILED):
            client.delete_file("path.txt")
