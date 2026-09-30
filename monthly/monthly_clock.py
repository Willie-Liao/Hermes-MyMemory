"""Civil-tick monthly refresh: current draft on source change, closed month on rollover."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_monthly = Path(__file__).resolve().parent
if str(_monthly) not in sys.path:
    sys.path.insert(0, str(_monthly))

from monthly_slice import canonical_source_fingerprint, previous_month_key  # noqa: E402
from monthly_state import load_state, month_file_path, save_state  # noqa: E402


def _month_after(month_key: str) -> str:
    year_s, _, month_s = month_key.partition("-")
    year, month = int(year_s), int(month_s)
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def _generate(month_key: str, reason: str) -> None:
    from monthly_actions import generate_month

    generate_month(month_key, reason=reason)


def maybe_run(local: datetime) -> dict[str, Any]:
    """Refresh the current month when canonical sources change; backfill/rollover the previous month.

    A recall stamp must not flip the fingerprint, so strength/recall_n are excluded from the hash.
    """
    payload: dict[str, Any] = {"outcome": "idle", "month": None, "error": None, "months": []}
    current = local.strftime("%Y-%m")
    previous = previous_month_key(local.date())
    state = load_state()
    last_clock = state.get("last_clock_month")
    hashes = dict(state.get("source_hash") or {})
    ran: list[str] = []
    first_install = last_clock is None
    entered_new = last_clock is not None and last_clock != current
    last_error = str(state.get("last_error") or "")
    retry_closed = bool(last_error) and _month_after(previous) == current
    try:
        if entered_new or retry_closed or (first_install and not month_file_path(previous).is_file()):
            _generate(previous, "clock-rollover" if entered_new or retry_closed else "clock-backfill")
            ran.append(previous)
            prev_fp = canonical_source_fingerprint(previous)
            if prev_fp:
                hashes[previous] = prev_fp
            state["last_monthly_generate_month"] = previous

        cur_fp = canonical_source_fingerprint(current)
        if cur_fp and hashes.get(current) != cur_fp:
            _generate(current, "clock-current")
            ran.append(current)
            hashes[current] = cur_fp

        state["source_hash"] = hashes
        state["last_clock_month"] = current
        state["last_generated_at"] = local.isoformat(timespec="seconds")
        state.pop("last_error", None)
        save_state(state)
        if ran:
            payload["outcome"] = "generated"
            payload["month"] = ran[-1]
            payload["months"] = ran
    except Exception as exc:
        state = load_state()
        state["last_error"] = str(exc)
        save_state(state)
        payload["outcome"] = "error"
        payload["error"] = str(exc)
        payload["month"] = previous if entered_new or first_install else current
    return payload
