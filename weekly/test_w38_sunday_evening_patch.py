"""Run patch_sunday_evening_week against copies of real W38 + Sunday leftover daily."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

REPO_HERMES = Path(__file__).resolve().parents[3]
STAGING = REPO_HERMES / "memories" / "staging"
W38 = STAGING / "weekly" / "2026-W38.md"
SUNDAY_DAILY = STAGING / "daily" / "2026-09-20.md"
SUNDAY = date(2026, 9, 20)
STUB = "User received comparison:"
LEFTOVER_MARKS = (
    "NCLC",
    "Category-Based Selection",
    "French language immigration",
    "Morning Tutoring",
    "George、Andrae",
    "culture room",
    "Day wrap-up",
    "blackboard notice",
    "Katy",
)


def _load_weekly():
    path = Path(__file__).with_name("weekly.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_w38_patch", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _no_llm(*_args, **_kwargs):
    raise RuntimeError("sunday evening patch test must not call a live LLM")


def _reset_w38_to_stub_baseline(weekly, path: Path) -> None:
    """Replay the 00:00/restamp W38 so the patch must ingest leftover bodies again."""
    payload = weekly.weekly_json.loads(path.read_text(encoding="utf-8"))
    item_cls = type(payload.summary[0])
    summary = tuple(
        item
        for item in payload.summary
        if not any(mark in item.text for mark in LEFTOVER_MARKS)
        and item.text.strip().strip("'") != STUB
    )
    summary = (*summary, item_cls(text=STUB, weekdays=("Sunday",)))
    intra = []
    for row in payload.intra_day_thread:
        if row.date == SUNDAY:
            intra.append(
                replace(row, text=f"- {STUB}", empty=False)
            )
            continue
        kept = [
            line
            for line in str(row.text or "").splitlines()
            if STUB not in line and not any(mark in line for mark in LEFTOVER_MARKS)
        ]
        intra.append(
            replace(row, text="\n".join(kept), empty=not bool("\n".join(kept).strip()))
        )
    payload = replace(payload, summary=summary, intra_day_thread=tuple(intra))
    path.write_text(weekly.weekly_json.dump_yaml(payload).rstrip() + "\n", encoding="utf-8")


def test_patch_sunday_evening_week_ingests_w38_sunday_leftover(tmp_path, monkeypatch):
    assert W38.is_file()
    assert SUNDAY_DAILY.is_file()
    weekly_dir = tmp_path / "memories" / "staging" / "weekly"
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    weekly_dir.mkdir(parents=True)
    daily_dir.mkdir(parents=True)
    target = weekly_dir / "2026-W38.md"
    shutil.copy(W38, target)
    shutil.copy(SUNDAY_DAILY, daily_dir / "2026-09-20.md")

    weekly = _load_weekly()
    _reset_w38_to_stub_baseline(weekly, target)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(weekly, "_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(weekly, "_call_weekly_llm", _no_llm)
    monkeypatch.setattr(weekly, "_call_weekly_llm_tools", _no_llm)

    result = weekly.patch_sunday_evening_week("2026-W38")
    assert result.get("outcome") == "ok"

    written = target.read_text(encoding="utf-8")
    daily = (daily_dir / "2026-09-20.md").read_text(encoding="utf-8")
    assert "French" in written
    assert "NCLC" in written
    assert "CRS" in written
    assert "Category-Based Selection" in written
    assert "Found 31 active jobs OK" in written
    assert "valid_from: 2026-09-19" in daily
    leftover_chunk = daily.split("mem-2026-09-20-event-7CA79E55C57B", 1)[1][:800]
    assert "valid_from: 2026-09-19" in leftover_chunk
    summary_stubs = [
        line
        for line in written.splitlines()
        if line.strip().startswith("- text:") and STUB in line and "French" not in line
    ]
    assert summary_stubs == []
    assert any(
        "French" in line and "NCLC" in line
        for line in written.splitlines()
    )
