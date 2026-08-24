from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from recall.conftest import HOP1, HOP2, OVERLAP, SEED, _block, write_fake_staging
from recall.embed import DEFAULT_MODEL, rerank_embed
from recall.ids import BlockIndex
from recall.lexical import rebuild_lexical
from recall.tools import TOOL_SCHEMAS, expand_memory, handle_tool, recall_memory

DIGEST_LEGACY = "mem-2026-06-01-fact-aaaaaaaaaaaa"
CANTEEN_EVENT = "mem-2026-08-10-event-bbbbbbbbbbbb"
PICNIC_EVENT = "mem-2026-08-16-event-cccccccccccc"
WEATHER_FACT = "mem-2026-08-16-fact-dddddddddddd"
TOO_FAR_EVENT = "mem-2026-08-18-event-eeeeeeeeeeee"
REJECTED_TIME = "mem-2026-08-10-fact-ffffffffffff"


def _clocked_time_staging(root: Path) -> Path:
    """Sparse notebook so exact/±3 ranges stay under three time hits."""
    daily = root / "daily"
    daily.mkdir(parents=True)
    (root / "weekly").mkdir(exist_ok=True)
    (root / "monthly").mkdir(exist_ok=True)
    (daily / "2026-06-01.md").write_text(
        _block(
            mem_id=DIGEST_LEGACY,
            type_="fact",
            entity="Memory Digest",
            body="Memory Digest plugin shipped in June.",
            extra=(
                "user_message_at: '2026-06-01T12:00:00+00:00'\n"
                "assistant_response_at: '2026-06-01T12:05:00+00:00'\n"
                "generated_at: '2026-06-01T12:06:00+00:00'\n"
            ),
        ),
        encoding="utf-8",
    )
    (daily / "2026-08-10.md").write_text(
        _block(
            mem_id=CANTEEN_EVENT,
            type_="event",
            entity="Canteen",
            body="Beginning: canteen lunch; Course: ate; Outcome: noted.",
            extra=(
                "predicate: lunch\n"
                "user_message_at: '2026-08-10T09:00:00+08:00'\n"
                "assistant_response_at: '2026-08-10T09:05:00+08:00'\n"
                "generated_at: '2026-08-10T09:06:00+08:00'\n"
            ),
        )
        + "\n"
        + _block(
            mem_id=REJECTED_TIME,
            type_="fact",
            entity="Canteen",
            body="Rejected canteen rumor.",
        ).replace("status: candidate\n", "status: rejected\n", 1),
        encoding="utf-8",
    )
    (daily / "2026-08-16.md").write_text(
        _block(
            mem_id=PICNIC_EVENT,
            type_="event",
            entity="Picnic",
            body="Beginning: picnic; Course: park; Outcome: done.",
            extra=(
                "predicate: picnic\n"
                "user_message_at: '2026-08-16T15:00:00+00:00'\n"
                "assistant_response_at: '2026-08-16T15:20:00+00:00'\n"
            ),
        )
        + "\n"
        + _block(
            mem_id=WEATHER_FACT,
            type_="fact",
            entity="Weather",
            body="It rained during the picnic.",
            extra="generated_at: '2026-08-16T18:00:00+00:00'\n",
        ),
        encoding="utf-8",
    )
    (daily / "2026-08-18.md").write_text(
        _block(
            mem_id=TOO_FAR_EVENT,
            type_="event",
            entity="Far",
            body="Beginning: eight days later; Course: outside; Outcome: excluded.",
            extra="predicate: too_far\n",
        ),
        encoding="utf-8",
    )
    rebuild_lexical(root)
    return root


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


def test_recall_bilingual_entity_queries(tmp_path):
    from recall.normalize import write_entity_index

    daily = tmp_path / "daily"
    daily.mkdir(parents=True)
    (tmp_path / "weekly").mkdir()
    (tmp_path / "monthly").mkdir()
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
    rebuild_lexical(tmp_path)
    write_entity_index(tmp_path)
    english = recall_memory("what did we do about memory digest?", staging=tmp_path)
    chinese = recall_memory("记忆摘要相关的记忆有哪些？", staging=tmp_path)
    assert "channel=entity_key" in english
    assert "key=memorydigest" in english
    assert "channel=entity_key" in chinese
    assert "key=memorydigest" in chinese
    assert old_id in english and new_id in english
    assert old_id in chinese and new_id in chinese


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


def test_block_index_derives_occurrence_interval(tmp_path):
    root = _clocked_time_staging(tmp_path)
    store = BlockIndex(root)
    clocked = store.get(CANTEEN_EVENT)
    assert clocked is not None
    assert clocked.occurred_start.isoformat() == "2026-08-10T09:00:00+08:00"
    assert clocked.occurred_end.isoformat() == "2026-08-10T09:05:00+08:00"
    legacy = store.get(TOO_FAR_EVENT)
    assert legacy is not None
    assert legacy.occurred_start == datetime(2026, 8, 18, tzinfo=timezone.utc)
    assert legacy.occurred_end == datetime(2026, 8, 19, tzinfo=timezone.utc)
    generated = store.get(WEATHER_FACT)
    assert generated is not None
    assert generated.occurred_start.isoformat() == "2026-08-16T18:00:00+00:00"
    assert generated.occurred_end.isoformat() == "2026-08-16T18:00:00+00:00"


