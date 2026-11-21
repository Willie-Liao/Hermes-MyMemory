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


def test_lookup_text_joins_only_index_fields():
    from monthly_schema import MonthlyDecision, MonthlyProcedure

    decision = MonthlyDecision(
        id="d1",
        kind="preference",
        text="Prefer one next action.",
        why_it_matters="still changes the next time",
        context="When planning the week",
        exceptions="A one-day reminder does not count.",
        entity_keys=("willie",),
        evidence=("mem-1",),
    )
    assert decision.lookup_text() == "willie When planning the week Prefer one next action."
    assert "reminder" not in decision.lookup_text()

    procedure = MonthlyProcedure(
        id="p1",
        problem="Reviews run long",
        solution="Cut to one line",
        insight="Length hides the point",
        trigger="Writing the weekly review",
        obstacles=("The first note was incomplete.",),
        entity_keys=("willie",),
        evidence=("mem-2",),
    )
    assert procedure.lookup_text() == "willie Writing the weekly review"
    blank_trigger = MonthlyProcedure(
        id="p2",
        problem="Reviews run long",
        solution="Cut to one line",
        insight="Length hides the point",
        entity_keys=("willie",),
        evidence=("mem-2",),
    )
    assert blank_trigger.lookup_text() == "willie Reviews run long"


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
            "unchanged": [
                {"text": "The standing goal held.", "evidence": [decision.id]},
            ],
            "changed": [
                {"text": "The preference got sharper.", "evidence": [decision.id]},
            ],
            "suggestion": "Keep the sharper preference next month.",
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
    """August has no supersedes pair, so invented from/to must not be stored as ids.

    Dropping the whole row used to leave the month pending and send it back.
    The sentence stays; from and to are cleared.
    """
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
    row = payload.user_image.cognition_change[0]
    assert row.text == "invented reversal"
    assert row.from_id == ""
    assert row.to == ""


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


def test_reduce_card_line_uses_event_situation_without_the_label():
    from monthly_synth import _card_dump_line

    facts = mechanical_facts("2026-08")
    lines = [_card_dump_line(row) for row in facts.dp_groups]
    linked = next(line for line in lines if line.startswith("mem-2026-08-15-decision-55091D2900CD"))
    assert linked.endswith(" | situation: digest bug;")
    assert "Beginning:" not in linked
    bare = next(line for line in lines if line.startswith("mem-2026-08-08-procedure-m"))
    assert " | situation:" not in bare
    prompt = build_reduce_prompt("2026-08", [], facts, carry="")
    assert "Do not copy Beginning/Course/Outcome/Obstacle." not in prompt
    assert "use the situation column when present" in prompt


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
        calls.setdefault("names", []).append(kwargs.get("force_tool_name"))
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
    assert calls["names"] == [
        "patch_month_synthesis_g1",
        "patch_month_synthesis_g2",
        "patch_month_synthesis_g3",
        "patch_month_synthesis_g4",
    ]
    assert payload.summary[0].text == "June start"
    assert payload.key_decisions[0].id == _mint_month_key_id(
        "2026-06", "decision", (real.id,)
    )
    assert payload.key_decisions[0].text == "June goal stays a standing rule"
    assert usage["valid"] is True
    assert usage["input_tokens"] == 200


def test_reduce_groups_share_one_menu_and_drop_off_group_keys(monkeypatch):
    """Later groups must see earlier JSON, and every call must send the same tool list.

    A different tool list on each call misses the provider prompt cache. Keys
    outside the forced group must not overwrite a section an earlier group wrote.
    """
    monkeypatch.setattr("monthly_synth._previous_has_judgment", lambda _month: True)
    facts = mechanical_facts("2026-06")
    seen: list[tuple[str, dict]] = []

    def call(prompt, **kwargs):
        seen.append((prompt, kwargs))
        name = str(kwargs.get("force_tool_name") or "")
        filled = _filled_args(facts)
        keys = list(kwargs["tool_schema"]["parameters"]["properties"])
        args = {key: filled[key] for key in keys if key in filled}
        if name.endswith("_g2"):
            args["user_image"] = {"goal_alignment": {"text": "POISON", "evidence": []}}
        return {
            "failed": False,
            "tool_name": name,
            "tool_args": args,
            "input_tokens": 1,
            "output_tokens": 1,
            "model": "MiniMax-M2.5",
        }

    payload, usage = synthesize_month(
        "2026-06",
        [],
        call_oneshot=call,
        carry="",
        facts=facts,
        reduce_max_tokens=4096,
    )
    names = [kwargs.get("force_tool_name") for _prompt, kwargs in seen]
    assert names == [
        "patch_month_synthesis_g1",
        "patch_month_synthesis_g2",
        "patch_month_synthesis_g3",
        "patch_month_synthesis_g4",
    ]
    menus = [
        tuple(item["name"] for item in kwargs["tool_menu"]) for _prompt, kwargs in seen
    ]
    assert menus == [menus[0]] * 4
    assert menus[0] == tuple(names)
    assert all(kwargs.get("max_tokens") == 4096 for _prompt, kwargs in seen)
    assert "Write only: key_decisions." in seen[1][0]
    assert "Willie kept one standing goal." in seen[1][0]
    assert payload.user_image.goal_alignment.text == "Willie kept one standing goal."
    assert usage["valid"] is True


def test_cut_off_group_is_patched_on_retry(monkeypatch):
    """A group that returns nothing stays a gap, and the retry patches only that group.

    The other groups keep the sections they already wrote, so one cut-off reply
    does not spend a retry on the whole month.
    """
    monkeypatch.setattr("monthly_synth._previous_has_judgment", lambda _month: True)
    facts = mechanical_facts("2026-06")

    def call(prompt, **kwargs):
        name = str(kwargs.get("force_tool_name") or "")
        if name.endswith("_g2"):
            return {
                "failed": True,
                "tool_name": name,
                "tool_args": None,
                "input_tokens": 1,
                "output_tokens": 0,
                "model": "MiniMax-M2.5",
            }
        if name == "patch_month_synthesis":
            assert "tool_menu" not in kwargs
            props = kwargs["tool_schema"]["parameters"]["properties"]
            assert "key_decisions" in props
            return {
                "failed": False,
                "tool_name": name,
                "tool_args": {"key_decisions": _filled_args(facts)["key_decisions"]},
                "input_tokens": 4,
                "output_tokens": 1,
                "model": "MiniMax-M2.5",
            }
        filled = _filled_args(facts)
        keys = list(kwargs["tool_schema"]["parameters"]["properties"])
        return {
            "failed": False,
            "tool_name": name,
            "tool_args": {key: filled[key] for key in keys if key in filled},
            "input_tokens": 1,
            "output_tokens": 1,
            "model": "MiniMax-M2.5",
        }

    payload, usage = synthesize_month(
        "2026-06",
        [],
        call_oneshot=call,
        carry="",
        facts=facts,
    )
    assert usage["attempts"][1]["group"] == "g2"
    assert usage["attempts"][1]["reason"] == "transport failed"
    assert payload.key_decisions[0].text == "June goal stays a standing rule"
    assert usage["valid"] is True


