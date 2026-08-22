"""Load and save .monthly-state.json without touching weekly's catch-up keys."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))
_digest = _mymemory / "digest"
if str(_digest) not in sys.path:
    sys.path.insert(0, str(_digest))

from operation_log import _atomic_replace_text  # noqa: E402


def hermes_home() -> Path:
    """Resolve HERMES_HOME so tests can sandbox state without writing the live tree."""
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3]


def state_path() -> Path:
    """Keep monthly catch-up off .weekly-state.json so a clock write cannot drop weekly keys."""
    return hermes_home() / "memories" / "staging" / ".monthly-state.json"


def monthly_staging_dir() -> Path:
    """One directory per cycle so YYYY-MM.md is never globbed as a malformed week."""
    return hermes_home() / "memories" / "staging" / "monthly"


def notes_dir() -> Path:
    """Content-keyed map cache lives beside month files, not in the state singleton."""
    return monthly_staging_dir() / ".notes"


def month_file_path(month_key: str) -> Path:
    return monthly_staging_dir() / f"{month_key}.md"


def load_state() -> dict[str, Any]:
    """Fail-open on a corrupt state file so a bad JSON blob cannot skip month generation."""
    path = state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    """Atomic replace so a crashed clock cannot leave a half-written catch-up key."""
    payload = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _atomic_replace_text(state_path(), payload)


def atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    """Reuse digest's tmp+replace so a failed map pass cannot corrupt a cached note."""
    text = json.dumps(dict(payload), indent=2, ensure_ascii=False, default=str) + "\n"
    _atomic_replace_text(path, text)


def atomic_text_write(path: Path, content: str) -> None:
    """Same replace primitive for YYYY-MM.md so a failed dump cannot truncate last month."""
    _atomic_replace_text(path, content)
