"""Civil-tick monthly writes: checkpoints on configured days, close on the 1st."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_monthly = Path(__file__).resolve().parent
if str(_monthly) not in sys.path:
    sys.path.insert(0, str(_monthly))

from monthly_slice import previous_month_key  # noqa: E402
from monthly_state import load_state, monthly_config, save_state  # noqa: E402


def _positive_int(bag: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(bag.get(key))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _clock_parts() -> tuple[int, int, tuple[int, ...], int]:
    """Hour, minute, checkpoint days, and retry days from config, else 00:05 / 10 / 20 / 3.

    A bad config value must not move the clock onto the phase-2 grid by accident,
    so anything unreadable falls back to those defaults.
    """
    bag = monthly_config()
    hour, minute = 0, 5
    raw = str(bag.get("write_at") or "00:05")
    try:
        hour_s, minute_s = raw.split(":", 1)
        parsed_hour, parsed_minute = int(hour_s), int(minute_s)
        if 0 <= parsed_hour <= 23 and 0 <= parsed_minute <= 59:
            hour, minute = parsed_hour, parsed_minute
    except (TypeError, ValueError):
        hour, minute = 0, 5
    days_raw = bag.get("checkpoint_days")
    days: list[int] = []
    if isinstance(days_raw, list):
        for item in days_raw:
            try:
                day = int(item)
            except (TypeError, ValueError):
                continue
            if 2 <= day <= 31:
                days.append(day)
    if not days:
        days = [10, 20]
    extra = _positive_int(bag, "retry_days", 3)
    return hour, minute, tuple(days), extra


def _slot(local: datetime) -> datetime:
    hour, minute, _days, _extra = _clock_parts()
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _wake_days() -> set[int]:
    _hour, _minute, days, extra = _clock_parts()
    wake: set[int] = set(range(1, extra + 1))
    for day in days:
        for offset in range(0, extra + 1):
            slot_day = day + offset
            if 1 <= slot_day <= 31:
                wake.add(slot_day)
    return wake


def next_write_at(local: datetime) -> datetime:
    """Next configured write time on a slot or retry day, strictly after ``local``.

    The digest sleep loop needs this instant before ``maybe_run``. Putting the
    slot on the phase-2 grid would mark the wake as past_tick and run phase 2.
    """
    wake = _wake_days()
    cursor = local
    for _ in range(62):
        slot = _slot(cursor)
        if cursor.day in wake and slot > local:
            return slot
        cursor = (cursor + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    raise RuntimeError("no monthly write slot within 62 days")


def _generate(month_key: str, reason: str, gaps_only: bool = False) -> dict[str, Any]:
    from monthly_actions import generate_month

    return generate_month(month_key, reason=reason, gaps_only=gaps_only)


def _due(local: datetime) -> bool:
    return local >= _slot(local)


def _anchor_for(day: int, days: tuple[int, ...], extra: int) -> int | None:
    for anchor in days:
        if anchor <= day <= anchor + extra:
            return anchor
    return None


def maybe_run(local: datetime) -> dict[str, Any]:
    """Write checkpoints on configured days and close the previous month on the 1st.

    A source-hash refresh rewrote the file on every daily edit. A failed slot
    stays open through the retry days and only patches stored gaps. A mid-month
    first boot does not backfill a window that already ended.
    """
    payload: dict[str, Any] = {"outcome": "idle", "month": None, "error": None, "months": []}
    current = local.strftime("%Y-%m")
    previous = previous_month_key(local.date())
    state = load_state()
    ran: list[str] = []
    closing = False
    preserve_error = False
    try:
        if _due(local):
            from monthly_actions import reduce_resume_path

            _hour, _minute, days, extra = _clock_parts()
            today = local.date().isoformat()
            close_last = extra
            if 1 <= local.day <= close_last and state.get("closed_for") != current:
                closing = True
                if state.get("close_tried_on") != today:
                    gaps_only = local.day != 1 and reduce_resume_path(previous).is_file()
                    result = _generate(previous, "clock-close", gaps_only)
                    state["close_tried_on"] = today
                    ran.append(previous)
                    if result.get("outcome") == "ok":
                        state["closed_for"] = current
                        state["last_monthly_generate_month"] = previous
                    save_state(state)
            elif (
                local.day > close_last
                and state.get("closed_for") != current
                and str(state.get("close_tried_on") or "").startswith(current)
            ):
                state["last_error"] = f"close {previous} incomplete after the retry window"
                state["closed_for"] = current
                preserve_error = True
            anchor = _anchor_for(local.day, days, extra)
            if anchor is not None:
                checkpoint = f"{current}-{anchor:02d}"
                if (
                    state.get("last_checkpoint") != checkpoint
                    and state.get("checkpoint_tried_on") != today
                ):
                    gaps_only = (
                        local.day != anchor and reduce_resume_path(current).is_file()
                    )
                    result = _generate(current, "clock-checkpoint", gaps_only)
                    state["checkpoint_tried_on"] = today
                    ran.append(current)
                    if result.get("outcome") == "ok":
                        state["last_checkpoint"] = checkpoint
                        state["last_monthly_generate_month"] = current
                    save_state(state)
            else:
                for past in days:
                    end_day = past + extra
                    if local.day <= end_day:
                        continue
                    checkpoint = f"{current}-{past:02d}"
                    if state.get("last_checkpoint") == checkpoint:
                        continue
                    tried = str(state.get("checkpoint_tried_on") or "")
                    window_end = f"{current}-{end_day:02d}"
                    if not (checkpoint <= tried <= window_end):
                        continue
                    state["last_error"] = (
                        f"checkpoint {checkpoint} incomplete after the retry window"
                    )
                    state["last_checkpoint"] = checkpoint
                    preserve_error = True
            state["last_clock_month"] = current
        state["last_generated_at"] = local.isoformat(timespec="seconds")
        if not preserve_error:
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
