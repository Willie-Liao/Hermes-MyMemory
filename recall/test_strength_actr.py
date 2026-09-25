from __future__ import annotations

from recall.strength import apply_recall, neighbor_credits, strength_value


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
