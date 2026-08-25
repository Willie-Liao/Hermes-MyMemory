"""Public digest actions shared by hooks and the ``/digest`` slash command.

Thin orchestration over :mod:`digest` internals so the slash handler never
duplicates worker, state, or validation logic. The on_agent_end / boundary
hooks keep calling ``digest._maybe_run_digest`` directly; this module exposes
the same core with typed, synchronous-by-default wrappers for manual use.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
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
    wait: bool = True,
    status_only: bool = False,
) -> dict[str, Any]:
    """Weekly UI Reorganise: oneshot Phase-2 on the daily file.

    No Phase-1 extract, no Hermes ``AIAgent``, no weekly generate.
    ``session_key`` / ``force`` are kept for bridge compatibility and ignored.
    UI passes wait=False so the HTTP request returns while Phase-2 runs;
    status_only reads the in-flight flag without starting another job.
    """
    _ = (session_key, force)
    from memory_staging import daily_staging_path, hermes_local_today_str

    target_date = date_str or hermes_local_today_str()
    daily_path = daily_staging_path(digest._hermes_home(), target_date)
    job_key = "weekly_reorganise_job"
    if isinstance(wait, str):
        wait = wait.strip().lower() not in {"0", "false", "no"}
    else:
        wait = bool(wait)
    if isinstance(status_only, str):
        status_only = status_only.strip().lower() in {"1", "true", "yes"}
    else:
        status_only = bool(status_only)

    with digest._digest_lock:
        state = digest._load_state()
        job = state.get(job_key) if isinstance(state.get(job_key), dict) else {}
        in_flight = bool(job.get("in_flight"))
        last_outcome = str(job.get("last_outcome") or "").strip()
        job_date = str(job.get("date") or "").strip()
        if status_only:
            if in_flight:
                return {
                    "outcome": "in_flight",
                    "path": str(daily_path),
                    "date": job_date or target_date,
                }
            return {
                "outcome": last_outcome or "idle",
                "path": str(daily_path),
                "date": job_date or target_date,
            }

    if not daily_path.exists():
        return {"outcome": "missing", "path": str(daily_path), "date": target_date}

    if wait:
        return digest.run_manual_phase2(daily_path, date_str=target_date)

    with digest._digest_lock:
        state = digest._load_state()
        job = state.get(job_key) if isinstance(state.get(job_key), dict) else {}
        if bool(job.get("in_flight")):
            return {
                "outcome": "in_flight",
                "path": str(daily_path),
                "date": str(job.get("date") or target_date),
            }
        state[job_key] = {
            "in_flight": True,
            "date": target_date,
            "last_outcome": "",
        }
        digest._save_state(state)

    def _target() -> None:
        outcome = "failed"
        try:
            result = digest.run_manual_phase2(daily_path, date_str=target_date)
            outcome = str(result.get("outcome") or "failed")
        except Exception:  # noqa: BLE001
            outcome = "failed"
        finally:
            with digest._digest_lock:
                state = digest._load_state()
                state[job_key] = {
                    "in_flight": False,
                    "date": target_date,
                    "last_outcome": outcome,
                }
                digest._save_state(state)

    threading.Thread(
        target=_target,
        name=f"weekly-reorganise-{target_date}",
        daemon=True,
    ).start()
    return {
        "outcome": "in_flight",
        "path": str(daily_path),
        "date": target_date,
    }


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


HISTORY_PRESETS = ("1d", "7d", "30d", "all")
_HISTORY_WINDOWS = {
    "1d": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
_CALIBRATION_PATH = Path(__file__).with_name("history_calibration.json")
_history_lock = threading.Lock()
_history_thread: threading.Thread | None = None
_history_stop_requested = False


def _history_now(now: datetime | None = None) -> datetime:
    if now is not None:
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now
    try:
        from memory_staging import hermes_local_now

        return hermes_local_now()
    except Exception:
        return datetime.now(timezone.utc)


def _as_unix(value: Any) -> float | None:
    """Parse SQLite message timestamps that may be unix seconds or ISO text."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _local_day(unix_ts: float, now: datetime) -> str:
    zone = now.tzinfo or timezone.utc
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).astimezone(zone).date().isoformat()


def _preset_cutoff(preset: str, now: datetime) -> float | None:
    delta = _HISTORY_WINDOWS[preset]
    if delta is None:
        return None
    return (now - delta).timestamp()


