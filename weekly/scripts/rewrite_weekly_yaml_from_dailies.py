"""Replace weekly Distill+Brief .md with schema YAML (no LLM, no sidecars).

Threads stay empty unless the existing MD already has cross-day-thread.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

MYMEMORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MYMEMORY))
sys.path.insert(0, str(MYMEMORY / "weekly"))

from memory_staging import read_week_status, write_week_status  # noqa: E402
from weekly_event_schema import WeeklyReviewPayload  # noqa: E402
from weekly_event_workers import (  # noqa: E402
    _entities_from_dailies,
    _intra_day_from_dailies,
    _parse_blocks,
)
from weekly_json import dump_yaml, load_sidecar, loads, write_sidecars  # noqa: E402

HERMES_HOME = Path(__file__).resolve().parents[4]
WEEKLY_DIR = HERMES_HOME / "memories" / "staging" / "weekly"
DAILY_DIR = HERMES_HOME / "memories" / "staging" / "daily"


def _week_dates(week_key: str) -> list[date]:
    year_s, _, week_s = week_key.partition("-W")
    start = date.fromisocalendar(int(year_s), int(week_s), 1)
    return [start + timedelta(days=i) for i in range(7)]


def _legend_from_dailies(week_dates: list[date], by_day: dict[date, Path]) -> dict[int, str]:
    legend: dict[int, str] = {}
    n = 1
    for day in week_dates:
        path = by_day.get(day)
        if path is None or not path.is_file():
            continue
        for block in _parse_blocks(path.read_text(encoding="utf-8")):
            fm = block.get("frontmatter") or {}
            if str(fm.get("type") or "").strip().casefold() != "event":
                continue
            bid = str(fm.get("id") or "").strip()
            if "-event-" in bid:
                legend[n] = bid
                n += 1
    return legend


def rewrite_week(week_key: str) -> Path:
    week_dates = _week_dates(week_key)
    by_day = {
        day: DAILY_DIR / f"{day.isoformat()}.md"
        for day in week_dates
        if (DAILY_DIR / f"{day.isoformat()}.md").is_file()
    }
    md_path = WEEKLY_DIR / f"{week_key}.md"
    existing_threads = ()
    if md_path.is_file():
        prior = loads(md_path.read_text(encoding="utf-8"))
        existing_threads = prior.cross_day_thread
    payload = WeeklyReviewPayload(
        days=(),
        legend=_legend_from_dailies(week_dates, by_day),
        week_key=week_key,
        cross_day_thread=existing_threads,
        intra_day_thread=_intra_day_from_dailies(week_dates, by_day),
        entities=_entities_from_dailies(week_dates, by_day),
        conflicts=(),
        hypotheses=(),
    )
    yaml_text = dump_yaml(payload)
    status = read_week_status(md_path) if md_path.is_file() else None
    write_week_status(
        md_path,
        status or "pending",
        week_key_str=week_key,
        content=yaml_text,
    )
    write_sidecars(md_path, payload)
    return md_path


def main() -> int:
    weeks = [f"2026-W{n:02d}" for n in range(25, 34)]
    for week in weeks:
        path = rewrite_week(week)
        obj = load_sidecar(path)
        assert set(obj) >= {
            "entities",
            "legend",
            "cross-day-thread",
            "intra-day-thread",
        }
        assert "## Distill" not in path.read_text(encoding="utf-8")
        print(week, "ok", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
