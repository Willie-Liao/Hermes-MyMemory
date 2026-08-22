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
