"""Event-first weekly schema (Steps 1 + 3 of the Event-First Weekly UI plan).

Defines one stable intermediate schema for date-labeled events, sourced
facts/procedures/decisions, conflicts, hypotheses, and span candidates, plus
helpers to render/parse the four-part weekly review brief:

    Weekly Brief — 2026-W31

    Monday — 2026-07-27 · Events [1]
    Project kickoff... [1] The daily digest... [2] ...

    Wednesday — 2026-07-29 · Events
    No record for this day.

    Conflict
    - ...

    Hypothesis
    - ...

    Possible overdue report
    - ... — explicit [N]

Legacy ``## Distill`` / ``## Brief`` loading stays intact for old weekly files;
this module never migrates on-disk documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal, Sequence

ClaimKind = Literal["fact", "procedure", "decision"]
TypedCiteKind = Literal["conflict", "hypothesis", "span"]

_ALLOWED_CLAIM_KINDS: frozenset[str] = frozenset({"fact", "procedure", "decision"})
# Align with memory-digest SPAN_CONFIDENCES; UI later filters to explicit|high.
_ALLOWED_SPAN_CONFIDENCE: frozenset[str] = frozenset(
    {"explicit", "high", "medium", "low"}
)
# Only these confidences appear in the Possible overdue report section.
OVERDUE_SPAN_CONFIDENCES: frozenset[str] = frozenset({"explicit", "high"})
_ALLOWED_TYPED_CITE_KINDS: frozenset[str] = frozenset(
    {"conflict", "hypothesis", "span"}
)

WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

EMPTY_DAY_TEXT = "No record for this day."

WEEKLY_BRIEF_TITLE_PREFIX = "Weekly Brief — "
CONFLICT_SECTION_TITLE = "Conflict"
HYPOTHESIS_SECTION_TITLE = "Hypothesis"
OVERDUE_SECTION_TITLE = "Possible overdue report"
CITE_MAP_SECTION_TITLE = "Cite map"
OVERDUE_ACTIONS_LINE = (
    "[Confirm] [Put off by ▾: 1 day | 7 days | 2 weeks | 1 month] [Set due date]"
)

_DAY_HEADER_RE = re.compile(
    r"^(?P<weekday>[A-Za-z]+) — (?P<date>\d{4}-\d{2}-\d{2}) · Events"
    r"(?P<markers>(?: \[\d+\])*)$"
)
_CITE_MARKER_RE = re.compile(r"\[(\d+)\]")
_WEEKLY_BRIEF_TITLE_RE = re.compile(
    r"^Weekly Brief — (?P<week>\d{4}-W\d{2})\s*$"
)
_SECTION_TITLE_RE = re.compile(
    r"^(Conflict|Hypothesis|Possible overdue report|Cite map)\s*$"
)
_BULLET_RE = re.compile(r"^-\s+(?P<body>.+)$")
_CITE_MAP_ENTRY_RE = re.compile(
    r"^\[(?P<n>\d+)\]\s+(?P<kind>event|conflict|hypothesis|span)\s+(?P<target>\S+)\s*$",
    re.IGNORECASE,
)
_OVERDUE_ROW_RE = re.compile(
    r"^(?P<label>.+?) — proposed end (?P<end>\d{4}-\d{2}-\d{2}) — "
    r"(?P<confidence>explicit|high|medium|low)"
    r"(?:\s+\[(?P<cite>\d+)\])?\s*$",
    re.IGNORECASE,
)
_TRAILING_CITE_RE = re.compile(r"^(?P<text>.*?)\s+\[(?P<cite>\d+)\]\s*$")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventCite:
    """One ``[N] mem-…`` citation marker attached to a day's events."""

    number: int
    mem_id: str

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"EventCite.number must be >= 1, got {self.number}")
        if not str(self.mem_id).strip():
            raise ValueError("EventCite.mem_id must be non-empty")


@dataclass(frozen=True)
class ClaimSentence:
    """One sourced sentence (fact | procedure | decision) with an optional cite.

    ``title`` is the Worker 2 Brief headline for an event paragraph (optional).
    """

    text: str
    kind: ClaimKind = "fact"
    cite: int | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        if not str(self.text).strip():
            raise ValueError("ClaimSentence.text must be non-empty")
        if self.kind not in _ALLOWED_CLAIM_KINDS:
            raise ValueError(
                f"ClaimSentence.kind {self.kind!r} not in {sorted(_ALLOWED_CLAIM_KINDS)}"
            )
        if self.cite is not None and self.cite < 1:
            raise ValueError(f"ClaimSentence.cite must be >= 1, got {self.cite}")
        if self.title is not None and not str(self.title).strip():
            raise ValueError("ClaimSentence.title must be non-empty when set")


