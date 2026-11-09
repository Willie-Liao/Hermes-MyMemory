from __future__ import annotations

import os

import pytest
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


def test_render_bands_writes_edges_once(staging, monkeypatch):
    """A second prefetch must not rebuild edges.jsonl when the cards are unchanged."""
    import recall.edges as edges
    from recall.tools import render_bands

    calls = {"n": 0}
    real = edges.extract_edges

    def _count(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(edges, "extract_edges", _count)
    render_bands(staging)
    path = staging / "edges.jsonl"
    assert path.is_file()
    assert path.read_text(encoding="utf-8").strip()
    assert calls["n"] == 1
    render_bands(staging)
    assert calls["n"] == 1


def test_load_edges_rewrites_when_a_daily_card_is_newer(staging, monkeypatch):
    """A newer daily card must refresh edges.jsonl or Band B keeps yesterday's links."""
    import recall.edges as edges

    calls = {"n": 0}
    real = edges.extract_edges

    def _count(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(edges, "extract_edges", _count)
    edges.load_edges(staging)
    assert calls["n"] == 1
    daily = next((staging / "daily").glob("*.md"))
    path = staging / "edges.jsonl"
    newer = path.stat().st_mtime + 5
    os.utime(daily, (newer, newer))
    edges.load_edges(staging)
    assert calls["n"] == 2


def test_load_edges_falls_back_when_staging_is_read_only(staging):
    """A read-only staging dir must still return edges instead of failing prefetch.

    Root ignores the directory mode, so the fallback never runs there and the
    assertion would see a file that root was allowed to write.
    """
    if os.geteuid() == 0:
        pytest.skip("root can write a mode-0555 directory")
    import recall.edges as edges

    edges.load_edges(staging)
    path = staging / "edges.jsonl"
    path.unlink()
    os.chmod(staging, 0o555)
    try:
        loaded = edges.load_edges(staging)
        assert loaded
        assert not path.exists()
    finally:
        os.chmod(staging, 0o755)
