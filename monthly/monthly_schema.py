"""Pin the monthly synthesis shape so a month file cannot be built as a second daily index.

An extract of every decision clause would duplicate L2 and skip the judgment
this layer exists to record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CAP_DECISIONS = 12
CAP_PROCEDURES = 8
CAP_SUMMARY = 8
CAP_PROGRESS = 6
CAP_CROSS_WEEK = 6
CAP_RISKS = 5
CAP_FOCUS = 5
CAP_NOTE_ITEMS = 6
NOTE_WORD_CAP = 40
MAP_BATCH_TOKENS = 8000
REDUCE_MAX_TOKENS = 4096
SOLUTION_CHAR_CAP = 200
CARRY_CARD_TOKEN_CAP = 600


def month_key(year: int, month: int) -> str:
    """Format YYYY-MM so weekly belongs_to matches the monthly file stem."""
    return f"{year:04d}-{month:02d}"


def _require_evidence(text: str, evidence: tuple[str, ...], label: str) -> None:
    """Refuse a synthesized body with no cites so recall cannot quote an unchecked claim."""
    if str(text or "").strip() and not evidence:
        raise ValueError(f"{label} body requires evidence")


@dataclass(frozen=True)
class MonthlyRange:
    """Calendar span of the month so week-slice membership is not guessed from belongs_to."""

    start: str
    end: str


@dataclass(frozen=True)
class MonthlyGenerator:
    """Record map/reduce call counts so a silent extra LLM pass is visible in the file."""

    model: str = ""
    stages: dict[str, int] = field(default_factory=dict)
    batch_tokens: int = MAP_BATCH_TOKENS


@dataclass(frozen=True)
class MonthlyEvidenceText:
    """Keep judgment next to the ids that licensed it so a paraphrase cannot hide its source."""

    text: str = ""
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_evidence(self.text, self.evidence, "evidence text")


@dataclass(frozen=True)
class MonthlyCognitionChange:
    """Anchor belief change on a supersedes pair so the model cannot invent a reversal."""

    text: str
    from_id: str
    to: str
    date: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_evidence(self.text, self.evidence, "cognition_change")
        if self.text.strip() and (not self.from_id or not self.to):
            raise ValueError("cognition_change requires from and to ids")


@dataclass(frozen=True)
class MonthlyDecisionPreference:
    """Separate counted Decision:/Preference: tallies from the one-line preference claim."""

    text: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_evidence(self.text, self.evidence, "decision_preference")


@dataclass(frozen=True)
class MonthlyBehaviorPattern:
    """Keep burst/role counts mechanical so the narrative cannot invent a collaborator."""

    text: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_evidence(self.text, self.evidence, "behavior_pattern")


@dataclass(frozen=True)
class MonthlyUserImage:
    """Hold the four portrait fields the month exists to answer, not a roster of blocks."""

    goal_alignment: MonthlyEvidenceText = field(default_factory=MonthlyEvidenceText)
    cognition_change: tuple[MonthlyCognitionChange, ...] = ()
    decision_preference: MonthlyDecisionPreference = field(
        default_factory=MonthlyDecisionPreference
    )
    behavior_pattern: MonthlyBehaviorPattern = field(default_factory=MonthlyBehaviorPattern)


@dataclass(frozen=True)
class MonthlySummaryItem:
    """One main month story as a single line so Band D cannot glue two threads into a paragraph.

    Weeks stay ISO week keys (not Monday–Sunday names) because a month story spans weekly files.
    """

    text: str
    weeks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.text).strip():
            raise ValueError("MonthlySummaryItem.text must be non-empty")


@dataclass(frozen=True)
class MonthlyProgress:
    """Group a cross-week story without copying Distill bodies into L4."""

    id: str
    title: str
    body: str
    state: str = "advanced"
    weeks: tuple[str, ...] = ()
    entity_keys: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_evidence(self.body, self.evidence, "core_progress")


@dataclass(frozen=True)
class MonthlyDecision:
    """Keep the ruling verbatim so an LLM rewording cannot silently change a must-not.

    Context and exceptions exist so recall can match *when* the preference applies
    instead of retrieving a standing rule for every chat turn.
    """

    id: str
    kind: str
    text: str
    why_it_matters: str = ""
    context: str = ""
    exceptions: str = ""
    date: str = ""
    valid_to: str = ""
    entity_keys: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    occurrence_n: int = 1
    first_seen: str = ""
    last_seen: str = ""
    strength: float = 0.0

    def __post_init__(self) -> None:
        _require_evidence(self.why_it_matters, self.evidence, "key_decisions")

    def lookup_text(self) -> str:
        """Join applicability fields so guidance ranking does not search verbatim rulings alone."""
        parts = [
            " ".join(self.entity_keys),
            self.context,
            self.text,
            self.exceptions,
            self.why_it_matters,
        ]
        return " ".join(p for p in parts if str(p).strip())


@dataclass(frozen=True)
class MonthlyProcedure:
    """Store the reusable Solution: plus trigger/obstacles so recall can match a task, not a dump.

    Obstacle clauses stay mechanical so the model cannot invent a failure the daily cards never named.
    """

    id: str
    problem: str
    solution: str
    insight: str = ""
    trigger: str = ""
    obstacles: tuple[str, ...] = ()
    entity_keys: tuple[str, ...] = ()
    weeks: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    occurrence_n: int = 1
    first_seen: str = ""
    last_seen: str = ""
    strength: float = 0.0

    def __post_init__(self) -> None:
        _require_evidence(self.problem, self.evidence, "key_procedures")

    def lookup_text(self) -> str:
        """Join trigger/problem/obstacles/solution so a paraphrased task can hit the case."""
        parts = [
            " ".join(self.entity_keys),
            self.trigger,
            self.problem,
            " ".join(self.obstacles),
            self.solution,
            self.insight,
        ]
        return " ".join(p for p in parts if str(p).strip())


@dataclass(frozen=True)
class MonthlyCrossWeekItem:
    """Track work that spans slices so month retrieval can name a thread without weekly threads."""

    id: str
    name: str
    start_period: str = ""
    current_status: str = "in_progress"
    expected_end_period: str = ""
    progress_this_month: str = ""
    block_reason: str = ""
    weeks: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_evidence(self.progress_this_month, self.evidence, "cross_week_items")


@dataclass(frozen=True)
class MonthlyRisk:
    """Pair a risk with a suggestion so problems_and_risks is not an open-decision dump."""

    content: str
    level: str = "medium"
    suggestion: str = ""
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_evidence(self.content, self.evidence, "problems_and_risks")


@dataclass(frozen=True)
class MonthlyComparisonChange:
    """Name a month-to-month shift with evidence so comparison cannot invent July."""

    text: str
    evidence: tuple[str, ...] = ()
    from_text: str = ""
    to_text: str = ""

    def __post_init__(self) -> None:
        _require_evidence(self.text, self.evidence, "comparison")


@dataclass(frozen=True)
class MonthlyComparison:
    """Carry last-month contrast in one place so two month files are not concatenated."""

    unchanged: tuple[MonthlyComparisonChange, ...] = ()
    changed: tuple[MonthlyComparisonChange, ...] = ()
    suggestion: str = ""
    empty_reason: str = ""


@dataclass(frozen=True)
class MonthlyFocus:
    """Keep next-month focus tied to unfinished items instead of a fresh wishlist."""

    id: str
    content: str
    target: str = ""
    priority: str = "medium"
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class MonthlyState:
    """Close a prior claim with invalid_at so contradiction never deletes history."""

    id: str
    text: str
    valid_from: str
    invalid_at: str | None = None
    invalidated_by: str | None = None
    status: str = "current"
    source: str = ""


@dataclass(frozen=True)
class MonthlyEntity:
    """Roster keys that span months or two-plus ISO weeks here; per-block id lists are weekly's 83% tax."""

    key: str
    canonical: str
    months: tuple[str, ...] = ()
    weeks: tuple[str, ...] = ()
    month_count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    aliases: tuple[str, ...] = ()
    embedding: None = None


