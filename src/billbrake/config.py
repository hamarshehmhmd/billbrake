from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

from .pricing import DEFAULT_PRICING


@dataclass
class Project:
    name: str
    keys: list[str]
    daily_usd: float | None = None
    monthly_usd: float | None = None
    # Worst-case cost reserved before a request is sent, so concurrent requests can't overshoot much.
    reserve_usd: float = 0.0


@dataclass
class Config:
    projects: list[Project]
    openai_base: str = "https://api.openai.com"
    anthropic_base: str = "https://api.anthropic.com"
    openai_key_env: str = "OPENAI_API_KEY"
    anthropic_key_env: str = "ANTHROPIC_API_KEY"
    db_path: str = "billbrake.db"
    admin_token: str | None = None
    block_unknown_models: bool = True
    pricing: dict[str, dict[str, float]] = field(default_factory=lambda: dict(DEFAULT_PRICING))

    def project_for_key(self, key: str | None) -> Project | None:
        if not key:
            return None
        for p in self.projects:
            if key in p.keys:
                return p
        return None


def _expand(v):
    if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
        return os.environ.get(v[2:-1], "")
    return v


def load(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return from_dict(raw)


def from_dict(raw: dict) -> Config:
    projects = []
    for name, p in (raw.get("projects") or {}).items():
        keys = [_expand(k) for k in p.get("keys", [])]
        projects.append(Project(name=name, keys=[k for k in keys if k], daily_usd=p.get("daily_usd"),
                                monthly_usd=p.get("monthly_usd"), reserve_usd=float(p.get("reserve_usd", 0.0))))
    if not projects:
        raise ValueError("config needs at least one project")
    up = raw.get("upstreams") or {}
    pricing = dict(DEFAULT_PRICING)
    pricing.update(raw.get("pricing") or {})
    return Config(
        projects=projects,
        openai_base=up.get("openai", {}).get("base_url", "https://api.openai.com").rstrip("/"),
        anthropic_base=up.get("anthropic", {}).get("base_url", "https://api.anthropic.com").rstrip("/"),
        openai_key_env=up.get("openai", {}).get("api_key_env", "OPENAI_API_KEY"),
        anthropic_key_env=up.get("anthropic", {}).get("api_key_env", "ANTHROPIC_API_KEY"),
        db_path=raw.get("db_path", "billbrake.db"),
        admin_token=_expand(raw.get("admin_token")) or None,
        block_unknown_models=raw.get("block_unknown_models", True),
        pricing=pricing,
    )
