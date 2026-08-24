"""Monthly dataclass round-trip, caps, and evidence rules."""

from __future__ import annotations

import pytest
from monthly_schema import (
    MonthlyDecision,
    MonthlyEntity,
    MonthlyEvidenceText,
    MonthlyPayload,
    MonthlyProgress,
    MonthlyRange,
    MonthlyState,
    month_key,
)


def test_month_key_matches_weekly_belongs_to():
    assert month_key(2026, 8) == "2026-08"


def test_payload_round_trip_without_arcs():
    payload = MonthlyPayload(
        key="2026-08",
        weeks=("2026-W32", "2026-W33"),
        range=MonthlyRange(start="2026-08-01", end="2026-08-31"),
        summary="tooling month",
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
