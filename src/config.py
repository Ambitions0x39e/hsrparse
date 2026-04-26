"""
src/config.py

Loads gszparse configuration from env.json at the package root.
All other modules should import from here rather than reading env.json directly.
"""

from __future__ import annotations

import json
import pathlib

_ENV_FILE = pathlib.Path(__file__).parent.parent / "env.json"

_cfg: dict[str, str] = {}
if _ENV_FILE.exists():
    _cfg = json.loads(_ENV_FILE.read_text(encoding="utf-8"))


def get(key: str, default: str = "") -> str:
    """Return the value for *key* from env.json, or *default* if absent."""
    return _cfg.get(key, default)
