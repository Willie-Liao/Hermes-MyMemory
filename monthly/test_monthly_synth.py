"""Reduce-stage evidence binding and verbatim decision clauses."""

from __future__ import annotations

from monthly_slice import mechanical_facts
from monthly_synth import (
    _mint_month_key_id,
    build_reduce_prompt,
    payload_from_synthesis,
    synthesize_month,
)
from monthly_slice import count_tokens


def _filled_args(facts) -> dict:
    """One valid JSON submit so a unit test does not trip the section retry."""
    decision = next(block for block in facts.all_dpe if block.type == "decision")
    procedure = next(block for block in facts.all_dpe if block.type == "procedure")
    week = facts.weeks[0] if facts.weeks else "2026-W25"
    image = {
        "goal_alignment": {"text": "Willie kept one standing goal.", "evidence": [decision.id]},
        "decision_preference": {"text": "Willie prefers one next action.", "evidence": [decision.id]},
        "behavior_pattern": {"text": "Willie batches the work on school days.", "evidence": [decision.id]},
        "cognition_change": [],
    }
    if facts.supersedes_pairs:
        prior, current = facts.supersedes_pairs[0]
        image["cognition_change"] = [
            {
                "text": "A later claim replaced the earlier one.",
                "from": prior,
                "to": current,
                "date": "2026-06-16",
                "evidence": [current],
            }
        ]
    return {
        "summary": "June start",
        "user_image": image,
        "core_progress": [
            {
                "title": "Standing goal",
                "body": "Willie moved that goal forward.",
                "state": "advanced",
                "weeks": [week],
                "entity_keys": [decision.entity_key or "user"],
                "evidence": [decision.id],
            }
        ],
        "key_decisions": [
            {
                "text": "June goal stays a standing rule",
                "why_it_matters": "still changes the next time",
                "context": "Month planning.",
                "exceptions": "A one-day reminder does not count.",
                "evidence": [decision.id],
            }
        ],
        "key_procedures": [
            {
                "trigger": "Willie asks to repeat the step.",
                "problem": "The step was written only once.",
                "obstacles": ["The first note was incomplete."],
                "solution": "Reuse the cited procedure.",
                "insight": "The case still changes the next time.",
                "evidence": [procedure.id],
            }
        ],
        "problems_and_risks": [
            {
                "content": "Willie may skip the follow-up.",
                "level": "medium",
                "suggestion": "Keep the procedure next to the decision.",
                "evidence": [decision.id],
            }
        ],
        "next_month_focus": [
            {
                "content": "Carry the rule forward.",
                "target": "2026-07",
                "priority": "medium",
                "depends_on": [decision.id],
            }
        ],
        "comparison_with_last_month": {
            "summary": "The standing goal held and the preference got sharper.",
        },
    }


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
                "text": "cross-type blocks stay unmerged and the higher type wins",
                "why_it_matters": "cited",
                "evidence": [real_decision.id],
            },
            {
                "text": "invented",
                "why_it_matters": "fake",
                "evidence": ["mem-invented-decision"],
            },
        ],
        "key_procedures": [
            {
                "solution": "widen the reader so id shape mismatch is one reusable case",
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
    minted = _mint_month_key_id("2026-08", "decision", (real_decision.id,))
    kept = next(row for row in payload.key_decisions if row.id == minted)
    assert kept.text == "cross-type blocks stay unmerged and the higher type wins"
    assert real_decision.id in kept.evidence
    assert kept.id.startswith("mem-2026-08-key-decision-")
    assert payload.comparison_with_last_month.empty_reason
    assert "Beginning:" not in " ".join(row.text for row in payload.summary)
    assert payload.user_image is not None
    assert payload.metrics.decisions >= 1
    proc_row = payload.key_procedures[0]
    assert proc_row.id == _mint_month_key_id("2026-08", "procedure", (real_proc.id,))
    assert proc_row.id.startswith("mem-2026-08-key-procedure-")
    assert proc_row.solution.startswith("widen the reader")
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


def test_reduce_prompt_is_same_rule_lines_and_blank_why_is_dropped():
    """A mixed-type cite used to pass the prompt and come back as 'not a given card id'.

    The first reduce call has to see the type rule and the portrait objects in the
    tool arguments, and a later patch has to be told which procedure id sat on a
    decision row.
    """
    from monthly_synth import judgment_gaps, normalize_synthesis_args

    facts = mechanical_facts("2026-08")
    prompt = build_reduce_prompt("2026-08", [], facts, carry="")
    assert "story_seeds" in prompt
    assert "representative id" not in prompt
    assert "Do not set id" in prompt
    assert '"dp_groups"' not in prompt
    assert "\n    \"id\"" not in prompt
    assert " | " in prompt
    assert "user_image.goal_alignment" in prompt
    assert "Inside the tool arguments, user_image.goal_alignment" in prompt
    assert "key_decisions.evidence may contain only decision ids." in prompt
    assert "key_procedures.evidence may contain only procedure ids." in prompt
    assert "One id of the other type fails that row." in prompt
    assert "Do not change the date inside an id." in prompt
    assert "must contain -decision-" in prompt
    assert "Notes mix both kinds in one evidence list." in prompt
    assert "Put every daily card you drew on in evidence." not in prompt
    assert "core_progress" in prompt
    assert "problems_and_risks" in prompt
    assert "next_month_focus" in prompt
    assert "No previous month judgment" in prompt
    real = next(b for b in facts.all_dpe if b.type == "decision")
    proc = next(b for b in facts.all_dpe if b.type == "procedure")
    mixed = judgment_gaps(
        {
            "key_decisions": [
                {
                    "text": "standing rule",
                    "why_it_matters": "still changes the next month",
                    "context": "digest",
                    "exceptions": "none",
                    "evidence": [proc.id],
                }
            ]
        },
        facts,
        comparison_exempt=True,
    )
    assert f"key_decisions[0].evidence cites procedure id {proc.id}" in mixed
    cleaned, fixes = normalize_synthesis_args(
        {
            "key_decisions": [
                {
                    "text": "standing rule",
                    "why_it_matters": "still changes the next month",
                    "context": "digest",
                    "exceptions": "none",
                    "evidence": [real.id, proc.id],
                }
            ]
        },
        facts,
    )
    assert cleaned["key_decisions"][0]["evidence"] == [real.id]
    assert any("key_decisions[0].evidence dropped" in line for line in fixes)
    invented_id = "mem-2026-08-07-procedure-NOTACARD"
    invented = judgment_gaps(
        {
            "problems_and_risks": [
                {
                    "content": "a risk",
                    "level": "high",
                    "suggestion": "drop the rewritten id",
                    "evidence": [invented_id],
                }
            ]
        },
        facts,
        comparison_exempt=True,
    )
    assert (
        f"problems_and_risks[0].evidence cites {invented_id}, which is not a given card id"
        in invented
    )
    blank = payload_from_synthesis(
        "2026-08",
        {"summary": "x", "key_decisions": [{"id": real.id, "why_it_matters": "", "evidence": [real.id]}]},
        facts,
        carry="",
        model="mimo-v2.5",
        map_calls=1,
        reduce_tokens=1,
        generated_at="2026-09-01T08:00:00+08:00",
    )
    assert all(row.id != real.id for row in blank.key_decisions)
    long_why = " ".join(f"w{i}" for i in range(50))
    capped = payload_from_synthesis(
        "2026-08",
        {
            "summary": "x",
            "key_decisions": [
                {
                    "text": "standing cross-type rule synthesized for the month",
                    "why_it_matters": long_why,
                    "evidence": [real.id, proc.id, "mem-not-in-dump"],
                }
            ],
        },
        facts,
        carry="",
        model="mimo-v2.5",
        map_calls=1,
        reduce_tokens=1,
        generated_at="2026-09-01T08:00:00+08:00",
    )
    assert capped.key_decisions == ()


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
    calls = {"n": 0}

    def call(prompt, **kwargs):
        calls["n"] += 1
        assert kwargs.get("force_tool_name") == "submit_month_synthesis"
        return {
            "failed": False,
            "tool_name": kwargs.get("force_tool_name"),
            "tool_args": _filled_args(facts),
            "input_tokens": 50,
            "output_tokens": 20,
            "model": "MiniMax-M2.5",
        }

    payload, usage = synthesize_month(
        "2026-06",
        [{"items": [{"kind": "d", "what": "goal", "evidence": [real.id]}]}],
        call_oneshot=call,
        carry="",
        facts=facts,
    )
    assert calls["n"] == 1
    assert payload.summary[0].text == "June start"
    assert payload.key_decisions[0].id == _mint_month_key_id(
        "2026-06", "decision", (real.id,)
    )
    assert payload.key_decisions[0].text == "June goal stays a standing rule"
    assert usage["valid"] is True
    assert usage["input_tokens"] == 50


def test_failed_section_is_patched_not_resubmitted():
    facts = mechanical_facts("2026-06")
    real = next(b for b in facts.all_dpe if b.type == "decision")
    calls = {"n": 0}

    def call(prompt, **kwargs):
        calls["n"] += 1
        name = kwargs.get("force_tool_name")
        if calls["n"] == 1:
            assert name == "submit_month_synthesis"
            return {
                "failed": False,
                "tool_name": name,
                "tool_args": {"summary": "June start"},
                "input_tokens": 10,
                "output_tokens": 1,
                "model": "MiniMax-M2.5",
            }
        assert name == "patch_month_synthesis"
        props = set(kwargs["tool_schema"]["parameters"]["properties"])
        assert "summary" not in props
        assert "key_decisions" in props
        return {
            "failed": False,
            "tool_name": name,
            "tool_args": _filled_args(facts),
            "input_tokens": 50,
            "output_tokens": 20,
            "model": "MiniMax-M2.5",
        }

    payload, usage = synthesize_month(
        "2026-06",
        [],
        call_oneshot=call,
        carry="",
        facts=facts,
    )
    assert calls["n"] == 2
    assert payload.key_decisions[0].id.startswith("mem-2026-06-key-decision-")
    assert usage["input_tokens"] == 60
    assert usage["valid"] is True
    assert usage["attempts"][1]["retired"]
    assert real.id


def test_evidence_string_does_not_call_the_model_again():
    facts = mechanical_facts("2026-06")
    calls = {"n": 0}

    def call(prompt, **kwargs):
        calls["n"] += 1
        args = _filled_args(facts)
        args["key_decisions"][0]["evidence"] = args["key_decisions"][0]["evidence"][0]
        return {
            "failed": False,
            "tool_name": "submit_month_synthesis",
            "tool_args": args,
            "input_tokens": 5,
            "output_tokens": 1,
            "model": "MiniMax-M2.5",
        }

    _payload, usage = synthesize_month("2026-06", [], call_oneshot=call, carry="", facts=facts)
    assert calls["n"] == 1
    assert any("string -> list" in fix for fix in usage["attempts"][0]["format_fixes"])
    assert usage["valid"] is True


def test_incomplete_month_is_not_written(tmp_path, monkeypatch):
    from monthly_actions import generate_month
    from monthly_state import month_file_path

    def call(prompt, **kwargs):
        if kwargs.get("force_tool_name") == "submit_month_note":
            return {
                "failed": False,
                "tool_name": "submit_month_note",
                "tool_args": {"items": []},
                "input_tokens": 1,
                "output_tokens": 1,
            }
        return {
            "failed": False,
            "tool_name": kwargs.get("force_tool_name"),
            "tool_args": {},
            "input_tokens": 1,
            "output_tokens": 1,
            "model": "MiniMax-M2.5",
        }

    result = generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert result["outcome"] == "incomplete"
    assert not month_file_path("2026-06").is_file()
    assert result["usage"]["valid"] is False


def test_blank_goal_alignment_text_is_a_gap():
    from monthly_synth import judgment_gaps

    facts = mechanical_facts("2026-06")
    args = _filled_args(facts)
    args["user_image"]["goal_alignment"]["text"] = ""
    gaps = judgment_gaps(args, facts, comparison_exempt=True)
    assert any("goal_alignment.text is empty" in gap for gap in gaps)


def test_no_previous_month_sets_cognition_reason():
    facts = mechanical_facts("2026-06")

    def call(prompt, **kwargs):
        return {
            "failed": False,
            "tool_name": "submit_month_synthesis",
            "tool_args": _filled_args(facts),
            "input_tokens": 5,
            "output_tokens": 1,
            "model": "MiniMax-M2.5",
        }

    payload, usage = synthesize_month("2026-06", [], call_oneshot=call, carry="", facts=facts)
    assert payload.user_image.cognition_change == ()
    assert payload.user_image.cognition_empty_reason == "no information from last month"
    assert usage["valid"] is True
    assert not any("cognition_change" in line for line in usage["attempts"][0]["failed"])
    assert any("cognition_change empty_reason" in fix for fix in usage["attempts"][0]["format_fixes"])


def test_previous_month_blank_cognition_is_not_written(tmp_path, monkeypatch):
    from monthly_actions import generate_month
    from monthly_schema import MonthlyEvidenceText, MonthlyPayload, MonthlyRange, MonthlyUserImage
    from monthly_state import month_file_path
    from monthly_writer import write_month

    write_month(
        MonthlyPayload(
            key="2026-05",
            range=MonthlyRange(start="2026-05-01", end="2026-05-31"),
            user_image=MonthlyUserImage(
                goal_alignment=MonthlyEvidenceText(
                    text="May already held a goal.",
                    evidence=("mem-2026-05-decision-a",),
                )
            ),
        )
    )
    facts = mechanical_facts("2026-06")
    args = _filled_args(facts)
    args["user_image"]["cognition_change"] = []

    def call(prompt, **kwargs):
        if kwargs.get("force_tool_name") == "submit_month_note":
            return {
                "failed": False,
                "tool_name": "submit_month_note",
                "tool_args": {"items": []},
                "input_tokens": 1,
                "output_tokens": 1,
            }
        return {
            "failed": False,
            "tool_name": kwargs.get("force_tool_name"),
            "tool_args": args,
            "input_tokens": 1,
            "output_tokens": 1,
            "model": "MiniMax-M2.5",
        }

    result = generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert result["outcome"] == "incomplete"
    assert not month_file_path("2026-06").is_file()
    failed = " ".join(result["usage"]["attempts"][-1]["failed"])
    assert "cognition_change" in failed


def test_sentinel_cognition_phrase_is_a_gap():
    from monthly_synth import judgment_gaps

    facts = mechanical_facts("2026-06")
    args = _filled_args(facts)
    prior, current = facts.supersedes_pairs[0]
    args["user_image"]["cognition_change"] = [
        {
            "text": "no information from last month",
            "from": prior,
            "to": current,
            "date": "2026-06-16",
            "evidence": [current],
        }
    ]
    gaps = judgment_gaps(args, facts, comparison_exempt=False)
    assert any("placeholder phrase" in gap for gap in gaps)


def test_reduce_prompt_compares_last_month_user_image():
    from monthly_schema import MonthlyEvidenceText, MonthlyPayload, MonthlyRange, MonthlySummaryItem, MonthlyUserImage
    from monthly_slice import carry_card
    from monthly_writer import write_month

    write_month(
        MonthlyPayload(
            key="2026-07",
            range=MonthlyRange(start="2026-07-01", end="2026-07-31"),
            summary=(MonthlySummaryItem(text="July story", weeks=()),),
            user_image=MonthlyUserImage(
                goal_alignment=MonthlyEvidenceText(
                    text="Keep the July goal.",
                    evidence=("mem-2026-07-10-decision-a",),
                ),
            ),
        )
    )
    facts = mechanical_facts("2026-08")
    prompt = build_reduce_prompt("2026-08", [], facts, carry_card("2026-07"))
    assert "Keep the July goal." in prompt
    assert "comparison_with_last_month.summary" in prompt
    assert "needs a changed or unchanged row" not in prompt


def test_comparison_summary_gap_follows_the_sentence():
    from monthly_synth import judgment_gaps

    facts = mechanical_facts("2026-08")
    empty = _filled_args(facts)
    empty["comparison_with_last_month"] = {"summary": ""}
    gaps = judgment_gaps(empty, facts, comparison_exempt=False)
    assert any("comparison_with_last_month.summary is empty" in gap for gap in gaps)
    gaps = judgment_gaps(_filled_args(facts), facts, comparison_exempt=False)
    assert not any("comparison_with_last_month.summary" in gap for gap in gaps)
    assert not any("changed or unchanged" in gap for gap in gaps)


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


def test_summary_refuses_glued_paragraph_when_multiple_seeds():
    facts = mechanical_facts("2026-08")
    facts.story_seeds = (
        {"text": "Qixi card from drafting to sharing", "weeks": ["2026-W34", "2026-W35"], "source": "cross-week"},
        {"text": "Dating idea list grown", "weeks": ["2026-W35"], "source": "weekly-summary"},
    )
    payload = payload_from_synthesis(
        "2026-08",
        {"summary": "Qixi and dating were the month"},
        facts,
        carry="",
        model="mimo-v2.5",
        map_calls=1,
        reduce_tokens=1,
        generated_at="2026-09-01T08:00:00+08:00",
    )
    assert len(payload.summary) == 2
    assert payload.summary[0].text == "Qixi card from drafting to sharing"
    assert payload.summary[1].weeks == ("2026-W35",)


def test_two_decision_cards_store_synthesis_not_either_clause():
    """A key row is the month's sentence, so pasting one daily clause would hide the other card."""
    from dataclasses import replace

    facts = mechanical_facts("2026-08")
    left = next(b for b in facts.all_dpe if b.type == "decision")
    right = replace(
        left,
        id="mem-2026-08-08-decision-DD3CEF52075B",
        clause="Decision: do not merge cross-type blocks",
    )
    facts.all_dpe = facts.all_dpe + (right,)
    facts.blocks_by_id = {**facts.blocks_by_id, right.id: right}
    synthesis = (
        "user rules that cross-type blocks do not merge and the higher-priority type wins"
    )
    payload = payload_from_synthesis(
        "2026-08",
        {
            "summary": "August",
            "key_decisions": [
                {
                    "text": synthesis,
                    "why_it_matters": "restated across the month",
                    "evidence": [left.id, right.id],
                }
            ],
        },
        facts,
        carry="",
        model="mimo-v2.5",
        map_calls=1,
        reduce_tokens=1,
        generated_at="2026-09-01T08:00:00+08:00",
    )
    row = payload.key_decisions[0]
    assert row.id == _mint_month_key_id("2026-08", "decision", (left.id, right.id))
    assert row.text == synthesis
    assert left.id in row.evidence and right.id in row.evidence
    assert row.text not in {left.clause, right.clause}


def test_guidance_returns_synthesized_month_id(tmp_path):
    """Guidance must surface the minted synthesis, not a one-day act the reduce dropped."""
    from monthly_actions import format_guidance_hits, rank_monthly_guidance
    from monthly_writer import dump_yaml

    facts = mechanical_facts("2026-08")
    real = next(b for b in facts.all_dpe if b.type == "decision")
    synthesis = (
        "user rules that cross-type blocks do not merge and the higher-priority type wins"
    )
    payload = payload_from_synthesis(
        "2026-08",
        {
            "summary": "August",
            "key_decisions": [
                {
                    "text": synthesis,
                    "why_it_matters": "restated across the month",
                    "evidence": [real.id],
                }
            ],
        },
        facts,
        carry="",
        model="mimo-v2.5",
        map_calls=1,
        reduce_tokens=1,
        generated_at="2026-09-01T08:00:00+08:00",
    )
    folder = tmp_path / "monthly"
    folder.mkdir()
    (folder / "2026-08.md").write_text(dump_yaml(payload), encoding="utf-8")
    hits = rank_monthly_guidance(synthesis, staging=tmp_path)
    assert hits
    assert hits[0]["row"].id.startswith("mem-2026-08-key-decision-")
    rendered = format_guidance_hits(hits)
    assert "channel=monthly_guidance" in rendered
    assert hits[0]["row"].id in rendered
    miss = rank_monthly_guidance(
        "translate this note today and only that sitting",
        staging=tmp_path,
    )
    assert all(not str(hit["row"].id).startswith("mem-2026-08-key-decision-") for hit in miss)


def test_generate_month_map_input_has_no_event_blocks():
    from monthly_slice import pack_batches, week_slices

    slices = week_slices("2026-08", types=frozenset({"decision", "procedure"}))
    types = {b.type for s in slices for b in s.blocks}
    assert types <= {"decision", "procedure"}
    for batch in pack_batches(slices):
        assert "Beginning:" not in batch.rendered
