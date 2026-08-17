"""Public digest actions shared by hooks and the ``/digest`` slash command.

Thin orchestration over :mod:`digest` internals so the slash handler never
duplicates worker, state, or validation logic. The on_agent_end / boundary
hooks keep calling ``digest._maybe_run_digest`` directly; this module exposes
the same core with typed, synchronous-by-default wrappers for manual use.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # package import (normal plugin load)
    from . import digest
except ImportError:  # pragma: no cover - direct pytest collection path
    _module_path = Path(__file__).with_name("digest.py")
    _mod_name = "memory_digest_core"
    _spec = importlib.util.spec_from_file_location(_mod_name, _module_path)
    if _spec is None or _spec.loader is None:
        raise
    digest = importlib.util.module_from_spec(_spec)
    sys.modules[_mod_name] = digest
    _spec.loader.exec_module(digest)

try:  # package import (normal plugin load)
    from . import span_weekly
except ImportError:  # pragma: no cover - direct pytest collection path
    _sw_module_path = Path(__file__).with_name("span_weekly.py")
    _sw_mod_name = "memory_digest_span_weekly"
    _sw_spec = importlib.util.spec_from_file_location(_sw_mod_name, _sw_module_path)
    if _sw_spec is None or _sw_spec.loader is None:
        raise
    span_weekly = importlib.util.module_from_spec(_sw_spec)
    sys.modules[_sw_mod_name] = span_weekly
    _sw_spec.loader.exec_module(span_weekly)

# Force span_weekly to share this module's digest instance (matters for direct
# pytest collection, where each independent fallback import above would
# otherwise create its own separate digest module and silently defeat
# monkeypatching against `dr.digest`).
span_weekly.digest = digest


def request_digest(
    session_key: str,
    *,
    reason: str = "slash_force",
    force: bool = True,
    sync: bool = True,
) -> dict[str, Any]:
    """Run a digest now. Defaults to a forced, synchronous run for slash use."""
    return digest._maybe_run_digest(session_key, reason, force=force, sync=sync)


def get_digest_status(session_key: str, session_id: str) -> dict[str, Any]:
    """Bookmark, undigested counts, in-flight flag, and the last log line."""
    with digest._digest_lock:
        state = digest._load_state()
        entry = dict(state.get("sessions", {}).get(session_key, {}))

    bookmark = int(entry.get("last_digest_message_id") or 0)
    resolved_id = session_id or entry.get("session_id") or ""
    messages = digest._fetch_messages(resolved_id, after_id=bookmark) if resolved_id else []
    user_count, assistant_count = digest._role_counts(messages)

    return {
        "session_key": session_key,
        "session_id": resolved_id,
        "bookmark": bookmark,
        "undigested_user": user_count,
        "undigested_assistant": assistant_count,
        "in_flight": bool(entry.get("digest_in_flight")),
        "last_digest_at": entry.get("last_digest_at"),
        "last_failure_at": entry.get("last_digest_failure_at"),
        "last_log": _last_log_line(),
        "has_state": bool(entry),
    }


def get_bookmark(session_key: str) -> int:
    with digest._digest_lock:
        state = digest._load_state()
        entry = state.get("sessions", {}).get(session_key, {})
    return int(entry.get("last_digest_message_id") or 0)


def set_bookmark(session_key: str, value: int) -> dict[str, Any]:
    """Set the digest bookmark to an absolute message id."""
    return _update_bookmark(session_key, lambda _current: max(0, int(value)))


def reset_bookmark(session_key: str) -> dict[str, Any]:
    """Clear the bookmark so the whole session is eligible for digest again."""
    return _update_bookmark(session_key, lambda _current: 0)


def rewind_bookmark(session_key: str, count: int) -> dict[str, Any]:
    """Move the bookmark back by ``count`` message ids (floored at 0)."""
    step = max(0, int(count))
    return _update_bookmark(session_key, lambda current: max(0, current - step))


def request_resummarise(
    session_key: str,
    session_id: str,
    *,
    date_str: str | None = None,
    platform: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Compatibility alias — prefer :func:`request_weekly_reorganise`."""
    _ = (session_id, platform)
    return request_weekly_reorganise(
        date_str=date_str,
        session_key=session_key if session_key and session_key != "weekly-ui" else None,
        force=force if force else True,
    )


def request_weekly_reorganise(
    *,
    date_str: str | None = None,
    session_key: str | None = None,
    force: bool = True,
) -> dict[str, Any]:
    """Weekly UI Reorganise: oneshot Phase-2 on the daily file.

    No Phase-1 extract, no Hermes ``AIAgent``, no weekly generate.
    ``session_key`` / ``force`` are kept for bridge compatibility and ignored.
    """
    _ = (session_key, force)
    from memory_staging import daily_staging_path, hermes_local_today_str

    target_date = date_str or hermes_local_today_str()
    daily_path = daily_staging_path(digest._hermes_home(), target_date)
    if not daily_path.exists():
        return {"outcome": "missing", "path": str(daily_path), "date": target_date}
    return digest.run_manual_phase2(daily_path, date_str=target_date)


def list_weekly_span_candidates(week_key: str) -> dict[str, Any]:
    """Open + overdue span candidates for one ISO week (Weekly UI, chat-independent)."""
    return span_weekly.list_weekly_span_candidates(week_key)


def validate_weekly_spans(
    week_key: str, candidates: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Validator-scored candidates for one ISO week, filtered to explicit/high."""
    return span_weekly.validate_weekly_spans(week_key, candidates)


def resolve_weekly_span(
    week_key: str,
    block_id: str,
    action: str,
    *,
    proposed_valid_to: str | None = None,
    interval: str | None = None,
    due_date: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Apply confirm / put_off / set_due_date to a daily block's valid_to."""
    return span_weekly.resolve_weekly_span(
        week_key,
        block_id,
        action,
        proposed_valid_to=proposed_valid_to,
        interval=interval,
        due_date=due_date,
        idempotency_key=idempotency_key,
    )


def _update_bookmark(session_key: str, transform) -> dict[str, Any]:
    with digest._digest_lock:
        state = digest._load_state()
        entry = state.get("sessions", {}).get(session_key)
        if entry is None:
            return {"outcome": "no_state", "session_key": session_key}
        previous = int(entry.get("last_digest_message_id") or 0)
        new_value = transform(previous)
        entry["last_digest_message_id"] = new_value
        entry["bookmark_edited_at"] = datetime.now(timezone.utc).isoformat()
        state["sessions"][session_key] = entry
        digest._save_state(state)
    digest._log(
        f"bookmark edited session_key={session_key} {previous} -> {new_value}"
    )
    return {"outcome": "updated", "previous": previous, "bookmark": new_value}


def request_phase2_tick(
    now: datetime | None = None,
    *,
    sync: bool = True,
    tz: Any = None,
) -> dict[str, Any]:
    """Run the existing one-proposer merge if a gated civil tick is due."""
    return digest.maybe_run_digest_clock(now=now, sync=sync, tz=tz)


def request_nightly_digest(
    now: datetime | None = None,
    *,
    sync: bool = True,
    tz: Any = None,
) -> dict[str, Any]:
    """Same clock path as ticks; leftover Phase 1 is decided by local 23:55."""
    return digest.maybe_run_digest_clock(now=now, sync=sync, tz=tz)


def _last_log_line() -> str:
    try:
        text = digest._log_file().read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""
