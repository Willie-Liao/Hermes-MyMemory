"""Monday 00:00 leftover overlay writes Sunday bodies into the week file."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path


def _load_weekly(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugin_dir = Path(__file__).resolve().parent
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    module_path = plugin_dir / "weekly.py"
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_sunday_patch_test", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _empty_thread(prompt: str, *, purpose: str = "weekly_llm", force_tool_name: str = ""):
    del prompt, purpose
    name = force_tool_name or "submit_weekly_thread"
    args = {"cross-day-thread": [], "entities": []}
    return {
        "final_response": "",
        "tool_name": name,
        "tool_args": args,
        "tool_calls": [(name, args)],
        "messages": [],
        "failed": False,
    }


def test_patch_sunday_evening_week_writes_leftover_into_weekly_md(tmp_path, monkeypatch):
    """Post-16:00 Sunday bodies land on Sunday intra and Chronicle; Mon–Sat wrap-up stays."""
    weekly = _load_weekly(tmp_path, monkeypatch)
    from weekly_event_schema import IntraDayThread, WeeklyReviewPayload, WeeklySummaryItem

    monday = "Monday wrap-up stays"
    payload = WeeklyReviewPayload(
        days=(),
        week_key="2026-W38",
        intra_day_thread=(
            IntraDayThread(
                date=date(2026, 9, 14),
                weekday="Monday",
                source_field="day_wrapup",
                text=f"- {monday}",
                empty=False,
            ),
            IntraDayThread(
                date=date(2026, 9, 20),
                weekday="Sunday",
                source_field="day_wrapup",
                text="",
                empty=True,
            ),
        ),
        summary=(WeeklySummaryItem(text=monday, weekdays=("Monday",)),),
    )
    target = tmp_path / "memories" / "staging" / "weekly" / "2026-W38.md"
    weekly._commit_weekly_outputs(target, "", payload, "2026-W38", reason="test")
    before = target.read_text(encoding="utf-8")

    sunday = tmp_path / "memories" / "staging" / "daily" / "2026-09-20.md"
    sunday.parent.mkdir(parents=True, exist_ok=True)
    sunday.write_text(
        "---\n"
        "id: mem-2026-09-20-event-MORNING\n"
        "type: event\n"
        "entity: Morning Only\n"
        "generated_at: '2026-09-20T15:47:12+08:00'\n"
        "valid_from: 2026-09-20\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [test]\n"
        "---\n"
        "Outcome: Morning only stub that must stay out of the midnight patch.\n"
        "\n"
        "---\n"
        "id: mem-2026-09-20-event-FRENCH01\n"
        "type: event\n"
        "entity: Express Entry French Policy\n"
        "generated_at: '2026-09-20T23:55:34+08:00'\n"
        "valid_from: 2026-09-20\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [test]\n"
        "---\n"
        "Beginning: User asked about French immigration.\n"
        "Course: Assistant explained CRS French points.\n"
        "Outcome: User received comparison: French pathway CRS 400 vs regular pool, French NCLC 7 is highest ROI.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(weekly, "_call_weekly_llm_tools", _empty_thread)
    monkeypatch.setattr(weekly, "_call_weekly_llm", lambda *_a, **_k: "")

    result = weekly.patch_sunday_evening_week("2026-W38")
    assert result["outcome"] == "ok"
    after = target.read_text(encoding="utf-8")
    assert after != before
    assert monday in after
    assert "French NCLC 7" in after
    assert "Morning only stub" not in after

    loaded = weekly.weekly_json.loads(after)
    sunday_row = next(row for row in loaded.intra_day_thread if row.date == date(2026, 9, 20))
    monday_row = next(row for row in loaded.intra_day_thread if row.date == date(2026, 9, 14))
    assert "French NCLC 7" in sunday_row.text
    assert monday_row.text.strip() == f"- {monday}"
    assert any("French NCLC 7" in item.text and "Sunday" in item.weekdays for item in loaded.summary)
    assert any(item.text == monday and item.weekdays == ("Monday",) for item in loaded.summary)
