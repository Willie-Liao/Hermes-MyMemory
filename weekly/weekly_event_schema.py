"""Event-first weekly schema (Steps 1 + 3 of the Event-First Weekly UI plan).

Defines one stable intermediate schema for date-labeled events and sourced
facts/procedures/decisions, plus helpers to render/parse the weekly review brief:

    Weekly Brief — 2026-W31

    Monday — 2026-07-27 · Events [1]
    Project kickoff... [1] The daily digest... [2] ...

    Wednesday — 2026-07-29 · Events
    No record for this day.

    Cite map
    - [1] event mem-…

Old briefs may still contain retired single-line headings between the days and
the cite map. Parsers skip those lines so day events still load. This module
never migrates on-disk documents and never emits those headings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal, Sequence

ClaimKind = Literal["fact", "procedure", "decision"]

_ALLOWED_CLAIM_KINDS: frozenset[str] = frozenset({"fact", "procedure", "decision"})
# Thread spans still use this closed set. Brief overdue rows no longer do.
_ALLOWED_SPAN_CONFIDENCE: frozenset[str] = frozenset(
    {"explicit", "high", "medium", "low"}
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
CITE_MAP_SECTION_TITLE = "Cite map"

_DAY_HEADER_RE = re.compile(
    r"^(?P<weekday>[A-Za-z]+) — (?P<date>\d{4}-\d{2}-\d{2}) · Events"
    r"(?P<markers>(?: \[\d+\])*)$"
)
_CITE_MARKER_RE = re.compile(r"\[(\d+)\]")
_WEEKLY_BRIEF_TITLE_RE = re.compile(
    r"^Weekly Brief — (?P<week>\d{4}-W\d{2})\s*$"
)
_BULLET_RE = re.compile(r"^-\s+(?P<body>.+)$")
_CITE_MAP_ENTRY_RE = re.compile(
    r"^\[(?P<n>\d+)\]\s+(?P<kind>\w+)\s+(?P<target>\S+)\s*$",
    re.IGNORECASE,
)
_TITLE_ONLY_RE = re.compile(r"[A-Za-z][A-Za-z ]*")
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
class WeeklySummaryItem:
    """One Chronicle bullet so the UI does not parse legend hops or raw thread dumps.

    Weekdays stay English Monday–Sunday so paint can append parentheses without a calendar.
    """

    text: str
    weekdays: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.text).strip():
            raise ValueError("WeeklySummaryItem.text must be non-empty")
        allowed = set(WEEKDAY_NAMES)
        for name in self.weekdays:
            if name not in allowed:
                raise ValueError(
                    f"WeeklySummaryItem.weekdays {name!r} not in {WEEKDAY_NAMES}"
                )


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
    embedding: Literal["true", "false"] = "false"

    def __post_init__(self) -> None:
        if self.embedding not in ("true", "false"):
            raise ValueError(
                f"WeeklyEntity.embedding {self.embedding!r} must be 'true' or 'false'"
            )


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
class DayBrief:
    """One calendar day's worth of cited events + sourced sentences."""

    day: date
    events: tuple[EventCite, ...] = ()
    sentences: tuple[ClaimSentence, ...] = ()


@dataclass(frozen=True)
class WeeklyReviewPayload:
    """Carry cross-day-thread, wrap-ups, and entities beside the date brief so JSON can dump without Distill."""

    days: tuple[DayBrief, ...]
    legend: dict[int, str] = field(default_factory=dict)
    week_key: str = ""
    cross_day_thread: tuple[SpanCandidate, ...] = ()
    intra_day_thread: tuple["IntraDayThread", ...] = ()
    entities: tuple["WeeklyEntity", ...] = ()
    summary: tuple["WeeklySummaryItem", ...] = ()


def known_week_mem_ids(payload: WeeklyReviewPayload) -> set[str]:
    """Ids already in the 16:00 YAML so a midnight patch cannot duplicate those cards."""
    ids: set[str] = set()
    for value in payload.legend.values():
        text = str(value or "").strip()
        if text:
            ids.add(text)
    for entity in payload.entities:
        for block_id in entity.week_blocks:
            text = str(block_id or "").strip()
            if text:
                ids.add(text)
    for day in payload.days:
        for event in day.events:
            text = str(event.mem_id or "").strip()
            if text:
                ids.add(text)
    return ids


def _offset_cite(value: int | None, offset: int) -> int | None:
    if value is None:
        return None
    return value + offset


def _delta_has_story(delta: WeeklyReviewPayload) -> bool:
    if delta.legend or delta.entities or delta.summary or delta.cross_day_thread:
        return True
    if any(row.events or row.sentences for row in delta.days):
        return True
    return any(not row.empty and str(row.text).strip() for row in delta.intra_day_thread)