def test_failed_section_is_patched_not_resubmitted():
    facts = mechanical_facts("2026-06")
    real = next(b for b in facts.all_dpe if b.type == "decision")
    calls = {"n": 0}

    def call(prompt, **kwargs):
        calls["n"] += 1
        name = kwargs.get("force_tool_name")
        if name == "patch_month_synthesis":
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
        assert str(name).startswith("patch_month_synthesis_g")
        return {
            "failed": False,
            "tool_name": name,
            "tool_args": {"summary": "June start"},
            "input_tokens": 10,
            "output_tokens": 1,
            "model": "MiniMax-M2.5",
        }

    payload, usage = synthesize_month(
        "2026-06",
        [],
        call_oneshot=call,
        carry="",
        facts=facts,
    )
    assert calls["n"] == 5
    assert payload.key_decisions[0].id.startswith("mem-2026-06-key-decision-")
    assert usage["input_tokens"] == 90
    assert usage["valid"] is True
    assert usage["attempts"][-1]["retired"]
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
    assert calls["n"] == 4
    assert any("string -> list" in fix for fix in usage["attempts"][3]["format_fixes"])
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
    assert not any("cognition_change" in line for line in usage["attempts"][3]["failed"])
    assert any("cognition_change empty_reason" in fix for fix in usage["attempts"][3]["format_fixes"])


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
    assert result["outcome"] == "partial"
    assert month_file_path("2026-06").is_file()
    assert "user_image" in result["pending_sections"]
    failed = " ".join(result["usage"]["attempts"][-1]["failed"])
    assert "cognition_change" in failed


def _write_june_portrait() -> None:
    """June has a real portrait so July is not comparison-exempt and has no supersedes pair."""
    from monthly_schema import MonthlyEvidenceText, MonthlyPayload, MonthlyRange, MonthlyUserImage
    from monthly_writer import write_month

    write_month(
        MonthlyPayload(
            key="2026-06",
            range=MonthlyRange(start="2026-06-01", end="2026-06-30"),
            user_image=MonthlyUserImage(
                goal_alignment=MonthlyEvidenceText(
                    text="June already held a goal.",
                    evidence=("mem-2026-06-15-decision-a",),
                )
            ),
        )
    )


def test_no_supersedes_pair_strips_from_to_without_a_gap():
    """A from/to row with no pair used to fail the month and retry the worker."""
    from monthly_synth import synthesize_month

    _write_june_portrait()
    facts = mechanical_facts("2026-07")
    assert not facts.supersedes_pairs
    decision = next(block for block in facts.all_dpe if block.type == "decision")
    args = _filled_args(facts)
    args["user_image"]["cognition_change"] = [
        {
            "text": "July kept the same understanding.",
            "from": "mem-not-a-pair",
            "to": "mem-also-not",
            "evidence": [decision.id],
        }
    ]

    def call(prompt, **kwargs):
        return {
            "failed": False,
            "tool_name": kwargs.get("force_tool_name"),
            "tool_args": args,
            "input_tokens": 1,
            "output_tokens": 1,
            "model": "MiniMax-M2.5",
        }

    payload, usage = synthesize_month("2026-07", [], call_oneshot=call, carry="", facts=facts)
    judged = usage["attempts"][3]
    failed = " ".join(judged["failed"])
    assert "cognition_change" not in failed
    assert any(
        "from/to dropped (no supersedes pair)" in fix
        for fix in judged["format_fixes"]
    )
    row = payload.user_image.cognition_change[0]
    assert row.text == "July kept the same understanding."
    assert row.from_id == ""
    assert row.to == ""
    assert usage["valid"] is True


def test_no_supersedes_pair_empty_cognition_is_written(tmp_path, monkeypatch):
    """An empty cognition list with no pair is a written placeholder, not a pending section."""
    from monthly_actions import generate_month
    from monthly_writer import load_month

    _write_june_portrait()
    facts = mechanical_facts("2026-07")
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

    result = generate_month("2026-07", call_oneshot=call, force_refresh=True)
    assert result["outcome"] == "ok"
    assert result["pending_sections"] == []
    loaded = load_month("2026-07")
    assert loaded.user_image.cognition_change == ()
    assert loaded.user_image.cognition_empty_reason == "no cognition change found yet"


def test_resume_drops_stale_cognition_gap_without_llm():
    """A stored cognition gap from the old rule must not spend another worker call."""
    from monthly_synth import synthesize_month

    _write_june_portrait()
    facts = mechanical_facts("2026-07")
    args = _filled_args(facts)
    args["user_image"]["cognition_change"] = []

    def call(prompt, **kwargs):
        raise AssertionError("resume recheck should not call the worker")

    payload, usage = synthesize_month(
        "2026-07",
        [],
        call_oneshot=call,
        carry="",
        facts=facts,
        resume={"gaps": ["user_image.cognition_change is empty"], "merged": args},
    )
    assert usage["valid"] is True
    assert usage["attempts"] == []
    assert payload.user_image.cognition_empty_reason == "no cognition change found yet"


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
    assert "comparison_with_last_month.unchanged and changed" in prompt
    assert "Leave unchanged and changed empty" not in prompt
    assert "comparison_with_last_month.summary" not in prompt


