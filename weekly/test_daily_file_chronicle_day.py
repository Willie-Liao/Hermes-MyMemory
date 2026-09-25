"""Chronicle weekday comes from the daily digest filename, not valid_from."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

SUNDAY = date(2026, 9, 20)
SATURDAY = date(2026, 9, 19)
LEFTOVER_ID = "mem-2026-09-20-event-7CA79E55C57B"


def _load_workers(mod_name: str = "memory_weekly_file_day_stamp"):
    path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_mem_id_to_daily_file_day_indexes_stem_not_valid_from(tmp_path):
    workers = _load_workers("memory_weekly_file_day_index")
    daily = tmp_path / "2026-09-20.md"
    daily.write_text(
        (
            "---\n"
            f"id: {LEFTOVER_ID}\n"
            "type: event\n"
            "valid_from: 2026-09-19\n"
            "status: candidate\n"
            "---\n"
            "leftover French\n"
        ),
        encoding="utf-8",
    )
    index = workers._mem_id_to_daily_file_day({SUNDAY: daily})
    assert index[LEFTOVER_ID] == SUNDAY


def test_stamp_event_blocks_uses_file_day_not_copied_valid_from():
    workers = _load_workers("memory_weekly_file_day_stamp_blocks")
    blocks = [
        {
            "frontmatter": {
                "id": "w-evt-2026-09-20-1",
                "type": "event",
                "valid_from": "2026-09-19",
                "valid_to": "2026-09-19",
                "related": [LEFTOVER_ID],
                "sources": ["daily:2026-09-20.md"],
            },
            "body": "User received comparison:",
        }
    ]
    stamped = workers._stamp_event_blocks_to_daily_file_day(
        blocks,
        {LEFTOVER_ID: SUNDAY},
    )
    fm = stamped[0]["frontmatter"]
    assert fm["valid_from"] == "2026-09-20"
    assert fm["valid_to"] == "2026-09-20"
    assert blocks[0]["frontmatter"]["valid_from"] == "2026-09-19"


def test_collect_daily_event_cards_uses_file_day_when_valid_from_in_week(
    tmp_path,
):
    workers = _load_workers("memory_weekly_collect_vf_in_week")
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-09-20.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(
        (
            f"---\nid: {LEFTOVER_ID}\ntype: event\nentity: IRCC\n"
            "predicate: recorded\nvalid_from: 2026-09-19\nstatus: candidate\n---\n"
            "French leftover on Sunday file.\n"
        ),
        encoding="utf-8",
    )
    week_dates = workers.iso_week_dates("2026-W38")
    cards = workers._collect_daily_event_cards({SUNDAY: daily}, week_dates)
    assert len(cards) == 1
    assert cards[0]["date"] == SUNDAY
    assert cards[0]["date"] != SATURDAY
