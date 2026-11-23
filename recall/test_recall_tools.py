from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
    assert "overlap check" not in text
    assert HOP1 in text and HOP2 in text
    assert "Obstacle: merge slots drifted" in text
    assert "Obstacle: extra commas in digest output" in text
    assert "type: procedure" in text
    pos1 = text.find(HOP1)
    pos2 = text.find(HOP2)
    pos_o = text.find(OVERLAP)
    assert pos1 != -1 and pos2 != -1
    if pos_o != -1:
        assert min(pos1, pos2) < pos_o


def test_handle_tool_id_expands_opened_card_once(staging):
    text = handle_tool("recall_memory", {"query": SEED}, staging=staging)
    assert text.count("## Memory / expand") == 1
    assert "Obstacle: merge slots drifted" in text
    assert "Obstacle: extra commas in digest output" in text
    assert "Beginning: semicolon digest bug returned" in text


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


def test_rerank_embed_uses_cached_pool_without_reencoding(monkeypatch):
    """Cards already in embed-cache must not be encoded again when global top-k misses them."""
    from recall.ids import BlockRecord

    recs = []
    cache = {}
    for i in range(12):
        bid = f"mem-2026-08-01-decision-{i:012d}"
        recs.append(
            BlockRecord(
                block_id=bid,
                path=Path("x.md"),
                parsed={},
                body="body",
                day="2026-08-01",
                item_type="decision",
                entity="e",
            )
        )
        cache[bid] = {"embedding": [1.0, 0.0]}
    calls: list[list[str]] = []

    def _encode(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _text in texts]

    monkeypatch.setattr("recall.embed._encode_texts", _encode)
    monkeypatch.setattr("recall.embed_cache.search_cache", lambda *_a, **_k: [])
    monkeypatch.setattr("recall.embed_cache.load_cache", lambda: cache)
    text = rerank_embed("prefetch正常吗", recs, k=8)
    assert calls == [["prefetch正常吗"]]
    assert "channel=embed" in text
    assert recs[0].block_id in text


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
    assert "not the week span" in schema["description"]
    assert "not the full Band C week span" in props["time_from"]["description"]
    assert "not the full Band C week span" in props["time_to"]["description"]
    assert "that block's ISO start and end" not in schema["description"]


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
    monkeypatch.setattr("recall.tools._encode_texts", _stub_encode)
    monkeypatch.setattr("recall.tools._scale_embed_locate", lambda *_a, **_k: None)
    text = recall_memory("qzxnmprefersdeckstructure", staging=staging)
    assert "channel=id" in text
    assert "channel=embed" not in text
    assert "mem-20260616-1607-cognitive-directionality" in text


CASEY_ID = "mem-20260616-1607-cognitive-directionality"


def _vec_for_cosine(x: float) -> list[float]:
    """Build a unit vector whose cosine with [1,0] equals x so stubs do not load GTE."""
    y = (max(0.0, 1.0 - x * x)) ** 0.5
    return [x, y]


def _write_linked_events(root: Path, specs: list[tuple[str, str, str]]) -> None:
    """One daily file per event so the newest day is the cap-1 survivor."""
    daily = root / "daily"
    daily.mkdir(parents=True)
    (root / "weekly").mkdir(exist_ok=True)
    (root / "monthly").mkdir(exist_ok=True)
    by_day: dict[str, list[str]] = {}
    for mem_id, day, _vec_name in specs:
        by_day.setdefault(day, []).append(
            _block(
                mem_id=mem_id,
                type_="event",
                entity="Casey",
                body="linked situation",
                related="mem-cited",
            )
        )
    for day, blocks in by_day.items():
        (daily / f"{day}.md").write_text("\n".join(blocks), encoding="utf-8")


def test_linked_event_scores_use_the_usage_centroid_not_the_closest_event(tmp_path):
    """One aligned episode must not outvote two orthogonal ones.

    Max pooling would report 1.0 and hide that most of this card's usage does
    not match the query. The centroid of three orthonormal events is 1/sqrt(3).
    Cap 1 still has to equal that single newest event.
    """
    from recall.ids import BlockIndex
    from recall.tools import linked_event_scores

    aligned = "mem-2026-08-12-event-aaaaaaaaaaaa"
    side = "mem-2026-08-10-event-bbbbbbbbbbbb"
    other = "mem-2026-08-08-event-cccccccccccc"
    _write_linked_events(
        tmp_path,
        [
            (aligned, "2026-08-12", "aligned"),
            (side, "2026-08-10", "side"),
            (other, "2026-08-08", "other"),
        ],
    )
    cache = {
        aligned: {"embedding": [1.0, 0.0, 0.0]},
        side: {"embedding": [0.0, 1.0, 0.0]},
        other: {"embedding": [0.0, 0.0, 1.0]},
    }
    store = BlockIndex(tmp_path)
    centroids: dict[str, list[float]] = {}
    scores = linked_event_scores(
        [1.0, 0.0, 0.0],
        {"card": ["mem-cited"]},
        store,
        cap=5,
        cache=cache,
        centroids_out=centroids,
    )
    expect = 1.0 / (3.0 ** 0.5)
    assert abs(scores["card"] - expect) < 1e-6
    assert scores["card"] != 1.0
    assert abs(centroids["card"][0] - expect) < 1e-6

    capped = linked_event_scores(
        [1.0, 0.0, 0.0],
        {"card": ["mem-cited"]},
        store,
        cap=1,
        cache=cache,
    )
    assert abs(capped["card"] - 1.0) < 1e-6


def test_stored_centroid_matches_a_fresh_usage_score(tmp_path):
    """A centroid saved at 01:00 must score the message the same as a fresh mean.

    Prefetch multiplies the message by that stored vector. Rebuilding the mean
    on the message path would do the same arithmetic twice and could drift.
    """
    from recall.ids import BlockIndex
    from recall.tools import linked_event_scores

    event_id = "mem-2026-08-12-event-aaaaaaaaaaaa"
    _write_linked_events(tmp_path, [(event_id, "2026-08-12", "aligned")])
    cache = {event_id: {"embedding": [1.0, 0.0, 0.0]}}
    store = BlockIndex(tmp_path)
    targets = {"card": ["mem-cited"]}
    built: dict[str, list[float]] = {}
    fresh = linked_event_scores(
        [1.0, 0.0, 0.0],
        targets,
        store,
        cap=5,
        cache=cache,
        centroids_out=built,
    )
    stored = linked_event_scores(
        [1.0, 0.0, 0.0],
        targets,
        store,
        cap=5,
        cache={},
        stored=built,
    )
    assert abs(stored["card"] - fresh["card"]) < 1e-6
    assert abs(stored["card"] - 1.0) < 1e-6