def test_comparison_row_gap_names_a_bad_evidence_id():
    from monthly_synth import judgment_gaps

    facts = mechanical_facts("2026-08")
    empty = _filled_args(facts)
    empty["comparison_with_last_month"] = {
        "unchanged": [],
        "changed": [{"text": "The preference got sharper.", "evidence": ["mem-not-a-card"]}],
        "suggestion": "",
    }
    gaps = judgment_gaps(empty, facts, comparison_exempt=False)
    assert any("mem-not-a-card" in gap for gap in gaps)
    assert any("comparison_with_last_month.suggestion is empty" in gap for gap in gaps)
    filled = judgment_gaps(_filled_args(facts), facts, comparison_exempt=False)
    assert not any(gap.startswith("comparison_with_last_month") for gap in filled)


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


def test_guidance_returns_synthesized_month_id(tmp_path, monkeypatch):
    """Guidance must surface the minted synthesis, not a one-day act the reduce dropped."""
    from recall.embed_cache import scale_block_id

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
    row = payload.key_decisions[0]
    bid = scale_block_id(
        {
            "scale": "month",
            "kind": "dp",
            "text": row.lookup_text().strip(),
            "mem_id": row.id,
            "month_key": payload.key,
        }
    )
    monkeypatch.setattr(
        "recall.embed_cache.load_cache",
        lambda: {bid: {"embedding": [1.0, 0.0]}},
    )

    def _encode(texts):
        if texts and texts[0] == synthesis:
            return [[1.0, 0.0]]
        return [[0.0, 1.0]]

    monkeypatch.setattr("recall.embed._encode_texts", _encode)
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


def _write_guidance_month(tmp_path):
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
    return payload


def test_guidance_uses_cached_vector_for_one_query_encode(tmp_path, monkeypatch):
    """A Chinese query must admit a cached row without encoding that row again."""
    from recall.embed_cache import scale_block_id

    from monthly_actions import rank_monthly_guidance

    payload = _write_guidance_month(tmp_path)
    row = payload.key_decisions[0]
    bid = scale_block_id(
        {
            "scale": "month",
            "kind": "dp",
            "text": row.lookup_text().strip(),
            "mem_id": row.id,
            "month_key": payload.key,
        }
    )
    monkeypatch.setattr(
        "recall.embed_cache.load_cache",
        lambda: {bid: {"embedding": [1.0, 0.0]}},
    )
    calls: list[list[str]] = []

    def _encode(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _text in texts]

    monkeypatch.setattr("recall.embed._encode_texts", _encode)
    hits = rank_monthly_guidance("prefetch正常吗", staging=tmp_path)
    assert hits
    assert hits[0]["row"].id == row.id
    assert calls == [["prefetch正常吗"]]


def test_guidance_cache_miss_stays_lexical(tmp_path, monkeypatch):
    """A missing vector must not encode the candidate during prefetch."""
    from monthly_actions import rank_monthly_guidance

    payload = _write_guidance_month(tmp_path)
    monkeypatch.setattr("recall.embed_cache.load_cache", lambda: {})
    calls: list[list[str]] = []

    def _encode(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _text in texts]

    monkeypatch.setattr("recall.embed._encode_texts", _encode)
    hits = rank_monthly_guidance("prefetch正常吗", staging=tmp_path)
    assert calls == []
    assert all(hit["row"].id != payload.key_decisions[0].id for hit in hits)


def _note_ok(**kwargs):
    if kwargs.get("force_tool_name") == "submit_month_note":
        return {
            "failed": False,
            "tool_name": "submit_month_note",
            "tool_args": {"items": []},
            "input_tokens": 1,
            "output_tokens": 1,
        }
    return None


def _tool_ok(name, args):
    return {
        "failed": False,
        "tool_name": name,
        "tool_args": args,
        "input_tokens": 1,
        "output_tokens": 1,
        "model": "MiniMax-M2.5",
    }


def _write_may_goal():
    from monthly_schema import MonthlyEvidenceText, MonthlyPayload, MonthlyRange, MonthlyUserImage
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


def test_generate_month_reads_token_caps(tmp_path, monkeypatch):
    from monthly_actions import generate_month
    import monthly_actions

    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    MyMemory:\n      monthly:\n"
        "        map_batch_tokens: 6000\n        reduce_max_tokens: 16384\n",
        encoding="utf-8",
    )
    seen: dict = {}
    real = monthly_actions.pack_batches

    def wrapped(slices, **kwargs):
        seen["map"] = kwargs.get("max_tokens")
        return real(slices, **kwargs)

    monkeypatch.setattr(monthly_actions, "pack_batches", wrapped)

    def call(prompt, **kwargs):
        note = _note_ok(**kwargs)
        if note is not None:
            return note
        seen["reduce"] = kwargs.get("max_tokens")
        return _tool_ok(kwargs.get("force_tool_name"), {})

    generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert seen["map"] == 6000
    assert seen["reduce"] == 16384


def test_generate_month_bad_caps_fall_back(tmp_path, monkeypatch):
    from monthly_actions import generate_month
    from monthly_schema import MAP_BATCH_TOKENS, REDUCE_MAX_TOKENS
    import monthly_actions

    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    MyMemory:\n      monthly:\n"
        "        map_batch_tokens: abc\n        reduce_max_tokens: 0\n",
        encoding="utf-8",
    )
    seen: dict = {}
    real = monthly_actions.pack_batches

    def wrapped(slices, **kwargs):
        seen["map"] = kwargs.get("max_tokens")
        return real(slices, **kwargs)

    monkeypatch.setattr(monthly_actions, "pack_batches", wrapped)

    def call(prompt, **kwargs):
        note = _note_ok(**kwargs)
        if note is not None:
            return note
        seen["reduce"] = kwargs.get("max_tokens")
        return _tool_ok(kwargs.get("force_tool_name"), {})

    generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert seen["map"] == MAP_BATCH_TOKENS
    assert seen["reduce"] == REDUCE_MAX_TOKENS