def test_recall_time_or_entity_progressively_widens_to_seven_days(tmp_path):
    root = _clocked_time_staging(tmp_path)
    text = recall_memory(
        "Memory Digest",
        time_from="2026-08-10",
        time_to="2026-08-10",
        staging=root,
    )
    assert DIGEST_LEGACY in text
    assert CANTEEN_EVENT in text
    assert PICNIC_EVENT in text
    assert TOO_FAR_EVENT not in text
    assert REJECTED_TIME not in text
    assert "widen_days=7" in text
    assert DIGEST_LEGACY in text.split(CANTEEN_EVENT)[0]
    unbounded = recall_memory("Memory Digest", staging=root)
    assert "channel=entity_key" in unbounded
    assert "widen_days=" not in unbounded
    malformed = recall_memory(
        "Memory Digest",
        time_from="not-a-date",
        time_to="2026-08-10",
        staging=root,
    )
    assert malformed == unbounded


def test_recall_time_only_ranks_events_first(tmp_path):
    root = _clocked_time_staging(tmp_path)
    text = recall_memory(
        "Memory Digest",
        time_from="2026-08-10",
        time_to="2026-08-10",
        staging=root,
    )
    picnic = text.find(PICNIC_EVENT)
    weather = text.find(WEATHER_FACT)
    assert picnic != -1 and weather != -1
    assert picnic < weather


def test_recall_exact_id_ignores_time_bounds(tmp_path):
    root = _clocked_time_staging(tmp_path)
    text = recall_memory(
        DIGEST_LEGACY,
        time_from="2026-08-10",
        time_to="2026-08-10",
        staging=root,
    )
    assert "channel=id" in text
    assert DIGEST_LEGACY in text


def test_handle_tool_forwards_time_bounds(tmp_path):
    root = _clocked_time_staging(tmp_path)
    text = handle_tool(
        "recall_memory",
        {
            "query": "Memory Digest",
            "time_from": "2026-08-10",
            "time_to": "2026-08-10",
        },
        staging=root,
    )
    assert PICNIC_EVENT in text
    assert TOO_FAR_EVENT not in text
    assert "time_from=" in text and "time_to=" in text


def test_recall_tool_schema_exposes_optional_time_bounds():
    schema = next(row for row in TOOL_SCHEMAS if row["name"] == "recall_memory")
    props = schema["parameters"]["properties"]
    assert schema["parameters"]["required"] == ["query"]
    assert "time_from" in props and "time_to" in props
    assert "time_from" not in schema["parameters"]["required"]
    assert "time_to" not in schema["parameters"]["required"]


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


CASEY_ID = "mem-20260616-1607-cognitive-directionality"


def _vec_for_cosine(x: float) -> list[float]:
    """Build a unit vector whose cosine with [1,0] equals x so stubs do not load GTE."""
    y = (max(0.0, 1.0 - x * x)) ** 0.5
    return [x, y]


def test_embed_flat_pile_keeps_rank1_only(staging, monkeypatch):
    """Int8 piles of 0.83 vs 0.82 must not dump sibling cards into the LLM context."""
    def _stub_encode(texts):
        out = []
        for i, text in enumerate(texts):
            if i == 0:
                out.append([1.0, 0.0])
                continue
            if "Casey prefers visual outlines" in text:
                x = 0.83
            else:
                x = 0.82
            out.append(_vec_for_cosine(x))
        return out

    monkeypatch.setattr("recall.embed._encode_texts", _stub_encode)
    live = BlockIndex(staging).records
    text = rerank_embed("qzxnmprefersdeckstructure", live, k=8)
    assert isinstance(text, str)
    assert "channel=embed" in text
    assert CASEY_ID in text
    assert text.count("rank=") == 1
    assert HOP1 not in text
    assert HOP2 not in text


def test_embed_real_peak_keeps_two(staging, monkeypatch):
    """A real 0.90 vs 0.70 peak must still keep two cards, not collapse to rank 1."""
    def _stub_encode(texts):
        out = []
        for i, text in enumerate(texts):
            if i == 0:
                out.append([1.0, 0.0])
                continue
            if "Casey prefers visual outlines" in text:
                x = 0.90
            elif "merge slots drifted" in text:
                x = 0.70
            else:
                x = 0.20
            out.append(_vec_for_cosine(x))
        return out

    monkeypatch.setattr("recall.embed._encode_texts", _stub_encode)
    live = BlockIndex(staging).records
    text = rerank_embed("qzxnmprefersdeckstructure", live, k=8)
    assert isinstance(text, str)
    assert CASEY_ID in text
    assert HOP1 in text
    assert text.count("rank=") == 2
