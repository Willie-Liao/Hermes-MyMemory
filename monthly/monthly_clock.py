"""Civil-tick monthly writes: checkpoints on the 10th and 20th, close on the 1st."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_monthly = Path(__file__).resolve().parent
if str(_monthly) not in sys.path:
    sys.path.insert(0, str(_monthly))

from monthly_slice import previous_month_key  # noqa: E402
from monthly_state import load_state, save_state  # noqa: E402

_WRITE_DAYS = (1, 10, 20)


def _slot(local: datetime) -> datetime:
    return local.replace(hour=0, minute=5, second=0, microsecond=0)


def next_write_at(local: datetime) -> datetime:
    """Next 00:05 on the 1st, 10th, or 20th, strictly after ``local``.

    The digest sleep loop needs this instant before ``maybe_run``. Putting 00:05
    on the phase-2 grid would mark the wake as past_tick and run phase 2.
    """
    cursor = local
    for _ in range(62):
        slot = _slot(cursor)
        if cursor.day in _WRITE_DAYS and slot > local:
            return slot
        cursor = (cursor + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    raise RuntimeError("no monthly write slot within 62 days")


def _generate(month_key: str, reason: str) -> None:
    from monthly_actions import generate_month

    generate_month(month_key, reason=reason)


def _due(local: datetime) -> bool:
    return local >= _slot(local)


def maybe_run(local: datetime) -> dict[str, Any]:
    """Write the current month on the 10th and 20th, and close the previous month on the 1st.

    A source-hash refresh rewrote the file on every daily edit. Checkpoints stay
    put until the next slot, and a mid-month first boot does not backfill.
    """
    payload: dict[str, Any] = {"outcome": "idle", "month": None, "error": None, "months": []}
    current = local.strftime("%Y-%m")
    previous = previous_month_key(local.date())
    state = load_state()
    ran: list[str] = []
    closing = False
    try:
        if _due(local):
            closed_for = state.get("closed_for")
            last_clock = state.get("last_clock_month")
            closing = closed_for not in (None, current) or (
                closed_for is None and (last_clock not in (None, current) or local.day == 1)
            )
            if closing:
                _generate(previous, "clock-close")
                ran.append(previous)
                state["closed_for"] = current
                state["last_monthly_generate_month"] = previous
                save_state(state)
            elif closed_for is None:
                state["closed_for"] = current
            if local.day in (10, 20):
                checkpoint = local.date().isoformat()
                if state.get("last_checkpoint") != checkpoint:
                    _generate(current, "clock-checkpoint")
                    ran.append(current)
                    state["last_checkpoint"] = checkpoint
                    state["last_monthly_generate_month"] = current
                    save_state(state)
            state["last_clock_month"] = current
        state["last_generated_at"] = local.isoformat(timespec="seconds")
        state.pop("last_error", None)
        state.pop("source_hash", None)
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
        payload["month"] = previous if closing else current
    return payload