@dataclass(frozen=True)
class ConflictItem:
    """A tension between two or more related events/claims."""

    id: str
    text: str
    related_event_ids: tuple[str, ...] = ()
    cite: int | None = None

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError("ConflictItem.id must be non-empty")
        if not str(self.text).strip():
            raise ValueError("ConflictItem.text must be non-empty")


@dataclass(frozen=True)
class HypothesisItem:
    """An open, unconfirmed working bet tied back to one or more events."""

    id: str
    text: str
    related_event_ids: tuple[str, ...] = ()
    cite: int | None = None

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError("HypothesisItem.id must be non-empty")
        if not str(self.text).strip():
            raise ValueError("HypothesisItem.text must be non-empty")


@dataclass(frozen=True)
class ThreadStep:
    """Keep preference-change on the later step so weekly JSON does not mint Conflict fences.

    Seq 1 has no ``via``. Later ``via`` is evolves (continue) or invalidates (close an earlier seq).
    """

    seq: int
    date: date
    event_id: str
    text: str
    cite_n: int | None = None
    via: str | None = None
    to_seq: int | None = None

    def __post_init__(self) -> None:
        if self.seq < 1:
            raise ValueError(f"ThreadStep.seq must be >= 1, got {self.seq}")
        if not str(self.event_id).strip():
            raise ValueError("ThreadStep.event_id must be non-empty")
        if self.cite_n is not None and self.cite_n < 1:
            raise ValueError(f"ThreadStep.cite_n must be >= 1, got {self.cite_n}")
        if self.via is not None and self.via not in {"evolves", "invalidates"}:
            raise ValueError(
                f"ThreadStep.via {self.via!r} not in {{'evolves', 'invalidates'}}"
            )
        if self.to_seq is not None and self.to_seq >= self.seq:
            raise ValueError(
                f"ThreadStep.to_seq {self.to_seq} must be < seq {self.seq}"
            )


@dataclass(frozen=True)
class IntraDayThread:
    """Hold the day's wrap-up verbatim so Chronicle cannot substitute leftover Outcomes."""

    date: date
    weekday: str
    source_field: str
    text: str
    empty: bool = False


@dataclass(frozen=True)
class WeeklyEntity:
    """Collapse alias surfaces onto one key so weekly JSON and recall share a roster.

    A second normalizer would split Memory Digest / MemoryDigest into two nodes.
    """

    key: str
    canonical: str
    aliases: tuple[str, ...] = ()
    first_seen: date | None = None
    last_seen: date | None = None
    week_blocks: tuple[str, ...] = ()
    embedding: None = None


@dataclass(frozen=True)
class SpanCandidate:
    """A candidate multi-day span (e.g. an initiative) linking several events."""

    id: str
    label: str
    start_date: date
    end_date: date
    confidence: str
    related_event_ids: tuple[str, ...] = ()
    cite: int | None = None
    steps: tuple[ThreadStep, ...] = ()
    outcome: dict | None = None
    entity_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise ValueError("SpanCandidate.id must be non-empty")
        if not str(self.label).strip():
            raise ValueError("SpanCandidate.label must be non-empty")
        if self.confidence not in _ALLOWED_SPAN_CONFIDENCE:
            raise ValueError(
                f"SpanCandidate.confidence {self.confidence!r} not in "
                f"{sorted(_ALLOWED_SPAN_CONFIDENCE)}"
            )
        if self.end_date < self.start_date:
            raise ValueError(
                f"SpanCandidate.end_date {self.end_date} precedes start_date "
                f"{self.start_date}"
            )
        if self.cite is not None and self.cite < 1:
            raise ValueError(f"SpanCandidate.cite must be >= 1, got {self.cite}")


@dataclass(frozen=True)
class TypedCite:
    """Non-event citation target (conflict / hypothesis / span) for UI navigation.

    Event evidence keeps the continuous ``legend`` map; typed cites continue
    numbering after the max event cite and must never renumber event markers.
    """

    number: int
    kind: TypedCiteKind
    target_id: str

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"TypedCite.number must be >= 1, got {self.number}")
        if self.kind not in _ALLOWED_TYPED_CITE_KINDS:
            raise ValueError(
                f"TypedCite.kind {self.kind!r} not in "
                f"{sorted(_ALLOWED_TYPED_CITE_KINDS)}"
            )
        if not str(self.target_id).strip():
            raise ValueError("TypedCite.target_id must be non-empty")


