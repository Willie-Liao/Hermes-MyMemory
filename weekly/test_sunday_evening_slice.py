from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _load_workers():
    path = Path(__file__).with_name("weekly_event_workers.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_evening_slice", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _card(mem_id: str, generated_at: str, body: str) -> str:
    return (
        "---\n"
        f"id: {mem_id}\n"
        "type: event\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [test]\n"
        f"generated_at: '{generated_at}'\n"
        "---\n"
        f"{body}\n"
    )


def test_evening_slice_keeps_post_1600_and_skips_known_ids():
    workers = _load_workers()
    text = "\n".join(
        [
            _card(
                "mem-early",
                "2026-08-16T15:59:00+08:00",
                "Beginning: early; Course: x; Outcome: y",
            ),
            _card(
                "mem-known",
                "2026-08-16T17:00:00+08:00",
                "Beginning: known; Course: x; Outcome: y",
            ),
            _card(
                "mem-late",
                "2026-08-16T23:56:00+08:00",
                "Beginning: leftover; Course: x; Outcome: y",
            ),
        ]
    )
    after = datetime(2026, 8, 16, 16, 0, tzinfo=SHANGHAI)
    before = datetime(2026, 8, 17, 0, 0, tzinfo=SHANGHAI)
    sliced = workers.evening_daily_slice_text(
        text,
        after=after,
        before=before,
        known_ids={"mem-known"},
    )
    assert "mem-late" in sliced
    assert "mem-early" not in sliced
    assert "mem-known" not in sliced