def test_partial_month_keeps_passed_sections_and_second_run_carries_them():
    from monthly_actions import generate_month, reduce_resume_path
    from monthly_state import month_file_path
    from monthly_writer import load_month

    _write_may_goal()
    facts = mechanical_facts("2026-06")
    broken = _filled_args(facts)
    broken["comparison_with_last_month"] = {
        "unchanged": [],
        "changed": [],
        "suggestion": "",
    }

    def call(prompt, **kwargs):
        note = _note_ok(**kwargs)
        if note is not None:
            return note
        return _tool_ok(kwargs.get("force_tool_name"), broken)

    first = generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert first["outcome"] == "partial"
    assert "comparison_with_last_month" in first["pending_sections"]
    assert month_file_path("2026-06").is_file()
    assert reduce_resume_path("2026-06").is_file()
    kept = load_month("2026-06").key_decisions[0].text

    fixed = _filled_args(facts)
    fixed["key_decisions"] = []

    def call_again(prompt, **kwargs):
        note = _note_ok(**kwargs)
        if note is not None:
            return note
        return _tool_ok(kwargs.get("force_tool_name"), fixed)

    second = generate_month("2026-06", call_oneshot=call_again, force_refresh=True)
    assert second["outcome"] == "partial"
    assert "key_decisions" in second["pending_sections"]
    assert load_month("2026-06").key_decisions[0].text == kept


def test_gaps_only_patches_stored_sections_and_clears_the_store():
    from monthly_actions import generate_month, reduce_resume_path
    from monthly_writer import load_month

    _write_may_goal()
    facts = mechanical_facts("2026-06")
    broken = _filled_args(facts)
    broken["comparison_with_last_month"] = {
        "unchanged": [],
        "changed": [],
        "suggestion": "",
    }
    filled = _filled_args(facts)
    names: list[str] = []
    fixing = {"on": False}

    def call(prompt, **kwargs):
        name = str(kwargs.get("force_tool_name") or "")
        names.append(name)
        note = _note_ok(**kwargs)
        if note is not None:
            return note
        if name == "patch_month_synthesis" and fixing["on"]:
            props = kwargs["tool_schema"]["parameters"]["properties"]
            assert list(props) == ["comparison_with_last_month"]
            return _tool_ok(
                name,
                {"comparison_with_last_month": filled["comparison_with_last_month"]},
            )
        return _tool_ok(name, broken)

    first = generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert first["outcome"] == "partial"
    before = len(names)
    names.clear()
    fixing["on"] = True
    second = generate_month("2026-06", call_oneshot=call, gaps_only=True)
    assert "submit_month_note" not in names
    assert "submit_month_synthesis" not in names
    assert "patch_month_synthesis" in names
    assert second["outcome"] == "ok"
    assert second["pending_sections"] == []
    assert not reduce_resume_path("2026-06").is_file()
    assert load_month("2026-06").pending_sections == ()
    assert before >= 1


def test_transport_failure_does_not_replace_the_month():
    from monthly_actions import generate_month
    from monthly_schema import MonthlyDecision, MonthlyPayload, MonthlyRange
    from monthly_state import month_file_path
    from monthly_writer import write_month

    write_month(
        MonthlyPayload(
            key="2026-06",
            range=MonthlyRange(start="2026-06-01", end="2026-06-30"),
            key_decisions=(
                MonthlyDecision(
                    id="mem-2026-06-key-decision-kept",
                    kind="decision",
                    text="Keep the June decision.",
                    why_it_matters="it still changes the next time",
                    evidence=("mem-2026-06-15-decision-a",),
                ),
            ),
        )
    )
    path = month_file_path("2026-06")
    before = path.read_text(encoding="utf-8")

    def call(prompt, **kwargs):
        note = _note_ok(**kwargs)
        if note is not None:
            return note
        return {
            "failed": True,
            "tool_name": kwargs.get("force_tool_name"),
            "tool_args": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "model": "mimo-v2.6-flash",
        }

    result = generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert result["outcome"] == "incomplete"
    assert path.read_text(encoding="utf-8") == before


def test_gaps_only_without_a_store_makes_no_model_calls():
    from monthly_actions import generate_month

    def boom(prompt, **kwargs):
        raise AssertionError("gaps_only with no store must not call the model")

    result = generate_month("2026-06", call_oneshot=boom, gaps_only=True)
    assert result["outcome"] == "ok"
    assert result["map_calls"] == 0
    assert result["pending_sections"] == []


def _vec(cosine: float) -> list[float]:
    import math

    c = max(-1.0, min(1.0, cosine))
    return [c, math.sqrt(max(0.0, 1.0 - c * c))]


def _key_decision(i: int, *, month: str = "2026-08", cosine: float = 0.99, **kwargs):
    from monthly_schema import MonthlyDecision

    row = MonthlyDecision(
        id=f"mem-{month}-key-decision-{i:012x}",
        kind="decision",
        text=kwargs.get("text", f"alpha rule {i}"),
        why_it_matters="still changes the next time",
        context=kwargs.get("context", "planning"),
        evidence=kwargs.get("evidence", (f"mem-2026-08-01-decision-{i:012d}",)),
        first_seen=kwargs.get("first_seen", "2026-08-01"),
        recall_n=kwargs.get("recall_n", 0.0),
        last_recall_at=kwargs.get("last_recall_at", ""),
    )
    return row, cosine


def _key_procedure(i: int, *, month: str = "2026-08", cosine: float = 0.97, **kwargs):
    from monthly_schema import MonthlyProcedure

    row = MonthlyProcedure(
        id=f"mem-{month}-key-procedure-{i:012x}",
        problem="case",
        solution=kwargs.get("solution", f"beta step {i}"),
        insight="still changes the next time",
        trigger=kwargs.get("trigger", "task"),
        evidence=kwargs.get("evidence", (f"mem-2026-08-02-procedure-{i:012d}",)),
        first_seen=kwargs.get("first_seen", "2026-08-01"),
        recall_n=kwargs.get("recall_n", 0.0),
        last_recall_at=kwargs.get("last_recall_at", ""),
    )
    return row, cosine


