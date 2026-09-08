from __future__ import annotations

from recall.normalize import build_entity_index, entity_key


def test_lookup_key_sentence_and_cjk(staging):
    from recall.normalize import load_entity_index, lookup_key

    idx = load_entity_index(staging)
    assert lookup_key("what did we do about memory digest?", idx) == "memorydigest"
    assert lookup_key("林主任相关的记忆有哪些？", idx) == "林主任"
    assert lookup_key("Jordan", idx) == "jordan"


def test_alias_collapse_keeps_jordan_and_gitnexus_apart():
    assert entity_key("Memory Digest") == "memorydigest"
    assert entity_key("MemoryDigest") == "memorydigest"
    assert entity_key("memory-digest") == "memorydigest"
    assert entity_key("memory-digest-plugin") == "memorydigestplugin"
    assert entity_key("Jordan") == "jordan"
    assert entity_key("gitnexus") == "gitnexus"
    assert entity_key("林主任") == "林主任"
    assert entity_key("Jordan") != entity_key("gitnexus")


def test_real_staging_index_counts(staging):
    index = build_entity_index(staging)
    assert "memorydigest" in index
    days = index["memorydigest"]["days"]
    assert "2026-08-12" in days
    multi = [k for k, n in index.items() if len(n.get("days") or []) > 1]
    assert "memorydigest" in multi
    n_blocks = sum(len(n.get("mem_ids") or []) for n in index.values())
    assert n_blocks >= 8
    assert entity_key("memory-digest-plugin").startswith("memorydigest")


def test_bilingual_aliases_collapse_to_english_key(tmp_path):
    from recall.conftest import _block
    from recall.normalize import lookup_key

    daily = tmp_path / "daily"
    daily.mkdir()
    old_id = "mem-2026-08-01-fact-aaaaaaaaaaaa"
    new_id = "mem-2026-08-24-event-bbbbbbbbbbbb"
    (daily / "2026-08-01.md").write_text(
        _block(
            mem_id=old_id,
            type_="fact",
            entity="记忆摘要",
            body="Factual: legacy Chinese-only Memory Digest card.",
        ),
        encoding="utf-8",
    )
    (daily / "2026-08-24.md").write_text(
        _block(
            mem_id=new_id,
            type_="event",
            entity="Memory Digest",
            body="Beginning: asked; Course: traced; Outcome: recalled.",
            extra="entity_aliases: [记忆摘要]\npredicate: user_requested_memory_recall\n",
        ),
        encoding="utf-8",
    )
    index = build_entity_index(tmp_path)
    assert "memorydigest" in index
    assert "记忆摘要" not in index
    node = index["memorydigest"]
    assert node["canonical"] == "Memory Digest"
    assert "记忆摘要" in node["aliases"]
    assert old_id in node["mem_ids"]
    assert new_id in node["mem_ids"]
    assert lookup_key("what did we do about memory digest?", index) == "memorydigest"
    assert lookup_key("记忆摘要相关的记忆有哪些？", index) == "memorydigest"


def test_ambiguous_alias_claims_do_not_merge(tmp_path):
    from recall.conftest import _block
    from recall.normalize import lookup_key

    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "2026-08-24.md").write_text(
        _block(
            mem_id="mem-2026-08-24-fact-aaaaaaaaaaaa",
            type_="fact",
            entity="Memory Digest",
            body="Factual: first claimant.",
            extra="entity_aliases: [记忆摘要]\n",
        )
        + "\n"
        + _block(
            mem_id="mem-2026-08-24-fact-bbbbbbbbbbbb",
            type_="fact",
            entity="Other Topic",
            body="Factual: second claimant.",
            extra="entity_aliases: [记忆摘要]\n",
        ),
        encoding="utf-8",
    )
    index = build_entity_index(tmp_path)
    assert "memorydigest" in index
    assert "othertopic" in index
    assert index["memorydigest"]["mem_ids"] != index["othertopic"]["mem_ids"]
    assert "记忆摘要" not in index or len(index["记忆摘要"]["mem_ids"]) != 2
    assert lookup_key("记忆摘要", index) is None