def test_r2_cosine_drops_a_shared_cone_that_raw_cosine_keeps():
    """Two vectors that only agree along the model mean must not look like a match.

    Raw cosine stays high because both sit in the same cone. After projection
    off that axis they are orthogonal, so guidance can stop treating the cone
    as evidence.
    """
    from recall.embed import _cosine, _l2_normalize

    mu = [1.0, 0.0, 0.0]
    left = [10.0, 1.0, 0.0]
    right = [10.0, 0.0, 1.0]
    assert _cosine(left, right) > 0.8
    assert abs(_cosine(left, right, mean_dir=mu)) < 1e-6
    import numpy as np

    projected = _l2_normalize(np.array([left, right], dtype="float32"), mean_dir=mu)
    assert abs(float(projected[0, 0])) < 1e-5
    assert abs(float(projected[1, 0])) < 1e-5
    assert abs(float(projected[0] @ projected[1])) < 1e-5


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


def test_guidance_mode_monthly_preference_before_procedure(staging, monkeypatch):
    """Monthly guidance uses cached D/P vectors; encode the query once when lexical is below 0.92."""

    from recall.embed_cache import scale_block_id

    monthly = staging / "monthly" / "2026-08.md"
    monthly.write_text(
        monthly.read_text(encoding="utf-8")
        + "key_decisions:\n"
        + "  - id: mem-2026-08-05-decision-PREF\n"
        + "    kind: preference\n"
        + "    text: user prefers concise review summaries\n"
        + "    why_it_matters: still changes the next weekly review\n"
        + "    context: when writing weekly review summaries\n"
        + "    exceptions: do not shorten quotes\n"
        + "    evidence: [mem-2026-08-05-decision-PREF]\n"
        + "    strength: 1.0\n"
        + "key_procedures:\n"
        + "  - id: mem-2026-08-05-procedure-SUMM\n"
        + "    trigger: writing weekly review summaries\n"
        + "    problem: reviews too long\n"
        + "    obstacles: [reviews too long]\n"
        + "    solution: keep bullets under forty words\n"
        + "    insight: the next review still needs the short form\n"
        + "    evidence: [mem-2026-08-05-procedure-SUMM]\n"
        + "    strength: 9.0\n",
        encoding="utf-8",
    )
    import sys
    from pathlib import Path as _Path

    mdir = _Path(__file__).resolve().parent.parent / "monthly"
    if str(mdir) not in sys.path:
        sys.path.insert(0, str(mdir))
    from monthly_actions import _iter_payloads

    cache: dict[str, dict] = {}
    for payload in _iter_payloads(staging):
        for row in (*payload.key_decisions, *payload.key_procedures):
            bid = scale_block_id(
                {
                    "scale": "month",
                    "kind": "dp",
                    "text": row.lookup_text().strip(),
                    "mem_id": str(row.id).strip(),
                    "month_key": str(payload.key).strip(),
                }
            )
            cache[bid] = {"embedding": [1.0, 0.0]}

    def _stub_encode(texts):
        q = texts[0] if texts else ""
        if "review summaries" in q:
            return [[1.0, 0.0]]
        return [[0.0, 1.0]]

    monkeypatch.setattr("recall.embed_cache.load_cache", lambda: cache)
    monkeypatch.setattr("recall.embed._encode_texts", _stub_encode)
    text = recall_memory(
        "writing weekly review summaries",
        staging=staging,
        mode="guidance",
    )
    assert "channel=monthly_guidance" in text
    assert "mem-2026-08-05-decision-PREF" in text
    pref_at = text.index("mem-2026-08-05-decision-PREF")
    proc_at = text.index("mem-2026-08-05-procedure-SUMM")
    assert pref_at < proc_at
    assert "channel=fts5" not in text
    miss = recall_memory("unrelated weather chat", staging=staging, mode="guidance")
    assert "channel=miss" in miss


def test_guidance_mode_daily_dp_fallback_skips_events(staging):
    text = recall_memory("strip semicolon digest bug", staging=staging, mode="guidance")
    assert "channel=daily_dp" in text or "channel=miss" in text
    if "channel=daily_dp" in text:
        assert "type: event" not in text
        assert "- mem-" in text
    normal = recall_memory("what did we do about memory digest?", staging=staging)
    assert "channel=" in normal
    assert "channel=monthly_guidance" not in normal


SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
_CACHE_BLOCK_ID = "mem-2026-08-16-fact-embedcache01"


