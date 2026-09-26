from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
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


def test_evening_slice_keeps_saturday_valid_from():
    workers = _load_workers()
    text = (
        "---\n"
        "id: mem-late\n"
        "type: event\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [test]\n"
        "valid_from: 2026-08-15\n"
        "valid_to: 2026-08-15\n"
        "generated_at: '2026-08-16T23:56:00+08:00'\n"
        "---\n"
        "Beginning: leftover; Course: x; Outcome: y\n"
    )
    after = datetime(2026, 8, 16, 16, 0, tzinfo=SHANGHAI)
    before = datetime(2026, 8, 17, 0, 0, tzinfo=SHANGHAI)
    sliced = workers.evening_daily_slice_text(
        text,
        after=after,
        before=before,
        known_ids=set(),
    )
    assert "mem-late" in sliced
    assert "valid_from: 2026-08-15" in sliced
    assert "valid_from: 2026-08-16" not in sliced


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


FRENCH_EVENT = (
    "---\n"
    "id: mem-2026-09-20-event-7CA79E55C57B\n"
    "type: event\n"
    "valid_from: 2026-09-19\n"
    "generated_at: '2026-09-20T23:55:34+08:00'\n"
    "---\n"
    "Beginning: User asked for details on French language immigration policies "
    "from a shared IRCC link; Course: Assistant explained CRS French points "
    "(+25/+50) and separately provided comprehensive Category-Based Selection "
    "data including 2025 draw scores (379-481); Outcome: User received "
    "comparison: French pathway CRS 400 vs regular pool 520-550+, with "
    "recommendation that French NCLC 7 is highest ROI for their profile\n"
)

FRENCH_FACT = (
    "---\n"
    "id: mem-2026-09-20-fact-04A40F813628\n"
    "type: fact\n"
    "valid_from: 2026-09-19\n"
    "generated_at: '2026-09-20T23:55:34+08:00'\n"
    "---\n"
    "Factual: French NCLC 7+ adds 25-50 CRS points; Category-Based Selection "
    "bypasses CRS pool with draws at 379-481 (2025) vs regular pool 520-550+\n"
)

EARLY_EVENT = (
    "---\n"
    "id: mem-early\n"
    "type: event\n"
    "valid_from: 2026-09-20\n"
    "generated_at: '2026-09-20T15:47:12+08:00'\n"
    "---\n"
    "Beginning: early chair; Course: x; Outcome: y\n"
)


def test_evening_slice_summary_items_use_leftover_bodies_as_sunday():
    workers = _load_workers()
    items = workers.evening_slice_summary_items(FRENCH_EVENT + "\n" + FRENCH_FACT)
    texts = [item.text for item in items]
    assert any("CRS" in text and "French" in text and "NCLC" in text for text in texts)
    assert any("NCLC 7+" in text and "Category-Based Selection" in text for text in texts)
    assert all(item.weekdays == ("Sunday",) for item in items)
    early_items = workers.evening_slice_summary_items(EARLY_EVENT)
    assert early_items
    combined = workers.evening_slice_summary_items(
        FRENCH_EVENT + "\n" + FRENCH_FACT
    )
    assert all("early chair" not in item.text for item in combined)


def test_overlay_evening_slice_replaces_worker1_stub_summary():
    workers = _load_workers()
    sunday = date(2026, 9, 20)
    stub = workers.WeeklyReviewPayload(
        days=(),
        week_key="2026-W38",
        summary=(
            workers.WeeklySummaryItem(
                text="User received comparison:",
                weekdays=("Sunday",),
            ),
        ),
        intra_day_thread=(
            workers.IntraDayThread(
                date=sunday,
                weekday="Sunday",
                source_field="events",
                text="- User received comparison:",
                empty=False,
            ),
        ),
    )
    overlay = workers.overlay_evening_slice_on_payload(
        stub,
        FRENCH_EVENT + "\n" + FRENCH_FACT,
        sunday,
    )
    texts = [item.text for item in overlay.summary]
    assert all("User received comparison:" != item for item in texts)
    assert any("French" in text and "NCLC" in text for text in texts)
    sunday_intra = next(row for row in overlay.intra_day_thread if row.date == sunday)
    assert sunday_intra.empty is False
    assert "NCLC" in sunday_intra.text
    assert "valid_from: 2026-09-19" in (FRENCH_EVENT)
