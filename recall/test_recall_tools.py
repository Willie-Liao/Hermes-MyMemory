from __future__ import annotations

from recall.conftest import HOP1, HOP2, OVERLAP, SEED, write_fake_staging
from recall.embed import DEFAULT_MODEL, rerank_embed
from recall.ids import BlockIndex
from recall.lexical import rebuild_lexical
from recall.tools import expand_memory, handle_tool, recall_memory


def test_recall_sentence_hits_seed_cluster(staging):
    fts = recall_memory(
        "Did we already fix the semicolon digest bug, and what else broke in that stretch?",
        staging=staging,
    )
    assert SEED in fts
    assert HOP1 in fts or "channel=fts5" in fts
    ent = recall_memory("what did we do about memory digest?", staging=staging)
    assert "key=memorydigest" in ent or SEED in ent


def test_recall_typo_id_one_hex_off(staging):
    typo = "mem-2026-08-12-event-9625547B667C"
    text = recall_memory(typo, staging=staging)
    assert SEED in text
    assert "channel=id" in text


def test_recall_channels(staging):
    fts = recall_memory("semicolon", staging=staging)
    assert "channel=fts5" in fts
    assert SEED in fts
    ent = recall_memory("memory digest", staging=staging)
    assert "channel=entity_key" in ent
    assert "key=memorydigest" in ent
    ident = recall_memory(SEED, staging=staging)
    assert "channel=id" in ident
    assert "Beginning:" in ident or "event body" in ident.lower()


def test_expand_ppr_order_and_depth_clamp(staging):
    text = expand_memory(SEED, depth=5, staging=staging)
    assert "depth=2" in text
    assert "Beginning:" not in text
    assert HOP1 in text and HOP2 in text
    pos1 = text.find(HOP1)
    pos2 = text.find(HOP2)
    pos_o = text.find(OVERLAP)
    assert pos1 != -1 and pos2 != -1
    if pos_o != -1:
        assert min(pos1, pos2) < pos_o


def test_handle_tool_recall_then_expand(staging):
    text = handle_tool(
        "recall_memory",
        {"query": "semicolon digest bug"},
        staging=staging,
    )
    assert "channel=" in text
    assert SEED in text
    assert HOP1 in text and HOP2 in text
    assert "depth=2" in text
    ladder_only = recall_memory("semicolon digest bug", staging=staging)
    assert "depth=2" not in ladder_only


def test_handle_tool_expand_rewrites_to_recall_first(staging):
    text = handle_tool(
        "expand_memory",
        {"id_or_key": SEED, "depth": 0},
        staging=staging,
    )
    assert SEED in text
    assert HOP1 in text and HOP2 in text
    assert "depth=2" in text


def test_handle_tool_ghost_skips_expand(staging):
    text = handle_tool(
        "recall_memory",
        {"query": "mem-2099-01-01-event-DEADBEEFDEAD"},
        staging=staging,
    )
    assert "channel=miss" in text or "miss" in text
    assert "depth=2" not in text


def test_handle_tool_entity_query_expands_listed_seeds(staging):
    text = handle_tool(
        "recall_memory",
        {"query": "what did we do about memory digest?"},
        staging=staging,
    )
    assert SEED in text
    assert HOP1 in text and HOP2 in text


def test_handle_tool_mixed_cjk_english(staging):
    text = handle_tool(
        "recall_memory",
        {"query": "记忆摘要相关的记忆有哪些？ also memory digest"},
        staging=staging,
    )
    assert SEED in text or "mem-" in text


def test_handle_tool_writes_strength_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_PERSIST_IN_TEST", "1")
    write_fake_staging(tmp_path)
    daily = tmp_path / "daily"
    handle_tool("recall_memory", {"query": SEED}, staging=tmp_path)
    text = (daily / "2026-08-12.md").read_text(encoding="utf-8")
    assert "recall_n:" in text
    assert "strength:" in text
    assert "last_recall_at:" in text
    assert "first_seen:" in text


_REJECTED_ID = "mem-2026-08-21-fact-dddddddddddd"


def test_recall_omits_rejected_except_exact_id(tmp_path, monkeypatch):
    write_fake_staging(tmp_path)
    daily = tmp_path / "daily"
    daily.joinpath("2026-08-21.md").write_text(
        "---\n"
        f"id: {_REJECTED_ID}\n"
        "type: fact\n"
        "entity: Canteen\n"
        "confidence: high\n"
        "status: rejected\n"
        "valid_from: 2026-08-01\n"
        "valid_to: 2026-08-20\n"
        "rejected_reason: rejected by mem-2026-08-20-fact-bbbbbbbbbbbb\n"
        "sources: [session s-fake]\n"
        "---\n"
        "Canteen zxqvrejectedcanteen is open.\n",
        encoding="utf-8",
    )
    rebuild_lexical(tmp_path)
    fts = recall_memory("zxqvrejectedcanteen", staging=tmp_path)
    assert _REJECTED_ID not in fts
    ident = recall_memory(_REJECTED_ID, staging=tmp_path)
    assert "channel=id" in ident
    assert "status: rejected" in ident
    assert "valid_to: 2026-08-20" in ident
    assert "rejected_reason: rejected by mem-2026-08-20-fact-bbbbbbbbbbbb" in ident
    assert "Canteen zxqvrejectedcanteen is open." in ident


def test_default_model_is_gte_multilingual_base():
    assert DEFAULT_MODEL == "Alibaba-NLP/gte-multilingual-base"


def test_rerank_embed_missing_onnx_failopen(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMEMORY_GTE_ONNX", str(tmp_path / "no-such.onnx"))
    write_fake_staging(tmp_path)
    live = BlockIndex(tmp_path).records
    assert rerank_embed("quantum pineapple recipe", live, k=8) == []


def test_recall_channel_embed_when_fts_misses(staging, monkeypatch):
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)

    def _stub_encode(texts):
        out = []
        for i, text in enumerate(texts):
            if i == 0:
                out.append([1.0, 0.0])
                continue
            hit = "Casey prefers visual outlines" in text
            out.append([1.0, 0.0] if hit else [0.0, 1.0])
        return out

    monkeypatch.setattr("recall.embed._encode_texts", _stub_encode)
    text = recall_memory("qzxnmprefersdeckstructure", staging=staging)
    assert "channel=embed" in text
    assert "mem-20260616-1607-cognitive-directionality" in text
