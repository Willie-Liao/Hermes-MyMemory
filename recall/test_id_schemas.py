from __future__ import annotations

from recall.ids import BlockIndex, classify_daily_id, resolve_id


def test_four_daily_schemas_and_filename_mismatch(staging):
    samples = {
        "A": "mem-2026-08-12-event-9625547B667B",
        "B": "mem-2026-07-11-w28-backlog-scan-delivered",
        "C": "mem-20260616-1607-cognitive-directionality",
        "D": "mem-20260617-career-pivot",
    }
    for shape, mem_id in samples.items():
        assert classify_daily_id(mem_id) == shape
        rec = resolve_id(mem_id, staging=staging)
        assert rec is not None, mem_id
        assert rec.block_id == mem_id
        assert rec.path.is_file()

    mismatch = "mem-2026-08-17-decision-A4E9C8CBE86B"
    rec = resolve_id(mismatch, staging=staging)
    assert rec is not None
    assert rec.path.name == "2026-08-16.md"
    fact = resolve_id("mem-2026-08-17-fact-4E29203F0247", staging=staging)
    assert fact is not None
    assert fact.path.name == "2026-08-16.md"


def test_all_daily_ids_resolve_to_containing_file(staging):
    store = BlockIndex(staging)
    assert len(store.records) >= 8
    for rec in store.records:
        hit = resolve_id(rec.block_id, staging=staging)
        assert hit is not None, rec.block_id
        assert hit.path.resolve() == rec.path.resolve()
        assert rec.block_id in rec.path.read_text(encoding="utf-8")
