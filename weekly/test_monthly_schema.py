"""Monthly dataclasses only: round-trip and belongs_to key format, no writer."""

from __future__ import annotations

from monthly_schema import (
    MonthlyArc,
    MonthlyEntity,
    MonthlyOpen,
    MonthlyPayload,
    MonthlyState,
    month_key,
)


def test_month_key_matches_weekly_belongs_to():
    assert month_key(2026, 8) == "2026-08"


def test_payload_round_trip_has_arcs_state_open():
    payload = MonthlyPayload(
        key="2026-08",
        weeks=("2026-W31", "2026-W33"),
        arcs=(
            MonthlyArc(
                id="aug-a1",
                label="digest",
                weeks=("2026-W33",),
                thread_ids=("w33-t1",),
                entity_keys=("memorydigest",),
            ),
        ),
        entities=(
            MonthlyEntity(key="memorydigest", canonical="memory-digest"),
        ),
        state=(
            MonthlyState(
                id="st-1",
                text="model is mimo",
                valid_from="2026-08-13",
                invalid_at=None,
                invalidated_by=None,
                status="current",
            ),
        ),
        open=(MonthlyOpen(arc_id="aug-a1", question="truncation fixed?"),),
    )
    data = payload.to_dict()
    restored = MonthlyPayload(
        key=data["key"],
        weeks=tuple(data["weeks"]),
        arcs=(MonthlyArc(**data["arcs"][0]),),
        entities=(MonthlyEntity(**data["entities"][0]),),
        state=(MonthlyState(**data["state"][0]),),
        open=(MonthlyOpen(**data["open"][0]),),
        schema_version=data["schema_version"],
        cycle=data["cycle"],
    )
    assert restored.key == "2026-08"
    assert restored.key == month_key(2026, 8)
    assert restored.state[0].invalid_at is None
    assert restored.arcs[0].thread_ids == ("w33-t1",)
