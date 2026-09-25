from __future__ import annotations

from pathlib import Path

BANNED = ("Beginning:", "Course:", "Outcome:", "Obstacle:")


def test_monthly_no_bodies_and_evidence_ids(staging):
    path = staging / "monthly" / "2026-08.md"
    text = path.read_text(encoding="utf-8")
    for marker in BANNED:
        assert marker not in text
    assert "st-kimi" in text and "st-mimo" in text
    assert "2026-W32" in text and "2026-W33" in text
    assert "mem-2026-08-12-event-9625547B667B" in text
    # evidence entries are bare ids, not yaml bodies
    assert "Beginning:" not in text