@dataclass(frozen=True)
class MonthlyMetrics:
    """Mechanical tallies so the synthesis cannot invent a completion percentage."""

    decisions: int = 0
    procedures: int = 0
    events: int = 0
    facts: int = 0
    active_days: int = 0
    open_decisions: int = 0
    superseded: int = 0
    weeks: int = 0


@dataclass(frozen=True)
class MonthlyNoteItem:
    """Cap a map-stage keep so reduce does not re-read the month's raw clauses."""

    kind: str
    what: str
    why_it_mattered: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_evidence(self.what, self.evidence, "note")
        words = len(self.what.split())
        if words > NOTE_WORD_CAP:
            raise ValueError(f"note what exceeds {NOTE_WORD_CAP} words")


@dataclass(frozen=True)
class MonthlyPayload:
    """Month file: synthesis plus mechanical state. No event bodies, no weekly Distill."""

    key: str
    weeks: tuple[str, ...] = ()
    range: MonthlyRange = field(default_factory=lambda: MonthlyRange(start="", end=""))
    generated_at: str = ""
    generator: MonthlyGenerator = field(default_factory=MonthlyGenerator)
    summary: tuple[MonthlySummaryItem, ...] = ()
    user_image: MonthlyUserImage = field(default_factory=MonthlyUserImage)
    core_progress: tuple[MonthlyProgress, ...] = ()
    key_decisions: tuple[MonthlyDecision, ...] = ()
    key_procedures: tuple[MonthlyProcedure, ...] = ()
    cross_week_items: tuple[MonthlyCrossWeekItem, ...] = ()
    problems_and_risks: tuple[MonthlyRisk, ...] = ()
    comparison_with_last_month: MonthlyComparison = field(default_factory=MonthlyComparison)
    next_month_focus: tuple[MonthlyFocus, ...] = ()
    state: tuple[MonthlyState, ...] = ()
    entities: tuple[MonthlyEntity, ...] = ()
    metrics: MonthlyMetrics = field(default_factory=MonthlyMetrics)
    schema_version: int = 2
    cycle: str = "monthly"

    def __post_init__(self) -> None:
        caps = (
            (self.summary, CAP_SUMMARY, "summary"),
            (self.core_progress, CAP_PROGRESS, "core_progress"),
            (self.key_decisions, CAP_DECISIONS, "key_decisions"),
            (self.key_procedures, CAP_PROCEDURES, "key_procedures"),
            (self.cross_week_items, CAP_CROSS_WEEK, "cross_week_items"),
            (self.problems_and_risks, CAP_RISKS, "problems_and_risks"),
            (self.next_month_focus, CAP_FOCUS, "next_month_focus"),
        )
        for items, cap, label in caps:
            if len(items) > cap:
                raise ValueError(f"{label} exceeds cap {cap}")

    def to_dict(self) -> dict[str, Any]:
        """asdict-shaped tree so tests can round-trip without the YAML writer."""
        return payload_to_dict(self)


