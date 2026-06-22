"""Tiny per-user preference store backed by a JSON file.

Currently holds one preference: whether to show how-to guide images during a
check-in. Kept in ``data/prefs.json`` so it survives restarts; in production the
``data/`` directory is a Docker volume (see ``docker-compose.yml``) so the file
persists across redeploys.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "prefs.json"
_LOCK = threading.Lock()


def _load() -> dict:
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def get_show_guides(user_id: int) -> bool:
    """Whether to send guide images during a check-in for this user (default True)."""
    return bool(_load().get(str(user_id), {}).get("show_guides", True))


def set_show_guides(user_id: int, value: bool) -> None:
    """Persist this user's guide-image preference (atomic write)."""
    with _LOCK:
        data = _load()
        data.setdefault(str(user_id), {})["show_guides"] = bool(value)
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_PATH)
