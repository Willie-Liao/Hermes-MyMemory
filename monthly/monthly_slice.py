"""Week-slices, degree ordering, and 8K batch packing — zero LLM.

Dumping a month of daily files into one prompt is already a retrieval
strategy; this module prices and partitions that material first.
"""

from __future__ import annotations

import calendar
import hashlib
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

_mymemory = Path(__file__).resolve().parent.parent
_weekly = _mymemory / "weekly"
for path in (_mymemory, _weekly, Path(__file__).resolve().parent):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from monthly_schema import (  # noqa: E402
    CARRY_CARD_TOKEN_CAP,
    MAP_BATCH_TOKENS,
    MonthlyMetrics,
    MonthlyState,
    month_key as format_month_key,
)
from monthly_state import hermes_home  # noqa: E402
from weekly_event_workers import parse_blocks  # noqa: E402
from weekly_json import load_sidecar, normalize_entity_key  # noqa: E402

_MEM_ID_RE = re.compile(
    r"(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)",
    re.IGNORECASE,
)
_WRAPUP_SPLIT = "## Day wrap-up"
SYNTHESIS_TYPES = frozenset({"decision", "procedure", "event"})


def count_tokens(text: str) -> int:
    """Count with o200k when tiktoken is present so the 8K cap matches the worker tokenizer."""
    try:
        import tiktoken

        return len(tiktoken.get_encoding("o200k_base").encode(text or ""))
    except Exception:
        return max(1, (len(text or "") + 3) // 4)


def calendar_range(month_key: str) -> tuple[date, date]:
    year_s, _, month_s = month_key.partition("-")
    year, month = int(year_s), int(month_s)
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def week_key_for(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def previous_month_key(day: date) -> str:
    first = day.replace(day=1)
    prev = first - timedelta(days=1)
    return format_month_key(prev.year, prev.month)


def clause_body(body: str) -> str:
    """Drop the wrap-up trailer so Band A prose cannot leak into a monthly prompt."""
    text = body or ""
    if _WRAPUP_SPLIT in text:
        text = text.split(_WRAPUP_SPLIT, 1)[0]
    return text.strip()


def _related_ids(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, str):
        found.extend(_MEM_ID_RE.findall(value))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_related_ids(item))
    return tuple(dict.fromkeys(found))


def _supersedes_ids(value: Any) -> tuple[str, ...]:
    return _related_ids(value)


def daily_dir() -> Path:
    return hermes_home() / "memories" / "staging" / "daily"


def weekly_dir() -> Path:
    return hermes_home() / "memories" / "staging" / "weekly"


def iter_daily_files() -> list[Path]:
    folder = daily_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.md") if p.stat().st_size > 0)


@dataclass(frozen=True)
class SliceBlock:
    """One typed daily card stripped to the clause the map prompt is allowed to see."""

    id: str
    day: date
    type: str
    entity: str
    valid_from: str
    valid_to: str
    confidence: str
    importance: str
    body: str
    clause: str
    related: tuple[str, ...]
    supersedes: tuple[str, ...]
    degree: int
    week_key: str
    entity_key: str


@dataclass(frozen=True)
class WeekSlice:
    """ISO week ∩ calendar month so Aug 1-2 stay in August instead of July's belongs_to."""

    week_key: str
    days: tuple[date, ...]
    blocks: tuple[SliceBlock, ...]
    rendered: str
    tokens: int


@dataclass(frozen=True)
class Batch:
    """Greedy pack of consecutive slices under MAP_BATCH_TOKENS, never split mid-week."""

    index: int
    slices: tuple[WeekSlice, ...]
    rendered: str
    tokens: int
    ids: frozenset[str]
    source_sha256: str


@dataclass
class MechanicalFacts:
    """Counts and supersedes chains the reduce stage must not re-derive from notes."""

    metrics: MonthlyMetrics
    state: tuple[MonthlyState, ...]
    open_decision_ids: tuple[str, ...]
    cross_month_entities: tuple[dict[str, Any], ...]
    supersedes_pairs: tuple[tuple[str, str], ...]
    intra_day_thread_text: str
    weeks: tuple[str, ...]
    decision_kind_counts: dict[str, int]
    behavior: dict[str, Any]
    blocks_by_id: dict[str, SliceBlock] = field(default_factory=dict)
    all_dpe: tuple[SliceBlock, ...] = ()

    def rendered(self) -> str:
        lines = [
            f"metrics: {self.metrics}",
            f"open_decisions: {list(self.open_decision_ids)}",
            f"supersedes_pairs: {list(self.supersedes_pairs)}",
            f"cross_month_entities: {[row['key'] for row in self.cross_month_entities]}",
            f"entity_weeks: { {row['key']: list(row.get('weeks') or ()) for row in self.cross_month_entities} }",
            f"decision_kind_counts: {self.decision_kind_counts}",
            f"behavior: {self.behavior}",
        ]
        if self.intra_day_thread_text.strip():
            lines.append("intra_day_thread:")
            lines.append(self.intra_day_thread_text)
        return "\n".join(lines)


def load_all_blocks() -> list[tuple[date, dict[str, Any], str]]:
    """Parse every nonempty daily file through weekly's splitter so id shapes stay identical.

    Rejected cards are closed contradictions; leaving them in would let monthly
    synthesis resurrect a fact Phase 2 already marked dated.
    """
    rows: list[tuple[date, dict[str, Any], str]] = []
    for path in iter_daily_files():
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in parse_blocks(text):
            fm = block.get("frontmatter")
            if not isinstance(fm, dict):
                continue
            if str(fm.get("status") or "").strip() == "rejected":
                continue
            rows.append((day, fm, str(block.get("body") or "")))
    return rows


def inbound_degree(rows: Iterable[tuple[date, dict[str, Any], str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for _day, fm, _body in rows:
        for mem_id in _related_ids(fm.get("related")):
            counts[mem_id] += 1
    return counts


def render_clause_line(block: SliceBlock) -> str:
    """Keep the canonical key on every map line so batches can name the same node."""
    if block.entity_key:
        return f"{block.entity_key} | {block.id} | {block.clause}"
    return f"{block.id} | {block.clause}"


def _slice_block(
    day: date,
    fm: dict[str, Any],
    body: str,
    degree: int,
) -> SliceBlock | None:
    mem_id = str(fm.get("id") or "").strip()
    kind = str(fm.get("type") or "").strip().casefold()
    if not mem_id:
        return None
    if kind not in SYNTHESIS_TYPES and kind != "fact":
        return None
    entity = str(fm.get("entity") or "")
    return SliceBlock(
        id=mem_id,
        day=day,
        type=kind,
        entity=entity,
        valid_from=str(fm.get("valid_from") or day.isoformat()),
        valid_to=str(fm.get("valid_to") or ""),
        confidence=str(fm.get("confidence") or ""),
        importance=str(fm.get("importance") or ""),
        body=body,
        clause=clause_body(body),
        related=_related_ids(fm.get("related")),
        supersedes=_supersedes_ids(fm.get("supersedes")),
        degree=degree,
        week_key=week_key_for(day),
        entity_key=normalize_entity_key(entity),
    )


def blocks_for_month(month_key: str) -> tuple[SliceBlock, ...]:
    rows = load_all_blocks()
    degrees = inbound_degree(rows)
    start, end = calendar_range(month_key)
    out: list[SliceBlock] = []
    for day, fm, body in rows:
        if day < start or day > end:
            continue
        block = _slice_block(day, fm, body, degrees[str(fm.get("id") or "")])
        if block is not None:
            out.append(block)
    return tuple(out)


def week_slices(month_key: str, *, types: frozenset[str] = SYNTHESIS_TYPES) -> tuple[WeekSlice, ...]:
    """Partition calendar-month D/P/E cards into ISO week ∩ month slices."""
    selected = [b for b in blocks_for_month(month_key) if b.type in types]
    grouped: dict[str, list[SliceBlock]] = defaultdict(list)
    for block in selected:
        grouped[block.week_key].append(block)
    slices: list[WeekSlice] = []
    for key in sorted(grouped):
        items = grouped[key]
        items.sort(key=lambda b: (-b.degree, b.day.isoformat(), b.id))
        rendered = "\n".join(render_clause_line(b) for b in items)
        days = tuple(sorted({b.day for b in items}))
        slices.append(
            WeekSlice(
                week_key=key,
                days=days,
                blocks=tuple(items),
                rendered=rendered,
                tokens=count_tokens(rendered),
            )
        )
    return tuple(slices)


def _split_slice_by_day(slice_: WeekSlice, max_tokens: int) -> list[WeekSlice]:
    if slice_.tokens <= max_tokens:
        return [slice_]
    by_day: dict[date, list[SliceBlock]] = defaultdict(list)
    for block in slice_.blocks:
        by_day[block.day].append(block)
    parts: list[WeekSlice] = []
    buf: list[SliceBlock] = []
    buf_days: list[date] = []

    def flush() -> None:
        nonlocal buf, buf_days
        if not buf:
            return
        rendered = "\n".join(render_clause_line(b) for b in buf)
        parts.append(
            WeekSlice(
                week_key=slice_.week_key,
                days=tuple(buf_days),
                blocks=tuple(buf),
                rendered=rendered,
                tokens=count_tokens(rendered),
            )
        )
        buf, buf_days = [], []

    for day in sorted(by_day):
        day_blocks = by_day[day]
        trial = buf + day_blocks
        rendered = "\n".join(render_clause_line(b) for b in trial)
        if buf and count_tokens(rendered) > max_tokens:
            flush()
        buf.extend(day_blocks)
        buf_days.append(day)
    flush()
    return parts


def pack_batches(
    slices: tuple[WeekSlice, ...] | list[WeekSlice],
    *,
    max_tokens: int = MAP_BATCH_TOKENS,
) -> tuple[Batch, ...]:
    """Cap a single prompt at 8K so month synthesis latency stays flat as the corpus triples.

    Slices are never split across batches: a week cut in half would let the model
    describe the same story twice with different evidence.
    """
    units: list[WeekSlice] = []
    for slice_ in slices:
        units.extend(_split_slice_by_day(slice_, max_tokens))
    batches: list[Batch] = []
    buf: list[WeekSlice] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf, buf_tokens
        if not buf:
            return
        rendered = "\n\n".join(
            f"# {row.week_key}\n{row.rendered}" for row in buf
        )
        ids = frozenset(b.id for row in buf for b in row.blocks)
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        batches.append(
            Batch(
                index=len(batches) + 1,
                slices=tuple(buf),
                rendered=rendered,
                tokens=count_tokens(rendered),
                ids=ids,
                source_sha256=digest,
            )
        )
        buf, buf_tokens = [], 0

    for slice_ in units:
        extra = slice_.tokens + (2 if buf else 0)
        if buf and buf_tokens + extra > max_tokens:
            flush()
        buf.append(slice_)
        buf_tokens += extra
    flush()
    return tuple(batches)


def _kind_from_clause(clause: str) -> str:
    lowered = clause.lstrip()
    if lowered.startswith("Preference:"):
        return "preference"
    return "decision"


def mechanical_facts(month_key: str) -> MechanicalFacts:
    """Build counts, state rows, and bilingual entity aliases with zero LLM calls so monthly roster keys cannot split on original-language surfaces."""
    month_blocks = blocks_for_month(month_key)
    dpe = tuple(b for b in month_blocks if b.type in SYNTHESIS_TYPES)
    counts = Counter(b.type for b in month_blocks)
    active_days = {b.day for b in month_blocks}
    open_decisions = tuple(
        b.id for b in month_blocks if b.type == "decision" and b.valid_to.strip().casefold() == "open"
    )
    kind_counts: Counter[str] = Counter()
    explicit = 0
    for block in month_blocks:
        if block.type != "decision":
            continue
        kind_counts[_kind_from_clause(block.clause)] += 1
        if block.confidence.strip().casefold() == "explicit":
            explicit += 1
    kind_counts["explicit"] = explicit
    kind_counts["open"] = len(open_decisions)

    pairs: list[tuple[str, str]] = []
    by_id = {b.id: b for b in month_blocks}
    all_rows = load_all_blocks()
    all_by_id: dict[str, SliceBlock] = {}
    degrees = inbound_degree(all_rows)
    for day, fm, body in all_rows:
        block = _slice_block(day, fm, body, degrees[str(fm.get("id") or "")])
        if block is not None:
            all_by_id[block.id] = block
    state_rows: list[MonthlyState] = []
    idx = 0
    for block in month_blocks:
        if not block.supersedes:
            continue
        for prior in block.supersedes:
            pairs.append((prior, block.id))
            prior_block = all_by_id.get(prior) or by_id.get(prior)
            old_id = f"st-{idx}"
            new_id = f"st-{idx + 1}"
            idx += 2
            prior_text = (prior_block.clause if prior_block else prior)[:200]
            state_rows.append(
                MonthlyState(
                    id=old_id,
                    text=prior_text,
                    valid_from=prior_block.valid_from if prior_block else block.valid_from,
                    invalid_at=block.valid_from,
                    invalidated_by=new_id,
                    status="superseded",
                    source=prior,
                )
            )
            state_rows.append(
                MonthlyState(
                    id=new_id,
                    text=block.clause[:200],
                    valid_from=block.valid_from,
                    invalid_at=None,
                    invalidated_by=None,
                    status="current",
                    source=block.id,
                )
            )

    entity_months: dict[str, set[str]] = defaultdict(set)
    entity_weeks: dict[str, set[str]] = defaultdict(set)
    entity_meta: dict[str, dict[str, Any]] = {}
    claims: dict[str, set[str]] = defaultdict(set)
    english_canonical: dict[str, str] = {}
    for _day, fm, _body in all_rows:
        entity = str(fm.get("entity") or "").strip()
        ek = normalize_entity_key(entity)
        if not ek:
            continue
        english_canonical.setdefault(ek, entity)
        raw_aliases = fm.get("entity_aliases")
        if not isinstance(raw_aliases, list):
            continue
        for alias in raw_aliases:
            ak = normalize_entity_key(str(alias or "").strip())
            if ak and ak != ek:
                claims[ak].add(ek)
    unique_redirect = {
        ak: next(iter(eks)) for ak, eks in claims.items() if len(eks) == 1
    }
    for day, fm, body in all_rows:
        entity = str(fm.get("entity") or "").strip()
        if not entity:
            continue
        raw_key = normalize_entity_key(entity)
        if not raw_key:
            continue
        key = unique_redirect.get(raw_key, raw_key)
        entity_months[key].add(day.strftime("%Y-%m"))
        if day.strftime("%Y-%m") == month_key:
            entity_weeks[key].add(week_key_for(day))
        meta = entity_meta.setdefault(
            key,
            {
                "canonical": english_canonical.get(key) or entity,
                "aliases": set(),
                "first": day.isoformat(),
                "last": day.isoformat(),
                "count": 0,
            },
        )
        preferred = english_canonical.get(key)
        if preferred:
            meta["canonical"] = preferred
        if entity != meta["canonical"]:
            meta["aliases"].add(entity)
        raw_aliases = fm.get("entity_aliases")
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                text = str(alias or "").strip()
                if text and text != meta["canonical"]:
                    meta["aliases"].add(text)
        meta["last"] = day.isoformat()
        if day.isoformat() < meta["first"]:
            meta["first"] = day.isoformat()
        if day.strftime("%Y-%m") == month_key:
            meta["count"] += 1

    cross = []
    for key, months in sorted(entity_months.items()):
        weeks_this_month = tuple(sorted(entity_weeks.get(key, ())))
        if len(months) < 2 and len(weeks_this_month) < 2:
            continue
        meta = entity_meta[key]
        cross.append(
            {
                "key": key,
                "canonical": meta["canonical"],
                "aliases": tuple(sorted(meta.get("aliases") or ())),
                "months": tuple(sorted(months)),
                "weeks": weeks_this_month,
                "month_count": int(meta["count"]),
                "first_seen": meta["first"],
                "last_seen": meta["last"],
            }
        )

    roles: Counter[str] = Counter()
    predicates: set[str] = set()
    for block in month_blocks:
        if block.type != "event":
            continue
        # participants live on frontmatter; recover from related-less body is not needed
    # Re-parse event fm for participants/predicate from original rows
    start, end = calendar_range(month_key)
    event_count = 0
    for day, fm, _body in all_rows:
        if day < start or day > end:
            continue
        if str(fm.get("type") or "").casefold() != "event":
            continue
        event_count += 1
        pred = str(fm.get("predicate") or "").strip()
        if pred:
            predicates.add(pred)
        parts = fm.get("participants")
        if isinstance(parts, list):
            for row in parts:
                if isinstance(row, dict):
                    roles[str(row.get("entity") or "")] += 1

    weeks = tuple(sorted({b.week_key for b in dpe}))
    intra_bits: list[str] = []
    folder = weekly_dir()
    if folder.is_dir():
        for path in sorted(folder.glob("*.md")):
            try:
                obj = load_sidecar(path)
            except (OSError, ValueError, FileNotFoundError):
                continue
            if not isinstance(obj, dict):
                continue
            belongs = str(obj.get("belongs_to") or "")
            # include weekly files whose range overlaps the calendar month
            week = str(obj.get("week_key") or path.stem)
            if week not in weeks and belongs != month_key:
                continue
            intra = obj.get("intra-day-thread") or []
            if intra:
                intra_bits.append(f"{week}: {intra}")

    metrics = MonthlyMetrics(
        decisions=counts.get("decision", 0),
        procedures=counts.get("procedure", 0),
        events=counts.get("event", 0),
        facts=counts.get("fact", 0),
        active_days=len(active_days),
        open_decisions=len(open_decisions),
        superseded=len(pairs),
        weeks=len(weeks),
    )
    behavior = {
        "active_days": len(active_days),
        "blocks_per_active_day": round(len(month_blocks) / len(active_days), 1)
        if active_days
        else 0,
        "distinct_predicates": len(predicates),
        "roles": dict(roles),
    }
    return MechanicalFacts(
        metrics=metrics,
        state=tuple(state_rows),
        open_decision_ids=open_decisions,
        cross_month_entities=tuple(cross),
        supersedes_pairs=tuple(pairs),
        intra_day_thread_text="\n".join(intra_bits),
        weeks=weeks,
        decision_kind_counts=dict(kind_counts),
        behavior=behavior,
        blocks_by_id={b.id: b for b in month_blocks},
        all_dpe=dpe,
    )


def carry_card(previous_month_key: str) -> str:
    """Compact last-month residue so comparison cannot reread the previous file's bodies."""
    from monthly_writer import load_month

    try:
        payload = load_month(previous_month_key)
    except FileNotFoundError:
        return ""
    except (OSError, ValueError):
        return ""
    current_state = [row.text for row in payload.state if row.status == "current"]
    unfinished = [
        item.name for item in payload.cross_week_items if item.current_status != "completed"
    ]
    text = "\n".join(
        [
            f"previous_month: {payload.key}",
            f"summary: {payload.summary}",
            f"current_state: {current_state}",
            f"unfinished: {unfinished}",
        ]
    )
    if count_tokens(text) > CARRY_CARD_TOKEN_CAP:
        text = text[: CARRY_CARD_TOKEN_CAP * 4]
    return text


def slice_corpus_tokens(month_key: str) -> int:
    return sum(row.tokens for row in week_slices(month_key))
