"""Weekly/digest-bridge span ops: list and resolve daily-block valid_to.

These are non-chat entry points. They must never call
``build_recall_injection_context`` or ``on_pre_llm_call``.
They reuse on-disk primitives
(``_expiring_blocks``, ``patch_daily_block_valid_to``) so daily-block writes stay consistent.
"""

from __future__ import annotations

import calendar
import importlib.util
import sys
from datetime import date, timedelta
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


PUT_OFF_INTERVALS: dict[str, int] = {"1d": 1, "7d": 7, "2w": 14}
PUT_OFF_MONTH_INTERVAL = "1mo"
VALID_ACTIONS = frozenset({"confirm", "put_off", "set_due_date"})


def _valid_iso_date(value: str) -> bool:
    value = (value or "").strip()
    if not digest._DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _add_months(base: date, months: int) -> date:
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _week_date_bounds(week_key: str) -> tuple[date, date] | None:
    parsed = digest.parse_week_key(str(week_key or ""))
    if parsed is None:
        return None
    year, week = parsed
    try:
        start = date.fromisocalendar(year, week, 1)
        end = date.fromisocalendar(year, week, 7)
    except ValueError:
        return None
    return start, end


def _daily_files_for_week(hermes_home: Path, week_key: str) -> list[Path] | None:
    """Daily staging files whose date falls inside the ISO week. None if week_key invalid."""
    bounds = _week_date_bounds(week_key)
    if bounds is None:
        return None
    start, end = bounds
    daily_dir = digest.daily_staging_dir(hermes_home)
    if not daily_dir.exists():
        return []
    files: list[Path] = []
    for path in sorted(daily_dir.glob("*.md")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start <= day <= end:
            files.append(path)
    return files


def _lookup_block(files: list[Path], block_id: str) -> dict[str, Any] | None:
    """Find one block's current frontmatter regardless of expiring status."""
    import yaml

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _line_no, raw_frontmatter, body in digest._frontmatter_blocks(text):
            try:
                parsed = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(parsed, dict):
                continue
            if str(parsed.get("id", "")).strip() != block_id:
                continue
            return {
                "file": path.name,
                "id": block_id,
                "entity": str(parsed.get("entity", "")),
                "valid_from": str(parsed.get("valid_from", "")).strip(),
                "valid_to": str(parsed.get("valid_to", "")).strip(),
                "body": body,
            }
    return None


def list_weekly_span_candidates(week_key: str) -> dict[str, Any]:
    """Open + overdue span candidates scoped to one ISO week (unvalidated)."""
    hermes_home = digest._hermes_home()
    files = _daily_files_for_week(hermes_home, week_key)
    if files is None:
        return {"week_key": week_key, "outcome": "invalid_week", "candidates": []}
    candidates = digest._expiring_blocks(files, open_only=False)
    return {"week_key": week_key, "outcome": "listed", "candidates": candidates}


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
    """Apply one of the three UI resolution choices to a daily block's valid_to.

    - confirm: apply ``proposed_valid_to``.
    - put_off: add ``interval`` (1d|7d|2w|1mo) to the current/proposed due date
      (or today when the block is still ``open``).
    - set_due_date: apply a user-selected ISO ``due_date`` verbatim.

    Idempotent: repeating the exact same call (same block/action/params, or an
    explicit ``idempotency_key``) returns the cached result instead of
    re-applying the write (e.g. re-adding a put_off interval on retry).
    """
    block_id = str(block_id or "").strip()
    action = str(action or "").strip()
    if not block_id:
        return {"outcome": "error", "error": "missing block_id"}
    if action not in VALID_ACTIONS:
        return {"outcome": "error", "error": f"unknown action: {action!r}"}

    hermes_home = digest._hermes_home()
    files = _daily_files_for_week(hermes_home, week_key)
    if files is None:
        return {"outcome": "error", "error": f"invalid week_key: {week_key!r}"}

    key = idempotency_key or (
        f"{action}:{proposed_valid_to or ''}:{interval or ''}:{due_date or ''}"
    )
    state_key = f"{week_key}:{block_id}"

    with digest._digest_lock:
        state = digest._load_state()
        resolutions = state.get("weekly_span_resolutions")
        if not isinstance(resolutions, dict):
            resolutions = {}
            state["weekly_span_resolutions"] = resolutions

        prior = resolutions.get(state_key)
        if isinstance(prior, dict) and prior.get("idempotency_key") == key:
            cached = dict(prior.get("result") or {})
            cached["outcome"] = "duplicate"
            cached["idempotent"] = True
            return cached

        candidate = _lookup_block(files, block_id)
        if candidate is None:
            return {
                "outcome": "error",
                "error": f"block {block_id!r} not found in week {week_key}",
            }

        current_valid_to = str(candidate.get("valid_to") or "").strip()

        if action == "confirm":
            target = str(proposed_valid_to or "").strip()
            if not _valid_iso_date(target):
                return {
                    "outcome": "error",
                    "error": "confirm requires a valid proposed_valid_to (YYYY-MM-DD)",
                }
        elif action == "set_due_date":
            target = str(due_date or "").strip()
            if not _valid_iso_date(target):
                return {
                    "outcome": "error",
                    "error": "set_due_date requires a valid ISO due_date (YYYY-MM-DD)",
                }
        else:  # put_off
            interval_key = str(interval or "").strip()
            is_month = interval_key == PUT_OFF_MONTH_INTERVAL
            offset_days = PUT_OFF_INTERVALS.get(interval_key)
            if offset_days is None and not is_month:
                return {"outcome": "error", "error": f"unknown interval: {interval!r}"}

            if current_valid_to and _valid_iso_date(current_valid_to):
                base = date.fromisoformat(current_valid_to)
            elif proposed_valid_to and _valid_iso_date(str(proposed_valid_to)):
                base = date.fromisoformat(str(proposed_valid_to))
            else:
                base = digest.hermes_local_today()

            new_date = _add_months(base, 1) if is_month else base + timedelta(days=offset_days)
            target = new_date.isoformat()

        try:
            applied = digest.patch_daily_block_valid_to(hermes_home, block_id, valid_to=target)
        except ValueError as exc:
            return {"outcome": "error", "error": str(exc)}

        result: dict[str, Any] = {
            "outcome": "applied" if applied else "not_found",
            "week_key": week_key,
            "block_id": block_id,
            "action": action,
            "previous_valid_to": current_valid_to,
            "valid_to": target,
            "applied": bool(applied),
        }
        resolutions[state_key] = {"idempotency_key": key, "result": dict(result)}
        digest._save_state(state)
        return result
