"""Lightweight Google Cloud Storage wrapper.

Provides upload, signed URL, and delete operations for Jira issue archival.
"""
import json
import logging
from datetime import timedelta
from typing import Optional

from src.config import Config

logger = logging.getLogger(__name__)

# Error messages
ERR_GCS_NOT_CONFIGURED = "GCS not configured — set gcs.bucket_name in config"
ERR_GCS_UPLOAD_FAILED = "GCS upload failed"
ERR_GCS_SIGNED_URL_FAILED = "GCS signed URL generation failed"
ERR_GCS_DELETE_FAILED = "GCS delete failed"


class GCSClient:
    """Simplified GCS client for archive operations."""

    def __init__(self, config: Config):
        self._config = config
        self._bucket_name = config.get("gcs.bucket_name", "")
        self._credentials_json = config.get("gcs.credentials_json", "")
        self._client = None
        self._bucket = None

    def _get_bucket(self):
        """Lazy-initialize GCS client and return the bucket."""
        if self._bucket is not None:
            return self._bucket

        if not self._bucket_name:
            raise RuntimeError(ERR_GCS_NOT_CONFIGURED)

        from google.cloud import storage as gcs_storage

        if self._credentials_json:
            creds_dict = json.loads(self._credentials_json)
            self._client = gcs_storage.Client.from_service_account_info(creds_dict)
        else:
            self._client = gcs_storage.Client()

        self._bucket = self._client.bucket(self._bucket_name)
        logger.info("GCS client initialized for bucket: %s", self._bucket_name)
        return self._bucket

    def upload_file(
        self,
        content: bytes,
        path: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload content to GCS.

        Args:
            content: File content as bytes.
            path: Object path within the bucket.
            content_type: MIME content type.

        Returns:
            The GCS URI (gs://bucket/path).
        """
        try:
            bucket = self._get_bucket()
            blob = bucket.blob(path)
            blob.upload_from_string(content, content_type=content_type)
            uri = f"gs://{self._bucket_name}/{path}"
            logger.info("Uploaded %d bytes to %s", len(content), uri)
            return uri
        except Exception as exc:
            logger.error("%s to %s: %s", ERR_GCS_UPLOAD_FAILED, path, exc)
            raise RuntimeError(f"{ERR_GCS_UPLOAD_FAILED}: {exc}") from exc

    def get_signed_url(self, path: str, expiry_minutes: int = 60) -> str:
        """Generate a signed download URL for a GCS object.

        Args:
            path: Object path within the bucket.
            expiry_minutes: URL validity duration in minutes.

        Returns:
            Signed URL string.
        """
        try:
            bucket = self._get_bucket()
            blob = bucket.blob(path)
            url = blob.generate_signed_url(
                expiration=timedelta(minutes=expiry_minutes),
                method="GET",
            )
            logger.debug("Generated signed URL for %s (expires in %dm)", path, expiry_minutes)
            return url
        except Exception as exc:
            logger.error("%s for %s: %s", ERR_GCS_SIGNED_URL_FAILED, path, exc)
            raise RuntimeError(f"{ERR_GCS_SIGNED_URL_FAILED}: {exc}") from exc

    def delete_file(self, path: str) -> bool:
        """Delete a GCS object.

        Args:
            path: Object path within the bucket.

        Returns:
            True if deleted successfully.
        """
        try:
            bucket = self._get_bucket()
            blob = bucket.blob(path)
            blob.delete()
            logger.info("Deleted GCS object: %s", path)
            return True
        except Exception as exc:
            logger.error("%s %s: %s", ERR_GCS_DELETE_FAILED, path, exc)
            raise RuntimeError(f"{ERR_GCS_DELETE_FAILED}: {exc}") from exc
