"""Month-rollover trigger on the digest civil clock, deduped by .monthly-state.json."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_monthly = Path(__file__).resolve().parent
if str(_monthly) not in sys.path:
    sys.path.insert(0, str(_monthly))

from monthly_slice import previous_month_key  # noqa: E402
from monthly_state import load_state, save_state  # noqa: E402


def _month_after(month_key: str) -> str:
    year_s, _, month_s = month_key.partition("-")
    year, month = int(year_s), int(month_s)
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def maybe_run(local: datetime) -> dict[str, Any]:
    """Generate last month once on the first tick of a new month; never the in-progress month."""
    payload: dict[str, Any] = {"outcome": "idle", "month": None, "error": None}
    current = local.strftime("%Y-%m")
    target = previous_month_key(local.date())
    state = load_state()
    last_gen = state.get("last_monthly_generate_month")
    last_clock = state.get("last_clock_month")
    last_error = str(state.get("last_error") or "")
    if last_gen == target:
        if last_clock != current:
            state["last_clock_month"] = current
            save_state(state)
        return payload
    entered_new = last_clock is not None and last_clock != current
    first_day_install = last_clock is None and local.day == 1
    retry = bool(last_error) and _month_after(target) == current
    should = _month_after(target) == current and (
        entered_new or first_day_install or retry
    )
    if not should:
        if last_clock is None:
            state["last_clock_month"] = current
            save_state(state)
        return payload
    try:
        from monthly_actions import generate_month

        generate_month(target, reason="clock")
        state = load_state()
        state["last_monthly_generate_month"] = target
        state["last_generated_at"] = local.isoformat(timespec="seconds")
        state["last_clock_month"] = current
        state.pop("last_error", None)
        save_state(state)
        payload["outcome"] = "generated"
        payload["month"] = target
    except Exception as exc:
        state = load_state()
        state["last_error"] = str(exc)
        save_state(state)
        payload["outcome"] = "error"
        payload["error"] = str(exc)
        payload["month"] = target
    return payload
