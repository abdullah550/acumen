"""Configuration loading: packaged defaults, optionally overridden by ./config.yml and environment."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).with_name("default_config.yml")


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config(dict):
    """A dict with dotted-path access: ``cfg.get_path("signals.enter_score")``."""

    def get_path(self, path: str, default: Any = None) -> Any:
        node: Any = self
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def contact(self) -> str:
        env_name = self.get_path("data.contact_env", "ACUMEN_CONTACT")
        return os.environ.get(env_name) or self.get_path("app.contact", "") or ""

    @property
    def user_agent(self) -> str:
        contact = self.contact
        base = "AcumenPersonalAnalytics/1.0"
        return f"{base} (+{contact})" if contact else base


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    with open(_DEFAULT_PATH, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    override_path = Path(path) if path else Path("config.yml")
    if override_path.exists():
        with open(override_path, "r", encoding="utf-8") as fh:
            user = yaml.safe_load(fh) or {}
        if not isinstance(user, dict):
            raise ValueError(f"{override_path} must contain a mapping at the top level")
        cfg = _deep_merge(cfg, user)
    return Config(cfg)
