"""Monthly dataclass round-trip, caps, and evidence rules."""

from __future__ import annotations

import pytest
from monthly_schema import (
    CAP_SUMMARY,
    MonthlyDecision,
    MonthlyEntity,
    MonthlyEvidenceText,
    MonthlyPayload,
    MonthlyProcedure,
    MonthlyProgress,
    MonthlyRange,
    MonthlyState,
    MonthlySummaryItem,
    month_key,
)


def test_month_key_matches_weekly_belongs_to():
    assert month_key(2026, 8) == "2026-08"


def test_payload_round_trip_without_arcs():
    payload = MonthlyPayload(
        key="2026-08",
        weeks=("2026-W32", "2026-W33"),
        range=MonthlyRange(start="2026-08-01", end="2026-08-31"),
        summary=(MonthlySummaryItem(text="tooling month", weeks=("2026-W32",)),),
        core_progress=(
            MonthlyProgress(
                id="cp-1",
                title="weekly pipeline",
                body="retries became the deliverable",
                evidence=("mem-2026-08-16-decision-B878262C2B39",),
            ),
        ),
        entities=(
            MonthlyEntity(
                key="memorydigest",
                canonical="memory-digest",
                weeks=("2026-W31", "2026-W32", "2026-W33"),
                aliases=("记忆摘要",),
            ),
        ),
        state=(
            MonthlyState(
                id="st-1",
                text="model is mimo",
                valid_from="2026-08-13",
                status="current",
            ),
        ),
    )
    data = payload.to_dict()
    assert "arcs" not in data
    assert "week_blocks" not in str(data)
    assert data["month_key"] == "2026-08"
    assert data["entities"][0]["weeks"] == ["2026-W31", "2026-W32", "2026-W33"]
    assert data["core_progress"][0]["evidence"]
    from monthly_writer import dump_yaml, loads

    restored = loads(dump_yaml(payload))
    assert restored.entities[0].weeks == ("2026-W31", "2026-W32", "2026-W33")
    assert restored.entities[0].aliases == ("记忆摘要",)
    assert "week_blocks" not in dump_yaml(payload)


def test_empty_evidence_raises():
    with pytest.raises(ValueError):
        MonthlyEvidenceText(text="a claim", evidence=())
    with pytest.raises(ValueError):
        MonthlyDecision(
            id="mem-1",
            kind="decision",
            text="must",
            why_it_matters="matters",
            evidence=(),
        )


def test_decision_cap_enforced():
    rows = tuple(
        MonthlyDecision(
            id=f"mem-{i}",
            kind="decision",
            text="x",
            why_it_matters="y",
            evidence=("mem-1",),
        )
        for i in range(13)
    )
    with pytest.raises(ValueError):
        MonthlyPayload(key="2026-08", key_decisions=rows)


def test_v2_round_trip_preserves_guidance_fields_and_summary_bullets():
    payload = MonthlyPayload(
        key="2026-08",
        schema_version=2,
        summary=(
            MonthlySummaryItem(text="Qixi card from drafting to sharing", weeks=("2026-W34", "2026-W35")),
            MonthlySummaryItem(text="Weekly Chronicle investigation to UI fix", weeks=("2026-W35",)),
        ),
        key_decisions=(
            MonthlyDecision(
                id="mem-d1",
                kind="preference",
                text="user prefers concise review summaries",
                why_it_matters="keeps reviews scannable",
                context="when writing weekly review summaries",
                exceptions="do not shorten explicit user quotes",
                date="2026-08-05",
                valid_to="open",
                entity_keys=("memorydigest",),
                evidence=("mem-d1", "mem-d2"),
                occurrence_n=2,
                first_seen="2026-08-05",
                last_seen="2026-08-17",
                strength=1.23,
            ),
        ),
        key_procedures=(
            MonthlyProcedure(
                id="mem-p1",
                trigger="user asked to draft a reminder cron",
                problem="scheduled cron fired at the wrong cadence",
                obstacles=("scheduled cron triggered a reminder at the wrong cadence",),
                solution="treat the trigger as ad-hoc until the user confirms",
                insight="confirm cadence before arming",
                entity_keys=("hermes-cron",),
                weeks=("2026-W32",),
                evidence=("mem-p1",),
                occurrence_n=1,
                first_seen="2026-08-05",
                last_seen="2026-08-05",
                strength=0.69,
            ),
        ),
    )
    data = payload.to_dict()
    assert payload.schema_version == 2
    assert data["schema_version"] == 2
    assert data["summary"] == [
        {"text": "Qixi card from drafting to sharing", "weeks": ["2026-W34", "2026-W35"]},
        {"text": "Weekly Chronicle investigation to UI fix", "weeks": ["2026-W35"]},
    ]
    dec = data["key_decisions"][0]
    assert dec["context"] == "when writing weekly review summaries"
    assert dec["exceptions"] == "do not shorten explicit user quotes"
    assert dec["occurrence_n"] == 2
    assert dec["strength"] == 1.23
    proc = data["key_procedures"][0]
    assert proc["trigger"].startswith("user asked")
    assert proc["obstacles"][0].startswith("scheduled cron")
    assert list(dec.keys())[:6] == [
        "id",
        "kind",
        "text",
        "why_it_matters",
        "context",
        "exceptions",
    ]
    from monthly_writer import dump_yaml, loads

    restored = loads(dump_yaml(payload))
    assert restored.summary[0].text.startswith("Qixi")
    assert restored.summary[1].weeks == ("2026-W35",)
    assert restored.key_decisions[0].context == "when writing weekly review summaries"
    assert restored.key_procedures[0].obstacles[0].startswith("scheduled cron")


def test_v1_scalar_summary_loads_as_one_bullet():
    from monthly_writer import payload_from_dict

    loaded = payload_from_dict(
        {
            "month_key": "2026-06",
            "schema_version": 1,
            "summary": "a paragraph month story",
        }
    )
    assert loaded.schema_version == 1
    assert len(loaded.summary) == 1
    assert loaded.summary[0].text == "a paragraph month story"
    assert loaded.summary[0].weeks == ()


def test_summary_cap_enforced():
    rows = tuple(
        MonthlySummaryItem(text=f"story {i}", weeks=("2026-W32",))
        for i in range(CAP_SUMMARY + 1)
    )
    with pytest.raises(ValueError):
        MonthlyPayload(key="2026-08", summary=rows)