def _tuple_of(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def payload_to_dict(payload: MonthlyPayload) -> dict[str, Any]:
    """Serialize dataclasses with YAML names (`from`, not `from_id`) for the month file."""
    def evidence_text(item: MonthlyEvidenceText) -> dict[str, Any]:
        return {"text": item.text, "evidence": list(item.evidence)}

    img = payload.user_image
    return {
        "schema_version": payload.schema_version,
        "cycle": payload.cycle,
        "month_key": payload.key,
        "range": {"start": payload.range.start, "end": payload.range.end},
        "weeks": list(payload.weeks),
        "generated_at": payload.generated_at,
        "generator": {
            "model": payload.generator.model,
            "stages": dict(payload.generator.stages),
            "batch_tokens": payload.generator.batch_tokens,
        },
        "summary": [
            {"text": row.text, "weeks": list(row.weeks)} for row in payload.summary
        ],
        "user_image": {
            "goal_alignment": evidence_text(img.goal_alignment),
            "cognition_change": [
                {
                    "text": row.text,
                    "from": row.from_id,
                    "to": row.to,
                    "date": row.date,
                    "evidence": list(row.evidence),
                }
                for row in img.cognition_change
            ],
            "decision_preference": {
                "text": img.decision_preference.text,
                "counts": dict(img.decision_preference.counts),
                "evidence": list(img.decision_preference.evidence),
            },
            "behavior_pattern": {
                "text": img.behavior_pattern.text,
                "metrics": dict(img.behavior_pattern.metrics),
                "evidence": list(img.behavior_pattern.evidence),
            },
        },
        "core_progress": [
            {
                "id": row.id,
                "title": row.title,
                "body": row.body,
                "state": row.state,
                "weeks": list(row.weeks),
                "entity_keys": list(row.entity_keys),
                "evidence": list(row.evidence),
            }
            for row in payload.core_progress
        ],
        "key_decisions": [
            {
                "id": row.id,
                "kind": row.kind,
                "text": row.text,
                "why_it_matters": row.why_it_matters,
                "context": row.context,
                "exceptions": row.exceptions,
                "date": row.date,
                "valid_to": row.valid_to,
                "entity_keys": list(row.entity_keys),
                "supersedes": list(row.supersedes),
                "evidence": list(row.evidence),
                "occurrence_n": row.occurrence_n,
                "first_seen": row.first_seen,
                "last_seen": row.last_seen,
                "strength": row.strength,
            }
            for row in payload.key_decisions
        ],
        "key_procedures": [
            {
                "id": row.id,
                "trigger": row.trigger,
                "problem": row.problem,
                "obstacles": list(row.obstacles),
                "solution": row.solution,
                "insight": row.insight,
                "entity_keys": list(row.entity_keys),
                "weeks": list(row.weeks),
                "evidence": list(row.evidence),
                "occurrence_n": row.occurrence_n,
                "first_seen": row.first_seen,
                "last_seen": row.last_seen,
                "strength": row.strength,
            }
            for row in payload.key_procedures
        ],
        "cross_week_items": [
            {
                "id": row.id,
                "name": row.name,
                "start_period": row.start_period,
                "current_status": row.current_status,
                "expected_end_period": row.expected_end_period,
                "progress_this_month": row.progress_this_month,
                "block_reason": row.block_reason,
                "weeks": list(row.weeks),
                "evidence": list(row.evidence),
            }
            for row in payload.cross_week_items
        ],
        "problems_and_risks": [
            {
                "content": row.content,
                "level": row.level,
                "suggestion": row.suggestion,
                "evidence": list(row.evidence),
            }
            for row in payload.problems_and_risks
        ],
        "comparison_with_last_month": {
            "unchanged": [
                {
                    "text": row.text,
                    "evidence": list(row.evidence),
                    "from": row.from_text,
                    "to": row.to_text,
                }
                for row in payload.comparison_with_last_month.unchanged
            ],
            "changed": [
                {
                    "text": row.text,
                    "evidence": list(row.evidence),
                    "from": row.from_text,
                    "to": row.to_text,
                }
                for row in payload.comparison_with_last_month.changed
            ],
            "suggestion": payload.comparison_with_last_month.suggestion,
            "empty_reason": payload.comparison_with_last_month.empty_reason,
        },
        "next_month_focus": [
            {
                "id": row.id,
                "content": row.content,
                "target": row.target,
                "priority": row.priority,
                "depends_on": list(row.depends_on),
            }
            for row in payload.next_month_focus
        ],
        "state": [
            {
                "id": row.id,
                "text": row.text,
                "valid_from": row.valid_from,
                "invalid_at": row.invalid_at,
                "invalidated_by": row.invalidated_by,
                "status": row.status,
                "source": row.source,
            }
            for row in payload.state
        ],
        "entities": [
            {
                "key": row.key,
                "canonical": row.canonical,
                "months": list(row.months),
                "weeks": list(row.weeks),
                "month_count": row.month_count,
                "first_seen": row.first_seen,
                "last_seen": row.last_seen,
                "aliases": list(row.aliases),
                "embedding": row.embedding,
            }
            for row in payload.entities
        ],
        "metrics": {
            "decisions": payload.metrics.decisions,
            "procedures": payload.metrics.procedures,
            "events": payload.metrics.events,
            "facts": payload.metrics.facts,
            "active_days": payload.metrics.active_days,
            "open_decisions": payload.metrics.open_decisions,
            "superseded": payload.metrics.superseded,
            "weeks": payload.metrics.weeks,
        },
    }
