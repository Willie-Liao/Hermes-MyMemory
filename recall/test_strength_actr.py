from __future__ import annotations

import math
from datetime import date

from recall.strength import apply_recall, base_level, neighbor_credits, strength_value


def test_strength_fields_optional_on_existing_shape():
    parsed = {
        "id": "mem-x",
        "type": "fact",
        "importance": 3,
    }
    apply_recall(parsed, now=__import__("datetime").date(2026, 8, 19))
    assert "recall_n" in parsed
    assert "last_recall_at" in parsed
    assert "first_seen" in parsed
    assert "strength" in parsed
    assert 0 <= parsed["strength"] <= 10


def test_petrov_decay_uses_last_recall_and_keeps_frequency():
    """A newer last_recall_at must outrank the same count decayed from first_seen."""
    today = date(2026, 8, 1)
    fresh = base_level(1, "2026-01-01", last_recall_at=today, now=today)
    assert abs(fresh - math.log((0.5 ** -0.5) / 0.5)) < 1e-9
    recent = strength_value(
        recall_n=3,
        first_seen="2026-01-01",
        last_recall_at="2026-07-20",
        now=today,
    )
    stale = strength_value(
        recall_n=3,
        first_seen="2026-01-01",
        last_recall_at="2026-02-01",
        now=today,
    )
    assert recent > stale
    aged = strength_value(
        recall_n=3,
        first_seen="2026-01-01",
        last_recall_at="2026-07-20",
        now=date(2026, 8, 31),
    )
    assert aged < recent
    fractional = base_level(0.25, "2026-01-01", now=today)
    single = base_level(0.25, "2026-01-01", last_recall_at="2026-01-01", now=today)
    assert abs(fractional - single) < 1e-9


def test_partial_credit_does_not_refresh_last_recall():
    parsed = {
        "id": "mem-n",
        "type": "fact",
        "importance": 3,
        "recall_n": 1,
        "last_recall_at": "2026-01-01",
        "first_seen": "2026-01-01",
    }
    apply_recall(parsed, now=date(2026, 8, 1), credit=0.25)
    assert parsed["recall_n"] == 1.25
    assert parsed["last_recall_at"] == "2026-01-01"