@dataclass(frozen=True)
class DayBrief:
    """One calendar day's worth of cited events + sourced sentences."""

    day: date
    events: tuple[EventCite, ...] = ()
    sentences: tuple[ClaimSentence, ...] = ()


@dataclass(frozen=True)
class WeeklyReviewPayload:
    """Carry cross-day-thread, wrap-ups, and entities beside the four-part brief so JSON can dump without Distill."""

    days: tuple[DayBrief, ...]
    conflicts: tuple[ConflictItem, ...] = ()
    hypotheses: tuple[HypothesisItem, ...] = ()
    span_candidates: tuple[SpanCandidate, ...] = ()
    legend: dict[int, str] = field(default_factory=dict)
    typed_legend: dict[int, TypedCite] = field(default_factory=dict)
    week_key: str = ""
    cross_day_thread: tuple[SpanCandidate, ...] = ()
    intra_day_thread: tuple["IntraDayThread", ...] = ()
    entities: tuple["WeeklyEntity", ...] = ()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def weekday_label(day: date) -> str:
    """Return the full English weekday name for ``day`` (Monday=0 .. Sunday=6)."""
    return WEEKDAY_NAMES[day.weekday()]


def format_day_header(day: date, event_nums: Sequence[int] = ()) -> str:
    """Render ``Weekday — YYYY-MM-DD · Events [N...]`` (markers only if any)."""
    header = f"{weekday_label(day)} — {day.isoformat()} · Events"
    nums = list(event_nums)
    if nums:
        header = f"{header} {' '.join(f'[{n}]' for n in nums)}"
    return header


_TITLE_LINE_RE = re.compile(r"^\*\*(?P<title>.+?)\*\*\s*$")


