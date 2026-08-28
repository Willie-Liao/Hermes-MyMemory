"""Serialize a month as month_status frontmatter plus a YAML body."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

_monthly = Path(__file__).resolve().parent
_mymemory = _monthly.parent
for path in (_monthly, _mymemory):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from memory_staging import _split_week_doc_frontmatter, write_cycle_status  # noqa: E402
from monthly_schema import (  # noqa: E402
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
    MonthlyMetrics,
    MonthlyPayload,
    MonthlyProcedure,
    MonthlyProgress,
    MonthlyRange,
    MonthlyRisk,
    MonthlyState,
    MonthlySummaryItem,
    MonthlyUserImage,
    payload_to_dict,
)
from monthly_state import atomic_text_write, month_file_path, monthly_staging_dir  # noqa: E402

_KEY_ORDER = (
    "schema_version",
    "cycle",
    "month_key",
    "range",
    "weeks",
    "generated_at",
    "generator",
    "summary",
    "user_image",
    "core_progress",
    "key_decisions",
    "key_procedures",
    "cross_week_items",
    "problems_and_risks",
    "comparison_with_last_month",
    "next_month_focus",
    "state",
    "entities",
    "metrics",
)

BANNED_BODY_MARKERS = ("Beginning:", "Course:", "Outcome:", "Obstacle:")
RETIRED_KEYS = (
    "options",
    "pros",
    "cons",
    "completion_rate",
    "collaboration_steps",
)


def dumps(payload: MonthlyPayload) -> str:
    """JSON twin for the HTTP bridge; not written beside YYYY-MM.md."""
    import json

    return json.dumps(payload_to_dict(payload), ensure_ascii=False, indent=2) + "\n"


def dump_yaml(payload: MonthlyPayload) -> str:
    """YAML body of YYYY-MM.md so recall can parse the month without a sidecar."""
    data = payload_to_dict(payload)
    ordered = {key: data[key] for key in _KEY_ORDER}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True)


def _tuple(value: Any) -> tuple:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    if value is None:
        return ()
    return (value,)


def _summary_items(raw: Any) -> tuple[MonthlySummaryItem, ...]:
    """Wrap a v1 scalar summary as one bullet so old files still parse without a rewrite."""
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ()
        return (MonthlySummaryItem(text=text, weeks=()),)
    rows: list[MonthlySummaryItem] = []
    for item in raw if isinstance(raw, (list, tuple)) else ():
        if isinstance(item, MonthlySummaryItem):
            rows.append(item)
            continue
        if isinstance(item, str):
            text = item.strip()
            if text:
                rows.append(MonthlySummaryItem(text=text, weeks=()))
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        rows.append(MonthlySummaryItem(text=text, weeks=_tuple(item.get("weeks"))))
    return tuple(rows)


def _evidence_text(raw: Any) -> MonthlyEvidenceText:
    if not isinstance(raw, dict):
        return MonthlyEvidenceText()
    return MonthlyEvidenceText(
        text=str(raw.get("text") or ""),
        evidence=_tuple(raw.get("evidence")),
    )


def payload_from_dict(obj: dict[str, Any]) -> MonthlyPayload:
    """Re-coerce YAML strings into dataclasses so callers never see leftover dicts."""
    img = obj.get("user_image") if isinstance(obj.get("user_image"), dict) else {}
    rng = obj.get("range") if isinstance(obj.get("range"), dict) else {}
    gen = obj.get("generator") if isinstance(obj.get("generator"), dict) else {}
    metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
    cmp_raw = obj.get("comparison_with_last_month") if isinstance(obj.get("comparison_with_last_month"), dict) else {}
    pref = img.get("decision_preference") if isinstance(img.get("decision_preference"), dict) else {}
    beh = img.get("behavior_pattern") if isinstance(img.get("behavior_pattern"), dict) else {}

    def changes(key: str) -> tuple[MonthlyComparisonChange, ...]:
        out = []
        for row in cmp_raw.get(key) or []:
            if not isinstance(row, dict):
                continue
            out.append(
                MonthlyComparisonChange(
                    text=str(row.get("text") or ""),
                    evidence=_tuple(row.get("evidence")),
                    from_text=str(row.get("from") or ""),
                    to_text=str(row.get("to") or ""),
                )
            )
        return tuple(out)

    cognition = []
    for row in img.get("cognition_change") or []:
        if not isinstance(row, dict):
            continue
        cognition.append(
            MonthlyCognitionChange(
                text=str(row.get("text") or ""),
                from_id=str(row.get("from") or ""),
                to=str(row.get("to") or ""),
                date=str(row.get("date") or ""),
                evidence=_tuple(row.get("evidence")),
            )
        )
    return MonthlyPayload(
        key=str(obj.get("month_key") or obj.get("key") or ""),
        weeks=_tuple(obj.get("weeks")),
        range=MonthlyRange(start=str(rng.get("start") or ""), end=str(rng.get("end") or "")),
        generated_at=str(obj.get("generated_at") or ""),
        generator=MonthlyGenerator(
            model=str(gen.get("model") or ""),
            stages=dict(gen.get("stages") or {}),
            batch_tokens=int(gen.get("batch_tokens") or 8000),
        ),
        summary=_summary_items(obj.get("summary")),
        user_image=MonthlyUserImage(
            goal_alignment=_evidence_text(img.get("goal_alignment")),
            cognition_change=tuple(cognition),
            decision_preference=MonthlyDecisionPreference(
                text=str(pref.get("text") or ""),
                counts=dict(pref.get("counts") or {}),
                evidence=_tuple(pref.get("evidence")),
            ),
            behavior_pattern=MonthlyBehaviorPattern(
                text=str(beh.get("text") or ""),
                metrics=dict(beh.get("metrics") or {}),
                evidence=_tuple(beh.get("evidence")),
            ),
        ),
        core_progress=tuple(
            MonthlyProgress(
                id=str(row.get("id") or ""),
                title=str(row.get("title") or ""),
                body=str(row.get("body") or ""),
                state=str(row.get("state") or "advanced"),
                weeks=_tuple(row.get("weeks")),
                entity_keys=_tuple(row.get("entity_keys")),
                evidence=_tuple(row.get("evidence")),
            )
            for row in obj.get("core_progress") or []
            if isinstance(row, dict)
        ),
        key_decisions=tuple(
            MonthlyDecision(
                id=str(row.get("id") or ""),
                kind=str(row.get("kind") or "decision"),
                text=str(row.get("text") or ""),
                why_it_matters=str(row.get("why_it_matters") or ""),
                context=str(row.get("context") or ""),
                exceptions=str(row.get("exceptions") or ""),
                date=str(row.get("date") or ""),
                valid_to=str(row.get("valid_to") or ""),
                entity_keys=_tuple(row.get("entity_keys")),
                supersedes=_tuple(row.get("supersedes")),
                evidence=_tuple(row.get("evidence")),
                occurrence_n=int(row.get("occurrence_n") or 1),
                first_seen=str(row.get("first_seen") or ""),
                last_seen=str(row.get("last_seen") or ""),
                strength=float(row.get("strength") or 0.0),
            )
            for row in obj.get("key_decisions") or []
            if isinstance(row, dict)
        ),
        key_procedures=tuple(
            MonthlyProcedure(
                id=str(row.get("id") or ""),
                trigger=str(row.get("trigger") or ""),
                problem=str(row.get("problem") or ""),
                obstacles=_tuple(row.get("obstacles")),
                solution=str(row.get("solution") or ""),
                insight=str(row.get("insight") or ""),
                entity_keys=_tuple(row.get("entity_keys")),
                weeks=_tuple(row.get("weeks")),
                evidence=_tuple(row.get("evidence")),
                occurrence_n=int(row.get("occurrence_n") or 1),
                first_seen=str(row.get("first_seen") or ""),
                last_seen=str(row.get("last_seen") or ""),
                strength=float(row.get("strength") or 0.0),
            )
            for row in obj.get("key_procedures") or []
            if isinstance(row, dict)
        ),
        cross_week_items=tuple(
            MonthlyCrossWeekItem(
                id=str(row.get("id") or ""),
                name=str(row.get("name") or ""),
                start_period=str(row.get("start_period") or ""),
                current_status=str(row.get("current_status") or "in_progress"),
                expected_end_period=str(row.get("expected_end_period") or ""),
                progress_this_month=str(row.get("progress_this_month") or ""),
                block_reason=str(row.get("block_reason") or ""),
                weeks=_tuple(row.get("weeks")),
                evidence=_tuple(row.get("evidence")),
            )
            for row in obj.get("cross_week_items") or []
            if isinstance(row, dict)
        ),
        problems_and_risks=tuple(
            MonthlyRisk(
                content=str(row.get("content") or ""),
                level=str(row.get("level") or "medium"),
                suggestion=str(row.get("suggestion") or ""),
                evidence=_tuple(row.get("evidence")),
            )
            for row in obj.get("problems_and_risks") or []
            if isinstance(row, dict)
        ),
        comparison_with_last_month=MonthlyComparison(
            unchanged=changes("unchanged"),
            changed=changes("changed"),
            suggestion=str(cmp_raw.get("suggestion") or ""),
            empty_reason=str(cmp_raw.get("empty_reason") or ""),
        ),
        next_month_focus=tuple(
            MonthlyFocus(
                id=str(row.get("id") or ""),
                content=str(row.get("content") or ""),
                target=str(row.get("target") or ""),
                priority=str(row.get("priority") or "medium"),
                depends_on=_tuple(row.get("depends_on")),
            )
            for row in obj.get("next_month_focus") or []
            if isinstance(row, dict)
        ),
        state=tuple(
            MonthlyState(
                id=str(row.get("id") or ""),
                text=str(row.get("text") or ""),
                valid_from=str(row.get("valid_from") or ""),
                invalid_at=row.get("invalid_at"),
                invalidated_by=row.get("invalidated_by"),
                status=str(row.get("status") or "current"),
                source=str(row.get("source") or ""),
            )
            for row in obj.get("state") or []
            if isinstance(row, dict)
        ),
        entities=tuple(
            MonthlyEntity(
                key=str(row.get("key") or ""),
                canonical=str(row.get("canonical") or ""),
                months=_tuple(row.get("months")),
                weeks=_tuple(row.get("weeks")),
                month_count=int(row.get("month_count") or 0),
                first_seen=row.get("first_seen"),
                last_seen=row.get("last_seen"),
                aliases=_tuple(row.get("aliases")),
            )
            for row in obj.get("entities") or []
            if isinstance(row, dict)
        ),
        metrics=MonthlyMetrics(
            decisions=int(metrics.get("decisions") or 0),
            procedures=int(metrics.get("procedures") or 0),
            events=int(metrics.get("events") or 0),
            facts=int(metrics.get("facts") or 0),
            active_days=int(metrics.get("active_days") or 0),
            open_decisions=int(metrics.get("open_decisions") or 0),
            superseded=int(metrics.get("superseded") or 0),
            weeks=int(metrics.get("weeks") or 0),
        ),
        schema_version=int(obj.get("schema_version") or 1),
        cycle=str(obj.get("cycle") or "monthly"),
    )


def loads(raw: str) -> MonthlyPayload:
    """Re-read a month without an LLM so a failed reduce pass cannot lose the file."""
    text = raw or ""
    stripped = text.lstrip()
    if stripped.startswith("---"):
        fm, rest = _split_week_doc_frontmatter(text)
        text = rest if fm is not None else text
        stripped = text.lstrip()
    if stripped.startswith("{"):
        import json

        obj = json.loads(text)
    else:
        obj = yaml.safe_load(text)
    if not isinstance(obj, dict):
        raise ValueError("monthly schema root must be an object")
    return payload_from_dict(obj)


def write_month(payload: MonthlyPayload) -> Path:
    """Atomic month_status + YAML so a serializer crash leaves the previous file intact."""
    path = month_file_path(payload.key)
    body = dump_yaml(payload)
    monthly_staging_dir().mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.envelope")
    text = ""
    try:
        write_cycle_status(
            tmp,
            "pending",
            key_str=payload.key,
            content=body,
            cycle="month",
        )
        text = tmp.read_text(encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)
    if not text:
        raise RuntimeError("month envelope writer produced empty text")
    atomic_text_write(path, text)
    return path


def load_month(month_key: str) -> MonthlyPayload:
    """Re-read a month without an LLM so a failed arc pass cannot lose the mechanical payload."""
    path = month_file_path(month_key)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return loads(path.read_text(encoding="utf-8"))