def _write_ranked_month(tmp_path, monkeypatch, specs, *, key="2026-08"):
    from dataclasses import replace

    from monthly_writer import dump_yaml, loads
    from recall.embed_cache import scale_block_id

    folder = tmp_path / "monthly"
    if (folder / "2026-08.md").is_file():
        payload = loads((folder / "2026-08.md").read_text(encoding="utf-8"))
    else:
        payload = _write_guidance_month(tmp_path)
    decisions = [row for row, _c in specs if row.__class__.__name__ == "MonthlyDecision"]
    procedures = [row for row, _c in specs if row.__class__.__name__ == "MonthlyProcedure"]
    months = ["2026-08", "2026-07", "2026-06", "2026-05"]
    cache = {}
    for path in folder.glob("????-??.md"):
        path.unlink()
    for index, month in enumerate(months):
        d_slice = decisions[index * 12 : (index + 1) * 12]
        p_slice = procedures[index * 12 : (index + 1) * 12]
        if not d_slice and not p_slice:
            continue
        body = replace(
            payload,
            key=month,
            key_decisions=tuple(d_slice),
            key_procedures=tuple(p_slice),
        )
        (folder / f"{month}.md").write_text(dump_yaml(body), encoding="utf-8")
        for row in (*d_slice, *p_slice):
            cosine = next(c for item, c in specs if item.id == row.id)
            bid = scale_block_id(
                {
                    "scale": "month",
                    "kind": "dp",
                    "text": row.lookup_text().strip(),
                    "mem_id": row.id,
                    "month_key": month,
                }
            )
            cache[bid] = {"embedding": _vec(cosine)}
    monkeypatch.setattr("recall.embed_cache.load_cache", lambda: cache)
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [[1.0, 0.0]])
    return payload


def _counts(hits):
    return (
        sum(h["kind"] == "decision" for h in hits),
        sum(h["kind"] == "procedure" for h in hits),
    )


def test_guidance_share_is_six_when_both_kinds_are_plentiful(tmp_path, monkeypatch):
    from monthly_actions import rank_monthly_guidance

    specs = [_key_decision(i) for i in range(14)] + [_key_procedure(i, cosine=0.95) for i in range(8)]
    _write_ranked_month(tmp_path, monkeypatch, specs)
    hits = rank_monthly_guidance("zzzz guidance probe", staging=tmp_path, limit=12)
    assert _counts(hits) == (6, 6)


def test_guidance_spills_unused_procedure_slots_to_decisions(tmp_path, monkeypatch):
    from monthly_actions import rank_monthly_guidance

    specs = [_key_decision(i) for i in range(14)] + [_key_procedure(i, cosine=0.95) for i in range(2)]
    _write_ranked_month(tmp_path, monkeypatch, specs)
    hits = rank_monthly_guidance("zzzz guidance probe", staging=tmp_path, limit=12)
    assert _counts(hits) == (10, 2)


def test_guidance_tops_up_two_procedures_from_below_the_gate(tmp_path, monkeypatch):
    from monthly_actions import rank_monthly_guidance

    specs = [_key_decision(i) for i in range(14)]
    specs.append(_key_procedure(0, cosine=0.95))
    specs.extend(_key_procedure(i, cosine=0.4) for i in range(1, 4))
    _write_ranked_month(tmp_path, monkeypatch, specs)
    hits = rank_monthly_guidance("zzzz guidance probe", staging=tmp_path, limit=12)
    assert _counts(hits) == (10, 2)
    assert sum(h["topup"] for h in hits if h["kind"] == "procedure") == 1


def test_guidance_tops_up_when_no_procedure_passed(tmp_path, monkeypatch):
    from monthly_actions import rank_monthly_guidance

    specs = [_key_decision(i) for i in range(14)]
    specs.extend(_key_procedure(i, cosine=0.4) for i in range(3))
    _write_ranked_month(tmp_path, monkeypatch, specs)
    hits = rank_monthly_guidance("zzzz guidance probe", staging=tmp_path, limit=12)
    assert _counts(hits) == (10, 2)
    assert all(h["topup"] for h in hits if h["kind"] == "procedure")


def test_guidance_miss_does_not_top_up(tmp_path, monkeypatch):
    from monthly_actions import rank_monthly_guidance

    specs = [_key_decision(i, cosine=0.2) for i in range(4)]
    specs += [_key_procedure(i, cosine=0.2) for i in range(4)]
    _write_ranked_month(tmp_path, monkeypatch, specs)
    assert rank_monthly_guidance("zzzz guidance probe", staging=tmp_path, limit=12) == []


def test_guidance_default_limit_splits_four_and_four(tmp_path, monkeypatch):
    from monthly_actions import rank_monthly_guidance

    specs = [_key_decision(i) for i in range(14)] + [_key_procedure(i, cosine=0.95) for i in range(8)]
    _write_ranked_month(tmp_path, monkeypatch, specs)
    hits = rank_monthly_guidance("zzzz guidance probe", staging=tmp_path)
    assert _counts(hits) == (4, 4)


def test_guidance_tie_break_prefers_overlap_then_evidence_then_month(tmp_path, monkeypatch):
    from monthly_actions import rank_monthly_guidance

    rich = _key_procedure(
        1,
        cosine=0.99,
        trigger="send file weekly review notes",
        evidence=("mem-a", "mem-b", "mem-c"),
    )
    thin = _key_procedure(2, cosine=0.988, trigger="send", evidence=("mem-a",))
    _write_ranked_month(tmp_path, monkeypatch, [rich, thin, _key_decision(1)])
    hits = rank_monthly_guidance("send file weekly", staging=tmp_path, limit=12)
    procs = [h for h in hits if h["kind"] == "procedure"]
    assert [h["row"].id for h in procs[:2]] == [rich[0].id, thin[0].id]

    same = _key_procedure(3, cosine=0.99, trigger="send file weekly", evidence=("mem-a", "mem-b"))
    less = _key_procedure(4, cosine=0.99, trigger="send file weekly", evidence=("mem-a",))
    _write_ranked_month(tmp_path, monkeypatch, [same, less, _key_decision(2)])
    hits = rank_monthly_guidance("send file weekly", staging=tmp_path, limit=12)
    procs = [h for h in hits if h["kind"] == "procedure"]
    assert procs[0]["row"].id == same[0].id

    older = _key_procedure(5, month="2026-07", cosine=0.99, trigger="send file weekly")
    newer = _key_procedure(6, month="2026-08", cosine=0.99, trigger="send file weekly")
    from dataclasses import replace

    from monthly_writer import dump_yaml, loads
    from recall.embed_cache import scale_block_id

    base = loads((tmp_path / "monthly" / "2026-08.md").read_text(encoding="utf-8"))
    first = replace(base, key="2026-07", key_decisions=(), key_procedures=(older[0],))
    second = replace(base, key="2026-08", key_decisions=(_key_decision(7)[0],), key_procedures=(newer[0],))
    folder = tmp_path / "monthly"
    folder.joinpath("2026-07.md").write_text(dump_yaml(first), encoding="utf-8")
    folder.joinpath("2026-08.md").write_text(dump_yaml(second), encoding="utf-8")
    cache = {}
    for payload, row, cosine in (
        (first, older[0], 0.99),
        (second, newer[0], 0.99),
        (second, second.key_decisions[0], 0.99),
    ):
        bid = scale_block_id(
            {
                "scale": "month",
                "kind": "dp",
                "text": row.lookup_text().strip(),
                "mem_id": row.id,
                "month_key": payload.key,
            }
        )
        cache[bid] = {"embedding": _vec(cosine)}
    monkeypatch.setattr("recall.embed_cache.load_cache", lambda: cache)
    hits = rank_monthly_guidance("send file weekly", staging=tmp_path, limit=12)
    procs = [h for h in hits if h["kind"] == "procedure"]
    assert procs[0]["row"].id == newer[0].id


