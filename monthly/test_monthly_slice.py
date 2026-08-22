"""Corpus baseline and week-slice packing — zero LLM."""

from __future__ import annotations

from collections import Counter

import yaml
from monthly_slice import (
    MAP_BATCH_TOKENS,
    count_tokens,
    iter_daily_files,
    load_all_blocks,
    pack_batches,
    parse_blocks,
    week_slices,
    weekly_dir,
)
from weekly_event_workers import parse_blocks as weekly_parse_blocks
from weekly_json import load_sidecar


def _clause(body: str) -> str:
    return body.split("## Day wrap-up", 1)[0].strip()


def test_corpus_baseline():
    """Freeze the measured facts this pipeline prices against the live tree."""
    rows = load_all_blocks()
    by_month: dict[str, list] = {}
    type_counts = Counter()
    proc_both = 0
    proc_n = 0
    supersedes = 0
    for day, fm, body in rows:
        kind = str(fm.get("type") or "")
        type_counts[kind] += 1
        by_month.setdefault(day.strftime("%Y-%m"), []).append((day, fm, body))
        if kind == "procedure":
            proc_n += 1
            text = _clause(body)
            if "Obstacle:" in text and "Solution:" in text:
                proc_both += 1
        if fm.get("supersedes"):
            raw = fm.get("supersedes")
            if raw not in (None, "", [], ()):
                supersedes += 1
    decisions = {
        month: sum(1 for _d, fm, _b in items if fm.get("type") == "decision")
        for month, items in by_month.items()
    }
    assert decisions.get("2026-06") == 3
    assert decisions.get("2026-07") == 2
    assert decisions.get("2026-08") == 1
    assert sum(decisions.values()) == 6
    assert proc_n == 3
    assert proc_both == 3
    assert supersedes == 1

    clause_tokens = {}
    for month, items in by_month.items():
        text = "\n".join(
            f"{fm.get('id')} | {_clause(body)}"
            for _d, fm, body in items
            if fm.get("type") in {"decision", "procedure", "event"}
        )
        clause_tokens[month] = count_tokens(text)
    assert clause_tokens["2026-06"] > 0
    assert clause_tokens["2026-08"] > 0

    slices = week_slices("2026-08")
    by_week = {row.week_key: row.tokens for row in slices}
    assert set(by_week) >= {"2026-W31", "2026-W32", "2026-W33", "2026-W34"}

    entity_months: dict[str, set[str]] = {}
    from weekly_json import normalize_entity_key

    for day, fm, _body in rows:
        entity = str(fm.get("entity") or "").strip()
        if not entity:
            continue
        key = normalize_entity_key(entity)
        entity_months.setdefault(key, set()).add(day.strftime("%Y-%m"))
    cross = [k for k, months in entity_months.items() if len(months) >= 2]
    assert "gitnexus" in cross

    empty_threads = 0
    entity_share = []
    for path in sorted(weekly_dir().glob("*.md")):
        obj = load_sidecar(path)
        assert obj.get("cross-day-thread") == []
        empty_threads += 1
        dumped = yaml.safe_dump(obj.get("entities") or [], allow_unicode=True)
        total = count_tokens(path.read_text(encoding="utf-8"))
        entity_share.append(count_tokens(dumped) / total if total else 0)
    assert empty_threads >= 4
    assert max(entity_share) >= 0.20


def test_august_entity_week_spans():
    from monthly_slice import mechanical_facts

    facts = mechanical_facts("2026-08")
    by_key = {row["key"]: row for row in facts.cross_month_entities}
    assert "gitnexus" in by_key
    assert "memorydigest" in by_key
    assert "2026-W32" in by_key["gitnexus"]["weeks"]
    assert "2026-W33" in by_key["memorydigest"]["weeks"]
    assert "entity_weeks:" in facts.rendered()


def test_parse_blocks_alias_matches_weekly():
    sample = next(iter(iter_daily_files())).read_text(encoding="utf-8")
    assert parse_blocks is weekly_parse_blocks or parse_blocks(sample) == weekly_parse_blocks(sample)


def test_august_batches_under_cap():
    slices = week_slices("2026-08")
    batches = pack_batches(slices)
    assert len(slices) == 4
    assert len(batches) >= 1
    assert all(batch.tokens <= MAP_BATCH_TOKENS for batch in batches)
    ids = [block.id for batch in batches for block in (b for s in batch.slices for b in s.blocks)]
    assert len(ids) == len(set(ids))
    union = {block.id for s in slices for block in s.blocks}
    packed = {block.id for batch in batches for s in batch.slices for block in s.blocks}
    assert union == packed
    june = pack_batches(week_slices("2026-06"))
    assert len(june) == 1


def test_degree_orders_top_decision_first():
    slices = week_slices("2026-08")
    target = "mem-2026-08-15-decision-55091D2900CD"
    for slice_ in slices:
        ids = [b.id for b in slice_.blocks]
        if target in ids:
            assert ids[0] == target
            break
    else:
        raise AssertionError("expected decision not in August slices")


def test_facts_absent_from_batches():
    for batch in pack_batches(week_slices("2026-08")):
        assert "type: fact" not in batch.rendered
        types = {b.type for s in batch.slices for b in s.blocks}
        assert "fact" not in types


def test_load_all_blocks_skips_rejected_status():
    from pathlib import Path
    import os

    home = Path(os.environ["HERMES_HOME"])
    daily = home / "memories" / "staging" / "daily"
    rejected_id = "mem-2026-08-22-fact-eeeeeeeeeeee"
    daily.joinpath("2026-08-22.md").write_text(
        "---\n"
        f"id: {rejected_id}\n"
        "type: decision\n"
        "entity: Canteen\n"
        "confidence: high\n"
        "status: rejected\n"
        "sources: [session s-fake]\n"
        "---\n"
        "Decision: user wants the old canteen hours.\n",
        encoding="utf-8",
    )
    ids = {str(fm.get("id")) for _day, fm, _body in load_all_blocks()}
    assert rejected_id not in ids


def test_batch_composition_deterministic_and_carry_under_budget():
    from monthly_schema import CARRY_CARD_TOKEN_CAP
    from monthly_slice import carry_card

    first = pack_batches(week_slices("2026-08"))
    second = pack_batches(week_slices("2026-08"))
    assert [b.source_sha256 for b in first] == [b.source_sha256 for b in second]
    assert [frozenset(b.ids) for b in first] == [frozenset(b.ids) for b in second]
    assert count_tokens(carry_card("2026-07")) <= CARRY_CARD_TOKEN_CAP
    assert count_tokens(carry_card("2026-05")) <= CARRY_CARD_TOKEN_CAP
