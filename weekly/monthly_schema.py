"""Pin the monthly JSON shape so weekly belongs_to can join a month that is not generated yet.

A writer, cron, or UI here would invent a second monthly stack before any monthly distill exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def month_key(year: int, month: int) -> str:
    """Format YYYY-MM so weekly belongs_to matches the monthly file stem."""
    return f"{year:04d}-{month:02d}"


@dataclass(frozen=True)
class MonthlyEntity:
    """Reuse the weekly roster record so month and week join on the same entity key."""

    key: str
    canonical: str
    aliases: tuple[str, ...] = ()
    first_seen: str | None = None
    last_seen: str | None = None
    week_blocks: tuple[str, ...] = ()
    embedding: None = None


@dataclass(frozen=True)
class MonthlyArc:
    """Group weekly threads across a month without copying event bodies into L4."""

    id: str
    label: str
    weeks: tuple[str, ...] = ()
    thread_ids: tuple[str, ...] = ()
    entity_keys: tuple[str, ...] = ()
    state: str = "open"
    evidence: tuple[str, ...] = ()
    text: str = ""
    confidence: str = "high"


@dataclass(frozen=True)
class MonthlyState:
    """Close a prior claim with invalid_at so contradiction never deletes history."""

    id: str
    text: str
    valid_from: str
    invalid_at: str | None = None
    invalidated_by: str | None = None
    status: str = "current"


@dataclass(frozen=True)
class MonthlyOpen:
    """Keep unfinished questions off arc prose so open work stays queryable."""

    arc_id: str
    question: str


@dataclass(frozen=True)
class MonthlyPayload:
    """Month sidecar: arcs, roster, bi-temporal state. No event bodies, no weekly Distill."""

    key: str
    weeks: tuple[str, ...] = ()
    arcs: tuple[MonthlyArc, ...] = ()
    entities: tuple[MonthlyEntity, ...] = ()
    state: tuple[MonthlyState, ...] = ()
    open: tuple[MonthlyOpen, ...] = ()
    schema_version: int = 1
    cycle: str = "monthly"
    range: dict[str, str] = field(default_factory=dict)
    generated_at: str = ""
    generator: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """asdict so a later writer can round-trip without inventing a second schema."""
        return asdict(self)
