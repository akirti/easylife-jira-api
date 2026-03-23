"""Simplified configuration loader for jira-api service.

Loads a JSON config file and allows env var overrides with JIRA_API_ prefix.
Provides dot-path access via get(path, default).
"""
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENV_PREFIX = "JIRA_API_"
_DOT_SEPARATOR = "__"


class Config:
    """Load JSON config with env var overrides (JIRA_API_ prefix).

    Usage::

        cfg = Config("config/default.json")
        base_url = cfg.get("jira.base_url", "")
        port = cfg.get("server.port", 8001)
    """

    def __init__(self, config_path: str = "config/default.json"):
        self._data: dict = {}
        p = Path(config_path)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                self._data = json.load(f)
            logger.info("Loaded config from %s", p.resolve())
        else:
            logger.warning("Config file not found: %s — using defaults", config_path)
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """Override config values from JIRA_API_* env vars.

        Environment variable naming convention:
            JIRA_API_DATABASE__URI  ->  database.uri
            JIRA_API_JIRA__BASE_URL -> jira.base_url

        Double underscore (__) is the dot separator.
        Single underscore within a segment is preserved.
        """
        for key, value in os.environ.items():
            if not key.startswith(_ENV_PREFIX):
                continue
            suffix = key[len(_ENV_PREFIX):]
            dot_path = suffix.lower().replace(_DOT_SEPARATOR.lower(), ".")
            converted = self._convert(value)
            self._set_nested(dot_path, converted)
            logger.debug("Env override: %s -> %s", key, dot_path)

    def _set_nested(self, dot_path: str, value: Any) -> None:
        """Set a value at a dot-separated path, creating intermediate dicts."""
        keys = dot_path.split(".")
        node = self._data
        for segment in keys[:-1]:
            if segment not in node or not isinstance(node[segment], dict):
                node[segment] = {}
            node = node[segment]
        node[keys[-1]] = value

    @staticmethod
    def _convert(value: str) -> Any:
        """Convert string value to appropriate Python type."""
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    def get(self, dot_path: str, default: Any = None) -> Any:
        """Get a config value by dot-separated path.

        Examples::

            cfg.get("server.port", 8001)
            cfg.get("jira.base_url", "")
            cfg.get("attribute_map", {})
        """
        keys = dot_path.split(".")
        node = self._data
        for segment in keys:
            if not isinstance(node, dict) or segment not in node:
                return default
            node = node[segment]
        return node

    @property
    def data(self) -> dict:
        """Return the full configuration dictionary."""
        return self._data

    def __repr__(self) -> str:
        return f"Config(keys={list(self._data.keys())})"
