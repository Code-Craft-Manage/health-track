#!/usr/bin/env python3
"""Container liveness probe for the Health-Track bot.

The bot rewrites a heartbeat file every ``HEARTBEAT_INTERVAL`` seconds from its
asyncio event loop (see ``bot.py``). If the loop stalls or the whole process
hangs — the way it did on 2026-06-28, staying ``Up`` but no longer polling
Telegram — the file stops updating. Once it is older than ``MAX_AGE_SECONDS``
this probe fails, and the ``autoheal`` sidecar (see ``docker-compose.yml``)
restarts the container.

Note this catches a *stalled loop / hung process*. A poller that dies while the
loop keeps running would keep the heartbeat fresh, so ``bot.py`` covers that
case separately with an in-process watchdog. The two are defence in depth.

Exit code 0 = healthy, 1 = unhealthy (the contract Docker's HEALTHCHECK expects).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Same ``data/`` directory the bot and prefs use; a Docker volume in production.
HEARTBEAT_FILE = Path(__file__).resolve().parent / "data" / "heartbeat"

# The bot writes the heartbeat every ~30s; tolerate a few missed writes (slow
# disk, brief GC pause) before declaring the container unhealthy.
MAX_AGE_SECONDS = 120


def heartbeat_age(now: float, path: Path) -> float | None:
    """Seconds since the heartbeat was written, or ``None`` if missing/unreadable."""
    try:
        timestamp = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return now - timestamp


def is_healthy(now: float, path: Path, max_age: float = MAX_AGE_SECONDS) -> bool:
    """True when the heartbeat exists and was written within ``max_age`` seconds."""
    age = heartbeat_age(now, path)
    return age is not None and age <= max_age


def main() -> int:
    return 0 if is_healthy(time.time(), HEARTBEAT_FILE) else 1


if __name__ == "__main__":
    sys.exit(main())
