"""Map-stage cache and evidence filtering."""

from __future__ import annotations

from monthly_notes import map_batch
from monthly_slice import pack_batches, week_slices


def _fake_oneshot(items):
    calls = {"n": 0}

    def call(prompt, **kwargs):
        calls["n"] += 1
        return {
            "failed": False,
            "tool_name": kwargs.get("force_tool_name"),
            "tool_args": {"items": items},
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 0,
            "model": "mimo-v2.5",
        }

    return call, calls


def test_map_cache_second_run_skips_llm(tmp_path, monkeypatch):
    import monthly_notes
    import monthly_state

    monkeypatch.setattr(monthly_state, "notes_dir", lambda: tmp_path / ".notes")
    monkeypatch.setattr(monthly_notes, "notes_dir", lambda: tmp_path / ".notes")
    batches = pack_batches(week_slices("2026-08"))
    batch = batches[0]
    real_id = next(iter(batch.ids))
    items = [
        {
            "kind": "decision",
            "what": "house format for infographic pages",
            "why_it_mattered": "cited later",
            "evidence": [real_id, "mem-invented-id"],
        }
    ]
    fake, calls = _fake_oneshot(items)
    first = map_batch("2026-08", batch, call_oneshot=fake, force_refresh=True)
    assert calls["n"] == 1
    assert first["cache_hit"] is False
    assert all(eid in batch.ids for item in first["items"] for eid in item["evidence"])
    assert "mem-invented-id" not in str(first["items"])
    second = map_batch("2026-08", batch, call_oneshot=fake)
    assert second["cache_hit"] is True
    assert calls["n"] == 1


def test_note_word_cap_drops_item(tmp_path, monkeypatch):
    import monthly_notes
    import monthly_state

    monkeypatch.setattr(monthly_state, "notes_dir", lambda: tmp_path / ".notes")
    monkeypatch.setattr(monthly_notes, "notes_dir", lambda: tmp_path / ".notes")
    batches = pack_batches(week_slices("2026-08"))
    batch = batches[0]
    real_id = next(iter(batch.ids))
    long_what = " ".join(["word"] * 41)
    fake, _calls = _fake_oneshot(
        [{"kind": "x", "what": long_what, "why_it_mattered": "n", "evidence": [real_id]}]
    )
    record = map_batch("2026-08", batch, call_oneshot=fake, force_refresh=True)
    assert record["items"] == []


def test_note_item_cap_keeps_six(tmp_path, monkeypatch):
    import monthly_notes
    import monthly_state

    monkeypatch.setattr(monthly_state, "notes_dir", lambda: tmp_path / ".notes")
    monkeypatch.setattr(monthly_notes, "notes_dir", lambda: tmp_path / ".notes")
    batches = pack_batches(week_slices("2026-08"))
    batch = batches[0]
    real_id = next(iter(batch.ids))
    items = [
        {"kind": "x", "what": f"keep {i}", "why_it_mattered": "n", "evidence": [real_id]}
        for i in range(8)
    ]
    fake, _calls = _fake_oneshot(items)
    record = map_batch("2026-08", batch, call_oneshot=fake, force_refresh=True)
    assert len(record["items"]) == 6


def test_hash_miss_invalidates_only_that_batch(tmp_path, monkeypatch):
    from dataclasses import replace
    import monthly_notes
    import monthly_state

    monkeypatch.setattr(monthly_state, "notes_dir", lambda: tmp_path / ".notes")
    monkeypatch.setattr(monthly_notes, "notes_dir", lambda: tmp_path / ".notes")
    batches = pack_batches(week_slices("2026-08"))
    b1, b2 = batches[0], batches[1]
    id1, id2 = next(iter(b1.ids)), next(iter(b2.ids))
    fake1, calls1 = _fake_oneshot(
        [{"kind": "a", "what": "one", "why_it_mattered": "n", "evidence": [id1]}]
    )
    map_batch("2026-08", b1, call_oneshot=fake1, force_refresh=True)
    fake2, calls2 = _fake_oneshot(
        [{"kind": "b", "what": "two", "why_it_mattered": "n", "evidence": [id2]}]
    )
    map_batch("2026-08", b2, call_oneshot=fake2, force_refresh=True)
    assert calls1["n"] == 1 and calls2["n"] == 1
    hit = map_batch("2026-08", b1, call_oneshot=fake1)
    assert hit["cache_hit"] is True
    assert calls1["n"] == 1
    mutated = replace(b2, source_sha256="0" * 64)
    miss = map_batch("2026-08", mutated, call_oneshot=fake2)
    assert miss["cache_hit"] is False
    assert calls2["n"] == 2


def test_parse_failure_leaves_previous_note(tmp_path, monkeypatch):
    import monthly_notes
    import monthly_state

    monkeypatch.setattr(monthly_state, "notes_dir", lambda: tmp_path / ".notes")
    monkeypatch.setattr(monthly_notes, "notes_dir", lambda: tmp_path / ".notes")
    batches = pack_batches(week_slices("2026-08"))
    batch = batches[0]
    real_id = next(iter(batch.ids))
    fake, _calls = _fake_oneshot(
        [{"kind": "x", "what": "kept", "why_it_mattered": "n", "evidence": [real_id]}]
    )
    first = map_batch("2026-08", batch, call_oneshot=fake, force_refresh=True)
    assert first["items"]

    def boom(prompt, **kwargs):
        return {"failed": True, "tool_args": {}, "input_tokens": 1}

    after = map_batch("2026-08", batch, call_oneshot=boom, force_refresh=True)
    assert after.get("items") == first["items"]