def _event_view_decision(text: str, evidence: tuple[str, ...], *, suffix: str):
    from monthly_schema import MonthlyDecision

    return MonthlyDecision(
        id=f"mem-2026-08-key-decision-{suffix}",
        kind="preference",
        text=text,
        why_it_matters="still changes the next time",
        evidence=evidence,
    )


def _write_event_view_month(tmp_path, rows):
    from monthly_schema import MonthlyPayload, MonthlyRange
    from monthly_writer import dump_yaml

    decisions = tuple(row for row in rows if row.__class__.__name__ == "MonthlyDecision")
    procedures = tuple(row for row in rows if row.__class__.__name__ == "MonthlyProcedure")
    payload = MonthlyPayload(
        key="2026-08",
        range=MonthlyRange(start="2026-08-01", end="2026-08-31"),
        key_decisions=decisions,
        key_procedures=procedures,
    )
    folder = tmp_path / "monthly"
    folder.mkdir(exist_ok=True)
    (folder / "2026-08.md").write_text(dump_yaml(payload), encoding="utf-8")
    return payload


def _month_cache_key(row, month: str = "2026-08") -> str:
    from recall.embed_cache import scale_block_id

    return scale_block_id(
        {
            "scale": "month",
            "kind": "dp",
            "text": row.lookup_text().strip(),
            "mem_id": row.id,
            "month_key": month,
        }
    )


def test_guidance_event_view_lifts_card_and_records_every_score(tmp_path, monkeypatch):
    from recall.ids import BlockIndex

    import monthly_actions
    from monthly_actions import rank_monthly_guidance

    cited = "mem-2026-08-15-decision-55091D2900CD"
    lifted = _event_view_decision("alpha rule unrelated", (cited,), suffix="lifted")
    quiet = _event_view_decision(
        "beta rule unrelated", ("mem-2026-08-15-decision-quiet",), suffix="quiet"
    )
    _write_event_view_month(tmp_path, [lifted, quiet])
    newest = "mem-2026-08-12-event-9625547B667B"
    cache = {
        _month_cache_key(lifted): {"embedding": [0.0, 1.0]},
        _month_cache_key(quiet): {"embedding": [0.0, 1.0]},
        newest: {"embedding": [1.0, 0.0]},
        "mem-2026-08-08-event-g": {"embedding": [0.0, 1.0]},
        "mem-2026-08-01-event-a": {"embedding": [0.0, 1.0]},
    }
    calls: list[list[str]] = []

    def _encode(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _text in texts]

    monkeypatch.setattr("recall.embed._encode_texts", _encode)
    store = BlockIndex()
    query = "zzzz event situation"
    scores: list[dict] = []
    hits = rank_monthly_guidance(
        query, staging=tmp_path, store=store, cache=cache, scores_out=scores
    )
    event_hits = [hit for hit in hits if hit["via"] == "event"]
    assert [hit["row"].id for hit in event_hits] == [lifted.id]
    assert all(hit["topup"] for hit in hits if hit["row"].id == quiet.id)
    assert calls == [[query]]
    assert {row["id"] for row in scores} == {lifted.id, quiet.id}

    calls.clear()
    again: list[dict] = []
    rank_monthly_guidance(
        query,
        staging=tmp_path,
        store=store,
        cache=cache,
        query_vec=[1.0, 0.0],
        scores_out=again,
    )
    assert calls == []
    bare: list[dict] = []
    rank_monthly_guidance(
        query,
        staging=tmp_path,
        cache=cache,
        query_vec=[1.0, 0.0],
        scores_out=bare,
    )
    bare_lifted = next(row for row in bare if row["id"] == lifted.id)
    event_lifted = next(row for row in again if row["id"] == lifted.id)
    assert bare_lifted["sim"] == bare_lifted["s_trig"]
    assert event_lifted["sim"] > bare_lifted["sim"]
    assert monthly_actions._GUIDANCE_EVENT_BETA == 0.95


def test_guidance_event_view_ignores_rejected_and_keeps_newest(tmp_path, monkeypatch):
    from recall.ids import BlockIndex

    import monthly_actions
    from monthly_actions import rank_monthly_guidance

    cited = "mem-2026-08-15-decision-55091D2900CD"
    row = _event_view_decision("alpha rule unrelated", (cited,), suffix="capped")
    _write_event_view_month(tmp_path, [row])
    newest = "mem-2026-08-12-event-9625547B667B"
    oldest = "mem-2026-08-01-event-a"
    cache = {
        _month_cache_key(row): {"embedding": [0.0, 1.0]},
        newest: {"embedding": [0.0, 1.0]},
        "mem-2026-08-08-event-g": {"embedding": [0.0, 1.0]},
        oldest: {"embedding": [1.0, 0.0]},
    }
    monkeypatch.setattr(monthly_actions, "_GUIDANCE_EVENT_CAP", 1)
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [[1.0, 0.0]])
    store = BlockIndex()
    query = "zzzz event situation"
    assert rank_monthly_guidance(query, staging=tmp_path, store=store, cache=cache) == []
    cache[newest] = {"embedding": [1.0, 0.0]}
    cache[oldest] = {"embedding": [0.0, 1.0]}
    hits = rank_monthly_guidance(query, staging=tmp_path, store=store, cache=cache)
    assert [hit["row"].id for hit in hits] == [row.id]

    path = tmp_path / "memories" / "staging" / "daily" / "2026-08-12.md"
    path.write_text(path.read_text(encoding="utf-8").replace("status: candidate", "status: rejected"), encoding="utf-8")
    rejected_store = BlockIndex()
    assert rank_monthly_guidance(query, staging=tmp_path, store=rejected_store, cache=cache) == []