def _merge_intra_day(
    base: tuple[IntraDayThread, ...],
    delta: tuple[IntraDayThread, ...],
) -> tuple[IntraDayThread, ...]:
    """Append new Sunday bullets and pull the same lines off other weekdays.

    Leftover events often keep Saturday valid_from; without this strip they
    stay on Saturday after a repair patch restamps them onto Sunday.
    """
    by_day = {row.date: row for row in base}
    filled_days: set[date] = set()
    added_lines: set[str] = set()
    for row in delta:
        if row.empty or not str(row.text).strip():
            continue
        existing = by_day.get(row.date)
        if existing is None:
            by_day[row.date] = row
            filled_days.add(row.date)
            added_lines.update(
                _summary_text_key(line)
                for line in str(row.text or "").splitlines()
                if _summary_text_key(line)
            )
            continue
        merged_text = _merge_intra_lines(str(existing.text or ""), str(row.text or ""))
        if merged_text == str(existing.text or "").strip():
            continue
        by_day[row.date] = replace(existing, text=merged_text, empty=not bool(merged_text))
        filled_days.add(row.date)
        added_lines.update(
            _summary_text_key(line)
            for line in str(row.text or "").splitlines()
            if _summary_text_key(line)
        )
    if added_lines:
        for day, existing in list(by_day.items()):
            if day in filled_days:
                continue
            kept_lines = [
                line
                for line in str(existing.text or "").splitlines()
                if line.strip()
                and not any(
                    _summary_items_are_twins(_summary_text_key(line), other)
                    for other in added_lines
                )
            ]
            if kept_lines == [
                line for line in str(existing.text or "").splitlines() if line.strip()
            ]:
                continue
            text = "\n".join(kept_lines)
            by_day[day] = replace(existing, text=text, empty=not bool(text.strip()))
    return tuple(sorted(by_day.values(), key=lambda row: row.date))


def _merge_entities(
    base: tuple[WeeklyEntity, ...],
    delta: tuple[WeeklyEntity, ...],
) -> tuple[WeeklyEntity, ...]:
    by_key = {row.key: row for row in base}
    for row in delta:
        old = by_key.get(row.key)
        if old is None:
            by_key[row.key] = row
            continue
        blocks = tuple(dict.fromkeys((*old.week_blocks, *row.week_blocks)))
        aliases = tuple(dict.fromkeys((*old.aliases, *row.aliases)))
        seen_dates = [d for d in (old.first_seen, row.first_seen) if d is not None]
        last_dates = [d for d in (old.last_seen, row.last_seen) if d is not None]
        by_key[row.key] = replace(
            old,
            week_blocks=blocks,
            aliases=aliases,
            first_seen=min(seen_dates) if seen_dates else old.first_seen,
            last_seen=max(last_dates) if last_dates else old.last_seen,
            canonical=old.canonical or row.canonical,
        )
    return tuple(by_key.values())


def _merge_cross_day(
    base: tuple[SpanCandidate, ...],
    delta: tuple[SpanCandidate, ...],
    offset: int,
) -> tuple[SpanCandidate, ...]:
    merged = list(base)
    for thread in delta:
        overlap = None
        delta_keys = set(thread.entity_keys)
        if delta_keys:
            for existing in merged:
                if delta_keys & set(existing.entity_keys):
                    overlap = existing
                    break
        if overlap is None:
            new_id = thread.id
            used = {row.id for row in merged}
            if new_id in used:
                new_id = f"{thread.id}-evening"
            steps = tuple(
                replace(
                    step,
                    cite_n=_offset_cite(step.cite_n, offset),
                )
                for step in thread.steps
            )
            merged.append(replace(thread, id=new_id, steps=steps, cite=_offset_cite(thread.cite, offset)))
            continue
        next_seq = max((step.seq for step in overlap.steps), default=0)
        extra: list[ThreadStep] = []
        for step in thread.steps:
            next_seq += 1
            extra.append(
                replace(
                    step,
                    seq=next_seq,
                    cite_n=_offset_cite(step.cite_n, offset),
                    via=step.via or ("evolves" if next_seq > 1 else None),
                )
            )
        if not extra:
            continue
        end = extra[-1].date if extra[-1].date > overlap.end_date else overlap.end_date
        idx = merged.index(overlap)
        merged[idx] = replace(
            overlap,
            steps=(*overlap.steps, *extra),
            end_date=end,
        )
    return tuple(merged)


