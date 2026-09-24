"""Choosing paper or live from the dashboard.

The choice is persisted in `data/mode.json` and overrides AIT_MODE / AIT_LIVE_CONFIRM, so the
engine restarts into it and stays there across `trader start`. Going live from the dashboard
needs three things: every readiness check passing, the confirmation phrase typed by hand, and a
one-time code that only someone with a shell on this machine can print (`./trader live-code`),
because the dashboard's API token ships inside the browser bundle. A live start that is refused
falls back to paper and says why; it never leaves the dashboard dead."""

from __future__ import annotations

import json
import os
import secrets
from datetime import timedelta
from pathlib import Path

from app.core.types import utcnow

CODE_TTL = timedelta(minutes=10)


def mode_file(data_dir: Path) -> Path:
    return data_dir / "mode.json"


def read_choice(data_dir: Path) -> dict:
    p = mode_file(data_dir)
    try:
        d = json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        return {}
    return d if d.get("mode") in ("paper", "live") else {}


def write_choice(data_dir: Path, mode: str, **extra) -> None:
    p = mode_file(data_dir)
    p.write_text(json.dumps({"mode": mode, "at": utcnow().isoformat(), **extra}))
    os.chmod(p, 0o600)


def _code_file(data_dir: Path) -> Path:
    return data_dir / "live_code.json"


def issue_code(data_dir: Path) -> str:
    code = f"{secrets.randbelow(10**6):06d}"
    p = _code_file(data_dir)
    p.write_text(json.dumps({"code": code, "expires": (utcnow() + CODE_TTL).isoformat()}))
    os.chmod(p, 0o600)
    return code


def consume_code(data_dir: Path, code: str) -> bool:
    """True once for a fresh, matching code; any attempt burns it, so it cannot be guessed."""
    p = _code_file(data_dir)
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return False
    p.unlink(missing_ok=True)
    from datetime import datetime
    fresh = datetime.fromisoformat(d["expires"]) > utcnow()
    return fresh and secrets.compare_digest(str(d.get("code", "")), code.strip())