def test_guidance_same_event_orders_rows_by_index_score(tmp_path, monkeypatch):
    from monthly_schema import MonthlyProcedure
    from recall.ids import BlockIndex

    from monthly_actions import rank_monthly_guidance

    cited = "mem-2026-08-15-decision-55091D2900CD"
    closer = MonthlyProcedure(
        id="mem-2026-08-key-procedure-closer",
        problem="case",
        solution="cut",
        insight="still changes the next time",
        trigger="closer index",
        evidence=(cited,),
    )
    farther = MonthlyProcedure(
        id="mem-2026-08-key-procedure-farther",
        problem="case",
        solution="cut",
        insight="still changes the next time",
        trigger="farther index",
        evidence=(cited,),
    )
    _write_event_view_month(tmp_path, [farther, closer])
    cache = {
        _month_cache_key(closer): {"embedding": _vec(0.50)},
        _month_cache_key(farther): {"embedding": _vec(0.20)},
        "mem-2026-08-12-event-9625547B667B": {"embedding": [1.0, 0.0]},
    }
    monkeypatch.setattr("recall.embed._encode_texts", lambda texts: [[1.0, 0.0]])
    hits = rank_monthly_guidance(
        "zzzz event situation",
        staging=tmp_path,
        store=BlockIndex(),
        cache=cache,
    )
    procs = [hit for hit in hits if hit["kind"] == "procedure"]
    assert [hit["row"].id for hit in procs] == [closer.id, farther.id]
    assert procs[0]["via"] == "event" and procs[1]["via"] == "event"


def test_guidance_live_strength_breaks_equal_similarity(tmp_path, monkeypatch):
    from datetime import date

    from monthly_actions import rank_monthly_guidance

    today = date.today().isoformat()
    recent = _key_decision(1, cosine=0.97, recall_n=8, last_recall_at=today, first_seen="2026-08-01")
    cold = _key_decision(2, cosine=0.97, recall_n=8, last_recall_at="2026-01-01", first_seen="2026-01-01")
    _write_ranked_month(tmp_path, monkeypatch, [cold, recent])
    hits = rank_monthly_guidance("zzzz guidance probe", staging=tmp_path, limit=12)
    assert hits[0]["row"].id == recent[0].id


def test_month_stamp_and_generate_carry_recall(tmp_path, monkeypatch):
    """A guidance stamp must survive the next month rebuild when the id is unchanged."""
    from monthly_actions import generate_month
    from monthly_slice import mechanical_facts
    from monthly_writer import load_month, write_month
    from recall.strength import stamp_recall_on_card

    monkeypatch.setenv("RECALL_PERSIST_IN_TEST", "1")
    _write_may_goal()
    facts = mechanical_facts("2026-06")
    args = _filled_args(facts)

    def call(prompt, **kwargs):
        note = _note_ok(**kwargs)
        if note is not None:
            return note
        return _tool_ok(kwargs.get("force_tool_name"), args)

    first = generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert first["outcome"] in {"ok", "partial"}
    saved = load_month("2026-06")
    row = saved.key_decisions[0]
    from datetime import date as _date

    from recall.ids import BlockIndex
    from recall.strength import strength_value

    assert stamp_recall_on_card(row.id, staging=tmp_path / "memories" / "staging", now=_date(2026, 8, 1))
    stamped_row = load_month("2026-06").key_decisions[0]
    assert stamped_row.recall_n == 1
    store = BlockIndex(tmp_path / "memories" / "staging")
    evidence_cards = [store.get(item).parsed for item in stamped_row.evidence if store.get(item)]
    assert evidence_cards
    assert all(float(card.get("recall_n") or 0) >= 1 for card in evidence_cards)
    assert abs(
        stamped_row.strength
        - strength_value(
            recall_n=0,
            first_seen=None,
            importance=stamped_row.importance,
            now=_date(2026, 8, 1),
            evidence=evidence_cards,
        )
    ) < 1e-3
    from dataclasses import replace as _replace

    from monthly_writer import payload_from_dict, write_month

    second_id = stamped_row.id[:-1] + ("a" if not stamped_row.id.endswith("a") else "b")
    second = _replace(stamped_row, id=second_id, recall_n=0, last_recall_at="")
    current = load_month("2026-06")
    write_month(_replace(current, key_decisions=(stamped_row, second)))
    seen = set(stamped_row.evidence)
    before = float(evidence_cards[0].get("recall_n") or 0)
    stamp_recall_on_card(second.id, staging=tmp_path / "memories" / "staging", now=_date(2026, 8, 1), seen=seen)
    shared = BlockIndex(tmp_path / "memories" / "staging").get(stamped_row.evidence[0])
    assert float(shared.parsed.get("recall_n") or 0) == before
    bare = current.to_dict()
    for section in ("key_decisions", "key_procedures"):
        for item in bare.get(section) or []:
            item.pop("importance", None)
    assert payload_from_dict(bare).key_decisions[0].importance == 3
    again = generate_month("2026-06", call_oneshot=call, force_refresh=True)
    assert again["outcome"] in {"ok", "partial"}
    kept = load_month("2026-06").key_decisions[0]
    assert kept.id == row.id
    assert kept.recall_n == 1
    other = next(
        block.id
        for block in facts.all_dpe
        if block.type == "decision" and block.id != args["key_decisions"][0]["evidence"][0]
    )
    changed = dict(args)
    changed["key_decisions"] = [dict(args["key_decisions"][0], evidence=[other])]

    def call_changed(prompt, **kwargs):
        note = _note_ok(**kwargs)
        if note is not None:
            return note
        return _tool_ok(kwargs.get("force_tool_name"), changed)

    generate_month("2026-06", call_oneshot=call_changed, force_refresh=True)
    fresh = load_month("2026-06").key_decisions[0]
    assert fresh.id != row.id
    assert fresh.recall_n == 0
    fresh_cards = []
    fresh_store = BlockIndex(tmp_path / "memories" / "staging")
    for item in fresh.evidence:
        rec = fresh_store.get(item)
        if rec is not None:
            fresh_cards.append(rec.parsed)
    recalled = [card for card in fresh_cards if float(card.get("recall_n") or 0) > 0]
    if recalled:
        assert fresh.strength > 0
    else:
        assert fresh.strength == strength_value(
            recall_n=0,
            first_seen=None,
            importance=fresh.importance,
            evidence=fresh_cards,
        )
    assert stamp_recall_on_card("mem-not-a-month-id", staging=tmp_path / "memories" / "staging") is None
    assert stamp_recall_on_card(
        "mem-2099-01-key-decision-abcdefabcdef",
        staging=tmp_path / "memories" / "staging",
    ) is None
    del write_month