def _load_calibration() -> dict[str, Any]:
    try:
        data = json.loads(_CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data


def _session_bookmarks() -> dict[str, int]:
    with digest._digest_lock:
        state = digest._load_state()
    out: dict[str, int] = {}
    for key, entry in (state.get("sessions") or {}).items():
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("session_id") or key)
        out[sid] = int(entry.get("last_digest_message_id") or 0)
    return out


def _open_messages_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _scan_eligible_rows(
    db_path: Path,
    *,
    cutoff: float | None,
    bookmarks: dict[str, int],
) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not db_path.is_file():
        return [], None
    try:
        conn = _open_messages_ro(db_path)
    except sqlite3.Error as exc:
        return None, f"sqlite_open: {exc}"
    try:
        rows = conn.execute(
            """
            SELECT id, session_id, role, content, timestamp, active
            FROM messages
            WHERE active = 1 AND role IN ('user', 'assistant')
            ORDER BY id ASC
            """
        ).fetchall()
    except sqlite3.Error as ext:
        conn.close()
        return None, f"sqlite_schema: {ext}"
    conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        unix = _as_unix(row["timestamp"])
        if unix is None:
            continue
        if cutoff is not None and unix < cutoff:
            continue
        sid = str(row["session_id"] or "")
        mid = int(row["id"])
        if mid <= int(bookmarks.get(sid) or 0):
            continue
        content = (row["content"] or "").strip()
        if not content:
            continue
        out.append(
            {
                "id": mid,
                "session_id": sid,
                "role": str(row["role"]),
                "content": content[:2000],
                "timestamp": unix,
            }
        )
    return out, None


def _batch_messages(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Split eligible rows by session and civil day, then 12-user / 40-message caps."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        day = _local_day(float(row["timestamp"]), now)
        grouped.setdefault((str(row["session_id"]), day), []).append(row)
    batches: list[dict[str, Any]] = []
    max_users = digest.BATCH_USER_MESSAGES
    max_msgs = 40
    for (sid, day), items in sorted(grouped.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[1][0]["id"])):
        current: list[dict[str, Any]] = []
        users = 0
        for msg in items:
            next_users = users + (1 if msg["role"] == "user" else 0)
            if current and (next_users > max_users or len(current) >= max_msgs):
                batches.append(_batch_record(sid, day, current))
                current = []
                users = 0
                next_users = 1 if msg["role"] == "user" else 0
            current.append(msg)
            users = next_users
        if current:
            batches.append(_batch_record(sid, day, current))
    return batches


