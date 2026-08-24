"""Reduce-stage evidence binding and verbatim decision clauses."""

from __future__ import annotations

from monthly_slice import mechanical_facts
from monthly_synth import build_reduce_prompt, payload_from_synthesis, synthesize_month
from monthly_slice import count_tokens


def test_reduce_drops_invented_ids_and_keeps_verbatim():
    facts = mechanical_facts("2026-08")
    real_decision = next(b for b in facts.all_dpe if b.type == "decision")
    real_proc = next(b for b in facts.all_dpe if b.type == "procedure")
    args = {
        "summary": "August tooling",
        "user_image": {
            "goal_alignment": {
                "text": "goal paused",
                "evidence": [real_decision.id],
            }
        },
        "key_decisions": [
            {
                "id": real_decision.id,
                "why_it_matters": "cited",
                "evidence": [real_decision.id],
            },
            {
                "id": "mem-invented-decision",
                "why_it_matters": "fake",
                "evidence": ["mem-invented-decision"],
            },
        ],
        "key_procedures": [
            {
                "id": real_proc.id,
                "problem": "id shape mismatch",
                "insight": "widen the reader",
                "evidence": [real_proc.id],
            }
        ],
        "core_progress": [
            {
                "id": "cp-1",
                "title": "pipeline",
                "body": "retries",
                "evidence": [real_decision.id],
            }
        ],
    }
    payload = payload_from_synthesis(
        "2026-08",
        args,
        facts,
        carry="",
        model="mimo-v2.5",
        map_calls=2,
        reduce_tokens=100,
        generated_at="2026-09-01T08:00:00+08:00",
    )
    assert all(row.id != "mem-invented-decision" for row in payload.key_decisions)
    kept = next(row for row in payload.key_decisions if row.id == real_decision.id)
    assert kept.text
    assert "Decision:" not in kept.text or kept.text in real_decision.clause
    assert payload.comparison_with_last_month.empty_reason
    assert "Beginning:" not in payload.summary
    assert payload.user_image is not None
    assert payload.metrics.decisions >= 1
    clause = real_decision.clause
    if clause.startswith("Decision:"):
        clause = clause[len("Decision:") :].strip()
    elif clause.startswith("Preference:"):
        clause = clause[len("Preference:") :].strip()
    assert kept.text == clause
    gitnexus = next(row for row in payload.entities if row.key == "gitnexus")
    assert gitnexus.weeks == ("2026-W32",)


def test_cognition_change_requires_supersedes_pair():
    facts = mechanical_facts("2026-08")
    real = next(b for b in facts.all_dpe if b.type == "decision")
    args = {
        "summary": "x",
        "user_image": {
            "cognition_change": [
                {
                    "text": "invented reversal",
                    "from": "mem-nope-a",
                    "to": "mem-nope-b",
                    "date": "2026-08-15",
                    "evidence": [real.id],
                }
            ]
        },
    }
    payload = payload_from_synthesis(
        "2026-08",
        args,
        facts,
        carry="",
        model="mimo-v2.5",
        map_calls=1,
        reduce_tokens=10,
        generated_at="2026-09-01T08:00:00+08:00",
    )
    assert payload.user_image.cognition_change == ()


def test_august_reduce_prompt_under_4000():
    facts = mechanical_facts("2026-08")
    notes = [
        {"kind": "decision", "what": "x", "evidence": [facts.all_dpe[0].id]}
        for _ in range(12)
    ]
    prompt = build_reduce_prompt("2026-08", notes, facts, carry="")
    # Prefix + notes + mechanical facts; live August notes were ~3.4K by design.
    assert count_tokens(prompt) < 8000


def test_synthesize_month_forced_tool(monkeypatch):
    facts = mechanical_facts("2026-06")
    real = next(b for b in facts.all_dpe if b.type == "decision")

    def call(prompt, **kwargs):
        return {
            "failed": False,
            "tool_name": kwargs.get("force_tool_name"),
            "tool_args": {
                "summary": "June start",
                "key_decisions": [
                    {"id": real.id, "why_it_matters": "goal", "evidence": [real.id]}
                ],
            },
            "input_tokens": 50,
            "output_tokens": 20,
            "model": "mimo-v2.5",
        }

    payload, usage = synthesize_month(
        "2026-06",
        [{"items": [{"kind": "d", "what": "goal", "evidence": [real.id]}]}],
        call_oneshot=call,
        carry="",
        facts=facts,
    )
    assert payload.summary == "June start"
    assert payload.key_decisions[0].id == real.id
    assert usage["input_tokens"] == 50


def test_payload_from_synthesis_passes_bilingual_aliases():
    facts = mechanical_facts("2026-08")
    patched = [
        {**row, "aliases": ("记忆摘要",)} if row["key"] == "memorydigest" else dict(row)
        for row in facts.cross_month_entities
    ]
    if not any(row["key"] == "memorydigest" for row in patched):
        patched.append(
            {
                "key": "memorydigest",
                "canonical": "Memory Digest",
                "months": ("2026-07", "2026-08"),
                "weeks": ("2026-W35",),
                "month_count": 1,
                "first_seen": "2026-07-27",
                "last_seen": "2026-08-24",
                "aliases": ("记忆摘要",),
            }
        )
    facts.cross_month_entities = tuple(patched)
    payload = payload_from_synthesis(
        "2026-08",
        {"summary": "alias check"},
        facts,
        carry="",
        model="mimo-v2.5",
        map_calls=1,
        reduce_tokens=1,
        generated_at="2026-09-01T08:00:00+08:00",
    )
    digest = next(row for row in payload.entities if row.key == "memorydigest")
    assert digest.canonical
    assert "记忆摘要" in digest.aliases