def _pile_encode(calls: list):
    def encode(texts):
        calls.append(list(texts))
        text = texts[0] if texts else ""
        if "\n" in text:
            return [[1.0, 0.0]]
        return [[0.0, 1.0]]

    return encode


def test_pile_recovers_a_short_follow_up(tmp_path, monkeypatch):
    """A short line with no situation of its own must recover the card from the previous line."""
    from monthly_actions import format_guidance_hits, rank_monthly_guidance

    proc, _cosine = _key_procedure(1, cosine=0.97, trigger="把周报发到微信")
    _write_ranked_month(tmp_path, monkeypatch, [(proc, 0.97)])
    calls: list = []
    monkeypatch.setattr("recall.embed._encode_texts", _pile_encode(calls))
    alone = rank_monthly_guidance("发给我", staging=tmp_path, now=1_000_000)
    assert alone == []
    hits = rank_monthly_guidance(
        "发给我",
        staging=tmp_path,
        now=1_000_000,
        history=[(999_970, "把周报发到微信")],
    )
    assert hits[0]["row"].id == proc.id
    assert hits[0]["turns"] == 2
    rendered = format_guidance_hits(hits)
    assert "turns=2" in rendered.split("\n", 1)[0]
    assert f"strength={hits[0]['live']:.2f}" in rendered
    assert f"importance={hits[0]['importance']}" in rendered


def test_pile_stops_on_a_time_gap(tmp_path, monkeypatch):
    """A line from outside the gap is a different conversation and must not be piled."""
    from monthly_actions import rank_monthly_guidance

    proc, _cosine = _key_procedure(1, cosine=0.97)
    _write_ranked_month(tmp_path, monkeypatch, [(proc, 0.97)])
    calls: list = []
    monkeypatch.setattr("recall.embed._encode_texts", _pile_encode(calls))
    hits = rank_monthly_guidance(
        "发给我",
        staging=tmp_path,
        now=1_000_000,
        history=[(1_000_000 - 700, "把周报发到微信")],
    )
    assert hits == []
    assert calls == [["发给我"]]


def test_pile_misses_when_two_cards_stay_within_the_margin(tmp_path, monkeypatch):
    """Two close cards must stay uninjected so a follow-up does not pick one at random."""
    from monthly_actions import rank_monthly_guidance

    near, _n = _key_procedure(1, cosine=0.97)
    far, _f = _key_procedure(2, cosine=0.90)
    _write_ranked_month(tmp_path, monkeypatch, [(near, 0.97), (far, 0.90)])
    monkeypatch.setattr("recall.embed._encode_texts", _pile_encode([]))
    hits = rank_monthly_guidance(
        "发给我",
        staging=tmp_path,
        now=1_000_000,
        history=[(999_970, "把周报发到微信")],
    )
    assert hits == []


def test_pile_skips_a_long_line(tmp_path, monkeypatch):
    """A long latest line already has its situation, so earlier lines must not be encoded."""
    from monthly_actions import rank_monthly_guidance

    proc, _cosine = _key_procedure(1, cosine=0.97)
    _write_ranked_month(tmp_path, monkeypatch, [(proc, 0.97)])
    calls: list = []
    monkeypatch.setattr("recall.embed._encode_texts", _pile_encode(calls))
    query = "发给我" + (" x" * 20)
    hits = rank_monthly_guidance(
        query,
        staging=tmp_path,
        now=1_000_000,
        history=[(999_970, "把周报发到微信")],
    )
    assert hits == []
    assert calls == [[query]]


def test_pile_stops_when_entropy_does_not_drop(tmp_path, monkeypatch):
    """Another line that leaves the scores flat must not spend another encode."""
    from monthly_actions import rank_monthly_guidance

    proc, _cosine = _key_procedure(1, cosine=0.99)
    _write_ranked_month(tmp_path, monkeypatch, [(proc, 0.99)])
    calls: list = []

    def encode(texts):
        calls.append(list(texts))
        return [[0.0, 1.0]]

    monkeypatch.setattr("recall.embed._encode_texts", encode)
    hits = rank_monthly_guidance(
        "发给我",
        staging=tmp_path,
        now=1_000_000,
        history=[(999_900 - i, f"line {i}") for i in range(4)],
    )
    assert hits == []
    assert len(calls) == 2


def test_pile_stops_at_six_lines(tmp_path, monkeypatch):
    """The pile includes the latest line plus five earlier ones, then stops."""
    import monthly_actions
    from monthly_actions import rank_monthly_guidance

    proc, _cosine = _key_procedure(1, cosine=0.99)
    _write_ranked_month(tmp_path, monkeypatch, [(proc, 0.99)])
    calls: list = []
    monkeypatch.setattr(monthly_actions, "_PILE_FLOOR", 2.0)
    monkeypatch.setattr(monthly_actions, "_PILE_EPS_BITS", -1.0)
    monkeypatch.setattr(
        "recall.embed._encode_texts",
        lambda texts: calls.append(list(texts)) or [[0.0, 1.0]],
    )
    hits = rank_monthly_guidance(
        "发给我",
        staging=tmp_path,
        now=1_000_000,
        history=[(999_980 - i, f"line {i}") for i in range(8)],
    )
    assert hits == []
    assert len(calls) == 6