def _batch_record(session_id: str, day: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [int(m["id"]) for m in messages]
    chars = sum(len(str(m.get("content") or "")) for m in messages)
    users = sum(1 for m in messages if m.get("role") == "user")
    assistants = sum(1 for m in messages if m.get("role") == "assistant")
    return {
        "batch_id": f"{session_id}:{day}:{ids[0]}-{ids[-1]}",
        "session_id": session_id,
        "day": day,
        "start_id": ids[0],
        "end_id": ids[-1],
        "message_ids": ids,
        "messages": messages,
        "chars": chars,
        "user": users,
        "assistant": assistants,
    }


def _plan_hash(preset: str, cutoff: float | None, batches: list[dict[str, Any]]) -> str:
    payload = {
        "preset": preset,
        "cutoff": cutoff,
        "batches": [
            {
                "session_id": b["session_id"],
                "day": b["day"],
                "start_id": b["start_id"],
                "end_id": b["end_id"],
            }
            for b in batches
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _apply_estimates(plan: dict[str, Any]) -> dict[str, Any]:
    cal = _load_calibration()
    p1 = cal.get("phase1") if isinstance(cal.get("phase1"), dict) else {}
    p2 = cal.get("phase2") if isinstance(cal.get("phase2"), dict) else {}
    n_batches = int(plan.get("batch_count") or 0)
    n_days = int(plan.get("day_count") or 0)
    chars = int(plan.get("transcript_chars") or 0)
    ref = float(p1.get("ref_transcript_chars") or 6036) or 6036.0
    per_batch_chars = chars / max(1, n_batches)
    char_scale = max(0.75, min(1.5, per_batch_chars / ref)) if n_batches else 0.0

    def band(table: dict[str, Any], prefix: str, count: int, scale: float) -> dict[str, int]:
        low = int(table.get(f"{prefix}_low") or 0)
        typical = int(table.get(f"{prefix}_typical") or 0)
        high = int(table.get(f"{prefix}_high") or 0)
        return {
            "low": int(round(count * low * scale)),
            "typical": int(round(count * typical * scale)),
            "high": int(round(count * high * scale)),
        }

    digest_tokens = band(p1, "tokens_per_batch", n_batches, char_scale if n_batches else 0.0)
    digest_ms = band(p1, "elapsed_ms_per_batch", n_batches, 1.0)
    consol_tokens = band(p2, "tokens_per_day", n_days, 1.0)
    consol_ms = band(p2, "elapsed_ms_per_day", n_days, 1.0)
    plan["digest_tokens"] = digest_tokens
    plan["digest_elapsed_ms"] = digest_ms
    plan["consolidate_tokens"] = consol_tokens
    plan["consolidate_elapsed_ms"] = consol_ms
    plan["total_tokens"] = {
        k: digest_tokens[k] + consol_tokens[k] for k in ("low", "typical", "high")
    }
    plan["total_elapsed_ms"] = {
        k: digest_ms[k] + consol_ms[k] for k in ("low", "typical", "high")
    }
    plan["calibration"] = {
        "profile_version": cal.get("profile_version"),
        "regime": cal.get("regime"),
        "time_confidence": cal.get("time_confidence") or "low",
        "disclaimer": cal.get("disclaimer") or "",
        "n_eligible_phase1": (p1.get("n_eligible") or 0),
        "n_excluded_phase1": (p1.get("n_excluded") or 0),
    }
    return plan


def plan_history(
    preset: str,
    *,
    now: datetime | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Read-only eligible-message plan for one rolling history preset.

    Never writes digest state, staging, or the usage ledger. Automatic
    `_maybe_run_digest` / `_fetch_messages` paths stay unused so live
    chat extract cannot pick up a date-range scan.
    """
    key = str(preset or "").strip().lower()
    if key not in _HISTORY_WINDOWS:
        return {"outcome": "invalid_preset", "preset": preset}
    clock = _history_now(now)
    cutoff = _preset_cutoff(key, clock)
    db_path = (home or digest._hermes_home()) / "state.db"
    rows, err = _scan_eligible_rows(
        db_path, cutoff=cutoff, bookmarks=_session_bookmarks()
    )
    if err:
        return {
            "outcome": "error",
            "preset": key,
            "error": err,
            "cutoff_ts": cutoff,
            "cutoff_iso": datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
            if cutoff is not None
            else None,
        }
    batches = _batch_messages(rows or [], clock)
    sessions = sorted({b["session_id"] for b in batches})
    days = sorted({b["day"] for b in batches})
    public_batches = [
        {k: v for k, v in b.items() if k != "messages"} for b in batches
    ]
    plan = {
        "outcome": "ok",
        "preset": key,
        "cutoff_ts": cutoff,
        "cutoff_iso": datetime.fromtimestamp(cutoff, tz=clock.tzinfo or timezone.utc)
        .astimezone(clock.tzinfo or timezone.utc)
        .isoformat()
        if cutoff is not None
        else None,
        "message_count": sum(len(b["message_ids"]) for b in batches),
        "session_count": len(sessions),
        "batch_count": len(batches),
        "day_count": len(days),
        "days": days,
        "sessions": sessions,
        "transcript_chars": sum(int(b["chars"]) for b in batches),
        "batches": public_batches,
        "plan_hash": _plan_hash(key, cutoff, batches),
    }
    _apply_estimates(plan)
    return plan


def estimate_history(
    preset: str | None = None,
    *,
    now: datetime | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Compare all four presets, or one, without mutating Hermes state."""
    keys = HISTORY_PRESETS if preset in (None, "", "all_presets") else (str(preset),)
    if preset and str(preset) not in HISTORY_PRESETS and str(preset) != "all_presets":
        return {"outcome": "invalid_preset", "preset": preset}
    plans = [plan_history(key, now=now, home=home) for key in keys]
    return {"outcome": "ok", "plans": plans}


def _format_token_band(band: dict[str, Any]) -> str:
    return f"{band.get('low', 0):,}–{band.get('typical', 0):,}–{band.get('high', 0):,}"


def _format_time_band(band: dict[str, Any]) -> str:
    def mins(ms: Any) -> str:
        return f"{max(0, int(ms or 0)) / 60000:.1f}m"

    return f"{mins(band.get('low'))}–{mins(band.get('typical'))}–{mins(band.get('high'))}"


def format_history_matrix(payload: dict[str, Any]) -> str:
    """Plain-text four-option estimate table for slash and CLI."""
    lines = [
        "MyMemory history digest (estimate only — no LLM until --yes)",
        "Preset  msgs  sessions  batches  days  digest tokens (lo–typ–hi)  consolidate tokens  time (low conf.)",
    ]
    for plan in payload.get("plans") or []:
        if plan.get("outcome") != "ok":
            lines.append(f"{plan.get('preset')}: {plan.get('outcome')} {plan.get('error') or ''}".rstrip())
            continue
        cutoff = plan.get("cutoff_iso") or "beginning of store"
        lines.append(
            f"{plan['preset']:<6} {plan['message_count']:>4}  {plan['session_count']:>8}  "
            f"{plan['batch_count']:>7}  {plan['day_count']:>4}  "
            f"{_format_token_band(plan['digest_tokens']):>22}  "
            f"{_format_token_band(plan['consolidate_tokens']):>18}  "
            f"{_format_time_band(plan['total_elapsed_ms'])}"
        )
        lines.append(f"       cutoff {cutoff}")
    cal = ((payload.get("plans") or [{}])[0]).get("calibration") or {}
    if cal.get("disclaimer"):
        lines.append(cal["disclaimer"])
    lines.append("Confirm one range: hermes MyMemory digest history <1d|7d|30d|all> --yes")
    return "\n".join(lines)


def format_history_plan(plan: dict[str, Any]) -> str:
    if plan.get("outcome") == "invalid_preset":
        return f"Unknown history preset {plan.get('preset')!r}. Use 1d, 7d, 30d, or all."
    if plan.get("outcome") != "ok":
        return f"History plan failed: {plan.get('error') or plan.get('outcome')}"
    lines = [
        f"History {plan['preset']} cutoff {plan.get('cutoff_iso') or 'all stored messages'}",
        f"  messages {plan['message_count']} across {plan['session_count']} sessions, "
        f"{plan['batch_count']} digest batches, {plan['day_count']} days to consolidate",
        f"  digest (Phase-1) tokens lo/typ/hi: {_format_token_band(plan['digest_tokens'])}",
        f"  consolidate (Phase-2) tokens lo/typ/hi: {_format_token_band(plan['consolidate_tokens'])}",
        f"  elapsed lo/typ/hi: {_format_time_band(plan['total_elapsed_ms'])} "
        f"(time confidence {(plan.get('calibration') or {}).get('time_confidence', 'low')})",
        f"Confirm: /digest history {plan['preset']} --yes",
        "Backup memories/staging first. Already-bookmarked messages are skipped.",
    ]
    disc = (plan.get("calibration") or {}).get("disclaimer")
    if disc:
        lines.append(disc)
    return "\n".join(lines)


def _history_state(state: dict[str, Any] | None = None) -> dict[str, Any]:
    bag = state if state is not None else digest._load_state()
    raw = bag.get("history_backfill")
    return dict(raw) if isinstance(raw, dict) else {}


def _save_history(entry: dict[str, Any]) -> None:
    with digest._digest_lock:
        state = digest._load_state()
        state["history_backfill"] = entry
        digest._save_state(state)


def get_history_status() -> dict[str, Any]:
    """Progress for the last history backfill, marking orphan running jobs interrupted."""
    global _history_thread
    with digest._digest_lock:
        state = digest._load_state()
        entry = _history_state(state)
        if entry.get("status") == "running":
            alive = _history_thread is not None and _history_thread.is_alive()
            if not alive:
                entry["status"] = "interrupted"
                state["history_backfill"] = entry
                digest._save_state(state)
        return dict(entry)


def stop_history(*, yes: bool = False) -> dict[str, Any]:
    """Ask a running history worker to halt between LLM calls."""
    if not yes:
        return {"outcome": "needs_confirm", "hint": "history stop --yes"}
    global _history_stop_requested
    _history_stop_requested = True
    entry = get_history_status()
    if entry.get("status") == "running":
        entry["stop_requested"] = True
        _save_history(entry)
        return {"outcome": "stopping", **entry}
    return {"outcome": "idle", **entry}


def _hydrate_batch_messages(
    db_path: Path, batches: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Reload message bodies for stored batch ids without changing the plan hash."""
    wanted: set[int] = set()
    for batch in batches:
        wanted.update(int(i) for i in batch.get("message_ids") or [])
    if not wanted:
        return batches, None
    try:
        conn = _open_messages_ro(db_path)
    except sqlite3.Error as exc:
        return None, f"sqlite_open: {exc}"
    try:
        qmarks = ",".join("?" * len(wanted))
        rows = conn.execute(
            f"SELECT id, session_id, role, content, timestamp FROM messages "
            f"WHERE id IN ({qmarks})",
            tuple(wanted),
        ).fetchall()
    except sqlite3.Error as exc:
        conn.close()
        return None, f"sqlite_schema: {exc}"
    conn.close()
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        by_id[int(row["id"])] = {
            "id": int(row["id"]),
            "session_id": str(row["session_id"] or ""),
            "role": str(row["role"] or ""),
            "content": (row["content"] or "").strip()[:2000],
            "timestamp": _as_unix(row["timestamp"]) or 0.0,
        }
    hydrated: list[dict[str, Any]] = []
    for batch in batches:
        messages = [by_id[i] for i in batch["message_ids"] if i in by_id]
        item = dict(batch)
        item["messages"] = messages
        hydrated.append(item)
    return hydrated, None


def _execute_history_plan(plan: dict[str, Any], *, resume: bool = False) -> dict[str, Any]:
    """Serial Phase-1 batches then one Phase-2 per civil day; checkpoint after each unit."""
    global _history_stop_requested
    home = digest._hermes_home()
    stored = get_history_status() if resume else {}
    if resume:
        if not stored.get("batches"):
            return {"outcome": "no_state"}
        if (
            stored.get("plan_hash")
            and plan.get("plan_hash")
            and stored.get("plan_hash") != plan.get("plan_hash")
        ):
            return {
                "outcome": "plan_mismatch",
                "status": stored.get("status"),
                "stored_hash": stored.get("plan_hash"),
                "live_hash": plan.get("plan_hash"),
            }
        compact = list(stored.get("batches") or [])
        live_hash = str(stored.get("plan_hash") or plan.get("plan_hash"))
    else:
        compact = list(plan.get("batches") or [])
        live_hash = str(plan.get("plan_hash") or "")
    batches, err = _hydrate_batch_messages(home / "state.db", compact)
    if err:
        out = {**plan, "outcome": "error", "error": err, "status": "failed"}
        _save_history(out)
        return out
    done_batches = set(stored.get("completed_batches") or []) if resume else set()
    done_days = set(stored.get("completed_days") or []) if resume else set()
    entry: dict[str, Any] = {
        "status": "running",
        "preset": plan.get("preset") or stored.get("preset"),
        "plan_hash": live_hash,
        "cutoff_ts": plan.get("cutoff_ts", stored.get("cutoff_ts")),
        "cutoff_iso": plan.get("cutoff_iso", stored.get("cutoff_iso")),
        "batches": compact,
        "estimates": {
            "digest_tokens": plan.get("digest_tokens")
            or (stored.get("estimates") or {}).get("digest_tokens"),
            "consolidate_tokens": plan.get("consolidate_tokens")
            or (stored.get("estimates") or {}).get("consolidate_tokens"),
            "total_elapsed_ms": plan.get("total_elapsed_ms")
            or (stored.get("estimates") or {}).get("total_elapsed_ms"),
        },
        "completed_batches": sorted(done_batches),
        "completed_days": sorted(done_days),
        "current_batch": None,
        "outcomes": list(stored.get("outcomes") or []) if resume else [],
        "errors": list(stored.get("errors") or []) if resume else [],
        "stop_requested": False,
    }
    _save_history(entry)
    from memory_staging import daily_staging_path

    days_needed: dict[str, list[str]] = {}
    for batch in batches:
        days_needed.setdefault(batch["day"], []).append(batch["batch_id"])

    for batch in batches:
        if _history_stop_requested:
            entry["status"] = "stopped"
            entry["stop_requested"] = True
            _save_history(entry)
            return {**entry, "outcome": "stopped"}
        bid = batch["batch_id"]
        if bid in done_batches:
            continue
        entry["current_batch"] = bid
        _save_history(entry)
        transcript = digest._format_transcript(batch["messages"])
        daily_path = daily_staging_path(home, batch["day"])
        try:
            outcome = digest._run_digest_pipeline_entry(
                batch["session_id"],
                batch["session_id"],
                "history",
                daily_path,
                transcript,
                batch["end_id"],
                reason="history_backfill",
                user_count=batch["user"],
                assistant_count=batch["assistant"],
                batch_start_id=batch["start_id"],
            )
        except Exception as exc:
            entry["status"] = "failed"
            entry["errors"].append(f"{bid}: {exc}")
            _save_history(entry)
            return {**entry, "outcome": "failed"}
        entry["outcomes"].append({"batch_id": bid, "outcome": outcome})
        if outcome == "failed":
            entry["status"] = "failed"
            entry["errors"].append(f"{bid}: pipeline failed")
            _save_history(entry)
            return {**entry, "outcome": "failed"}
        done_batches.add(bid)
        entry["completed_batches"] = sorted(done_batches)
        _save_history(entry)

    for day, ids in days_needed.items():
        if _history_stop_requested:
            entry["status"] = "stopped"
            _save_history(entry)
            return {**entry, "outcome": "stopped"}
        if day in done_days:
            continue
        if not set(ids).issubset(done_batches):
            continue
        daily_path = daily_staging_path(home, day)
        try:
            digest.run_manual_phase2(daily_path, date_str=day)
        except Exception as exc:
            entry["status"] = "failed"
            entry["errors"].append(f"phase2 {day}: {exc}")
            _save_history(entry)
            return {**entry, "outcome": "failed"}
        done_days.add(day)
        entry["completed_days"] = sorted(done_days)
        _save_history(entry)

    entry["status"] = "completed"
    entry["current_batch"] = None
    _save_history(entry)
    return {**entry, "outcome": "completed"}


def request_history_run(
    preset: str,
    *,
    yes: bool = False,
    sync: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Estimate, or run, a history backfill. Unconfirmed calls never start workers."""
    global _history_thread, _history_stop_requested
    plan = plan_history(preset, now=now)
    if plan.get("outcome") != "ok":
        return plan
    if not yes:
        return {**plan, "outcome": "needs_confirm"}
    current = get_history_status()
    if current.get("status") == "running":
        return {"outcome": "in_flight", **current}
    _history_stop_requested = False
    if sync:
        return _execute_history_plan(plan)
    with _history_lock:
        if _history_thread is not None and _history_thread.is_alive():
            return {"outcome": "in_flight", **get_history_status()}
        _history_thread = threading.Thread(
            target=_execute_history_plan,
            args=(plan,),
            name="mymemory-history-backfill",
            daemon=True,
        )
        _history_thread.start()
    return {"outcome": "started", "preset": plan["preset"], "plan_hash": plan["plan_hash"]}


def resume_history(*, yes: bool = False, sync: bool = True) -> dict[str, Any]:
    """Continue the stored plan from the first unfinished batch or day."""
    if not yes:
        return {"outcome": "needs_confirm", "hint": "history resume --yes"}
    stored = get_history_status()
    if not stored:
        return {"outcome": "no_state"}
    if stored.get("status") == "completed":
        return {"outcome": "completed", **stored}
    if stored.get("status") == "running":
        return {"outcome": "in_flight", **stored}
    preset = str(stored.get("preset") or "")
    plan = {
        "outcome": "ok",
        "preset": preset,
        "plan_hash": stored.get("plan_hash"),
        "cutoff_ts": stored.get("cutoff_ts"),
        "cutoff_iso": stored.get("cutoff_iso"),
        "batches": list(stored.get("batches") or []),
        "digest_tokens": (stored.get("estimates") or {}).get("digest_tokens"),
        "consolidate_tokens": (stored.get("estimates") or {}).get("consolidate_tokens"),
        "total_elapsed_ms": (stored.get("estimates") or {}).get("total_elapsed_ms"),
    }
    global _history_thread, _history_stop_requested
    _history_stop_requested = False
    if sync:
        return _execute_history_plan(plan, resume=True)
    with _history_lock:
        if _history_thread is not None and _history_thread.is_alive():
            return {"outcome": "in_flight", **get_history_status()}
        _history_thread = threading.Thread(
            target=_execute_history_plan,
            kwargs={"plan": plan, "resume": True},
            name="mymemory-history-resume",
            daemon=True,
        )
        _history_thread.start()
    return {"outcome": "started", "preset": preset, "plan_hash": plan.get("plan_hash")}
