from __future__ import annotations

from recall.conftest import HOP1, HOP2, SEED
from recall.edges import extract_edges
from recall.ids import BlockIndex


def test_related_cross_day_and_no_event_to_event(staging):
    store = BlockIndex(staging)
    edges, dangling = extract_edges(staging, index=store)
    related = [e for e in edges if e["type"] == "related"]
    by_id = store.by_id

    def _cross_day(e):
        a, b = by_id.get(e["from"]), by_id.get(e["to"])
        return a and b and a.day != b.day

    def _cross_week(e):
        a, b = by_id.get(e["from"]), by_id.get(e["to"])
        if not a or not b or a.day == b.day:
            return False
        from recall.ids import iso_week

        return iso_week(a.day) != iso_week(b.day)

    cross_day = [e for e in related if _cross_day(e)]
    cross_week = [e for e in related if _cross_week(e)]
    assert len(cross_day) >= 1
    assert len(cross_week) >= 1
    pair = {(e["from"], e["to"]) for e in related}
    assert (SEED, HOP1) in pair
    assert (SEED, HOP2) in pair
    for e in related:
        assert e["weight"] == 0.9
        src = str(e["src"])
        assert src.startswith("daily/") or "#citemap" in src
    for e in related:
        a, b = by_id.get(e["from"]), by_id.get(e["to"])
        if a and b and a.item_type == "event" and b.item_type == "event":
            raise AssertionError(f"event-to-event {e}")
    dangling_set = set(dangling)
    for e in edges:
        assert e["to"] not in dangling_set
        assert e["to"] in by_id
