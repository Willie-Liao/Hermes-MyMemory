"""Phase-2 plugin clock: civil ticks in the Hermes IANA timezone.

Keeps merge off the extract path. Deadlines are local 08:00/12:00/16:00/20:00/23:55
so a quiet day does not pay for consolidate just because chat batched.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PHASE2_TICKS: tuple[tuple[int, int], ...] = (
    (8, 0),
    (12, 0),
    (16, 0),
    (20, 0),
    (23, 55),
)
NIGHTLY_TICK = (23, 55)
PHASE2_BLOCK_GATE = 25
PHASE2_COOLDOWN = timedelta(minutes=20)
DEFAULT_TZ_NAME = "Asia/Shanghai"


def digest_clock_tz(*, tz_name: str | None = None) -> ZoneInfo:
    """Resolve the civil zone Hermes cron already uses.

    Prefer an injected IANA name (tests), then hermes_time / config.yaml
    ``timezone:``, then Asia/Shanghai. A bad name must not take down the gateway.
    """
    if tz_name and str(tz_name).strip():
        try:
            return ZoneInfo(str(tz_name).strip())
        except (ZoneInfoNotFoundError, Exception):
            pass
    try:
        from hermes_time import get_timezone

        resolved = get_timezone()
        if resolved is not None:
            return resolved
    except Exception:
        pass
    try:
        return ZoneInfo(DEFAULT_TZ_NAME)
    except (ZoneInfoNotFoundError, Exception):
        return ZoneInfo("Etc/UTC")


def _as_local(now: datetime, tz: ZoneInfo) -> datetime:
    """Attach or convert ``now`` into ``tz`` so tick math never mixes naive UTC."""
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def next_deadline(now: datetime, tz: ZoneInfo) -> tuple[str, datetime]:
    """Next 08/12/16/20/23:55 civil instant strictly after ``now`` in ``tz``."""
    local = _as_local(now, tz)
    for hour, minute in PHASE2_TICKS:
        candidate = local.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate > local:
            kind = "nightly" if (hour, minute) == NIGHTLY_TICK else "tick"
            return kind, candidate
    nxt = (local + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    return "tick", nxt


def should_run_phase2_tick(
    block_count: int,
    last_phase2_at: datetime | None,
    now: datetime,
    *,
    cooldown: timedelta = PHASE2_COOLDOWN,
    ignore_block_gate: bool = False,
) -> bool:
    """Merge only when the daily file is crowded and the last merge cooled down.

    Nightly leftover/catch-up passes ``ignore_block_gate`` so a quiet day still
    consolidates; 08/12/16/20 keep the 25-card floor so noon does not pay for
    a sparse file.
    """
    if block_count <= 0:
        return False
    if not ignore_block_gate and block_count <= PHASE2_BLOCK_GATE:
        return False
    if last_phase2_at is None:
        return True
    last = last_phase2_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo or timezone.utc)
    stamp = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (stamp - last.astimezone(stamp.tzinfo)) >= cooldown


def should_run_nightly_leftover(
    last_nightly_date: str | None,
    now: datetime,
    tz: ZoneInfo,
) -> str | None:
    """Return the daily file date to flush, or None.

    23:55 writes today when that night is unstamped. 08:00 writes yesterday
    only if yesterday's 23:55 never stamped — so morning cannot consume
    tonight's leftover slot or land cards on the new civil day.
    """
    local = _as_local(now, tz)
    today = local.date()
    today_s = today.isoformat()
    yesterday_s = (today - timedelta(days=1)).isoformat()
    stamp = str(last_nightly_date or "")
    if (local.hour, local.minute) >= NIGHTLY_TICK:
        return None if stamp == today_s else today_s
    if local.hour == 8:
        if stamp in (yesterday_s, today_s):
            return None
        return yesterday_s
    return None


def parse_aware(value: Any, tz: ZoneInfo) -> datetime | None:
    """Parse ISO state stamps; naive values are treated as ``tz`` local."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed
