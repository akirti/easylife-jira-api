"""Tests for src/config.py — Config loader."""
import json
import os
import tempfile

import pytest

from src.config import Config


class TestConfigLoading:
    """Tests for loading config from JSON files."""

    def test_load_valid_json(self, test_config_data):
        """Config loads all keys from a valid JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()
            config = Config(f.name)
        os.unlink(f.name)

        assert config.get("server.port") == 8001
        assert config.get("jira.project_key") == "TEST"
        assert config.get("database.name") == "easylife_jira_test"

    def test_load_missing_file(self):
        """Config handles missing file gracefully with empty data."""
        config = Config("/nonexistent/path/config.json")
        assert config.data == {}

    def test_get_nested_value(self, test_config):
        """get() returns deeply nested values via dot path."""
        assert test_config.get("jwt.algorithm") == "HS256"
        assert test_config.get("sync.period_months") == 3

    def test_get_default_for_missing_key(self, test_config):
        """get() returns default when key does not exist."""
        assert test_config.get("nonexistent.key", "fallback") == "fallback"
        assert test_config.get("server.nonexistent") is None

    def test_get_top_level_dict(self, test_config):
        """get() returns a dict for a non-leaf path."""
        jira = test_config.get("jira")
        assert isinstance(jira, dict)
        assert jira["project_key"] == "TEST"

    def test_get_list_value(self, test_config):
        """get() returns list values correctly."""
        origins = test_config.get("server.cors_origins")
        assert isinstance(origins, list)
        assert "http://localhost:3000" in origins

    def test_data_property(self, test_config):
        """data property returns the full config dict."""
        data = test_config.data
        assert "server" in data
        assert "jwt" in data
        assert "jira" in data


class TestEnvOverrides:
    """Tests for environment variable overrides."""

    def test_env_override_simple_string(self, test_config_data):
        """JIRA_API_ env vars override config values."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()

            os.environ["JIRA_API_DATABASE__NAME"] = "overridden_db"
            try:
                config = Config(f.name)
                assert config.get("database.name") == "overridden_db"
            finally:
                del os.environ["JIRA_API_DATABASE__NAME"]
                os.unlink(f.name)

    def test_env_override_integer(self, test_config_data):
        """Env var integer values are converted properly."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()

            os.environ["JIRA_API_SERVER__PORT"] = "9999"
            try:
                config = Config(f.name)
                assert config.get("server.port") == 9999
            finally:
                del os.environ["JIRA_API_SERVER__PORT"]
                os.unlink(f.name)

    def test_env_override_boolean(self, test_config_data):
        """Env var boolean values are converted properly."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()

            os.environ["JIRA_API_SYNC__ENABLED"] = "true"
            try:
                config = Config(f.name)
                assert config.get("sync.enabled") is True
            finally:
                del os.environ["JIRA_API_SYNC__ENABLED"]
                os.unlink(f.name)

    def test_env_override_json_value(self, test_config_data):
        """Env var JSON values are parsed correctly."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()

            os.environ["JIRA_API_SERVER__CORS_ORIGINS"] = '["http://new-origin:3000"]'
            try:
                config = Config(f.name)
                origins = config.get("server.cors_origins")
                assert origins == ["http://new-origin:3000"]
            finally:
                del os.environ["JIRA_API_SERVER__CORS_ORIGINS"]
                os.unlink(f.name)

    def test_env_override_creates_new_key(self, test_config_data):
        """Env vars can create keys that do not exist in the JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(test_config_data, f)
            f.flush()

            os.environ["JIRA_API_CUSTOM__NEW_KEY"] = "new_value"
            try:
                config = Config(f.name)
                assert config.get("custom.new_key") == "new_value"
            finally:
                del os.environ["JIRA_API_CUSTOM__NEW_KEY"]
                os.unlink(f.name)


class TestConvert:
    """Tests for the _convert static method."""

    def test_convert_true(self):
        assert Config._convert("true") is True

    def test_convert_false(self):
        assert Config._convert("false") is False

    def test_convert_integer(self):
        assert Config._convert("42") == 42

    def test_convert_float(self):
        assert Config._convert("3.14") == 3.14

    def test_convert_json_list(self):
        assert Config._convert('["a", "b"]') == ["a", "b"]

    def test_convert_plain_string(self):
        assert Config._convert("hello world") == "hello world"


class TestRepr:
    """Tests for string representation."""

    def test_repr(self, test_config):
        """repr shows top-level keys."""
        r = repr(test_config)
        assert "Config" in r
        assert "server" in r
