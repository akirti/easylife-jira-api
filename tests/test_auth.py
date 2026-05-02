"""Tests for src/auth.py — JWT validation and auth dependencies."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException

from src.auth import (
    ADMIN_ROLES,
    CurrentUser,
    _get_jwt_settings,
    decode_token,
    get_current_user,
    init_auth,
    require_admin,
)
from tests.conftest import (
    TEST_ADMIN_ROLES,
    TEST_JWT_ALGORITHM,
    TEST_JWT_AUDIENCE,
    TEST_JWT_ISSUER,
    TEST_JWT_SECRET,
    TEST_USER_EMAIL,
    TEST_USER_ID,
    TEST_USER_NAME,
    TEST_USER_ROLES,
)


def _encode_token(payload: dict) -> str:
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGORITHM)


class TestCurrentUser:
    """Tests for the CurrentUser class."""

    def test_basic_attributes(self):
        """CurrentUser extracts standard fields from payload."""
        payload = {
            "sub": TEST_USER_ID,
            "email": TEST_USER_EMAIL,
            "username": TEST_USER_NAME,
            "roles": TEST_USER_ROLES,
            "groups": ["team-a"],
        }
        user = CurrentUser(payload)
        assert user.user_id == TEST_USER_ID
        assert user.email == TEST_USER_EMAIL
        assert user.username == TEST_USER_NAME
        assert user.roles == TEST_USER_ROLES
        assert user.groups == ["team-a"]

    def test_is_admin_false_for_viewer(self):
        """Non-admin roles return is_admin=False."""
        user = CurrentUser({"sub": "x", "roles": ["viewer"]})
        assert user.is_admin is False

    def test_is_admin_true_for_administrator(self):
        """Administrator role returns is_admin=True."""
        user = CurrentUser({"sub": "x", "roles": ["administrator"]})
        assert user.is_admin is True

    def test_is_admin_true_for_super_administrator(self):
        """Super-administrator role returns is_admin=True."""
        user = CurrentUser({"sub": "x", "roles": ["super-administrator"]})
        assert user.is_admin is True

    def test_missing_fields_use_defaults(self):
        """Missing payload fields default to empty values."""
        user = CurrentUser({})
        assert user.user_id == ""
        assert user.email == ""
        assert user.roles == []
        assert user.groups == []

    def test_repr(self):
        """repr contains user_id and email."""
        user = CurrentUser({"sub": "u1", "email": "e@x.com"})
        r = repr(user)
        assert "u1" in r
        assert "e@x.com" in r


class TestDecodeToken:
    """Tests for JWT decode_token function."""

    def test_valid_token(self, test_config):
        """Valid token decodes successfully."""
        init_auth(test_config)
        token = _encode_token({
            "sub": TEST_USER_ID,
            "email": TEST_USER_EMAIL,
            "iss": TEST_JWT_ISSUER,
            "aud": TEST_JWT_AUDIENCE,
            "exp": int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp()),
        })
        payload = decode_token(token)
        assert payload["sub"] == TEST_USER_ID
        assert payload["email"] == TEST_USER_EMAIL

    def test_expired_token_raises(self, test_config):
        """Expired token raises 401."""
        init_auth(test_config)
        token = _encode_token({
            "sub": TEST_USER_ID,
            "iss": TEST_JWT_ISSUER,
            "aud": TEST_JWT_AUDIENCE,
            "exp": int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()),
        })
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401

    def test_wrong_secret_raises(self, test_config):
        """Token signed with wrong secret raises 401."""
        init_auth(test_config)
        token = jwt.encode(
            {"sub": "x", "iss": TEST_JWT_ISSUER, "aud": TEST_JWT_AUDIENCE,
             "exp": int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp())},
            "wrong-secret",
            algorithm=TEST_JWT_ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401

    def test_wrong_issuer_raises(self, test_config):
        """Token with wrong issuer raises 401."""
        init_auth(test_config)
        token = _encode_token({
            "sub": "x",
            "iss": "wrong-issuer",
            "aud": TEST_JWT_AUDIENCE,
            "exp": int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp()),
        })
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401

    def test_wrong_audience_raises(self, test_config):
        """Token with wrong audience raises 401."""
        init_auth(test_config)
        token = _encode_token({
            "sub": "x",
            "iss": TEST_JWT_ISSUER,
            "aud": "wrong-audience",
            "exp": int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp()),
        })
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401

    def test_malformed_token_raises(self, test_config):
        """Completely invalid token string raises 401."""
        init_auth(test_config)
        with pytest.raises(HTTPException) as exc_info:
            decode_token("not-a-jwt-token")
        assert exc_info.value.status_code == 401


class TestGetCurrentUser:
    """Tests for the get_current_user dependency."""

    @pytest.mark.asyncio
    async def test_valid_credentials(self, test_config):
        """Valid bearer token returns CurrentUser."""
        init_auth(test_config)
        token = _encode_token({
            "sub": TEST_USER_ID,
            "email": TEST_USER_EMAIL,
            "username": TEST_USER_NAME,
            "roles": TEST_USER_ROLES,
            "iss": TEST_JWT_ISSUER,
            "aud": TEST_JWT_AUDIENCE,
            "exp": int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp()),
        })
        creds = MagicMock()
        creds.credentials = token
        user = await get_current_user(creds)
        assert user.user_id == TEST_USER_ID
        assert user.email == TEST_USER_EMAIL


class TestRequireAdmin:
    """Tests for the require_admin dependency."""

    @pytest.mark.asyncio
    async def test_admin_passes(self):
        """Admin user passes the check."""
        admin = CurrentUser({"sub": "a", "roles": TEST_ADMIN_ROLES})
        result = await require_admin(admin)
        assert result.is_admin is True

    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self):
        """Non-admin user gets 403 Forbidden."""
        user = CurrentUser({"sub": "u", "roles": ["viewer"]})
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(user)
        assert exc_info.value.status_code == 403


class TestJwtSecretValidation:
    """Tests for JWT secret validation on startup."""

    def test_rejects_default_insecure_secret(self, test_config_data):
        """_get_jwt_settings raises ValueError when secret is the insecure default."""
        import json, os, tempfile
        from src.config import Config

        test_config_data["jwt"]["secret_key"] = "change-me-in-production"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()
            config = Config(f.name)
        os.unlink(f.name)

        init_auth(config)
        with pytest.raises(ValueError, match="SECURITY"):
            _get_jwt_settings()

    def test_rejects_empty_secret(self, test_config_data):
        """_get_jwt_settings raises ValueError when secret is empty."""
        import json, os, tempfile
        from src.config import Config

        test_config_data["jwt"]["secret_key"] = ""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()
            config = Config(f.name)
        os.unlink(f.name)

        init_auth(config)
        with pytest.raises(ValueError, match="SECURITY"):
            _get_jwt_settings()

    def test_accepts_valid_secret(self, test_config):
        """_get_jwt_settings works with a proper secret."""
        init_auth(test_config)
        settings = _get_jwt_settings()
        assert settings["secret_key"] == TEST_JWT_SECRET
