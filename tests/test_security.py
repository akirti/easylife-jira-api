"""Security-focused tests for jira-api.

Tests verify:
- Regex escaping prevents NoSQL injection
- JWT weak secrets are rejected
- Admin-only endpoints enforce roles
- Connection pool is configured
"""
import re

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.config import Config


def _make_config(data: dict) -> Config:
    """Create a Config instance without reading a file."""
    cfg = Config.__new__(Config)
    cfg._data = data
    return cfg


@pytest.fixture(autouse=True)
def _reset_auth_module():
    """Reset the auth module's global _config between tests."""
    import src.auth as auth_mod
    original = auth_mod._config
    yield
    auth_mod._config = original


class TestRegexEscaping:
    """Verify $regex inputs are escaped to prevent ReDoS."""

    def test_assignee_special_chars_escaped(self):
        from src.routes.dashboard import _build_issue_filter

        result = _build_issue_filter("PROJ", None, None, "user.*+?()", None, None)
        regex_val = result["assignee"]["$regex"]
        assert ".*" not in regex_val
        assert re.escape("user.*+?()") == regex_val

    def test_assignee_backslash_escaped(self):
        from src.routes.dashboard import _build_issue_filter

        result = _build_issue_filter("PROJ", None, None, r"user\d+", None, None)
        regex_val = result["assignee"]["$regex"]
        assert regex_val == re.escape(r"user\d+")

    def test_no_regex_when_no_assignee(self):
        from src.routes.dashboard import _build_issue_filter

        result = _build_issue_filter("PROJ", None, None, None, None, None)
        assert "assignee" not in result

    def test_dollar_sign_escaped(self):
        """Ensure MongoDB operator chars like $ are escaped."""
        from src.routes.dashboard import _build_issue_filter

        result = _build_issue_filter("PROJ", None, None, "$admin", None, None)
        regex_val = result["assignee"]["$regex"]
        assert regex_val == re.escape("$admin")
        assert regex_val.startswith("\\$")

    def test_case_insensitive_flag_set(self):
        """Verify regex uses case-insensitive option."""
        from src.routes.dashboard import _build_issue_filter

        result = _build_issue_filter("PROJ", None, None, "alice", None, None)
        assert result["assignee"]["$options"] == "i"


class TestJwtSecretSecurity:
    """Verify weak JWT secrets are rejected."""

    def test_default_secret_rejected(self):
        from src.auth import init_auth, _get_jwt_settings

        cfg = _make_config({"jwt": {"secret_key": "change-me-in-production"}})
        init_auth(cfg)
        with pytest.raises(ValueError, match="must be configured securely"):
            _get_jwt_settings()

    def test_empty_secret_rejected(self):
        from src.auth import init_auth, _get_jwt_settings

        cfg = _make_config({"jwt": {"secret_key": ""}})
        init_auth(cfg)
        with pytest.raises(ValueError, match="must be configured securely"):
            _get_jwt_settings()

    def test_missing_secret_rejected(self):
        from src.auth import init_auth, _get_jwt_settings

        cfg = _make_config({"jwt": {}})
        init_auth(cfg)
        with pytest.raises(ValueError, match="must be configured securely"):
            _get_jwt_settings()

    def test_strong_secret_accepted(self):
        from src.auth import init_auth, _get_jwt_settings

        cfg = _make_config({
            "jwt": {
                "secret_key": "a-real-secret-key-32-chars-long!",
                "algorithm": "HS256",
                "issuer": "easylife-auth",
                "audience": "easylife-api",
            }
        })
        init_auth(cfg)
        settings = _get_jwt_settings()
        assert settings["secret_key"] == "a-real-secret-key-32-chars-long!"

    def test_uninitialised_auth_raises_runtime_error(self):
        import src.auth as auth_mod
        auth_mod._config = None
        with pytest.raises(RuntimeError, match="not initialized"):
            auth_mod._get_jwt_settings()


class TestAdminRoleEnforcement:
    """Verify admin-only dependencies enforce roles correctly."""

    def test_admin_role_recognized(self):
        from src.auth import CurrentUser

        user = CurrentUser({
            "sub": "u1", "email": "a@b.c", "username": "a",
            "roles": ["administrator"], "groups": [],
        })
        assert user.is_admin is True

    def test_super_admin_role_recognized(self):
        from src.auth import CurrentUser

        user = CurrentUser({
            "sub": "u2", "email": "s@b.c", "username": "s",
            "roles": ["super-administrator"], "groups": [],
        })
        assert user.is_admin is True

    def test_viewer_not_admin(self):
        from src.auth import CurrentUser

        user = CurrentUser({
            "sub": "u3", "email": "v@b.c", "username": "v",
            "roles": ["viewer"], "groups": [],
        })
        assert user.is_admin is False

    def test_empty_roles_not_admin(self):
        from src.auth import CurrentUser

        user = CurrentUser({
            "sub": "u4", "email": "e@b.c", "username": "e",
            "roles": [], "groups": [],
        })
        assert user.is_admin is False

    @pytest.mark.asyncio
    async def test_require_admin_rejects_viewer(self):
        from fastapi import HTTPException
        from src.auth import require_admin, CurrentUser

        viewer = CurrentUser({
            "sub": "u5", "email": "v@b.c", "username": "v",
            "roles": ["viewer"], "groups": [],
        })
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(viewer)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_admin_allows_admin(self):
        from src.auth import require_admin, CurrentUser

        admin = CurrentUser({
            "sub": "u6", "email": "a@b.c", "username": "a",
            "roles": ["administrator"], "groups": [],
        })
        result = await require_admin(admin)
        assert result.user_id == "u6"


class TestConnectionPoolSecurity:
    """Verify MongoDB connection pool is properly configured."""

    @pytest.mark.asyncio
    async def test_pool_params_set(self):
        from src.db import connect_db

        cfg = _make_config({
            "database": {
                "uri": "mongodb://localhost:27017",
                "name": "test",
            }
        })

        with patch("src.db.AsyncIOMotorClient") as mock_client, \
             patch("src.db._create_indexes", new_callable=AsyncMock):
            mock_client.return_value.__getitem__ = MagicMock()
            await connect_db(cfg)
            _, kwargs = mock_client.call_args
            assert kwargs["maxPoolSize"] >= 100, "Pool must support concurrent load"
            assert kwargs["retryWrites"] is True
            assert kwargs["retryReads"] is True
            assert kwargs["serverSelectionTimeoutMS"] <= 10000

    @pytest.mark.asyncio
    async def test_pool_has_min_size(self):
        from src.db import connect_db

        cfg = _make_config({
            "database": {
                "uri": "mongodb://localhost:27017",
                "name": "test",
            }
        })

        with patch("src.db.AsyncIOMotorClient") as mock_client, \
             patch("src.db._create_indexes", new_callable=AsyncMock):
            mock_client.return_value.__getitem__ = MagicMock()
            await connect_db(cfg)
            _, kwargs = mock_client.call_args
            assert kwargs.get("minPoolSize", 0) >= 10, "Min pool size should be reasonable"
