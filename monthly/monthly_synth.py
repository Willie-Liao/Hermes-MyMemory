"""Reduce stage: one oneshot writes every narrative field from notes, not raw dailies."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

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
    NOTE_WORD_CAP,
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
    calendar_range,
    clause_body,
    count_tokens,
    mechanical_facts,
    previous_month_key,
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
    "Same-rule paraphrase ranks above every singleton. "
    "Same topic is not the same rule. "
    "After repeats, only a sentence that would still change the next time. "
    "A finished one-day act is not a key row. "
    "Importance does not outrank a repeat. "
    f"Return at most {CAP_DECISIONS} key_decisions and {CAP_PROCEDURES} key_procedures. "
    "When choosing key_decisions and key_procedures, read story_seeds first. A card line "
    "that only carries out one story seed is that sitting's procedure or a finished act, "
    "even if the line repeats inside that story. A key decision or key procedure is a "
    "rule that still changes a later month after that story is over. "
    "If a card's only job is to carry out one story_seed, leave it out of "
    "key_decisions and key_procedures. "
    "Write key_decisions.text and key_procedures.solution as a synthesis of the same-type "
    "cards you cite, not a paste of one clause. "
    "key_decisions.evidence may contain only decision ids. "
    "key_procedures.evidence may contain only procedure ids. "
    "One id of the other type fails that row. "
    "Copy each evidence id from a card line. Do not change the date inside an id. "
    "A key_decisions evidence id must contain -decision-. "
    "A key_procedures evidence id must contain -procedure-. "
    "Notes mix both kinds in one evidence list. Do not copy a note list onto a key row. "
    "From that list, keep only the ids that contain -decision- on a key_decisions row, "
    "and only the ids that contain -procedure- on a key_procedures row. "
    "One leftover id of the other kind fails the whole row. "
    "Do not set id. Code mints mem-YYYY-MM-key-decision-{hash} or "
    "mem-YYYY-MM-key-procedure-{hash} from the sorted evidence ids. Do not invent daily ids. "
    f"why_it_matters and insight are required and at most {NOTE_WORD_CAP} words. "
    "Cite only ids from the card lines. Do not copy Beginning/Course/Outcome/Obstacle. "
    "summary must be an array of one-line stories (text + weeks); never glue two seeds into one string. "
    "Do not invent obstacles or exceptions. "
    "Inside the tool arguments, user_image.goal_alignment, user_image.decision_preference, "
    "and user_image.behavior_pattern must each be an object with non-empty text and card ids. "
    "A sentence in thinking does not count. "
    "core_progress, problems_and_risks, and next_month_focus each need at least one row that is "
    "a judgment about the user, with evidence ids from the card lines. "
    "An empty string or an empty array is a failure, not a filled section."
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


def _card_dump_line(row: dict[str, Any]) -> str:
    """One line per card so the reduce call cannot spend tokens on indented group JSON."""
    kind = str(row.get("kind") or row.get("type") or "")
    day = str(row.get("date") or row.get("first_seen") or "")
    if row.get("type") == "procedure":
        body = str(row.get("solution") or row.get("problem") or "")
    else:
        body = str(row.get("text") or "")
    body = " ".join(body.split())
    return f"{row.get('id')} | {kind} | {day} | {body}"


def _same_type_evidence(representative: str, raw: tuple[str, ...], type_ids: set[str]) -> tuple[str, ...]:
    """Keep the representative and same-type dump ids; a procedure cite on a decision row is dropped."""
    ordered: list[str] = []
    seen: set[str] = set()
    for item in (representative, *raw):
        if item in seen or item not in type_ids:
            continue
        seen.add(item)
        ordered.append(item)
    return tuple(ordered)


def _cognition_case_line(month_key: str, facts: MechanicalFacts) -> str:
    """Tell this call whether cognition is code-filled or must be a real sentence.

    The static prefix cannot know if last month exists. Without this line the model
    sends an empty string and the month file stays unwritten.
    """
    portrait_first = (
        "In this tool call, user_image.goal_alignment, user_image.decision_preference, and "
        "user_image.behavior_pattern must each be an object with non-empty text and card ids. "
        "null fails the month. Write those three objects before comparison_with_last_month.summary."
    )
    summary_rule = (
        "Then write comparison_with_last_month.summary as one summary of how those three objects "
        "differ from last month's user_image lines on the carry card. Leave unchanged and changed empty."
    )
    if not _previous_has_judgment(month_key):
        return (
            "No previous month judgment. Leave cognition_change and comparison_with_last_month.summary empty. "
            "Code writes no information from last month. "
            + portrait_first
        )
    if facts.supersedes_pairs:
        listed = "; ".join(f"from={prior} to={current}" for prior, current in facts.supersedes_pairs)
        return (
            portrait_first
            + " Previous month has a judgment. cognition_change must be a non-empty array. "
            "Copy one pair exactly, from and to unchanged, and put that to id in evidence: "
            + listed
            + ". Do not invent a pair. Do not send an empty string and do not send the phrase "
            "no information from last month. "
            + summary_rule
        )
    return (
        portrait_first
        + " Previous month has a judgment. Write cognition_change as text plus evidence only. "
        "Do not send an empty string and do not send the phrase no information from last month. "
        + summary_rule
    )


def build_reduce_prompt(
    month_key: str,
    notes: list[dict[str, Any]],
    facts: MechanicalFacts,
    carry: str,
) -> str:
    cards = "\n".join(_card_dump_line(row) for row in facts.dp_groups)
    payload = {
        "month_key": month_key,
        "notes": notes,
        "metrics": {
            "decisions": facts.metrics.decisions,
            "procedures": facts.metrics.procedures,
            "events": facts.metrics.events,
        },
        "story_seeds": [{"text": row.get("text"), "weeks": list(row.get("weeks") or ())} for row in facts.story_seeds],
        "carry_card": carry,
        "supersedes_pairs": list(facts.supersedes_pairs),
    }
    return (
        f"{REDUCE_PREFIX}\n{_cognition_case_line(month_key, facts)}\n\nCards:\n{cards}\n\n---\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


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


def _mint_month_key_id(month_key: str, kind: str, evidence: tuple[str, ...]) -> str:
    """Mint one month id from the cited daily cards so a re-reduce does not fork the row.

    Hashing the sorted evidence ids keeps the same synthesis addressable after
    the daily clauses are no longer copied into the key row.
    """
    digest = hashlib.sha256("\n".join(sorted(evidence)).encode("utf-8")).hexdigest()[:12]
    label = "key-decision" if kind == "decision" else "key-procedure"
    return f"mem-{month_key}-{label}-{digest}"


def _cited_card_ids(raw: tuple[str, ...], card_ids: set[str]) -> tuple[str, ...] | None:
    """Drop the row unless every cite is a card line of this type.

    Stripping an off-board id would still let a one-day act ride along inside a standing rule.
    """
    if not raw or any(item not in card_ids for item in raw):
        return None
    return tuple(dict.fromkeys(raw))


def _group_to_decision(
    group: dict[str, Any],
    why: str,
    *,
    text: str,
    minted_id: str,
) -> MonthlyDecision:
    """Store the month's synthesized ruling so guidance does not retrieve one sitting's clause."""
    cleaned = text.strip()
    for prefix in ("Decision:", "Preference:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    return MonthlyDecision(
        id=minted_id,
        kind=str(group.get("kind") or "decision"),
        text=cleaned,
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


def _group_to_procedure(
    group: dict[str, Any],
    insight: str,
    problem: str,
    *,
    solution: str,
    minted_id: str,
) -> MonthlyProcedure:
    """Store the month's synthesized solution so a key procedure is not one day's clause."""
    cleaned = solution.strip()
    if len(cleaned) > SOLUTION_CHAR_CAP:
        cleaned = cleaned[:SOLUTION_CHAR_CAP]
    obstacles = tuple(group.get("obstacles") or ())
    return MonthlyProcedure(
        id=minted_id,
        trigger=str(group.get("trigger") or ""),
        problem=problem or str(group.get("problem") or (obstacles[0] if obstacles else "")),
        obstacles=obstacles,
        solution=cleaned,
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
    comparison_exempt: bool = False,
) -> MonthlyPayload:
    """Bind synthesis args onto mechanical facts so a blank or cross-type key row cannot land in the month file.

    Bilingual aliases stay on the entity roster. Key order is the model's order.
    """
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
    cognition_empty_reason = ""
    if comparison_exempt:
        cognition_empty_reason = _NO_LAST_MONTH
    else:
        pair_set = set(facts.supersedes_pairs)
        for row in img_raw.get("cognition_change") or []:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text") or "").strip()
            if not text or text == _NO_LAST_MONTH:
                continue
            frm = str(row.get("from") or "")
            to = str(row.get("to") or "")
            evidence = _drop_unsourced(_as_ids(row.get("evidence")), allowed)
            if pair_set:
                if (frm, to) not in pair_set:
                    continue
                evidence = evidence or ((to,) if to in allowed else ())
                if not evidence:
                    continue
                cognition.append(
                    MonthlyCognitionChange(
                        text=text,
                        from_id=frm,
                        to=to,
                        date=str(row.get("date") or (facts.blocks_by_id[to].valid_from if to in facts.blocks_by_id else "")),
                        evidence=evidence,
                    )
                )
                continue
            if frm or to or not evidence:
                continue
            cognition.append(
                MonthlyCognitionChange(
                    text=text,
                    from_id="",
                    to="",
                    date=str(row.get("date") or ""),
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
    decision_ids = {b.id for b in facts.all_dpe if b.type == "decision"}
    procedure_ids = {b.id for b in facts.all_dpe if b.type == "procedure"}
    llm_decisions = [row for row in (args.get("key_decisions") or []) if isinstance(row, dict)]
    for row in llm_decisions:
        why_words = str(row.get("why_it_matters") or "").split()
        text = " ".join(str(row.get("text") or "").split())
        if not why_words or len(why_words) > NOTE_WORD_CAP or not text:
            continue
        why = " ".join(why_words)
        evidence = _cited_card_ids(_as_ids(row.get("evidence")), decision_ids)
        if not evidence:
            continue
        cited = [facts.blocks_by_id[i] for i in evidence if i in facts.blocks_by_id]
        dates = [b.valid_from for b in cited if b.valid_from]
        group = {
            "kind": str(row.get("kind") or (_lead_kind(cited[0].clause) if cited else "decision")),
            "context": str(row.get("context") or ""),
            "exceptions": str(row.get("exceptions") or ""),
            "date": dates[0] if dates else "",
            "valid_to": str(row.get("valid_to") or (cited[-1].valid_to if cited else "")),
            "entity_keys": tuple(
                dict.fromkeys(b.entity_key for b in cited if b.entity_key)
            ),
            "supersedes": tuple(
                dict.fromkeys(sid for b in cited for sid in (b.supersedes or ()))
            ),
            "evidence": evidence,
            "occurrence_n": len(evidence),
            "first_seen": min(dates) if dates else "",
            "last_seen": max(dates) if dates else "",
            "strength": float(row.get("strength") or 0.0),
        }
        decisions.append(
            _group_to_decision(
                group,
                why,
                text=text,
                minted_id=_mint_month_key_id(month_key, "decision", evidence),
            )
        )
        if len(decisions) >= CAP_DECISIONS:
            break

    procedures: list[MonthlyProcedure] = []
    llm_procs = [row for row in (args.get("key_procedures") or []) if isinstance(row, dict)]
    for row in llm_procs:
        insight_words = str(row.get("insight") or "").split()
        solution = " ".join(str(row.get("solution") or "").split())
        if not insight_words or len(insight_words) > NOTE_WORD_CAP or not solution:
            continue
        insight = " ".join(insight_words)
        evidence = _cited_card_ids(_as_ids(row.get("evidence")), procedure_ids)
        if not evidence:
            continue
        cited = [facts.blocks_by_id[i] for i in evidence if i in facts.blocks_by_id]
        dates = [b.valid_from for b in cited if b.valid_from]
        weeks = tuple(
            dict.fromkeys(week_key_for(b.day) for b in cited if b.day)
        )
        group = {
            "trigger": str(row.get("trigger") or ""),
            "obstacles": tuple(row.get("obstacles") or ()),
            "entity_keys": tuple(
                dict.fromkeys(b.entity_key for b in cited if b.entity_key)
            ),
            "weeks": weeks,
            "evidence": evidence,
            "occurrence_n": len(evidence),
            "first_seen": min(dates) if dates else "",
            "last_seen": max(dates) if dates else "",
            "strength": float(row.get("strength") or 0.0),
        }
        procedures.append(
            _group_to_procedure(
                group,
                insight,
                str(row.get("problem") or ""),
                solution=solution,
                minted_id=_mint_month_key_id(month_key, "procedure", evidence),
            )
        )
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
    if comparison_exempt or not carry.strip():
        empty_reason = "no information from last month"
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
            summary=" ".join(str(cmp_raw.get("summary") or "").split()),
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
        cognition_empty_reason=cognition_empty_reason,
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


_LIST_FIELDS = ("evidence", "obstacles", "weeks", "entity_keys", "depends_on")
_TEXT_FIELDS = (
    "text",
    "body",
    "title",
    "why_it_matters",
    "context",
    "exceptions",
    "trigger",
    "problem",
    "solution",
    "insight",
    "content",
    "suggestion",
    "target",
    "level",
    "priority",
    "state",
)
_NO_LAST_MONTH = "no information from last month"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_node(node: Any, fixes: list[str], path: str) -> Any:
    """Collapse whitespace and wrap a lone evidence string so shape never needs a model retry."""
    if isinstance(node, list):
        return [_normalize_node(item, fixes, path) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        here = f"{path}.{key}" if path else key
        if key in _LIST_FIELDS and isinstance(value, str):
            fixes.append(f"{here} string -> list")
            out[key] = _string_list(value)
            continue
        if key in _TEXT_FIELDS and isinstance(value, str):
            collapsed = " ".join(value.split())
            if collapsed != value:
                fixes.append(f"{here} whitespace")
            out[key] = collapsed
            continue
        out[key] = _normalize_node(value, fixes, here)
    return out


def normalize_synthesis_args(
    args: Mapping[str, Any], facts: MechanicalFacts
) -> tuple[dict[str, Any], list[str]]:
    """Drop a foreign id that rode in on a mixed note list before the gap check.

    Map notes put decision and procedure ids in one evidence array. One leftover
    foreign id fails the whole key row even when a same-type id is already there.
    A core_progress or problems row with one unknown id fails the same way.
    """
    fixes: list[str] = []
    normalized = _normalize_node(dict(args), fixes, "")
    image = normalized.get("user_image")
    if not isinstance(image, dict):
        image = {}
        normalized["user_image"] = image
    pref = image.get("decision_preference")
    if isinstance(pref, dict) and "counts" not in pref:
        pref["counts"] = dict(facts.decision_kind_counts)
        fixes.append("decision_preference.counts filled from mechanical facts")
    behavior = image.get("behavior_pattern")
    if isinstance(behavior, dict) and "metrics" not in behavior:
        behavior["metrics"] = dict(facts.behavior)
        fixes.append("behavior_pattern.metrics filled from mechanical facts")
    decision_ids = {block.id for block in facts.all_dpe if block.type == "decision"}
    procedure_ids = {block.id for block in facts.all_dpe if block.type == "procedure"}
    for key, keep in (("key_decisions", decision_ids), ("key_procedures", procedure_ids)):
        rows = normalized.get(key)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            ids = _string_list(row.get("evidence"))
            kept = [item for item in ids if item in keep]
            if not kept or len(kept) == len(ids):
                continue
            row["evidence"] = kept
            fixes.append(f"{key}[{index}].evidence dropped ids that are not {key} card ids")
    allowed = allowed_ids_from_facts(facts, [])
    for key in ("core_progress", "problems_and_risks"):
        rows = normalized.get(key)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            ids = _string_list(row.get("evidence"))
            kept = [item for item in ids if item in allowed]
            if not kept or len(kept) == len(ids):
                continue
            row["evidence"] = kept
            fixes.append(f"{key}[{index}].evidence dropped ids that are not card ids")
    return normalized, fixes


def _words(text: Any) -> list[str]:
    return str(text or "").split()


def _missing_text(value: Any) -> bool:
    return not str(value or "").strip()


def _bad_evidence(value: Any, allowed: set[str]) -> bool:
    ids = _string_list(value)
    return not ids or any(item not in allowed for item in ids)


def _row_gaps(
    label: str,
    row: Mapping[str, Any],
    fields: tuple[str, ...],
    allowed: set[str],
    foreign: set[str] | None = None,
    foreign_kind: str = "",
) -> list[str]:
    """Name the bad id so the next try can drop that string instead of resending the row.

    A wrong type and a date the model rewrote both used to come back as the same
    unnamed line, so the patch kept the id that was not on a card line.
    """
    gaps: list[str] = []
    for field in fields:
        if field in ("evidence",):
            continue
        if field in ("obstacles", "weeks", "entity_keys", "depends_on"):
            if not _string_list(row.get(field)):
                gaps.append(f"{label}.{field} is empty")
            continue
        if field in ("why_it_matters", "insight"):
            words = _words(row.get(field))
            if not words:
                gaps.append(f"{label}.{field} is empty")
            elif len(words) > NOTE_WORD_CAP:
                gaps.append(f"{label}.{field} exceeds {NOTE_WORD_CAP} words")
            continue
        if _missing_text(row.get(field)):
            gaps.append(f"{label}.{field} is empty")
    if _bad_evidence(row.get("evidence"), allowed):
        cited = next(
            (item for item in _string_list(row.get("evidence")) if foreign and item in foreign),
            "",
        )
        missing = next(
            (item for item in _string_list(row.get("evidence")) if item not in allowed),
            "",
        )
        if cited and foreign_kind:
            gaps.append(f"{label}.evidence cites {foreign_kind} id {cited}")
        elif missing:
            gaps.append(f"{label}.evidence cites {missing}, which is not a given card id")
        else:
            gaps.append(f"{label}.evidence is missing or not a given card id")
    return gaps


def judgment_gaps(
    args: Mapping[str, Any],
    facts: MechanicalFacts,
    *,
    comparison_exempt: bool,
) -> list[str]:
    """Name every required section that still lacks a user judgment.

    An empty list is the only signal that the month file may be written.
    """
    allowed = allowed_ids_from_facts(facts, list(args.get("_notes") or []))
    decision_ids = {block.id for block in facts.all_dpe if block.type == "decision"}
    procedure_ids = {block.id for block in facts.all_dpe if block.type == "procedure"}
    gaps: list[str] = []
    image = args.get("user_image") if isinstance(args.get("user_image"), dict) else {}
    for key in ("goal_alignment", "decision_preference", "behavior_pattern"):
        row = image.get(key) if isinstance(image.get(key), dict) else {}
        if _missing_text(row.get("text")):
            gaps.append(f"user_image.{key}.text is empty")
        if _bad_evidence(row.get("evidence"), allowed):
            missing = next(
                (item for item in _string_list(row.get("evidence")) if item not in allowed),
                "",
            )
            if missing:
                gaps.append(
                    f"user_image.{key}.evidence cites {missing}, which is not a given card id"
                )
            else:
                gaps.append(f"user_image.{key}.evidence is missing or not a given card id")
    changes = image.get("cognition_change") if isinstance(image.get("cognition_change"), list) else []
    if not comparison_exempt:
        if facts.supersedes_pairs:
            if not changes:
                gaps.append("user_image.cognition_change is empty")
            else:
                pair_set = set(facts.supersedes_pairs)
                ok = False
                for index, row in enumerate(changes):
                    if not isinstance(row, dict):
                        gaps.append(f"user_image.cognition_change[{index}] is not an object")
                        continue
                    if str(row.get("text") or "").strip() == _NO_LAST_MONTH:
                        gaps.append(f"user_image.cognition_change[{index}] is the placeholder phrase")
                        continue
                    pair = (str(row.get("from") or ""), str(row.get("to") or ""))
                    if pair not in pair_set:
                        gaps.append(f"user_image.cognition_change[{index}] is not a supersedes pair")
                        continue
                    if _missing_text(row.get("text")) or _missing_text(row.get("date")):
                        gaps.append(f"user_image.cognition_change[{index}] text or date is empty")
                        continue
                    if _bad_evidence(row.get("evidence"), allowed):
                        gaps.append(f"user_image.cognition_change[{index}].evidence is missing")
                        continue
                    ok = True
                if not ok:
                    gaps.append("user_image.cognition_change has no valid row")
        elif not changes:
            gaps.append("user_image.cognition_change is empty")
        else:
            ok = False
            for index, row in enumerate(changes):
                if not isinstance(row, dict):
                    gaps.append(f"user_image.cognition_change[{index}] is not an object")
                    continue
                if str(row.get("from") or "").strip() or str(row.get("to") or "").strip():
                    gaps.append(f"user_image.cognition_change[{index}] must omit from and to")
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    gaps.append(f"user_image.cognition_change[{index}] text is empty")
                    continue
                if text == _NO_LAST_MONTH:
                    gaps.append(f"user_image.cognition_change[{index}] is the placeholder phrase")
                    continue
                if _bad_evidence(row.get("evidence"), allowed):
                    gaps.append(f"user_image.cognition_change[{index}].evidence is missing")
                    continue
                ok = True
            if not ok:
                gaps.append("user_image.cognition_change has no valid row")
    for key, fields, id_set, foreign, foreign_kind in (
        (
            "core_progress",
            ("title", "body", "state", "weeks", "entity_keys"),
            allowed,
            set(),
            "",
        ),
        (
            "key_decisions",
            ("text", "why_it_matters", "context", "exceptions"),
            decision_ids,
            procedure_ids,
            "procedure",
        ),
        (
            "key_procedures",
            ("trigger", "problem", "obstacles", "solution", "insight"),
            procedure_ids,
            decision_ids,
            "decision",
        ),
        ("problems_and_risks", ("content", "level", "suggestion"), allowed, set(), ""),
    ):
        rows = args.get(key)
        if not isinstance(rows, list) or not rows:
            gaps.append(f"{key} is empty")
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                gaps.append(f"{key}[{index}] is not an object")
                continue
            gaps.extend(
                _row_gaps(
                    f"{key}[{index}]",
                    row,
                    fields,
                    id_set,
                    foreign,
                    foreign_kind,
                )
            )
    focus = args.get("next_month_focus")
    if not isinstance(focus, list) or not focus:
        gaps.append("next_month_focus is empty")
    else:
        for index, row in enumerate(focus):
            if not isinstance(row, dict):
                gaps.append(f"next_month_focus[{index}] is not an object")
                continue
            for field in ("content", "target", "priority"):
                if _missing_text(row.get(field)):
                    gaps.append(f"next_month_focus[{index}].{field} is empty")
            if "depends_on" not in row:
                gaps.append(f"next_month_focus[{index}].depends_on is missing")
    if not comparison_exempt:
        cmp_raw = args.get("comparison_with_last_month")
        summary = ""
        if isinstance(cmp_raw, dict):
            summary = " ".join(str(cmp_raw.get("summary") or "").split())
        if not summary or summary == _NO_LAST_MONTH:
            gaps.append("comparison_with_last_month.summary is empty")
    return gaps


def _section_keys(gaps: list[str]) -> list[str]:
    found: list[str] = []
    for line in gaps:
        head = line.split()[0].split(".", 1)[0].split("[", 1)[0].strip()
        if head and head not in found:
            found.append(head)
    return found


def _previous_has_judgment(month_key: str) -> bool:
    """True when last month already holds one of the six judgment sections.

    cognition_empty_reason is the code placeholder for a missing prior month.
    Counting it would force the next month to invent a change.
    """
    from monthly_writer import load_month

    start, _end = calendar_range(month_key)
    try:
        payload = load_month(previous_month_key(start))
    except (FileNotFoundError, OSError, ValueError):
        return False
    image = payload.user_image
    texts = (
        image.goal_alignment.text,
        image.decision_preference.text,
        image.behavior_pattern.text,
    )
    real_cognition = [
        row
        for row in image.cognition_change
        if str(row.text or "").strip() and str(row.text).strip() != _NO_LAST_MONTH
    ]
    if any(str(text or "").strip() for text in texts) or real_cognition:
        return True
    return bool(
        payload.core_progress
        or payload.key_decisions
        or payload.key_procedures
        or payload.problems_and_risks
        or payload.next_month_focus
    )


def synthesize_month(
    month_key: str,
    note_records: list[dict[str, Any]],
    *,
    call_oneshot: CallOneshot | None = None,
    carry: str = "",
    facts: MechanicalFacts | None = None,
) -> tuple[MonthlyPayload, dict[str, Any]]:
    """Submit the month once, then patch only sections that still lack a judgment.

    A second full submit dropped the first payload. Format mistakes are fixed here
    so they never spend another model call.
    """
    facts = facts or mechanical_facts(month_key)
    notes: list[dict[str, Any]] = []
    for record in note_records:
        notes.extend(item for item in (record.get("items") or []) if isinstance(item, dict))
    prompt = build_reduce_prompt(month_key, notes, facts, carry)
    caller = call_oneshot or _default_oneshot
    comparison_exempt = not _previous_has_judgment(month_key)
    merged: dict[str, Any] = {}
    gaps = ["user_image is empty"]
    attempts: list[dict[str, Any]] = []
    last_model = "MiniMax-M2.5"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt == 1:
            schema = submit_month_synthesis_schema()
            call_prompt = prompt
            reason = ""
        else:
            sections = _section_keys(gaps)
            schema = patch_month_synthesis_schema(sections)
            teaching = "Fix only these gaps:\n" + "\n".join(f"- {line}" for line in gaps)
            call_prompt = (
                f"{prompt}\n\nPrevious JSON:\n"
                f"{json.dumps(merged, ensure_ascii=False)}\n\n{teaching}"
            )
            reason = teaching
        started = time.perf_counter()
        result = caller(
            call_prompt,
            purpose="monthly-reduce",
            force_tool_name=schema["name"],
            tool_schema=schema,
            max_tokens=REDUCE_MAX_TOKENS,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        last_model = str(result.get("model") or last_model)
        input_tokens = int(result.get("input_tokens") or 0)
        output_tokens = int(result.get("output_tokens") or 0)
        if result.get("failed"):
            attempts.append(
                {
                    "n": attempt,
                    "tool": schema["name"],
                    "purpose": "monthly-reduce",
                    "reason": reason or "transport failed",
                    "format_fixes": [],
                    "failed": list(gaps),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "duration_ms": duration_ms,
                }
            )
            continue
        incoming = result.get("tool_args") if isinstance(result.get("tool_args"), dict) else {}
        if attempt == 1:
            merged = dict(incoming)
        elif str(result.get("tool_name") or "") == submit_month_synthesis_schema()["name"]:
            attempts.append(
                {
                    "n": attempt,
                    "tool": schema["name"],
                    "purpose": "monthly-reduce",
                    "reason": reason,
                    "format_fixes": [],
                    "failed": list(gaps),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "duration_ms": duration_ms,
                }
            )
            continue
        else:
            merged = merge_field_patch(merged, incoming)
        merged, format_fixes = normalize_synthesis_args(merged, facts)
        if comparison_exempt:
            merged["comparison_with_last_month"] = {
                "unchanged": [],
                "changed": [],
                "suggestion": "",
                "summary": "",
                "empty_reason": _NO_LAST_MONTH,
            }
            format_fixes.append("comparison_with_last_month empty_reason set by code")
            image = merged.get("user_image") if isinstance(merged.get("user_image"), dict) else {}
            image = dict(image)
            image["cognition_change"] = []
            image["cognition_empty_reason"] = _NO_LAST_MONTH
            merged["user_image"] = image
            format_fixes.append("cognition_change empty_reason set by code")
        gaps = judgment_gaps(merged, facts, comparison_exempt=comparison_exempt)
        attempts.append(
            {
                "n": attempt,
                "tool": schema["name"],
                "purpose": "monthly-reduce",
                "reason": reason,
                "format_fixes": format_fixes,
                "failed": list(gaps),
                "retired": [],
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "duration_ms": duration_ms,
            }
        )
        if not gaps:
            break
    for index in range(1, len(attempts)):
        before = set(_section_keys(list(attempts[index - 1].get("failed") or [])))
        after = set(_section_keys(list(attempts[index].get("failed") or [])))
        retired = []
        for section in sorted(before - after):
            sent = sum(
                1
                for prior in attempts[:index]
                if section in _section_keys(list(prior.get("failed") or []))
            )
            retired.append(f"{section} (attempt {attempts[index]['n']}, sent back {sent})")
        attempts[index]["retired"] = retired
    merged["_notes"] = notes
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = payload_from_synthesis(
        month_key,
        merged,
        facts,
        carry=carry,
        model=last_model,
        map_calls=len(note_records),
        reduce_tokens=sum(int(row["input_tokens"]) for row in attempts),
        generated_at=stamp,
        comparison_exempt=comparison_exempt,
    )
    usage_out = {
        "input_tokens": sum(int(row["input_tokens"]) for row in attempts),
        "output_tokens": sum(int(row["output_tokens"]) for row in attempts),
        "total_tokens": sum(int(row["total_tokens"]) for row in attempts),
        "cache_read_tokens": 0,
        "prompt_tokens_est": count_tokens(prompt),
        "failed": bool(gaps),
        "valid": not gaps,
        "model": last_model,
        "attempts": attempts,
    }
    return payload, usage_out
