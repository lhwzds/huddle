"""Configuration and agent persona loading."""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Source checkout: <repo>/backend/huddle; container: /app/huddle.
_package_path = Path(__file__).resolve()
_candidates = [_package_path.parents[1] / "config" / "huddle.yaml",
               _package_path.parents[2] / "config" / "huddle.yaml"]
DEFAULT_CONFIG = next((p for p in _candidates if p.is_file()), _candidates[1])
_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

@dataclass
class AgentSpec:
    key: str
    name: str
    role: str
    color: str
    lens: str
    model: str | None
    persona: str
    knowledge: str
    common: str = ""


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match) -> str:
            var, default = match.groups()
            result = os.environ.get(var)
            if default is not None and not result:
                return default
            if result is None:
                raise ValueError(f"Missing environment variable {var}")
            return result
        return _PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {_interpolate(k): _interpolate(v) for k, v in value.items()}
    return value


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path or os.environ.get("HUDDLE_CONFIG", DEFAULT_CONFIG)).expanduser().resolve()
    with path.open(encoding="utf-8") as f:
        return _interpolate(yaml.safe_load(f))


def params_for(model_cfg: dict, profile: str) -> dict:
    def merge(base: dict, override: dict) -> dict:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = copy.deepcopy(value)
        return base
    params = model_cfg.get("params") or {}
    return merge(copy.deepcopy(params.get("default") or {}), params.get(profile) or {})


@dataclass
class GroupSpec:
    key: str
    name: str
    description: str
    common: str
    members: list[AgentSpec]
    examples: list[str]


def config_root(config_path=None) -> Path:
    return Path(config_path or os.environ.get("HUDDLE_CONFIG", DEFAULT_CONFIG)).expanduser().resolve().parent


def _split_frontmatter(raw: str, what: str):
    parts = re.split(r"^---\s*$", raw, maxsplit=2, flags=re.MULTILINE)
    if len(parts) != 3:
        raise ValueError(f"Invalid frontmatter: {what}")
    return _interpolate(yaml.safe_load(parts[1]) or {}), parts[2]


def _load_agent(path: Path, key: str, common: str) -> AgentSpec:
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"), key)
    sections = re.split(r"^##\s+(Persona|Private knowledge)\s*$", body, flags=re.MULTILINE | re.IGNORECASE)
    content = {sections[i].lower(): sections[i+1].strip() for i in range(1, len(sections), 2)}
    return AgentSpec(key=key, name=meta["name"], role=meta["role"], color=meta["color"], lens=meta["lens"],
                     model=meta.get("model"), persona=content.get("persona", ""),
                     knowledge=content.get("private knowledge", ""), common=common)


def load_group(cfg: dict, key: str, *, config_path=None) -> GroupSpec:
    """A preset group: config/groups/<key>/group.md (frontmatter + shared context) and agents/*.md."""
    folder = config_root(config_path) / "groups" / key
    meta, body = _split_frontmatter((folder / "group.md").read_text(encoding="utf-8"), f"group {key}")
    common = body.strip()
    members = [_load_agent(folder / "agents" / f"{m}.md", m, common) for m in meta["members"]]
    return GroupSpec(key=key, name=meta["name"], description=meta.get("description", ""), common=common,
                     members=members, examples=list(meta.get("examples") or []))


def load_groups(cfg: dict | None = None, *, config_path=None) -> list[GroupSpec]:
    cfg = cfg if cfg is not None else load_config(config_path)
    return [load_group(cfg, key, config_path=config_path) for key in cfg["groups"]]


def default_group(cfg: dict) -> str:
    return cfg.get("default_group") or cfg["groups"][0]


def load_agents(cfg: dict | None = None, group: str | None = None, *, config_path=None) -> list[AgentSpec]:
    """The members of one preset group (the default group when none is given)."""
    cfg = cfg if cfg is not None else load_config(config_path)
    return load_group(cfg, group or default_group(cfg), config_path=config_path).members
