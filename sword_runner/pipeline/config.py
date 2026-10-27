from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from .io_utils import read_json


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def expand_value(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        text = value
        for key, replacement in variables.items():
            text = text.replace("{" + key + "}", replacement)
        return text
    if isinstance(value, list):
        return [expand_value(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: expand_value(item, variables) for key, item in value.items()}
    return value


def load_config(runner_root: Path, project_root: Path, config_path: Path | None = None) -> dict[str, Any]:
    default_path = runner_root / "config" / "default.json"
    cfg = read_json(default_path)

    local_env = runner_root / "config" / ".env"
    load_env_file(local_env)

    if config_path:
        user_cfg = read_json(config_path)
        cfg = deep_merge(cfg, user_cfg)

    variables = {
        "project_root": str(project_root).replace("\\", "/"),
        "runner_root": str(runner_root).replace("\\", "/"),
    }
    cfg = expand_value(cfg, variables)
    cfg["project_root"] = str(project_root)
    return cfg


def cfg_path(cfg: dict[str, Any], section: str, key: str) -> Path:
    return Path(cfg[section][key])

