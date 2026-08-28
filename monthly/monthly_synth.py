"""Reduce stage: one oneshot writes every narrative field from notes, not raw dailies."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_monthly = Path(__file__).resolve().parent
_mymemory = _monthly.parent
for path in (_monthly, _mymemory, _mymemory / "weekly"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from monthly_schema import (  # noqa: E402
    CAP_CROSS_WEEK,
    CAP_DECISIONS,
    CAP_FOCUS,
    CAP_PROCEDURES,
    CAP_PROGRESS,
    CAP_RISKS,
    CAP_SUMMARY,
    REDUCE_MAX_TOKENS,
    SOLUTION_CHAR_CAP,
    MonthlyBehaviorPattern,
    MonthlyCognitionChange,
    MonthlyComparison,
    MonthlyComparisonChange,
    MonthlyCrossWeekItem,
    MonthlyDecision,
    MonthlyDecisionPreference,
    MonthlyEntity,
    MonthlyEvidenceText,
    MonthlyFocus,
    MonthlyGenerator,
    MonthlyPayload,
    MonthlyProcedure,
    MonthlyProgress,
    MonthlyRange,
    MonthlyRisk,
    MonthlySummaryItem,
    MonthlyUserImage,
)
from monthly_slice import (  # noqa: E402
    MechanicalFacts,
    clause_body,
    count_tokens,
    mechanical_facts,
    week_key_for,
)
from monthly_tools import (  # noqa: E402
    merge_field_patch,
    patch_month_synthesis_schema,
    submit_month_synthesis_schema,
)

CallOneshot = Callable[..., dict[str, Any]]
MAX_ATTEMPTS = 3
REDUCE_PREFIX = (
    "Synthesize a monthly user portrait from notes, weekly story seeds, and mechanical D/P groups. "
    "Cite only ids listed. Do not copy Beginning/Course/Outcome/Obstacle. "
    "summary must be an array of one-line stories (text + weeks); never glue two seeds into one string. "
    "key_decisions.id and key_procedures.id must be ids from mechanical dp_groups. "
    "Do not invent obstacles, exceptions, or ids."
)


def _default_oneshot(prompt: str, **kwargs: Any) -> dict[str, Any]:
    from worker_llm import run_worker_llm_oneshot

    return run_worker_llm_oneshot(
        prompt,
        plugin="memory-monthly",
        purpose=kwargs.get("purpose") or "monthly-reduce",
        force_tool_name=kwargs.get("force_tool_name"),
        tool_schema=kwargs.get("tool_schema"),
        max_tokens=int(kwargs.get("max_tokens") or REDUCE_MAX_TOKENS),
    )


def _as_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _drop_unsourced(ids: tuple[str, ...], allowed: set[str]) -> tuple[str, ...]:
    """Delete synthesized items citing ids the model was never shown, because an
    unverifiable claim in a month file is worse than a missing one - recall will
    quote it for months without any way to check it.
    """
    return tuple(item for item in ids if item in allowed)


def _lead_kind(clause: str) -> str:
    if clause.lstrip().startswith("Preference:"):
        return "preference"
    return "decision"


def _verbatim_clause(block) -> str:
    text = clause_body(block.clause)
    for prefix in ("Decision:", "Preference:", "Solution:", "Obstacle:"):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _solution_clause(block) -> str:
    text = clause_body(block.clause)
    marker = "Solution:"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    return text[:SOLUTION_CHAR_CAP]


def build_reduce_prompt(
    month_key: str,
    notes: list[dict[str, Any]],
    facts: MechanicalFacts,
    carry: str,
) -> str:
    payload = {
        "month_key": month_key,
        "notes": notes,
        "mechanical": facts.rendered(),
        "story_seeds": list(facts.story_seeds),
        "weekly_summaries": list(facts.weekly_summaries),
        "cross_week_candidates": list(facts.cross_week_candidates),
        "dp_groups": [
            {k: v for k, v in row.items() if k != "text" or row.get("type") == "decision"}
            for row in facts.dp_groups
        ],
        "carry_card": carry,
        "decision_ids": [b.id for b in facts.all_dpe if b.type == "decision"],
        "procedure_ids": [b.id for b in facts.all_dpe if b.type == "procedure"],
        "supersedes_pairs": list(facts.supersedes_pairs),
    }
    return f"{REDUCE_PREFIX}\n\n---\n{json.dumps(payload, ensure_ascii=False, default=str)}"


def allowed_ids_from_facts(facts: MechanicalFacts, notes: list[dict[str, Any]]) -> set[str]:
    allowed = {b.id for b in facts.all_dpe}
    allowed.update(facts.open_decision_ids)
    for prior, current in facts.supersedes_pairs:
        allowed.add(prior)
        allowed.add(current)
    for note in notes:
        allowed.update(str(x) for x in (note.get("evidence") or []))
    return allowed


def _evidence_text(raw: Any, allowed: set[str]) -> MonthlyEvidenceText:
    if not isinstance(raw, dict):
        return MonthlyEvidenceText()
    text = str(raw.get("text") or "")
    evidence = _drop_unsourced(_as_ids(raw.get("evidence")), allowed)
    if text.strip() and not evidence:
        return MonthlyEvidenceText()
    return MonthlyEvidenceText(text=text, evidence=evidence)


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _summary_from_synthesis(args: dict[str, Any], facts: MechanicalFacts) -> tuple[MonthlySummaryItem, ...]:
    """Keep one bullet per seed; refuse a single glued paragraph when several stories exist."""
    seeds = list(facts.story_seeds)
    raw = args.get("summary")
    parsed: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(raw, str) and raw.strip():
        parsed = [(_one_line(raw), tuple(seeds[0]["weeks"]) if len(seeds) == 1 else ())]
    elif isinstance(raw, list):
        for row in raw:
            if isinstance(row, str) and row.strip():
                parsed.append((_one_line(row), ()))
            elif isinstance(row, dict):
                text = _one_line(str(row.get("text") or ""))
                if not text:
                    continue
                weeks = tuple(str(x) for x in (row.get("weeks") or []) if str(x).strip())
                parsed.append((text, weeks))
    if len(parsed) == 1 and len(seeds) > 1:
        parsed = []
    if not parsed:
        parsed = [(_one_line(s["text"]), tuple(s.get("weeks") or ())) for s in seeds]
    items: list[MonthlySummaryItem] = []
    for text, weeks in parsed:
        if not text:
            continue
        items.append(MonthlySummaryItem(text=text, weeks=weeks))
        if len(items) >= CAP_SUMMARY:
            break
    return tuple(items)


def _group_to_decision(group: dict[str, Any], why: str) -> MonthlyDecision:
    text = str(group.get("text") or "")
    for prefix in ("Decision:", "Preference:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return MonthlyDecision(
        id=str(group["id"]),
        kind=str(group.get("kind") or "decision"),
        text=text,
        why_it_matters=why,
        context=str(group.get("context") or ""),
        exceptions=str(group.get("exceptions") or ""),
        date=str(group.get("date") or ""),
        valid_to=str(group.get("valid_to") or ""),
        entity_keys=tuple(group.get("entity_keys") or ()),
        supersedes=tuple(group.get("supersedes") or ()),
        evidence=tuple(group.get("evidence") or (group["id"],)),
        occurrence_n=int(group.get("occurrence_n") or 1),
        first_seen=str(group.get("first_seen") or ""),
        last_seen=str(group.get("last_seen") or ""),
        strength=float(group.get("strength") or 0.0),
    )


def _group_to_procedure(group: dict[str, Any], insight: str, problem: str) -> MonthlyProcedure:
    solution = str(group.get("solution") or "")
    if len(solution) > SOLUTION_CHAR_CAP:
        solution = solution[:SOLUTION_CHAR_CAP]
    obstacles = tuple(group.get("obstacles") or ())
    return MonthlyProcedure(
        id=str(group["id"]),
        trigger=str(group.get("trigger") or ""),
        problem=problem or str(group.get("problem") or (obstacles[0] if obstacles else "")),
        obstacles=obstacles,
        solution=solution,
        insight=insight,
        entity_keys=tuple(group.get("entity_keys") or ()),
        weeks=tuple(group.get("weeks") or ()),
        evidence=tuple(group.get("evidence") or (group["id"],)),
        occurrence_n=int(group.get("occurrence_n") or 1),
        first_seen=str(group.get("first_seen") or ""),
        last_seen=str(group.get("last_seen") or ""),
        strength=float(group.get("strength") or 0.0),
    )


def payload_from_synthesis(
    month_key: str,
    args: dict[str, Any],
    facts: MechanicalFacts,
    *,
    carry: str,
    model: str,
    map_calls: int,
    reduce_tokens: int,
    generated_at: str,
) -> MonthlyPayload:
    """Bind synthesis args onto mechanical facts, including bilingual aliases, so monthly YAML cannot drop original-language surfaces."""
    from monthly_slice import calendar_range

    allowed = allowed_ids_from_facts(facts, [])
    for item in args.get("key_decisions") or []:
        if isinstance(item, dict) and str(item.get("id") or "") in facts.blocks_by_id:
            allowed.add(str(item["id"]))
    notes_ids = allowed_ids_from_facts(facts, args.get("_notes") or [])
    allowed |= notes_ids
    start, end = calendar_range(month_key)

    img_raw = args.get("user_image") if isinstance(args.get("user_image"), dict) else {}
    cognition: list[MonthlyCognitionChange] = []
    pair_set = set(facts.supersedes_pairs)
    for row in img_raw.get("cognition_change") or []:
        if not isinstance(row, dict):
            continue
        frm = str(row.get("from") or "")
        to = str(row.get("to") or "")
        if (frm, to) not in pair_set:
            continue
        text = str(row.get("text") or "")
        if not text.strip():
            continue
        evidence = _drop_unsourced(_as_ids(row.get("evidence")), allowed) or (to,)
        cognition.append(
            MonthlyCognitionChange(
                text=str(row.get("text") or ""),
                from_id=frm,
                to=to,
                date=str(row.get("date") or (facts.blocks_by_id[to].valid_from if to in facts.blocks_by_id else "")),
                evidence=evidence,
            )
        )

    progress: list[MonthlyProgress] = []
    for row in args.get("core_progress") or []:
        if not isinstance(row, dict):
            continue
        evidence = _drop_unsourced(_as_ids(row.get("evidence")), allowed)
        if not evidence:
            continue
        progress.append(
            MonthlyProgress(
                id=str(row.get("id") or f"cp-{len(progress)+1}"),
                title=str(row.get("title") or ""),
                body=str(row.get("body") or ""),
                state=str(row.get("state") or "advanced"),
                weeks=tuple(str(x) for x in (row.get("weeks") or [])),
                entity_keys=tuple(str(x) for x in (row.get("entity_keys") or [])),
                evidence=evidence,
            )
        )
        if len(progress) >= CAP_PROGRESS:
            break

    decisions: list[MonthlyDecision] = []
    groups_by_id = {str(g["id"]): g for g in facts.dp_groups}
    llm_decisions = [row for row in (args.get("key_decisions") or []) if isinstance(row, dict)]
    for row in llm_decisions:
        mem_id = str(row.get("id") or "")
        group = groups_by_id.get(mem_id)
        block = facts.blocks_by_id.get(mem_id)
        if group is None or group.get("type") != "decision":
            if block is None or block.type != "decision":
                continue
            group = {
                "id": mem_id,
                "kind": _lead_kind(block.clause),
                "text": _verbatim_clause(block) if _verbatim_clause(block) else block.clause,
                "date": block.valid_from,
                "valid_to": block.valid_to,
                "entity_keys": (block.entity_key,) if block.entity_key else (),
                "supersedes": block.supersedes,
                "evidence": (mem_id,),
                "occurrence_n": 1,
                "first_seen": block.valid_from,
                "last_seen": block.valid_from,
                "strength": 0.0,
                "context": "",
                "exceptions": "",
            }
        evidence = _drop_unsourced(_as_ids(row.get("evidence")), allowed) or tuple(group.get("evidence") or (mem_id,))
        why = str(row.get("why_it_matters") or "")
        if why.strip() and not evidence:
            why = ""
        group = {**group, "evidence": evidence}
        decisions.append(_group_to_decision(group, why))
        if len(decisions) >= CAP_DECISIONS:
            break
    if not decisions:
        for group in facts.dp_groups:
            if group.get("type") != "decision":
                continue
            decisions.append(_group_to_decision(group, ""))
            if len(decisions) >= CAP_DECISIONS:
                break

    procedures: list[MonthlyProcedure] = []
    llm_procs = [row for row in (args.get("key_procedures") or []) if isinstance(row, dict)]
    for row in llm_procs:
        mem_id = str(row.get("id") or "")
        group = groups_by_id.get(mem_id)
        block = facts.blocks_by_id.get(mem_id)
        if group is None or group.get("type") != "procedure":
            if block is None or block.type != "procedure":
                continue
            group = {
                "id": mem_id,
                "problem": str(row.get("problem") or ""),
                "solution": _solution_clause(block),
                "obstacles": (),
                "trigger": "",
                "entity_keys": (block.entity_key,) if block.entity_key else (),
                "weeks": (week_key_for(block.day),),
                "evidence": (mem_id,),
                "occurrence_n": 1,
                "first_seen": block.valid_from,
                "last_seen": block.valid_from,
                "strength": 0.0,
            }
        evidence = _drop_unsourced(_as_ids(row.get("evidence")), allowed) or tuple(group.get("evidence") or (mem_id,))
        insight = str(row.get("insight") or "")
        if insight.strip() and not evidence:
            insight = ""
        problem = str(group.get("problem") or "")
        group = {**group, "evidence": evidence}
        procedures.append(_group_to_procedure(group, insight, problem))
        if len(procedures) >= CAP_PROCEDURES:
            break
    if not procedures:
        for group in facts.dp_groups:
            if group.get("type") != "procedure":
                continue
            procedures.append(_group_to_procedure(group, "", str(group.get("problem") or "")))
            if len(procedures) >= CAP_PROCEDURES:
                break

    cross: list[MonthlyCrossWeekItem] = []
    for row in args.get("cross_week_items") or []:
        if not isinstance(row, dict):
            continue
        evidence = _drop_unsourced(_as_ids(row.get("evidence")), allowed)
        if not evidence:
            continue
        cross.append(
            MonthlyCrossWeekItem(
                id=str(row.get("id") or f"cw-{len(cross)+1}"),
                name=str(row.get("name") or ""),
                start_period=str(row.get("start_period") or ""),
                current_status=str(row.get("current_status") or "in_progress"),
                expected_end_period=str(row.get("expected_end_period") or ""),
                progress_this_month=str(row.get("progress_this_month") or ""),
                block_reason=str(row.get("block_reason") or ""),
                weeks=tuple(str(x) for x in (row.get("weeks") or [])),
                evidence=evidence,
            )
        )
        if len(cross) >= CAP_CROSS_WEEK:
            break

    risks: list[MonthlyRisk] = []
    for row in args.get("problems_and_risks") or []:
        if not isinstance(row, dict):
            continue
        evidence = _drop_unsourced(_as_ids(row.get("evidence")), allowed)
        if not evidence:
            continue
        risks.append(
            MonthlyRisk(
                content=str(row.get("content") or ""),
                level=str(row.get("level") or "medium"),
                suggestion=str(row.get("suggestion") or ""),
                evidence=evidence,
            )
        )
        if len(risks) >= CAP_RISKS:
            break

    cmp_raw = args.get("comparison_with_last_month") if isinstance(args.get("comparison_with_last_month"), dict) else {}
    empty_reason = ""
    if not carry.strip():
        empty_reason = "no previous month file"
        comparison = MonthlyComparison(empty_reason=empty_reason)
    else:
        def _changes(key: str) -> tuple[MonthlyComparisonChange, ...]:
            out: list[MonthlyComparisonChange] = []
            for row in cmp_raw.get(key) or []:
                if not isinstance(row, dict):
                    continue
                evidence = _drop_unsourced(_as_ids(row.get("evidence")), allowed)
                if not evidence:
                    continue
                out.append(
                    MonthlyComparisonChange(
                        text=str(row.get("text") or ""),
                        evidence=evidence,
                        from_text=str(row.get("from") or ""),
                        to_text=str(row.get("to") or ""),
                    )
                )
            return tuple(out)

        comparison = MonthlyComparison(
            unchanged=_changes("unchanged"),
            changed=_changes("changed"),
            suggestion=str(cmp_raw.get("suggestion") or ""),
        )

    focus: list[MonthlyFocus] = []
    for row in args.get("next_month_focus") or []:
        if not isinstance(row, dict):
            continue
        focus.append(
            MonthlyFocus(
                id=str(row.get("id") or f"nf-{len(focus)+1}"),
                content=str(row.get("content") or ""),
                target=str(row.get("target") or ""),
                priority=str(row.get("priority") or "medium"),
                depends_on=tuple(str(x) for x in (row.get("depends_on") or [])),
            )
        )
        if len(focus) >= CAP_FOCUS:
            break

    pref_raw = img_raw.get("decision_preference") if isinstance(img_raw.get("decision_preference"), dict) else {}
    behavior_raw = img_raw.get("behavior_pattern") if isinstance(img_raw.get("behavior_pattern"), dict) else {}
    pref_ev = _drop_unsourced(_as_ids(pref_raw.get("evidence")), allowed)
    pref_text = str(pref_raw.get("text") or "")
    if pref_text.strip() and not pref_ev:
        pref_text = ""
    beh_ev = _drop_unsourced(_as_ids(behavior_raw.get("evidence")), allowed)
    beh_text = str(behavior_raw.get("text") or "")
    if beh_text.strip() and not beh_ev:
        beh_text = ""
    user_image = MonthlyUserImage(
        goal_alignment=_evidence_text(img_raw.get("goal_alignment"), allowed),
        cognition_change=tuple(cognition),
        decision_preference=MonthlyDecisionPreference(
            text=pref_text,
            counts=dict(facts.decision_kind_counts),
            evidence=pref_ev,
        ),
        behavior_pattern=MonthlyBehaviorPattern(
            text=beh_text,
            metrics=dict(facts.behavior),
            evidence=beh_ev,
        ),
    )

    entities = tuple(
        MonthlyEntity(
            key=str(row["key"]),
            canonical=str(row["canonical"]),
            months=tuple(row["months"]),
            weeks=tuple(row.get("weeks") or ()),
            month_count=int(row["month_count"]),
            first_seen=row.get("first_seen"),
            last_seen=row.get("last_seen"),
            aliases=tuple(row.get("aliases") or ()),
        )
        for row in facts.cross_month_entities
    )
    return MonthlyPayload(
        key=month_key,
        weeks=facts.weeks,
        range=MonthlyRange(start=start.isoformat(), end=end.isoformat()),
        generated_at=generated_at,
        generator=MonthlyGenerator(
            model=model,
            stages={"map": map_calls, "reduce": 1},
            batch_tokens=8000,
        ),
        summary=_summary_from_synthesis(args, facts),
        user_image=user_image,
        core_progress=tuple(progress),
        key_decisions=tuple(decisions),
        key_procedures=tuple(procedures),
        cross_week_items=tuple(cross),
        problems_and_risks=tuple(risks),
        comparison_with_last_month=comparison,
        next_month_focus=tuple(focus),
        state=facts.state,
        entities=entities,
        metrics=facts.metrics,
    )


def synthesize_month(
    month_key: str,
    note_records: list[dict[str, Any]],
    *,
    call_oneshot: CallOneshot | None = None,
    carry: str = "",
    facts: MechanicalFacts | None = None,
) -> tuple[MonthlyPayload, dict[str, Any]]:
    """Write every narrative field in one call, from notes rather than from raw blocks."""
    facts = facts or mechanical_facts(month_key)
    notes: list[dict[str, Any]] = []
    for record in note_records:
        notes.extend(item for item in (record.get("items") or []) if isinstance(item, dict))
    prompt = build_reduce_prompt(month_key, notes, facts, carry)
    caller = call_oneshot or _default_oneshot
    previous: dict[str, Any] | None = None
    usage: dict[str, Any] = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        submit = attempt == 1
        schema = submit_month_synthesis_schema() if submit else patch_month_synthesis_schema()
        result = caller(
            prompt,
            purpose="monthly-reduce",
            force_tool_name=schema["name"],
            tool_schema=schema,
            max_tokens=REDUCE_MAX_TOKENS,
        )
        usage = result
        if result.get("failed"):
            continue
        name = str(result.get("tool_name") or "")
        args = result.get("tool_args") if isinstance(result.get("tool_args"), dict) else {}
        if submit:
            previous = args
        else:
            if name == submit_month_synthesis_schema()["name"]:
                continue
            previous = merge_field_patch(previous or {}, args)
        if isinstance(previous, dict) and previous.get("summary") is not None:
            break
    args = dict(previous or {})
    args["_notes"] = notes
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = payload_from_synthesis(
        month_key,
        args,
        facts,
        carry=carry,
        model=str(usage.get("model") or "mimo-v2.5"),
        map_calls=len(note_records),
        reduce_tokens=int(usage.get("input_tokens") or 0),
        generated_at=stamp,
    )
    usage_out = {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "prompt_tokens_est": count_tokens(prompt),
        "failed": bool(usage.get("failed")),
    }
    return payload, usage_out