def worker2_format_day_events(sentences: Sequence[ClaimSentence]) -> str:
    """Worker 2: one named plain paragraph per event, blank-line separated.

    Each event is ``**title**\\nbody`` when ``title`` is set; otherwise just the
    body. Citation markers may trail a sentence. Empty → ``EMPTY_DAY_TEXT``.
    """
    if not sentences:
        return EMPTY_DAY_TEXT
    blocks: list[str] = []
    for sentence in sentences:
        text = sentence.text.strip()
        if sentence.cite is not None:
            text = f"{text} [{sentence.cite}]"
        title = (sentence.title or "").strip()
        if title:
            blocks.append(f"**{title}**\n{text}")
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def render_date_brief_section(days: Sequence[DayBrief]) -> str:
    """Render the full date-grouped brief text for a sequence of days."""
    blocks: list[str] = []
    for day_brief in days:
        header = format_day_header(day_brief.day, [e.number for e in day_brief.events])
        paragraph = worker2_format_day_events(day_brief.sentences)
        blocks.append(f"{header}\n{paragraph}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_day_header(header: str) -> list[str]:
    """Check ``Weekday — YYYY-MM-DD · Events [N...]`` shape and weekday/date match."""
    errors: list[str] = []
    match = _DAY_HEADER_RE.match(header.strip())
    if not match:
        errors.append(f"malformed day header: {header!r}")
        return errors
    date_str = match.group("date")
    try:
        parsed = date.fromisoformat(date_str)
    except ValueError:
        errors.append(f"invalid date in header: {date_str!r}")
        return errors
    weekday_word = match.group("weekday")
    expected = weekday_label(parsed)
    if weekday_word != expected:
        errors.append(
            f"weekday label {weekday_word!r} does not match date {date_str} "
            f"(expected {expected!r})"
        )
    return errors


def validate_empty_day_paragraph(day_brief: DayBrief) -> list[str]:
    """A day with no events/sentences must render exactly ``EMPTY_DAY_TEXT``."""
    if day_brief.events or day_brief.sentences:
        return []
    paragraph = worker2_format_day_events(day_brief.sentences)
    if paragraph != EMPTY_DAY_TEXT:
        return [f"empty day {day_brief.day.isoformat()} must render {EMPTY_DAY_TEXT!r}"]
    return []


def validate_sentence_final_cites(paragraph: str) -> list[str]:
    """Each ``[N]`` marker must sit immediately after sentence-ending punctuation.

    Title lines (``**…**``) are ignored for cite placement checks.
    """
    errors: list[str] = []
    if paragraph == EMPTY_DAY_TEXT:
        return errors
    # Check each event block / paragraph separately so blank lines are fine.
    for block in re.split(r"\n\s*\n", paragraph):
        body_lines: list[str] = []
        for line in block.splitlines():
            if _TITLE_LINE_RE.match(line.strip()):
                continue
            body_lines.append(line)
        body = "\n".join(body_lines).strip()
        if not body or body == EMPTY_DAY_TEXT:
            continue
        for match in _CITE_MARKER_RE.finditer(body):
            preceding = body[: match.start()].rstrip()
            if not preceding or preceding[-1] not in ".!?":
                errors.append(
                    f"citation {match.group(0)} is not immediately after a "
                    "sentence-ending mark"
                )
    return errors


def validate_week_order(days: Sequence[DayBrief]) -> list[str]:
    """Enforce Monday..Sunday labeling in order, on consecutive calendar days."""
    errors: list[str] = []
    if not days:
        errors.append("no days provided")
        return errors
    if len(days) != 7:
        errors.append(f"expected 7 days (Monday..Sunday), got {len(days)}")
    prev_day: date | None = None
    for idx, day_brief in enumerate(days):
        label = weekday_label(day_brief.day)
        if idx < len(WEEKDAY_NAMES) and label != WEEKDAY_NAMES[idx]:
            errors.append(
                f"day index {idx} is {label} ({day_brief.day.isoformat()}), "
                f"expected {WEEKDAY_NAMES[idx]}"
            )
        if prev_day is not None and (day_brief.day - prev_day).days != 1:
            errors.append(
                f"days not consecutive: {prev_day.isoformat()} -> "
                f"{day_brief.day.isoformat()}"
            )
        prev_day = day_brief.day
    return errors


def validate_no_duplicate_ids(payload: WeeklyReviewPayload) -> list[str]:
    """Reject id collisions across conflicts, hypotheses, and span candidates."""
    errors: list[str] = []
    owner_by_id: dict[str, str] = {}
    groups: tuple[tuple[str, Sequence[object]], ...] = (
        ("conflict", payload.conflicts),
        ("hypothesis", payload.hypotheses),
        ("span_candidate", payload.span_candidates),
    )
    for kind, items in groups:
        for item in items:
            item_id = str(getattr(item, "id"))
            if item_id in owner_by_id:
                errors.append(
                    f"duplicate id {item_id!r}: used by both "
                    f"{owner_by_id[item_id]} and {kind}"
                )
                continue
            owner_by_id[item_id] = kind
    return errors


def validate_weekly_review_payload(payload: WeeklyReviewPayload) -> list[str]:
    """Run all Step 1 structural checks over a full ``WeeklyReviewPayload``."""
    errors: list[str] = []
    errors.extend(validate_week_order(payload.days))
    errors.extend(validate_no_duplicate_ids(payload))
    for day_brief in payload.days:
        header = format_day_header(day_brief.day, [e.number for e in day_brief.events])
        errors.extend(validate_day_header(header))
        errors.extend(validate_empty_day_paragraph(day_brief))
        paragraph = worker2_format_day_events(day_brief.sentences)
        errors.extend(validate_sentence_final_cites(paragraph))
    return errors


# ---------------------------------------------------------------------------
# Typed citations + four-part render / parse (Step 3)
# ---------------------------------------------------------------------------


def max_event_cite_number(legend: dict[int, str]) -> int:
    """Highest event citation number, or 0 when the legend is empty."""
    return max(legend.keys(), default=0)


def overdue_span_candidates(
    spans: Sequence[SpanCandidate],
) -> tuple[SpanCandidate, ...]:
    """Return only explicit/high spans for the Possible overdue report."""
    return tuple(
        s for s in spans if s.confidence in OVERDUE_SPAN_CONFIDENCES
    )


def assign_typed_citations(payload: WeeklyReviewPayload) -> WeeklyReviewPayload:
    """Attach typed cite numbers after the event legend max; never renumber events.

    Conflict → hypothesis receive the next contiguous numbers. Span candidates
    are not Brief-cited (Possible overdue UI uses digest validate only).
    """
    next_n = max_event_cite_number(payload.legend) + 1
    typed: dict[int, TypedCite] = {}

    new_conflicts: list[ConflictItem] = []
    for item in payload.conflicts:
        cite = next_n
        next_n += 1
        typed[cite] = TypedCite(number=cite, kind="conflict", target_id=item.id)
        new_conflicts.append(replace(item, cite=cite))

    new_hypotheses: list[HypothesisItem] = []
    for item in payload.hypotheses:
        cite = next_n
        next_n += 1
        typed[cite] = TypedCite(number=cite, kind="hypothesis", target_id=item.id)
        new_hypotheses.append(replace(item, cite=cite))

    # Keep any carry-over spans uncited; Brief overdue stays empty by design.
    new_spans = [replace(item, cite=None) for item in payload.span_candidates]

    return WeeklyReviewPayload(
        days=payload.days,
        conflicts=tuple(new_conflicts),
        hypotheses=tuple(new_hypotheses),
        span_candidates=tuple(new_spans),
        legend=dict(payload.legend),
        typed_legend=typed,
        week_key=payload.week_key,
        cross_day_thread=payload.cross_day_thread,
        intra_day_thread=payload.intra_day_thread,
        entities=payload.entities,
    )


def _bullet_with_cite(text: str, cite: int | None) -> str:
    body = text.strip()
    if cite is None:
        return f"- {body}"
    if _CITE_MARKER_RE.search(body):
        # Already carries a marker; still ensure the assigned cite is present.
        if f"[{cite}]" not in body:
            body = f"{body} [{cite}]"
        return f"- {body}"
    if body and body[-1] not in ".!?":
        body = f"{body}."
    return f"- {body} [{cite}]"


def render_conflict_section(conflicts: Sequence[ConflictItem]) -> str:
    lines = [CONFLICT_SECTION_TITLE]
    if not conflicts:
        lines.append("- None.")
    else:
        for item in conflicts:
            lines.append(_bullet_with_cite(item.text, item.cite))
    return "\n".join(lines)


def render_hypothesis_section(hypotheses: Sequence[HypothesisItem]) -> str:
    lines = [HYPOTHESIS_SECTION_TITLE]
    if not hypotheses:
        lines.append("- None.")
    else:
        for item in hypotheses:
            lines.append(_bullet_with_cite(item.text, item.cite))
    return "\n".join(lines)


def render_overdue_section(spans: Sequence[SpanCandidate]) -> str:
    """Brief Possible overdue is always empty; UI uses digest validate only."""
    del spans  # retained for call-site compatibility
    return "\n".join([OVERDUE_SECTION_TITLE, "- None."])


def render_cite_map_section(payload: WeeklyReviewPayload) -> str:
    """Machine-readable citation targets (event + typed) for UI / round-trip."""
    lines = [CITE_MAP_SECTION_TITLE]
    for n, mem_id in sorted(payload.legend.items()):
        lines.append(f"- [{n}] event {mem_id}")
    for n, typed in sorted(payload.typed_legend.items()):
        lines.append(f"- [{n}] {typed.kind} {typed.target_id}")
    if len(lines) == 1:
        lines.append("- None.")
    return "\n".join(lines)


def render_four_part_brief(week_key: str, payload: WeeklyReviewPayload) -> str:
    """Render the four display sections (no Cite map footer)."""
    key = (week_key or payload.week_key or "").strip()
    parts = [
        f"{WEEKLY_BRIEF_TITLE_PREFIX}{key}".rstrip(),
        "",
        render_date_brief_section(payload.days),
        "",
        render_conflict_section(payload.conflicts),
        "",
        render_hypothesis_section(payload.hypotheses),
        "",
        render_overdue_section(payload.span_candidates),
    ]
    return "\n".join(parts).rstrip() + "\n"


def render_weekly_review_brief(week_key: str, payload: WeeklyReviewPayload) -> str:
    """Render four-part brief plus Cite map (Worker 2 / persistence body)."""
    assigned = (
        payload
        if payload.typed_legend
        else assign_typed_citations(payload)
    )
    if week_key and not assigned.week_key:
        assigned = replace(assigned, week_key=week_key)
    body = render_four_part_brief(week_key or assigned.week_key, assigned)
    return body.rstrip() + "\n\n" + render_cite_map_section(assigned) + "\n"


def is_four_part_brief(text: str) -> bool:
    """True when Brief body looks like the Event-First four-part container."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _WEEKLY_BRIEF_TITLE_RE.match(stripped.splitlines()[0].strip()):
        return True
    return (
        CONFLICT_SECTION_TITLE in stripped
        and HYPOTHESIS_SECTION_TITLE in stripped
        and OVERDUE_SECTION_TITLE in stripped
        and " · Events" in stripped
    )


def _split_sentences_with_cites(paragraph: str) -> tuple[ClaimSentence, ...]:
    if paragraph == EMPTY_DAY_TEXT or not paragraph.strip():
        return ()
    # Split on sentence-final cite markers: "... mark. [N]"
    pieces: list[ClaimSentence] = []
    remaining = paragraph.strip()
    pattern = re.compile(r"(.*?[.!?])\s*\[(\d+)\]\s*")
    pos = 0
    for match in pattern.finditer(remaining):
        if match.start() != pos:
            # Uncited leading fragment — keep as fact without cite.
            gap = remaining[pos : match.start()].strip()
            if gap:
                pieces.append(ClaimSentence(text=gap, kind="fact", cite=None))
        text = match.group(1).strip()
        cite = int(match.group(2))
        pieces.append(ClaimSentence(text=text, kind="fact", cite=cite))
        pos = match.end()
    tail = remaining[pos:].strip()
    if tail:
        trail = _TRAILING_CITE_RE.match(tail)
        if trail:
            pieces.append(
                ClaimSentence(
                    text=trail.group("text").strip(),
                    kind="fact",
                    cite=int(trail.group("cite")),
                )
            )
        else:
            pieces.append(ClaimSentence(text=tail, kind="fact", cite=None))
    return tuple(pieces)


def _claim_from_event_block(block_lines: Sequence[str]) -> ClaimSentence | None:
    """Parse one Worker 2 event block (optional ``**title**`` + body)."""
    if not block_lines:
        return None
    title: str | None = None
    body_lines = list(block_lines)
    first = body_lines[0].strip()
    title_match = _TITLE_LINE_RE.match(first)
    if title_match:
        title = title_match.group("title").strip()
        body_lines = body_lines[1:]
    body = " ".join(line.strip() for line in body_lines if line.strip()).strip()
    if not body:
        return None
    pieces = _split_sentences_with_cites(body)
    if not pieces:
        return ClaimSentence(text=body, kind="fact", cite=None, title=title)
    if len(pieces) == 1:
        only = pieces[0]
        return ClaimSentence(
            text=only.text, kind=only.kind, cite=only.cite, title=title
        )
    # Multiple cited sentences in one block — keep first title, join text.
    joined = " ".join(
        f"{p.text} [{p.cite}]" if p.cite is not None else p.text for p in pieces
    )
    return ClaimSentence(text=joined, kind="fact", cite=None, title=title)


def _parse_day_blocks(lines: list[str]) -> tuple[DayBrief, ...]:
    days: list[DayBrief] = []
    i = 0
    while i < len(lines):
        header_match = _DAY_HEADER_RE.match(lines[i].strip())
        if not header_match:
            i += 1
            continue
        day = date.fromisoformat(header_match.group("date"))
        markers = [
            int(n) for n in _CITE_MARKER_RE.findall(header_match.group("markers") or "")
        ]
        i += 1
        raw_lines: list[str] = []
        while i < len(lines):
            stripped = lines[i].strip()
            if _DAY_HEADER_RE.match(stripped) or _SECTION_TITLE_RE.match(stripped):
                break
            raw_lines.append(lines[i])
            i += 1
        # Event mem_ids filled later from Cite map / legend.
        events = tuple(EventCite(n, f"event-{n}") for n in markers)
        # Split blank-line-separated event blocks (Worker 2 layout).
        blocks: list[list[str]] = []
        current: list[str] = []
        for line in raw_lines:
            if not line.strip():
                if current:
                    blocks.append(current)
                    current = []
                continue
            current.append(line)
        if current:
            blocks.append(current)

        sentences_list: list[ClaimSentence] = []
        if not blocks:
            sentences: tuple[ClaimSentence, ...] = ()
        elif (
            len(blocks) == 1
            and blocks[0]
            and " ".join(x.strip() for x in blocks[0]).strip() == EMPTY_DAY_TEXT
        ):
            sentences = ()
        else:
            for block in blocks:
                claim = _claim_from_event_block(block)
                if claim is not None:
                    sentences_list.append(claim)
            sentences = tuple(sentences_list)
        days.append(DayBrief(day=day, events=events, sentences=sentences))
    return tuple(days)


def _strip_trailing_cite(text: str) -> tuple[str, int | None]:
    match = _TRAILING_CITE_RE.match(text.strip())
    if not match:
        return text.strip(), None
    return match.group("text").strip(), int(match.group("cite"))


def _parse_bullet_section(
    lines: list[str], start: int
) -> tuple[list[tuple[str, int | None]], int]:
    """Parse ``- body`` bullets until the next section title; return (items, next_i)."""
    items: list[tuple[str, int | None]] = []
    i = start
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if _SECTION_TITLE_RE.match(stripped) or _DAY_HEADER_RE.match(stripped):
            break
        if stripped.startswith(OVERDUE_ACTIONS_LINE[:9]):  # "[Confirm]"
            i += 1
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            body = bullet.group("body").strip()
            if body.casefold() == "none.":
                i += 1
                continue
            text, cite = _strip_trailing_cite(body)
            items.append((text, cite))
            i += 1
            continue
        # Continuation / action line under overdue
        i += 1
    return items, i


def parse_four_part_brief(text: str) -> WeeklyReviewPayload:
    """Parse a four-part (+ optional Cite map) brief back into a payload."""
    lines = (text or "").splitlines()
    week_key = ""
    if lines:
        title = _WEEKLY_BRIEF_TITLE_RE.match(lines[0].strip())
        if title:
            week_key = title.group("week")

    # Locate section starts
    section_starts: dict[str, int] = {}
    for idx, raw in enumerate(lines):
        match = _SECTION_TITLE_RE.match(raw.strip())
        if match:
            section_starts[match.group(1)] = idx

    conflict_i = section_starts.get(CONFLICT_SECTION_TITLE)
    # Date region: after title until Conflict (or end)
    date_end = conflict_i if conflict_i is not None else len(lines)
    days = _parse_day_blocks(lines[1:date_end])

    conflicts: list[ConflictItem] = []
    hypotheses: list[HypothesisItem] = []
    spans: list[SpanCandidate] = []
    legend: dict[int, str] = {}
    typed_legend: dict[int, TypedCite] = {}

    if conflict_i is not None:
        items, _ = _parse_bullet_section(lines, conflict_i + 1)
        for idx, (body, cite) in enumerate(items, start=1):
            conflicts.append(
                ConflictItem(id=f"conflict-{idx}", text=body, cite=cite)
            )

    hyp_i = section_starts.get(HYPOTHESIS_SECTION_TITLE)
    if hyp_i is not None:
        items, _ = _parse_bullet_section(lines, hyp_i + 1)
        for idx, (body, cite) in enumerate(items, start=1):
            hypotheses.append(
                HypothesisItem(id=f"hypothesis-{idx}", text=body, cite=cite)
            )

    overdue_i = section_starts.get(OVERDUE_SECTION_TITLE)
    if overdue_i is not None:
        i = overdue_i + 1
        span_idx = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                continue
            if _SECTION_TITLE_RE.match(stripped):
                break
            bullet = _BULLET_RE.match(stripped)
            if bullet:
                body = bullet.group("body").strip()
                if body.casefold() == "none.":
                    i += 1
                    continue
                row = _OVERDUE_ROW_RE.match(body)
                if row:
                    span_idx += 1
                    cite = int(row.group("cite")) if row.group("cite") else None
                    end = date.fromisoformat(row.group("end"))
                    spans.append(
                        SpanCandidate(
                            id=f"span-{span_idx}",
                            label=row.group("label").strip(),
                            start_date=end,  # start unknown in display row
                            end_date=end,
                            confidence=row.group("confidence").casefold(),
                            cite=cite,
                        )
                    )
            i += 1

    cite_i = section_starts.get(CITE_MAP_SECTION_TITLE)
    if cite_i is not None:
        i = cite_i + 1
        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                continue
            if _SECTION_TITLE_RE.match(stripped) or _DAY_HEADER_RE.match(stripped):
                break
            bullet = _BULLET_RE.match(stripped)
            if not bullet:
                i += 1
                continue
            body = bullet.group("body").strip()
            if body.casefold() == "none.":
                i += 1
                continue
            entry = _CITE_MAP_ENTRY_RE.match(body)
            if entry:
                n = int(entry.group("n"))
                kind = entry.group("kind").casefold()
                target = entry.group("target")
                if kind == "event":
                    legend[n] = target
                elif kind in _ALLOWED_TYPED_CITE_KINDS:
                    typed_legend[n] = TypedCite(
                        number=n, kind=kind, target_id=target  # type: ignore[arg-type]
                    )
            i += 1

    # Reconcile event mem_ids and typed block IDs from cite map.
    reconciled_days: list[DayBrief] = []
    for day_brief in days:
        events = tuple(
            EventCite(e.number, legend.get(e.number, e.mem_id))
            for e in day_brief.events
        )
        reconciled_days.append(
            DayBrief(day=day_brief.day, events=events, sentences=day_brief.sentences)
        )

    reconciled_conflicts: list[ConflictItem] = []
    for item in conflicts:
        target = None
        if item.cite is not None and item.cite in typed_legend:
            tc = typed_legend[item.cite]
            if tc.kind == "conflict":
                target = tc.target_id
        reconciled_conflicts.append(
            replace(item, id=target or item.id)
        )

    reconciled_hypotheses: list[HypothesisItem] = []
    for item in hypotheses:
        target = None
        if item.cite is not None and item.cite in typed_legend:
            tc = typed_legend[item.cite]
            if tc.kind == "hypothesis":
                target = tc.target_id
        reconciled_hypotheses.append(
            replace(item, id=target or item.id)
        )

    reconciled_spans: list[SpanCandidate] = []
    for item in spans:
        target = None
        start = item.start_date
        if item.cite is not None and item.cite in typed_legend:
            tc = typed_legend[item.cite]
            if tc.kind == "span":
                target = tc.target_id
        reconciled_spans.append(
            replace(item, id=target or item.id, start_date=start)
        )

    # If legend came only from day events without cite map, keep placeholder mem ids.
    if not legend:
        for day_brief in reconciled_days:
            for event in day_brief.events:
                legend[event.number] = event.mem_id

    return WeeklyReviewPayload(
        days=tuple(reconciled_days),
        conflicts=tuple(reconciled_conflicts),
        hypotheses=tuple(reconciled_hypotheses),
        span_candidates=tuple(reconciled_spans),
        legend=legend,
        typed_legend=typed_legend,
        week_key=week_key,
        cross_day_thread=(),
        intra_day_thread=(),
        entities=(),
    )


def payloads_structurally_equal(
    left: WeeklyReviewPayload,
    right: WeeklyReviewPayload,
    *,
    ignore_span_start: bool = True,
) -> list[str]:
    """Return mismatch descriptions for a golden round-trip comparison."""
    errors: list[str] = []
    if left.week_key != right.week_key:
        errors.append(f"week_key {left.week_key!r} != {right.week_key!r}")
    if len(left.days) != len(right.days):
        errors.append(f"day count {len(left.days)} != {len(right.days)}")
    for a, b in zip(left.days, right.days):
        if a.day != b.day:
            errors.append(f"day mismatch {a.day} != {b.day}")
        if [e.number for e in a.events] != [e.number for e in b.events]:
            errors.append(f"event markers on {a.day}: {a.events} != {b.events}")
        if [e.mem_id for e in a.events] != [e.mem_id for e in b.events]:
            errors.append(f"event mem_ids on {a.day} differ")
        if worker2_format_day_events(a.sentences) != worker2_format_day_events(b.sentences):
            errors.append(f"paragraph mismatch on {a.day}")
    if [(c.id, c.text, c.cite) for c in left.conflicts] != [
        (c.id, c.text, c.cite) for c in right.conflicts
    ]:
        errors.append("conflicts differ")
    if [(h.id, h.text, h.cite) for h in left.hypotheses] != [
        (h.id, h.text, h.cite) for h in right.hypotheses
    ]:
        errors.append("hypotheses differ")
    def _span_key(s: SpanCandidate) -> tuple:
        if ignore_span_start:
            return (s.id, s.label, s.end_date, s.confidence, s.cite)
        return (s.id, s.label, s.start_date, s.end_date, s.confidence, s.cite)

    left_overdue = overdue_span_candidates(left.span_candidates)
    right_overdue = overdue_span_candidates(right.span_candidates)
    if [_span_key(s) for s in left_overdue] != [_span_key(s) for s in right_overdue]:
        errors.append("overdue spans differ")
    if left.legend != right.legend:
        errors.append(f"legend {left.legend} != {right.legend}")
    if left.typed_legend != right.typed_legend:
        errors.append("typed_legend differs")
    return errors