def _embed_cache_staging(tmp_path: Path, monkeypatch, body: str = "Casey uses a blue notebook.") -> None:
    from recall import embed_cache

    staging = tmp_path / "staging"
    daily = staging / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (staging / "weekly").mkdir(exist_ok=True)
    (daily / "2026-08-16.md").write_text(
        (
            f"id: {_CACHE_BLOCK_ID}\n"
            "type: fact\n"
            "entity: Casey\n"
            "predicate: notebook\n"
            "confidence: high\n"
            "status: candidate\n"
            "related: []\n"
            "---\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "embeddings"
    monkeypatch.setattr(embed_cache, "_staging_root", lambda: staging)
    monkeypatch.setattr(embed_cache, "_cache_root", lambda: cache)
    monkeypatch.setattr(embed_cache, "_embed_cache_tz", lambda: SHANGHAI)


def _legacy_split_blocks(content: str) -> list[dict]:
    """The retired ``\\n---\\n`` scanner, kept only as an oracle for hash stability."""
    import yaml

    sections = content.split("\n---\n")
    blocks = []
    i = 0
    while i < len(sections):
        sec = sections[i].strip()
        if not sec.startswith("id:"):
            i += 1
            continue
        body = ""
        if i + 1 < len(sections):
            nxt = sections[i + 1].strip()
            if nxt and not nxt.startswith("id:") and not nxt.startswith("type:"):
                body = nxt
                i += 2
            else:
                i += 1
        else:
            i += 1
        parsed = yaml.safe_load(sec)
        blocks.append({
            "id": str(parsed.get("id", "")).strip(),
            "type": str(parsed.get("type", "")).strip(),
            "entity": str(parsed.get("entity", "")).strip(),
            "predicate": str(parsed.get("predicate", "")).strip(),
            "body": body.strip()[:500],
        })
    return blocks


def test_scan_all_blocks_keeps_first_card_when_file_opens_with_fence(tmp_path, monkeypatch):
    from recall import embed_cache

    staging = tmp_path / "staging"
    daily = staging / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-08-16.md").write_text(
        "---\n"
        "id: mem-first\n"
        "type: event\n"
        "entity: Casey\n"
        "predicate: opened\n"
        "---\n"
        "First card body.\n"
        "\n"
        "---\n"
        "id: mem-second\n"
        "type: decision\n"
        "entity: Casey\n"
        "predicate: chose\n"
        "---\n"
        "Second card body.\n"
        "\n"
        "---\n"
        "id: mem-third\n"
        "type: procedure\n"
        "entity: Casey\n"
        "predicate: steps\n"
        "---\n"
        "Third card body.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(embed_cache, "_staging_root", lambda: staging)
    found = embed_cache.scan_all_blocks()
    assert list(found) == ["mem-first", "mem-second", "mem-third"]
    assert found["mem-first"]["body"] == "First card body."
    assert found["mem-first"]["type"] == "event"


def test_scan_all_blocks_embeds_procedure_obstacle_and_solution(tmp_path, monkeypatch):
    """A slotted procedure gets two view ids; a decision body does not."""
    from recall import embed_cache
    from recall.embed_cache import scale_block_id

    staging = tmp_path / "staging"
    daily = staging / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-08-16.md").write_text(
        "---\n"
        "id: mem-proc\n"
        "type: procedure\n"
        "entity: Casey\n"
        "predicate: send\n"
        "---\n"
        "Obstacle: WeChat has no file flag; Solution: send the chapter via MEDIA.\n"
        "\n"
        "---\n"
        "id: mem-dec\n"
        "type: decision\n"
        "entity: Casey\n"
        "predicate: chose\n"
        "---\n"
        "Preference: keep one next action.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(embed_cache, "_staging_root", lambda: staging)
    found = embed_cache.scan_all_blocks()
    obs = scale_block_id({"scale": "day", "kind": "obstacle", "mem_id": "mem-proc"})
    sol = scale_block_id({"scale": "day", "kind": "solution", "mem_id": "mem-proc"})
    assert found["mem-proc"]["body"].startswith("Obstacle:")
    assert found[obs]["body"] == "WeChat has no file flag"
    assert found[sol]["body"] == "send the chapter via MEDIA."
    assert found[obs]["kind"] == "obstacle"
    assert found[sol]["kind"] == "solution"
    assert found[obs]["scale"] == "day"
    view_owners = [
        row.get("mem_id")
        for key, row in found.items()
        if str(key).startswith("emb-scale:")
    ]
    assert view_owners == ["mem-proc", "mem-proc"]


def test_month_scale_views_stay_out_of_channel_four(tmp_path, monkeypatch):
    """Channel 4 locates from summary and dp rows; solution and context are cache-only."""
    from recall.tools import _scale_embed_locate, collect_month_scale_rows

    staging = tmp_path / "staging"
    monthly = staging / "monthly"
    monthly.mkdir(parents=True)
    (monthly / "2026-08.md").write_text(
        "schema_version: 2\n"
        "cycle: monthly\n"
        "month_key: '2026-08'\n"
        "key_decisions:\n"
        "  - id: mem-d\n"
        "    kind: preference\n"
        "    text: Prefer one next action.\n"
        "    why_it_matters: still changes the next time\n"
        "    context: When planning the week\n"
        "    evidence: [mem-d]\n"
        "key_procedures:\n"
        "  - id: mem-p\n"
        "    problem: Reviews run long\n"
        "    solution: send the chapter via MEDIA\n"
        "    insight: keep it short\n"
        "    trigger: Writing the weekly review\n"
        "    obstacles: [The first note was incomplete.]\n"
        "    evidence: [mem-p]\n"
        "summary:\n"
        "  - text: June start\n"
        "    weeks: ['2026-W32']\n",
        encoding="utf-8",
    )
    plain = collect_month_scale_rows(staging)
    assert {row["kind"] for row in plain} <= {"summary", "dp"}
    assert all("MEDIA" not in str(row.get("text") or "") for row in plain)
    assert all("planning the week" not in str(row.get("text") or "").casefold() for row in plain)
    viewed = collect_month_scale_rows(staging, views=True)
    by_kind = {row["kind"]: row for row in viewed}
    assert by_kind["dp_solution"]["text"] == "send the chapter via MEDIA"
    assert by_kind["dp_solution"]["mem_id"] == "mem-p"
    assert by_kind["dp_context"]["text"] == "When planning the week"
    assert by_kind["dp_context"]["mem_id"] == "mem-d"
    seen: list[bool] = []

    def _spy(root, views=False):
        seen.append(bool(views))
        return []

    monkeypatch.setattr("recall.tools.collect_month_scale_rows", _spy)
    monkeypatch.setattr("recall.tools.collect_week_scale_rows", lambda *_a, **_k: [])
    assert _scale_embed_locate("send the chapter via MEDIA", staging) is None
    assert seen == [False]


def test_parse_blocks_matches_legacy_split_when_file_has_no_opening_fence(tmp_path, monkeypatch):
    from recall import embed_cache

    staging = tmp_path / "staging"
    daily = staging / "daily"
    daily.mkdir(parents=True)
    content = (
        "id: mem-a\n"
        "type: fact\n"
        "entity: Casey\n"
        "predicate: notebook\n"
        "confidence: high\n"
        "status: candidate\n"
        "related: []\n"
        "---\n"
        "Casey uses a blue notebook.\n"
        "\n"
        "---\n"
        "id: mem-b\n"
        "type: decision\n"
        "entity: Hermes\n"
        "predicate: ship\n"
        "confidence: high\n"
        "status: active\n"
        "related:\n"
        "  - mem-a\n"
        "---\n"
        "Ship the notebook change.\n"
    )
    path = daily / "2026-08-16.md"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(embed_cache, "_staging_root", lambda: staging)
    new = embed_cache._parse_blocks_from_file(path)
    old = _legacy_split_blocks(content)
    assert [b["id"] for b in new] == [b["id"] for b in old]
    for got, expect in zip(new, old):
        for key in ("id", "type", "entity", "predicate", "body"):
            assert got[key] == expect[key]


def test_embed_cache_reembeds_on_hash_change(tmp_path, monkeypatch):
    from recall import embed_cache

    calls: list[list[str]] = []

    def encode_fn(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    _embed_cache_staging(tmp_path, monkeypatch, "first body")
    first = embed_cache.incremental_update(encode_fn=encode_fn)
    assert first["embedded"] == 1
    assert len(calls) == 1
    same = embed_cache.incremental_update(encode_fn=encode_fn)
    assert same["embedded"] == 0
    assert len(calls) == 1
    _embed_cache_staging(tmp_path, monkeypatch, "second body")
    changed = embed_cache.incremental_update(encode_fn=encode_fn)
    assert changed["embedded"] == 1
    assert len(calls) == 2


def test_embed_cache_skips_before_one_am_shanghai(tmp_path, monkeypatch):
    from recall import embed_cache

    calls: list[int] = []

    def encode_fn(texts):
        calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]

    _embed_cache_staging(tmp_path, monkeypatch)
    now = datetime(2026, 8, 16, 0, 30, tzinfo=SHANGHAI)
    stats = embed_cache.incremental_update(encode_fn=encode_fn, now=now)
    assert calls == []
    assert stats.get("embedded", 0) == 0
    meta = json.loads((tmp_path / "embeddings" / "embed-cache-meta.json").read_text()) if (
        tmp_path / "embeddings" / "embed-cache-meta.json"
    ).is_file() else {}
    assert meta.get("last_checked_on") != "2026-08-16"


def test_embed_cache_one_am_stamps_last_checked_on(tmp_path, monkeypatch):
    from recall import embed_cache

    def encode_fn(texts):
        return [[1.0, 0.0] for _ in texts]

    _embed_cache_staging(tmp_path, monkeypatch)
    now = datetime(2026, 8, 16, 1, 0, tzinfo=SHANGHAI)
    stats = embed_cache.incremental_update(encode_fn=encode_fn, now=now)
    assert stats["embedded"] == 1
    meta = json.loads((tmp_path / "embeddings" / "embed-cache-meta.json").read_text(encoding="utf-8"))
    assert meta["last_checked_on"] == "2026-08-16"


def test_embed_cache_stores_own_event_centroid(tmp_path, monkeypatch):
    """The 01:00 save must keep one centroid for a decision and the event it owns.

    Reloading that vector and dotting the message with it has to match a centroid
    built from the same cache, or prefetch would score a different episode.
    """
    from recall import embed_cache
    from recall.ids import BlockIndex
    from recall.tools import linked_event_scores

    decision_id = "mem-2026-08-16-decision-aaaaaaaaaaaa"
    event_id = "mem-2026-08-16-event-bbbbbbbbbbbb"
    staging = tmp_path / "staging"
    daily = staging / "daily"
    daily.mkdir(parents=True)
    (staging / "weekly").mkdir()
    (daily / "2026-08-16.md").write_text(
        (
            "---\n"
            f"id: {event_id}\n"
            "type: event\n"
            "entity: Casey\n"
            "predicate: class_correction\n"
            "confidence: explicit\n"
            "status: candidate\n"
            f"related: [{decision_id}]\n"
            "---\n"
            "Nina was filed under the wrong class.\n"
            "\n"
            "---\n"
            f"id: {decision_id}\n"
            "type: decision\n"
            "entity: User\n"
            "predicate: ask_before_class\n"
            "confidence: explicit\n"
            "status: candidate\n"
            "related: []\n"
            "---\n"
            "Ask for the class before writing a class-linked note.\n"
        ),
        encoding="utf-8",
    )
    cache_dir = tmp_path / "embeddings"
    monkeypatch.setattr(embed_cache, "_staging_root", lambda: staging)
    monkeypatch.setattr(embed_cache, "_cache_root", lambda: cache_dir)
    monkeypatch.setattr(embed_cache, "_embed_cache_tz", lambda: SHANGHAI)

    def encode_fn(texts):
        rows = []
        for text in texts:
            if "wrong class" in text:
                rows.append([1.0, 0.0, 0.0])
            else:
                rows.append([0.0, 1.0, 0.0])
        return rows

    embed_cache.incremental_update(
        encode_fn=encode_fn, now=datetime(2026, 8, 16, 1, 0, tzinfo=SHANGHAI)
    )
    meta = json.loads((cache_dir / "embed-cache-meta.json").read_text(encoding="utf-8"))
    assert decision_id in meta["centroids"]
    blocks = json.loads((cache_dir / "embed-cache.json").read_text(encoding="utf-8"))["blocks"]
    store = BlockIndex(staging)
    fresh: dict[str, list[float]] = {}
    linked_event_scores(
        None,
        {decision_id: [decision_id]},
        store,
        cap=5,
        cache=blocks,
        mean_dir=meta["mean_dir"],
        centroids_out=fresh,
    )
    assert meta["centroids"][decision_id] == fresh[decision_id]
    query = [1.0, 0.0, 0.0]
    from_disk = linked_event_scores(
        query,
        {decision_id: [decision_id]},
        store,
        cap=5,
        cache=blocks,
        mean_dir=meta["mean_dir"],
        stored=meta["centroids"],
    )
    rebuilt = linked_event_scores(
        query,
        {decision_id: [decision_id]},
        store,
        cap=5,
        cache=blocks,
        mean_dir=meta["mean_dir"],
    )
    assert abs(from_disk[decision_id] - rebuilt[decision_id]) < 1e-6


def test_embed_cache_writes_mean_direction_and_keeps_it_on_noop_tick(tmp_path, monkeypatch):
    """The 01:00 rebuild must store the cone axis, and a no-op next day must not drop it.

    R2 reads mean_dir from meta. A stamp-only tick that rewrote meta from a
    fresh dict would erase the axis and every later query would score raw cosine.
    """
    from recall import embed_cache

    def encode_fn(texts):
        return [[3.0, 4.0] for _ in texts]

    _embed_cache_staging(tmp_path, monkeypatch)
    now = datetime(2026, 8, 16, 1, 0, tzinfo=SHANGHAI)
    embed_cache.incremental_update(encode_fn=encode_fn, now=now)
    meta = json.loads((tmp_path / "embeddings" / "embed-cache-meta.json").read_text(encoding="utf-8"))
    assert len(meta["mean_dir"]) == 2
    assert meta["mean_dir"] == [0.6, 0.8]
    assert meta["mean_norm"] == 5.0
    assert embed_cache.mean_direction() == [0.6, 0.8]

    later = embed_cache.incremental_update(
        encode_fn=encode_fn, now=datetime(2026, 8, 17, 1, 0, tzinfo=SHANGHAI)
    )
    assert later["embedded"] == 0
    kept = json.loads((tmp_path / "embeddings" / "embed-cache-meta.json").read_text(encoding="utf-8"))
    assert kept["mean_dir"] == [0.6, 0.8]
    assert kept["mean_norm"] == 5.0
    assert kept["last_checked_on"] == "2026-08-17"


def test_mean_direction_recomputes_when_meta_width_mismatches(tmp_path, monkeypatch):
    from recall import embed_cache

    _embed_cache_staging(tmp_path, monkeypatch)
    cache = {
        "a": {"embedding": [1.0, 0.0]},
        "b": {"embedding": [0.0, 1.0]},
    }
    embed_cache.save_cache(cache, {"mean_dir": [1.0], "dim": 1})
    assert embed_cache.mean_direction(cache) == [0.7071067811865475, 0.7071067811865475]
    assert embed_cache.mean_direction({}) is None


def test_embed_cache_once_per_civil_day(tmp_path, monkeypatch):
    from recall import embed_cache

    calls: list[int] = []

    def encode_fn(texts):
        calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]

    _embed_cache_staging(tmp_path, monkeypatch)
    embed_cache.incremental_update(
        encode_fn=encode_fn, now=datetime(2026, 8, 16, 1, 0, tzinfo=SHANGHAI)
    )
    _embed_cache_staging(tmp_path, monkeypatch, "changed after first check")
    later = embed_cache.incremental_update(
        encode_fn=encode_fn, now=datetime(2026, 8, 16, 8, 0, tzinfo=SHANGHAI)
    )
    assert later["embedded"] == 0
    assert len(calls) == 1


def test_embed_cache_01am_includes_month_week_sidecar(tmp_path, monkeypatch):
    """Month/week sentences share the daily 01:00 pass, not a 03:00 clock."""
    from recall import embed_cache

    _embed_cache_staging(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    (staging / "weekly" / "2026-W32.md").write_text(
        "---\nweek: 2026-W32\n---\n"
        "schema_version: 2\nweek_key: 2026-W32\n"
        "range:\n  start: '2026-08-03'\n  end: '2026-08-09'\n"
        "summary:\n  - text: Pitch-line WeChat to Zhang.\n    weekdays: [Wednesday]\n",
        encoding="utf-8",
    )
    encode_calls: list[int] = []

    def encode_fn(texts):
        encode_calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]

    stats = embed_cache.incremental_update(
        encode_fn=encode_fn, now=datetime(2026, 8, 16, 1, 0, tzinfo=SHANGHAI)
    )
    assert stats.get("outcome") != "idle"
    assert stats["embedded"] >= 2
    cache = json.loads((tmp_path / "embeddings" / "embed-cache.json").read_text())
    scale_ids = [k for k in cache.get("blocks", {}) if str(k).startswith("emb-scale:")]
    assert scale_ids, cache.get("blocks", {}).keys()
    assert embed_cache.EMBED_TICK == (1, 0)


def test_embed_clock_next_wake_shanghai(tmp_path, monkeypatch):
    from recall import embed_cache

    monkeypatch.setattr(embed_cache, "_embed_cache_tz", lambda: SHANGHAI)
    before = datetime(2026, 8, 16, 0, 30, tzinfo=SHANGHAI)
    assert embed_cache._next_embed_check_at(before, SHANGHAI) == datetime(
        2026, 8, 16, 1, 0, tzinfo=SHANGHAI
    )
    after = datetime(2026, 8, 16, 1, 5, tzinfo=SHANGHAI)
    assert embed_cache._next_embed_check_at(after, SHANGHAI) == datetime(
        2026, 8, 17, 1, 0, tzinfo=SHANGHAI
    )


def test_parse_embedding_slot_coerces_none_and_yaml_bool():
    from recall.embed_cache import parse_embedding_slot

    assert parse_embedding_slot(None) == "false"
    assert parse_embedding_slot("false") == "false"
    assert parse_embedding_slot("true") == "true"
    assert parse_embedding_slot(True) == "true"
    assert parse_embedding_slot(False) == "false"


def test_blocks_embedded_requires_every_id_with_vector(tmp_path, monkeypatch):
    from recall import embed_cache

    cache_dir = tmp_path / "embeddings"
    cache_dir.mkdir()
    (cache_dir / "embed-cache.json").write_text(
        json.dumps(
            {
                "blocks": {
                    "mem-1": {"hash": "a", "embedding": [0.1, 0.2]},
                    "mem-empty": {"hash": "b", "embedding": []},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(embed_cache, "_cache_root", lambda: cache_dir)
    assert embed_cache.blocks_embedded(()) == "false"
    assert embed_cache.blocks_embedded(("mem-1",)) == "true"
    assert embed_cache.blocks_embedded(("mem-1", "mem-missing")) == "false"
    assert embed_cache.blocks_embedded(("mem-empty",)) == "false"


def test_monthly_entity_embedded_unions_week_blocks(tmp_path, monkeypatch):
    from recall import embed_cache

    staging = tmp_path / "staging"
    weekly = staging / "weekly"
    weekly.mkdir(parents=True)
    (weekly / "2026-W31.md").write_text(
        "---\nweek: 2026-W31\n---\n"
        "week_key: 2026-W31\n"
        "entities:\n"
        "  - key: memorydigest\n"
        "    canonical: memory-digest\n"
        "    week_blocks: [mem-1]\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "embeddings"
    cache_dir.mkdir()
    (cache_dir / "embed-cache.json").write_text(
        json.dumps({"blocks": {"mem-1": {"hash": "a", "embedding": [1.0]}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(embed_cache, "_staging_root", lambda: staging)
    monkeypatch.setattr(embed_cache, "_cache_root", lambda: cache_dir)
    assert (
        embed_cache.monthly_entity_embedded("memorydigest", ("2026-W31",)) == "true"
    )
    assert embed_cache.monthly_entity_embedded("memorydigest", ("2026-W99",)) == "false"


def test_embed_cache_gate_follows_injected_zone_not_host(tmp_path, monkeypatch):
    from recall import embed_cache

    calls: list[str] = []

    def encode_fn(texts):
        calls.append("ran")
        return [[1.0, 0.0] for _ in texts]

    utc = datetime(2026, 8, 16, 4, 30, tzinfo=ZoneInfo("UTC"))
    _embed_cache_staging(tmp_path, monkeypatch)
    monkeypatch.setattr(embed_cache, "_embed_cache_tz", lambda: NEW_YORK)
    ny = embed_cache.incremental_update(encode_fn=encode_fn, now=utc)
    assert calls == []
    assert ny.get("embedded", 0) == 0
    monkeypatch.setattr(embed_cache, "_embed_cache_tz", lambda: SHANGHAI)
    sh = embed_cache.incremental_update(encode_fn=encode_fn, now=utc)
    assert calls == ["ran"]
    assert sh["embedded"] == 1


def test_scale_embed_month_locates_civil_day_before_daily(staging, monkeypatch):
    """Month cosine hit must re-enter recall_memory on one day, not dump channel=embed."""
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "recall.tools.collect_month_scale_rows",
        lambda *_a, **_k: [
            {"kind": "summary", "text": "Pitch-line WeChat to Zhang", "weeks": ("2026-W32",)}
        ],
    )
    monkeypatch.setattr(
        "recall.tools.collect_week_scale_rows",
        lambda *_a, **_k: [
            {
                "kind": "summary",
                "text": "Pitch-line WeChat to Zhang",
                "week_key": "2026-W32",
                "start": "2026-08-03",
                "weekdays": ("Monday",),
            }
        ],
    )
    monkeypatch.setattr(
        "recall.tools.rank_plain_passages",
        lambda _q, rows: [(0.91, dict(rows[0]))] if rows else [],
    )
    daily_calls: list[str] = []

    def _spy_daily(query, records=None, k=8, *_a, **_k):
        daily_calls.append(str(query))
        return []

    monkeypatch.setattr("recall.tools.rerank_embed", _spy_daily)
    text = recall_memory("qzxnmprefersdeckstructure", staging=staging)
    assert "channel=embed" not in text
    assert "time_from=2026-08-03" in text or "channel=time_or" in text or "channel=l1" in text or "channel=miss" in text
    assert daily_calls, "bounded re-entry should still be allowed to try daily embed"


def test_scale_embed_skipped_when_time_bounds_set(staging, monkeypatch):
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)

    def _boom(*_a, **_k):
        raise AssertionError("month/week bags must not run when bounds are set")

    monkeypatch.setattr("recall.tools._scale_embed_locate", _boom)
    text = recall_memory(
        "picnic",
        staging=staging,
        time_from="2026-08-12",
        time_to="2026-08-12",
    )
    assert "channel=time_or" in text or "channel=id" in text


def test_scale_embed_dp_locates_channel_id(staging, monkeypatch):
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "recall.tools._scale_embed_locate",
        lambda *_a, **_k: ("id", "mem-20260616-1607-cognitive-directionality"),
    )
    text = recall_memory("qzxnmprefersdeckstructure", staging=staging)
    assert "channel=id" in text
    assert "mem-20260616-1607-cognitive-directionality" in text
    assert "channel=embed" not in text


def test_embed_all_scales_miss_falls_to_l1_or_miss(staging, monkeypatch):
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("recall.tools._scale_embed_locate", lambda *_a, **_k: None)
    monkeypatch.setattr("recall.tools.rerank_embed", lambda *_a, **_k: [])
    text = recall_memory("qzxnmprefersdeckstructure", staging=staging)
    assert "channel=embed" not in text
    assert "channel=l1" in text or "channel=miss" in text


def _dp_card(root: Path, mem_id: str, type_: str, *, recall_n: float = 0, last: str = "") -> None:
    extra = ""
    if recall_n or last:
        extra = f"recall_n: {recall_n}\nlast_recall_at: '{last}'\nfirst_seen: '2026-01-01'\n"
    daily = root / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (root / "weekly").mkdir(exist_ok=True)
    (root / "monthly").mkdir(exist_ok=True)
    path = daily / f"{mem_id.replace(':', '')}.md"
    path.write_text(
        _block(mem_id=mem_id, type_=type_, entity="Guide", body=f"body {mem_id}", extra=extra),
        encoding="utf-8",
    )


def _guidance_rows(monkeypatch, rows: list[dict]) -> None:
    monkeypatch.setattr(
        "monthly_actions.rank_monthly_guidance",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr("recall.tools.search_lexical", lambda *_a, **_k: rows)
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: False)


def test_daily_guidance_splits_six_and_spills_to_decisions(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "monthly"))
    rows = []
    for i in range(10):
        mid = f"mem-2026-08-01-decision-{i:012d}"
        _dp_card(tmp_path, mid, "decision")
        rows.append({"id": mid, "bm25": -2.0})
    for i in range(4):
        mid = f"mem-2026-08-02-procedure-{i:012d}"
        _dp_card(tmp_path, mid, "procedure")
        rows.append({"id": mid, "bm25": -1.0})
    _guidance_rows(monkeypatch, rows)
    text = recall_memory("balance the kinds", staging=tmp_path, mode="guidance")
    assert text.count("  decision  ") == 8
    assert text.count("  procedure  ") == 4


def test_daily_guidance_tops_up_the_short_kind(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "monthly"))
    rows = []
    for i in range(5):
        mid = f"mem-2026-08-03-decision-{i:012d}"
        _dp_card(tmp_path, mid, "decision")
        rows.append({"id": mid, "bm25": -2.0})
    only = "mem-2026-08-04-procedure-000000000001"
    _dp_card(tmp_path, only, "procedure")
    rows.append({"id": only, "bm25": -1.0})
    extra = "mem-2026-08-04-procedure-000000000002"
    _dp_card(tmp_path, extra, "procedure")
    _guidance_rows(monkeypatch, rows)
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [])
    monkeypatch.setattr("recall.tools.rerank_embed", lambda *_a, **_k: extra)
    text = recall_memory("need both kinds", staging=tmp_path, mode="guidance")
    assert text.count("  procedure  ") == 2
    assert extra in text


def test_daily_guidance_asks_again_when_rerank_returns_one_card(tmp_path, monkeypatch):
    """Gap pruning returns one card, so the floor must ask for the next one."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "monthly"))
    rows = []
    for i in range(2):
        mid = f"mem-2026-08-04-procedure-{i:012d}"
        _dp_card(tmp_path, mid, "procedure")
        rows.append({"id": mid, "bm25": -1.0})
    first = "mem-2026-08-05-decision-000000000001"
    second = "mem-2026-08-05-decision-000000000002"
    _dp_card(tmp_path, first, "decision")
    _dp_card(tmp_path, second, "decision")
    _guidance_rows(monkeypatch, rows)
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [])
    pending = [first, second]

    def _one(*_a, **_k):
        return pending.pop(0) if pending else ""

    monkeypatch.setattr("recall.tools.rerank_embed", _one)
    text = recall_memory("need two decisions", staging=tmp_path, mode="guidance")
    assert text.count("  decision  ") == 2
    assert first in text and second in text


def test_daily_guidance_rank_uses_live_strength(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "monthly"))
    fresh = "mem-2026-08-05-decision-000000000010"
    stale = "mem-2026-08-05-decision-000000000011"
    _dp_card(tmp_path, fresh, "decision", recall_n=6, last="2026-11-18")
    _dp_card(tmp_path, stale, "decision", recall_n=6, last="2026-01-02")
    _guidance_rows(
        monkeypatch,
        [{"id": fresh, "bm25": -1.0}, {"id": stale, "bm25": -1.0}],
    )
    text = recall_memory("same score", staging=tmp_path, mode="guidance")
    assert text.index(fresh) < text.index(stale)


def _unit_vec(cosine: float) -> list[float]:
    import math

    c = max(-1.0, min(1.0, cosine))
    return [c, math.sqrt(max(0.0, 1.0 - c * c))]


def test_daily_guidance_event_lifts_a_card_the_text_misses(tmp_path, monkeypatch):
    """The usage centroid lifts a card whose own text is orthogonal to the query.

    The toy cache is the whole vector set, so its mean is the signal. Freezing
    the cone keeps the planted axes as the R2 input.
    """
    near = "mem-2026-08-02-procedure-000000000001"
    far = "mem-2026-08-02-procedure-000000000002"
    filler = "mem-2026-08-02-procedure-000000000004"
    event = "mem-2026-08-09-event-000000000003"
    _dp_card(tmp_path, near, "procedure")
    _dp_card(tmp_path, far, "procedure")
    _dp_card(tmp_path, filler, "procedure")
    (tmp_path / "daily" / "2026-08-09.md").write_text(
        _block(
            mem_id=event,
            type_="event",
            entity="Guide",
            body="Beginning: the weekly file went out.",
            related=near,
        ),
        encoding="utf-8",
    )
    cache = {
        near: {"embedding": [0.0, 1.0]},
        far: {"embedding": [0.0, 1.0]},
        filler: {"embedding": [1.0, 0.0]},
        event: {"embedding": [1.0, 0.0]},
    }
    calls: list[list[str]] = []

    def _encode(texts):
        calls.append(list(texts))
        return [[1.0, 0.0]]

    def _boom(*_a, **_k):
        raise AssertionError("vector path must not search by keyword")

    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("recall.embed._encode_texts", _encode)
    monkeypatch.setattr("recall.embed_cache.load_cache", lambda: cache)
    monkeypatch.setattr("recall.embed_cache.mean_direction", lambda cache=None: None)
    monkeypatch.setattr("recall.tools.search_lexical", _boom)
    monkeypatch.setattr("recall.tools.rerank_embed", _boom)
    query = "zzzz event situation"
    text = recall_memory(query, staging=tmp_path, mode="guidance")
    assert "channel=daily_dp" in text
    assert near in text
    assert far not in text
    assert event not in text
    assert calls == [[query]]


def _day_view_id(mem_id: str, kind: str) -> str:
    from recall.embed_cache import scale_block_id

    return scale_block_id({"scale": "day", "kind": kind, "mem_id": mem_id})


def _slotted_procedure(root: Path, mem_id: str) -> None:
    daily = root / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (root / "weekly").mkdir(exist_ok=True)
    (root / "monthly").mkdir(exist_ok=True)
    (daily / f"{mem_id}.md").write_text(
        _block(
            mem_id=mem_id,
            type_="procedure",
            entity="Guide",
            body="Obstacle: WeChat has no file flag; Solution: send the chapter via MEDIA.",
        ),
        encoding="utf-8",
    )


def test_daily_guidance_solution_view_rescues_a_slotted_procedure(tmp_path, monkeypatch):
    """The solution vector admits a procedure whose full-card vector is orthogonal."""
    slotted = "mem-2026-08-02-procedure-000000000031"
    _slotted_procedure(tmp_path, slotted)
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [[1.0, 0.0]])
    monkeypatch.setattr("recall.embed_cache.mean_direction", lambda cache=None: None)
    monkeypatch.setattr(
        "recall.embed_cache.load_cache",
        lambda: {
            slotted: {"embedding": [0.0, 1.0]},
            _day_view_id(slotted, "obstacle"): {"embedding": [0.0, 1.0]},
            _day_view_id(slotted, "solution"): {"embedding": [1.0, 0.0]},
        },
    )
    text = recall_memory("zzzz media delivery", staging=tmp_path, mode="guidance")
    assert "channel=daily_dp" in text
    assert slotted in text
    assert "via=solution" in text


def test_daily_guidance_ignores_full_card_when_slots_miss(tmp_path, monkeypatch):
    """A slotted procedure whose views miss stays out even if the full card matches."""
    good = "mem-2026-08-02-procedure-000000000041"
    other = "mem-2026-08-02-procedure-000000000042"
    bad = "mem-2026-08-02-procedure-000000000043"
    _dp_card(tmp_path, good, "procedure")
    _dp_card(tmp_path, other, "procedure")
    _slotted_procedure(tmp_path, bad)
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [[1.0, 0.0]])
    monkeypatch.setattr("recall.embed_cache.mean_direction", lambda cache=None: None)
    monkeypatch.setattr(
        "recall.embed_cache.load_cache",
        lambda: {
            good: {"embedding": [1.0, 0.0]},
            other: {"embedding": [1.0, 0.0]},
            bad: {"embedding": [1.0, 0.0]},
            _day_view_id(bad, "obstacle"): {"embedding": [0.0, 1.0]},
            _day_view_id(bad, "solution"): {"embedding": [0.0, 1.0]},
        },
    )
    text = recall_memory("zzzz media delivery", staging=tmp_path, mode="guidance")
    assert good in text and other in text
    assert bad not in text


def test_daily_guidance_shared_event_orders_by_card_score(tmp_path, monkeypatch):
    """Two cards that share one centroid still sort by their own text score."""
    closer = "mem-2026-08-02-procedure-000000000011"
    farther = "mem-2026-08-02-procedure-000000000012"
    event = "mem-2026-08-09-event-000000000013"
    _dp_card(tmp_path, closer, "procedure")
    _dp_card(tmp_path, farther, "procedure")
    (tmp_path / "daily" / "2026-08-09.md").write_text(
        _block(
            mem_id=event,
            type_="event",
            entity="Guide",
            body="Beginning: both procedures share this episode.",
            extra=f"related: [{closer}, {farther}]\n",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [[1.0, 0.0]])
    monkeypatch.setattr("recall.embed_cache.mean_direction", lambda cache=None: None)
    monkeypatch.setattr(
        "recall.embed_cache.load_cache",
        lambda: {
            closer: {"embedding": _unit_vec(0.50)},
            farther: {"embedding": _unit_vec(0.20)},
            event: {"embedding": [1.0, 0.0]},
        },
    )
    text = recall_memory("zzzz event situation", staging=tmp_path, mode="guidance")
    assert text.index(closer) < text.index(farther)


def test_daily_guidance_keeps_card_when_only_the_event_clears_the_floor(tmp_path, monkeypatch):
    """A 0.20 card score stays under the shared cosine gate; the event centroid does not.

    The daily path uses ``_GUIDANCE_COS_GATE``, so a card the month path would
    reject cannot come back on a lower raw floor.
    """
    lifted = "mem-2026-08-02-procedure-000000000021"
    plain = "mem-2026-08-02-procedure-000000000022"
    filler = "mem-2026-08-02-procedure-000000000024"
    event = "mem-2026-08-09-event-000000000023"
    _dp_card(tmp_path, lifted, "procedure")
    _dp_card(tmp_path, plain, "procedure")
    _dp_card(tmp_path, filler, "procedure")
    (tmp_path / "daily" / "2026-08-09.md").write_text(
        _block(
            mem_id=event,
            type_="event",
            entity="Guide",
            body="Beginning: only one procedure is named.",
            related=lifted,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: True)
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [[1.0, 0.0]])
    monkeypatch.setattr("recall.embed_cache.mean_direction", lambda cache=None: None)
    monkeypatch.setattr(
        "recall.embed_cache.load_cache",
        lambda: {
            lifted: {"embedding": _unit_vec(0.20)},
            plain: {"embedding": _unit_vec(0.20)},
            filler: {"embedding": [1.0, 0.0]},
            event: {"embedding": [1.0, 0.0]},
        },
    )
    text = recall_memory("zzzz event situation", staging=tmp_path, mode="guidance")
    assert lifted in text
    assert plain not in text


def test_daily_guidance_without_a_vector_stays_on_keywords(tmp_path, monkeypatch):
    mid = "mem-2026-08-06-decision-000000000031"
    _dp_card(tmp_path, mid, "decision")
    seen: list[str] = []

    def _lexical(*_a, **_k):
        seen.append("lexical")
        return [{"id": mid, "bm25": -1.0}]

    def _rerank(*_a, **_k):
        seen.append("rerank")
        return ""

    monkeypatch.setattr("monthly_actions.rank_monthly_guidance", lambda *_a, **_k: [])
    monkeypatch.setattr("recall.tools.embed_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr("recall.tools.search_lexical", _lexical)
    monkeypatch.setattr("recall.tools.rerank_embed", _rerank)
    text = recall_memory("balance the kinds", staging=tmp_path, mode="guidance")
    assert mid in text
    assert seen == ["lexical"]


def test_daily_guidance_full_miss(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "monthly"))
    _guidance_rows(monkeypatch, [])
    text = recall_memory("nothing here", staging=tmp_path, mode="guidance")
    assert "channel=miss" in text


def test_recall_credits_every_hit_and_partial_neighbors(tmp_path, monkeypatch):
    """Hits get a full recall; a shown neighbor gets fractional credit only."""
    from datetime import date

    from recall.tools import _recall_then_expand

    monkeypatch.setenv("RECALL_PERSIST_IN_TEST", "1")
    hits = [
        "mem-2026-08-06-fact-000000000021",
        "mem-2026-08-06-fact-000000000022",
        "mem-2026-08-06-fact-000000000023",
    ]
    neighbor = "mem-2026-08-06-fact-000000000031"
    two_hop = "mem-2026-08-06-fact-000000000032"
    for mid in (*hits, neighbor, two_hop):
        extra = ""
        if mid == neighbor:
            extra = "recall_n: 1\nlast_recall_at: '2020-01-01'\nfirst_seen: '2020-01-01'\n"
        daily = tmp_path / "daily"
        daily.mkdir(parents=True, exist_ok=True)
        (daily / f"{mid[-6:]}.md").write_text(
            _block(mem_id=mid, type_="fact", entity="N", body="n", extra=extra),
            encoding="utf-8",
        )
    found = "\n".join(f"- {mid}  2026-08-06  fact  body" for mid in hits)
    blob = (
        "## Memory / expand  seed=x  depth=2\n"
        f"- π=0.20  {neighbor}  2026-08-06\n"
        f"- π=0.10  {two_hop}  2026-08-06\n"
        f"- π=0.10  {hits[0]}  2026-08-06\n"
    )
    monkeypatch.setattr("recall.tools.recall_memory", lambda *_a, **_k: found)
    monkeypatch.setattr("recall.tools.expand_memory", lambda *_a, **_k: blob)
    monkeypatch.setattr("recall.tools.load_edges", lambda *_a, **_k: [])

    def _adj(_edges, min_weight=0.5):
        del min_weight
        return {
            hits[0]: {
                neighbor: (1.0, "related", "x"),
                hits[1]: (1.0, "related", "x"),
            }
        }

    monkeypatch.setattr("recall.tools.adjacency", _adj)
    _recall_then_expand("anything", staging=tmp_path)
    from recall.ids import BlockIndex

    store = BlockIndex(tmp_path)
    today = date.today().isoformat()
    for mid in hits:
        parsed = store.get(mid).parsed
        assert float(parsed["recall_n"]) == 1.0
        assert str(parsed["last_recall_at"])[:10] == today
    neighbor_row = store.get(neighbor).parsed
    assert abs(float(neighbor_row["recall_n"]) - 1.25) < 1e-9
    assert str(neighbor_row["last_recall_at"])[:10] == "2020-01-01"
    assert "recall_n" not in store.get(two_hop).parsed


def test_ack_guidance_returns_miss_without_encoding(tmp_path, monkeypatch):
    """An acknowledgment must not encode, or a nod would inject whatever card is nearest."""
    calls: list = []
    monkeypatch.setattr(
        "recall.embed._encode_texts",
        lambda texts: calls.append(list(texts)) or [[1.0, 0.0]],
    )
    monkeypatch.setattr("recall.embed.embed_enabled", lambda staging=None: True)
    from recall.tools import recall_memory

    text = recall_memory("哈哈", staging=tmp_path, mode="guidance")
    assert "channel=miss" in text
    assert calls == []


def test_history_decision_matches_the_pile_stops():
    """The offline grid must stop where the live pile stops, or calibration would ship a looser rule."""
    from recall.probe_guidance_gate import _history_decision

    labels = {"right"}
    step0 = [("right", 0.95), ("other", 0.40)]
    kind, used = _history_decision([step0], labels, floor=0.85, margin=0.10, eps=0.15, gate=0.92, positive=True)
    assert kind == "step0" and used == 1

    missed = [[("right", 0.40), ("other", 0.20)], [("right", 0.90), ("other", 0.70)]]
    kind, used = _history_decision(missed, labels, floor=0.85, margin=0.10, eps=0.15, gate=0.92, positive=True)
    assert kind == "pile" and used == 2

    close = [[("right", 0.40), ("other", 0.20)], [("right", 0.90), ("other", 0.85)]]
    kind, used = _history_decision(close, labels, floor=0.85, margin=0.10, eps=0.15, gate=0.92, positive=True)
    assert kind == "miss" and used == 2

    wrong = [[("other", 0.95), ("right", 0.40)]]
    kind, used = _history_decision(wrong, labels, floor=0.85, margin=0.10, eps=0.15, gate=0.92, positive=True)
    assert kind == "miss" and used == 1

    flat = [
        [("right", 0.40), ("other", 0.39)],
        [("right", 0.40), ("other", 0.39)],
        [("right", 0.99), ("other", 0.10)],
    ]
    kind, used = _history_decision(flat, labels, floor=0.85, margin=0.10, eps=0.15, gate=0.92, positive=True)
    assert kind == "miss" and used == 2