def _merge_days(
    base: tuple[DayBrief, ...],
    delta: tuple[DayBrief, ...],
    offset: int,
) -> tuple[DayBrief, ...]:
    by_day = {row.day: row for row in base}
    for row in delta:
        if not row.events and not row.sentences:
            continue
        events = tuple(
            replace(event, number=event.number + offset) for event in row.events
        )
        sentences = tuple(
            replace(sentence, cite=_offset_cite(sentence.cite, offset))
            for sentence in row.sentences
        )
        existing = by_day.get(row.day)
        if existing is None:
            by_day[row.day] = replace(row, events=events, sentences=sentences)
            continue
        by_day[row.day] = replace(
            existing,
            events=(*existing.events, *events),
            sentences=(*existing.sentences, *sentences),
        )
    return tuple(sorted(by_day.values(), key=lambda row: row.day))


def _summary_text_key(text: str) -> str:
    return str(text or "").strip().lstrip("- ").strip()


def _summary_items_are_twins(left: str, right: str) -> bool:
    """Treat a 10-word Outcome stub and the leftover B/C/O body as one Chronicle row.

    Worker-1 titles clip Outcome; leftover bodies start at Beginning, so prefix
    match on the whole string would keep both lines in the Weekly UI.
    """
    a = _summary_text_key(left)
    b = _summary_text_key(right)
    if not a or not b:
        return False
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter.split()) > 10:
        return False
    return shorter in longer


def _merge_intra_lines(old_text: str, new_text: str) -> str:
    """Replace Outcome stubs with leftover bodies instead of listing both."""
    lines: list[str] = []
    keys: list[str] = []

    def add(raw: str) -> None:
        stripped = raw.strip()
        if not stripped:
            return
        key = _summary_text_key(stripped)
        formatted = stripped if stripped.startswith("- ") else f"- {stripped}"
        for i, old_key in enumerate(keys):
            if _summary_items_are_twins(old_key, key):
                if len(key) >= len(old_key):
                    lines[i] = formatted
                    keys[i] = key
                return
        lines.append(formatted)
        keys.append(key)

    for line in str(old_text or "").splitlines():
        add(line)
    for line in str(new_text or "").splitlines():
        add(line)
    return "\n".join(lines)


def _merge_evening_summary(
    base: tuple[WeeklySummaryItem, ...],
    delta: tuple[WeeklySummaryItem, ...],
) -> tuple[WeeklySummaryItem, ...]:
    """Replace truncated Saturday twins with the longer Sunday leftover line.

    Unique-by-text would keep the stub and drop the body the Weekly UI needs.
    """
    summary = list(base)
    for item in delta:
        twin_i = next(
            (
                i
                for i, old in enumerate(summary)
                if _summary_items_are_twins(old.text, item.text)
            ),
            None,
        )
        if twin_i is None:
            summary.append(item)
            continue
        old = summary[twin_i]
        old_key = _summary_text_key(old.text)
        new_key = _summary_text_key(item.text)
        longer_text = item.text if len(new_key) >= len(old_key) else old.text
        days = item.weekdays or old.weekdays
        if "Sunday" in item.weekdays:
            days = tuple(name for name in days if name != "Saturday")
            if "Sunday" not in days:
                days = (*days, "Sunday")
        summary[twin_i] = WeeklySummaryItem(text=longer_text, weekdays=days)
    return tuple(summary)


def _merge_sunday_evening_payload(
    base: WeeklyReviewPayload,
    delta: WeeklyReviewPayload,
) -> WeeklyReviewPayload:
    """Append Sunday-evening Worker-1 output onto the 16:00 YAML without rewriting weekday rows.

    A second full generate_week would replace Mon–Sat intra-day and renumber cites.
    """
    if not _delta_has_story(delta):
        return base
    offset = max(base.legend.keys(), default=0)
    legend = dict(base.legend)
    for number, mem_id in sorted(delta.legend.items()):
        legend[number + offset] = mem_id
    intra = _merge_intra_day(base.intra_day_thread, delta.intra_day_thread)
    return replace(
        base,
        days=_merge_days(base.days, delta.days, offset),
        legend=legend,
        intra_day_thread=intra,
        entities=_merge_entities(base.entities, delta.entities),
        summary=_merge_evening_summary(base.summary, delta.summary),
        cross_day_thread=_merge_cross_day(
            base.cross_day_thread, delta.cross_day_thread, offset
        ),
        week_key=base.week_key or delta.week_key,
    )


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


def validate_weekly_review_payload(payload: WeeklyReviewPayload) -> list[str]:
    """Run structural checks over a full ``WeeklyReviewPayload``.

    Conflict, hypothesis, and span-candidate id checks are gone with those
    brief sections. Day order and sentence cites are what still corrupt a brief.
    """
    errors: list[str] = []
    errors.extend(validate_week_order(payload.days))
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


def _is_section_title(stripped: str) -> bool:
    """Stop a day paragraph at Cite map or a retired single-line heading.

    Old briefs still put extra headings between Sunday and the cite map.
    A heading is letters and spaces only, so event sentences and bullets stay
    inside the day. Naming those headings here would keep them in the renderer.
    """
    if stripped == CITE_MAP_SECTION_TITLE:
        return True
    return _TITLE_ONLY_RE.fullmatch(stripped) is not None


def assign_typed_citations(payload: WeeklyReviewPayload) -> WeeklyReviewPayload:
    """Return the payload unchanged so a cite pass cannot drop Worker-1 summary.

    Rebuilding the payload to number retired sections omitted summary and
    emptied Chronicle. Event legend numbers are already assigned.
    """
    return payload


def render_cite_map_section(payload: WeeklyReviewPayload) -> str:
    """Machine-readable event citation targets for UI / round-trip."""
    lines = [CITE_MAP_SECTION_TITLE]
    for n, mem_id in sorted(payload.legend.items()):
        lines.append(f"- [{n}] event {mem_id}")
    if len(lines) == 1:
        lines.append("- None.")
    return "\n".join(lines)


def render_four_part_brief(week_key: str, payload: WeeklyReviewPayload) -> str:
    """Render the date brief (no Cite map footer, no retired sections)."""
    key = (week_key or payload.week_key or "").strip()
    parts = [
        f"{WEEKLY_BRIEF_TITLE_PREFIX}{key}".rstrip(),
        "",
        render_date_brief_section(payload.days),
    ]
    return "\n".join(parts).rstrip() + "\n"


def render_weekly_review_brief(week_key: str, payload: WeeklyReviewPayload) -> str:
    """Render the date brief plus Cite map (Worker 2 / persistence body)."""
    assigned = assign_typed_citations(payload)
    if week_key and not assigned.week_key:
        assigned = replace(assigned, week_key=week_key)
    body = render_four_part_brief(week_key or assigned.week_key, assigned)
    return body.rstrip() + "\n\n" + render_cite_map_section(assigned) + "\n"


def is_four_part_brief(text: str) -> bool:
    """True when Brief body is the event-first date container.

    A Weekly Brief title is enough. Retired headings are not required, so old
    files and date-only briefs both match.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _WEEKLY_BRIEF_TITLE_RE.match(stripped.splitlines()[0].strip()):
        return True
    return " · Events" in stripped


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
            if _DAY_HEADER_RE.match(stripped) or _is_section_title(stripped):
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


def parse_four_part_brief(text: str) -> WeeklyReviewPayload:
    """Parse a date brief (+ optional Cite map) back into a payload.

    Retired single-line headings between the days and the cite map are skipped
    so an old file still yields the same day events and event legend.
    """
    lines = (text or "").splitlines()
    week_key = ""
    if lines:
        title = _WEEKLY_BRIEF_TITLE_RE.match(lines[0].strip())
        if title:
            week_key = title.group("week")

    days = _parse_day_blocks(lines[1:])
    legend: dict[int, str] = {}
    cite_i = next(
        (idx for idx, raw in enumerate(lines) if raw.strip() == CITE_MAP_SECTION_TITLE),
        None,
    )
    if cite_i is not None:
        i = cite_i + 1
        while i < len(lines):
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                continue
            if _is_section_title(stripped) or _DAY_HEADER_RE.match(stripped):
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
            if entry and entry.group("kind").casefold() == "event":
                legend[int(entry.group("n"))] = entry.group("target")
            i += 1

    reconciled_days: list[DayBrief] = []
    for day_brief in days:
        events = tuple(
            EventCite(e.number, legend.get(e.number, e.mem_id))
            for e in day_brief.events
        )
        reconciled_days.append(
            DayBrief(day=day_brief.day, events=events, sentences=day_brief.sentences)
        )

    if not legend:
        for day_brief in reconciled_days:
            for event in day_brief.events:
                legend[event.number] = event.mem_id

    return WeeklyReviewPayload(
        days=tuple(reconciled_days),
        legend=legend,
        week_key=week_key,
        cross_day_thread=(),
        intra_day_thread=(),
        entities=(),
    )


def payloads_structurally_equal(
    left: WeeklyReviewPayload,
    right: WeeklyReviewPayload,
) -> list[str]:
    """Return mismatch descriptions for a golden round-trip comparison.

    Retired brief sections are not part of the payload, so only week, days,
    and the event legend can drift.
    """
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
    if left.legend != right.legend:
        errors.append(f"legend {left.legend} != {right.legend}")
    return errors
