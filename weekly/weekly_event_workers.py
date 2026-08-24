"""Parallel Worker 1 coordinator: one event worker + analyst workers.

Topology:
  1. One ``worker1_event`` LLM reads all active-day events and groups by date
     (emits mem ids in ``related``; no fact/procedure/decision attach).
  2. Post-worker validator: mem ids resolve in daily F/P/D and agree with event text.
  3. Deterministic merger (Mon–Sun) + DayBriefs (event cites on date; narrative sans cites).
  4. Conflict / Hypothesis / Span analysts run in parallel on merged events.
  5. Never writes hot memory; purposes are observable via ``call_llm(..., purpose=)``.

Returns Distill YAML blocks plus a ``WeeklyReviewPayload``. Step 3 renders the
four-part Brief deterministically from that payload.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

try:
    from .weekly_citations import normalize_event_citations
    from .weekly_distill_validate import _frontmatter_blocks
    from .weekly_event_schema import (
        EMPTY_DAY_TEXT,
        ClaimSentence,
        ConflictItem,
        DayBrief,
        EventCite,
        HypothesisItem,
        IntraDayThread,
        SpanCandidate,
        ThreadStep,
        WeeklyEntity,
        WeeklyReviewPayload,
        WeeklySummaryItem,
        weekday_label,
    )
    from .weekly_event_validate import validate_event_blocks_against_dailies
    from . import weekly_tools
except ImportError:  # pragma: no cover - flat pytest load
    from weekly_citations import normalize_event_citations  # type: ignore[no-redef]
    from weekly_distill_validate import _frontmatter_blocks  # type: ignore[no-redef]
    from weekly_event_schema import (  # type: ignore[no-redef]
        EMPTY_DAY_TEXT,
        ClaimSentence,
        ConflictItem,
        DayBrief,
        EventCite,
        HypothesisItem,
        IntraDayThread,
        SpanCandidate,
        ThreadStep,
        WeeklyEntity,
        WeeklyReviewPayload,
        WeeklySummaryItem,
        weekday_label,
    )
    from weekly_event_validate import (  # type: ignore[no-redef]
        validate_event_blocks_against_dailies,
    )
    import weekly_tools  # type: ignore[no-redef]

CallLlmFn = Callable[..., str]
CallLlmToolsFn = Callable[..., dict[str, Any]]
LogFn = Callable[[str], None]

# Cap day-bundle chars sent to the LLM (attempt 1). Retries omit the full dump.
MAX_DAILY_CHARS = 28000
MAX_DAY_FILE_CHARS = 8000

EVENT_WORKER_PURPOSES: tuple[str, ...] = ("worker1_event",)
ANALYST_PURPOSES: tuple[str, ...] = (
    "worker1_thread",
    "worker1_summary",
)

_EVENT_ONLY_FORBIDDEN: frozenset[str] = frozenset(
    {"fact", "procedure", "decision", "conflict", "hypothesis"}
)
_CLAIM_KINDS: frozenset[str] = frozenset({"fact", "procedure", "decision"})
_WEEK_KEY_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{2})$")
_MEM_ID_RE = re.compile(
    r"(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)",
    re.IGNORECASE,
)
MAX_WORKER_ATTEMPTS = 3
MAX_RETRY_CHARS = 4000

# Re-export for tests / callers that need the empty-day contract.
__all__ = [
    "ANALYST_PURPOSES",
    "EMPTY_DAY_TEXT",
    "EVENT_WORKER_PURPOSES",
    "Worker1Result",
    "attach_claim_sentences",
    "iso_week_dates",
    "merge_event_blocks",
    "parse_blocks",
    "partition_days",
    "run_parallel_worker1",
]


@dataclass
class Worker1Result:
    """Carry thread and entity extras beside Distill blocks so generate can dump JSON without a second LLM pass."""

    blocks: list[dict[str, Any]]
    legend: dict[int, str]
    payload: WeeklyReviewPayload
    errors: list[str] = field(default_factory=list)
    fallback_days: tuple[date, ...] = ()
    purposes_called: tuple[str, ...] = ()
    cross_day_thread: tuple[SpanCandidate, ...] = ()
    entities: tuple[WeeklyEntity, ...] = ()
    summary: tuple[WeeklySummaryItem, ...] = ()


def iso_week_dates(week_key: str) -> list[date]:
    """Return Monday..Sunday for an ISO week key ``YYYY-Www``."""
    match = _WEEK_KEY_RE.match((week_key or "").strip())
    if not match:
        raise ValueError(f"invalid week_key: {week_key!r}")
    year = int(match.group("year"))
    week = int(match.group("week"))
    start = date.fromisocalendar(year, week, 1)
    return [start + timedelta(days=i) for i in range(7)]


def partition_days(days: Sequence[date], n: int = 3) -> list[list[date]]:
    """Split ordered days into ``n`` contiguous chunks (date-preserving)."""
    if n < 1:
        raise ValueError("n must be >= 1")
    ordered = list(days)
    chunks: list[list[date]] = [[] for _ in range(n)]
    if not ordered:
        return chunks
    base, rem = divmod(len(ordered), n)
    idx = 0
    for i in range(n):
        take = base + (1 if i < rem else 0)
        chunks[i] = ordered[idx : idx + take]
        idx += take
    return chunks


def _files_by_day(files: Sequence[Path]) -> dict[date, Path]:
    by_day: dict[date, Path] = {}
    for path in files:
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        by_day[day] = path
    return by_day


def parse_blocks(content: str) -> list[dict[str, Any]]:
    """Share the weekly Distill splitter so monthly cannot invent a second YAML block parser."""
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    region = text
    # Allow optional ## Distill / ## Events headers
    header = re.search(r"^##\s+\S+", text, flags=re.MULTILINE)
    if header and "---" in text[header.end() :]:
        region = text[header.end() :].strip()
    blocks: list[dict[str, Any]] = []
    for _line_no, fm, body in _frontmatter_blocks(region):
        if fm.get("__yaml_error__"):
            continue
        blocks.append({"frontmatter": fm, "body": body})
    return blocks


_parse_blocks = parse_blocks


def _render_block(block: dict[str, Any]) -> str:
    fm = block.get("frontmatter")
    if not isinstance(fm, dict):
        fm = {}
    body = str(block.get("body") or "").rstrip()
    dumped = yaml.safe_dump(
        fm,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    parts = ["---", dumped, "---"]
    if body:
        parts.append(body)
    return "\n".join(parts)


def _day_bundle(
    by_day: dict[date, Path],
    days: Sequence[date],
    *,
    max_total_chars: int = MAX_DAILY_CHARS,
    max_file_chars: int = MAX_DAY_FILE_CHARS,
) -> str:
    chunks: list[str] = []
    used = 0
    for day in days:
        path = by_day.get(day)
        label = f"{weekday_label(day)} — {day.isoformat()}"
        if path is None:
            piece = f"# {label}\n\n(no daily file)\n"
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                piece = f"# {label}\n\n(read failed: {exc})\n"
            else:
                if len(text) > max_file_chars:
                    text = text[: max_file_chars - 20] + "\n…(truncated)…\n"
                piece = f"# Source: {path.name} ({label})\n\n{text}"
        if used + len(piece) > max_total_chars and chunks:
            chunks.append(
                f"# … remaining days omitted (cap {max_total_chars} chars) …\n"
            )
            break
        chunks.append(piece)
        used += len(piece)
    return "\n\n---\n\n".join(chunks) if chunks else "(empty partition)"


def _build_event_worker_prompt(
    week_key: str,
    purpose: str,
    days: Sequence[date],
    day_bundle: str,
    *,
    attempt: int = 1,
    errors: Sequence[str] = (),
    previous_args: Mapping[str, Any] | None = None,
) -> str:
    day_list = ", ".join(
        f"{weekday_label(d)} {d.isoformat()}" for d in days
    ) or "(no days assigned)"
    base = (
        f"You are weekly Worker 1 event extractor ({purpose}).\n\n"
        f"Week: {week_key}\n"
        f"Active days: {day_list}\n\n"
        "Call submit_weekly_event with an events array (type:event only).\n"
        "Each event needs entity, predicate, participants "
        "(User/requester + Assistant/executor), valid_from/valid_to, "
        "confidence (explicit|high|medium|low), sources, related "
        "(must include a mem-… id), beginning, course, outcome.\n"
        "Do NOT emit fact/procedure/decision/conflict/hypothesis.\n"
        "Do NOT write ## Brief. Do NOT write hot memory.\n"
        "Do NOT emit free-form YAML — use the tool call only.\n"
        "If a day has no event evidence, omit events for that day.\n"
    )
    if attempt <= 1:
        return base + f"\nDAILY SOURCES (ALL ACTIVE DAYS):\n{day_bundle}\n"
    return (
        base
        + "\n"
        + weekly_tools.failed_fields_teach(
            errors,
            previous_args or {},
            role="event",
            patch_tool="patch_weekly_event",
            attempt=attempt,
            max_attempts=MAX_WORKER_ATTEMPTS,
        )
    )


def _build_analyst_prompt(
    *,
    purpose: str,
    week_key: str,
    role: str,
    merged_events_md: str,
    legend: dict[int, str],
    extra_context: str = "",
    attempt: int = 1,
    errors: Sequence[str] = (),
    previous_args: Mapping[str, Any] | None = None,
) -> str:
    """Thread-only analyst prompt; conflict/hypothesis Distill fences would hide via: invalidates."""
    _ = role
    legend_lines = "\n".join(
        f"[{n}] {mem}" for n, mem in sorted(legend.items())
    ) or "(empty legend)"
    submit = "submit_weekly_thread"
    patch = "patch_weekly_thread"
    rules = (
        f"Call {submit} with cross-day-thread. Each chain needs ≥2 "
        "distinct step dates, existing event_id values from MERGED EVENTS "
        "(mem-…-event-… ids), label, and step text. Seq 1 has no via. "
        "Later steps via evolves or invalidates (optional to_seq). "
        "Drop one-day chains. Do not emit wrap-ups, entities, legend, "
        "conflicts, or hypotheses."
    )
    base = (
        f"You are weekly Worker 1 thread analyst ({purpose}).\n\n"
        f"Week: {week_key}\n\n"
        f"{rules}\n"
        "Do NOT write hot memory. Do NOT invent events.\n"
        "Do NOT emit Distill YAML — use the tool.\n\n"
        f"CITATION LEGEND:\n{legend_lines}\n"
    )
    if attempt <= 1:
        base += f"\nMERGED EVENTS:\n{merged_events_md}\n"
        if extra_context.strip():
            base += f"\n\nADDITIONAL CONTEXT:\n{extra_context}\n"
        return base
    return base + "\n" + weekly_tools.failed_fields_teach(
        errors,
        previous_args or {},
        role="thread",
        patch_tool=patch,
        attempt=attempt,
        max_attempts=MAX_WORKER_ATTEMPTS,
    )


def _default_call_llm_tools(
    prompt: str,
    *,
    purpose: str,
    force_tool_name: str,
) -> dict[str, Any]:
    """Production path: forced weekly tool call via shared worker_llm helper."""
    try:
        from worker_llm import run_worker_llm_tools
    except ImportError:  # pragma: no cover
        from worker_llm import run_worker_llm_tools  # type: ignore

    weekly_tools.ensure_weekly_tools_registered()
    return run_worker_llm_tools(
        prompt,
        plugin="memory-weekly",
        purpose=purpose,
        platform="cli",
        max_iterations=2,
        enabled_toolsets=[weekly_tools.WEEKLY_TOOLSET],
        force_tool_name=force_tool_name,
    )


def _invoke_weekly_tools(
    prompt: str,
    *,
    purpose: str,
    force_tool_name: str,
    call_llm_tools: CallLlmToolsFn | None,
) -> dict[str, Any]:
    fn = call_llm_tools or _default_call_llm_tools
    return fn(prompt, purpose=purpose, force_tool_name=force_tool_name)


def _validate_event_only_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    """Post-tool graph check only — field shape is owned by schema + render.

    Keep: type:event (or skip) and related must cite a mem-… id.
    """
    errors: list[str] = []
    if not blocks:
        return errors  # empty partition is OK
    for block in blocks:
        fm = block.get("frontmatter") or {}
        block_type = str(fm.get("type") or "").strip().casefold()
        if block_type in _EVENT_ONLY_FORBIDDEN:
            errors.append(f"event worker must not emit type: {block_type}")
            continue
        if block_type != "event":
            errors.append(f"event worker unknown type: {block_type or '(missing)'}")
            continue
        related = fm.get("related")
        if not isinstance(related, list) or not related:
            errors.append("event related must include a mem-… id")
            continue
        if not any(_MEM_ID_RE.search(str(entry)) for entry in related):
            errors.append("event related must include a mem-… id")
    return errors


def _validate_analyst_blocks(
    blocks: list[dict[str, Any]], *, expected_type: str, event_ids: set[str]
) -> list[str]:
    """Post-tool graph check — field presence is owned by schema + render."""
    errors: list[str] = []
    for block in blocks:
        fm = block.get("frontmatter") or {}
        block_type = str(fm.get("type") or "").strip().casefold()
        if block_type != expected_type:
            errors.append(
                f"{expected_type} analyst must not emit type: {block_type or '(missing)'}"
            )
            continue
        related = fm.get("related") or []
        if not isinstance(related, list) or not related:
            errors.append(f"{expected_type} related is required")
        elif expected_type in {"conflict", "hypothesis"} and event_ids:
            has_event = any(str(r).strip() in event_ids for r in related)
            if not has_event:
                errors.append(
                    f"{expected_type} related must include a week event id"
                )
    return errors


def _fallback_events_from_dailies(
    by_day: dict[date, Path],
    days: Sequence[date],
    *,
    purpose: str,
) -> list[dict[str, Any]]:
    """Bounded deterministic fallback: promote daily event blocks for assigned days."""
    blocks: list[dict[str, Any]] = []
    for day in days:
        path = by_day.get(day)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _line_no, fm, body in _frontmatter_blocks(text):
            if not isinstance(fm, dict) or fm.get("__yaml_error__"):
                continue
            if str(fm.get("type") or "").strip().casefold() != "event":
                continue
            if str(fm.get("status") or "").strip() in {
                "approved",
                "rejected",
                "dropped",
            }:
                continue
            block_id = str(fm.get("id") or "").strip() or f"fallback-{purpose}-{day.isoformat()}"
            mem_id = block_id if block_id.startswith("mem-") else ""
            if not mem_id:
                mem_match = _MEM_ID_RE.search(body or "") or _MEM_ID_RE.search(
                    str(fm.get("sources") or "")
                )
                mem_id = mem_match.group(1) if mem_match else f"mem-{day.isoformat()}-fallback"
            related = fm.get("related")
            if not isinstance(related, list) or not related:
                related = [mem_id]
            out_fm = {
                "id": f"w-evt-{day.isoformat()}-{len(blocks) + 1}",
                "type": "event",
                "entity": fm.get("entity") or "Unknown",
                "predicate": fm.get("predicate") or "recorded",
                "participants": fm.get("participants")
                if isinstance(fm.get("participants"), list) and fm.get("participants")
                else [{"entity": str(fm.get("entity") or "Unknown")}],
                "valid_from": str(fm.get("valid_from") or day.isoformat()),
                "valid_to": str(fm.get("valid_to") or day.isoformat()),
                "confidence": fm.get("confidence") or "medium",
                "status": "candidate",
                "sources": fm.get("sources")
                if isinstance(fm.get("sources"), list) and fm.get("sources")
                else [f"daily:{path.name}"],
                "related": related,
            }
            body_text = (body or "").strip() or f"Fallback event from {path.name}."
            blocks.append({"frontmatter": out_fm, "body": body_text})
    if not blocks:
        # Still represent the day: one synthetic placeholder per day with sources
        for day in days:
            path = by_day.get(day)
            if path is None:
                continue
            mem_id = f"mem-{day.isoformat()}-day"
            blocks.append(
                {
                    "frontmatter": {
                        "id": f"w-evt-{day.isoformat()}-1",
                        "type": "event",
                        "entity": "DailySource",
                        "predicate": "day_recorded",
                        "participants": [{"entity": "DailySource"}],
                        "valid_from": day.isoformat(),
                        "valid_to": day.isoformat(),
                        "confidence": "low",
                        "status": "candidate",
                        "sources": [f"daily:{path.name}"],
                        "related": [mem_id],
                    },
                    "body": (
                        f"Bounded fallback for {weekday_label(day)} "
                        f"{day.isoformat()} after worker failure [1]."
                    ),
                }
            )
    return blocks


def _run_event_worker(
    *,
    week_key: str,
    purpose: str,
    days: Sequence[date],
    by_day: dict[date, Path],
    call_llm: CallLlmFn | None = None,
    call_llm_tools: CallLlmToolsFn | None = None,
    log: LogFn,
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """Returns (blocks, used_fallback, error notes). Uses forced submit/patch tools.

    Schema failures may fall back to daily events. Agreement-validator exhaustion
    returns no blocks and ``used_fallback=False`` (hard fail — skip analysts).
    """
    day_bundle = _day_bundle(by_day, days)
    daily_files = [by_day[d] for d in days if d in by_day]
    last_errors: list[str] = []
    previous_args: dict[str, Any] = {}
    agreement_failed = False

    for attempt in range(1, MAX_WORKER_ATTEMPTS + 1):
        force_name = (
            "submit_weekly_event" if attempt == 1 else "patch_weekly_event"
        )
        prompt = _build_event_worker_prompt(
            week_key,
            purpose,
            days,
            day_bundle if attempt == 1 else "",
            attempt=attempt,
            errors=last_errors,
            previous_args=previous_args,
        )
        try:
            log(
                f"weekly {purpose} LLM tool call start {week_key} "
                f"attempt={attempt}/{MAX_WORKER_ATTEMPTS} tool={force_name} "
                f"days={len(days)}"
            )
            result = _invoke_weekly_tools(
                prompt,
                purpose=purpose,
                force_tool_name=force_name,
                call_llm_tools=call_llm_tools,
            )
        except Exception as exc:  # noqa: BLE001
            last_errors = [f"agent error: {exc}"]
            agreement_failed = False
            log(f"weekly {purpose} agent failed {week_key}: {exc}")
            continue

        tool_name = str(result.get("tool_name") or "").strip()
        tool_args = result.get("tool_args")
        if not isinstance(tool_args, dict):
            tool_args = {}

        if attempt >= 2 and tool_name == "submit_weekly_event":
            last_errors = [
                "attempt 2+ must call patch_weekly_event only "
                "(no from-scratch submit)"
            ]
            agreement_failed = False
            continue

        if weekly_tools.is_skip_event(tool_name, tool_args):
            log(f"weekly {purpose} skip {week_key}")
            return [], False, []

        if attempt == 1:
            if tool_name not in {"submit_weekly_event", "skip_weekly_event"}:
                last_errors = [
                    f"expected submit_weekly_event tool call, got {tool_name or 'none'}"
                ]
                agreement_failed = False
                continue
            previous_args = dict(tool_args)
        else:
            if tool_name != "patch_weekly_event":
                last_errors = [
                    f"expected patch_weekly_event, got {tool_name or 'none'}"
                ]
                agreement_failed = False
                continue
            previous_args = weekly_tools.merge_field_patch(previous_args, tool_args)

        enum_errors = weekly_tools.validate_closed_choice_args(
            previous_args, role="event"
        )
        if enum_errors:
            last_errors = enum_errors
            agreement_failed = False
            continue

        blocks = weekly_tools.render_events_from_tool_args(previous_args)
        errors = _validate_event_only_blocks(blocks)
        if errors:
            last_errors = errors
            agreement_failed = False
            log(
                f"weekly {purpose} validation failed {week_key} "
                f"attempt={attempt}: {'; '.join(errors[:3])}"
            )
            continue
        agree_errors = validate_event_blocks_against_dailies(blocks, daily_files)
        if agree_errors:
            last_errors = agree_errors
            agreement_failed = True
            log(
                f"weekly {purpose} agreement failed {week_key} "
                f"attempt={attempt}: {'; '.join(agree_errors[:3])}"
            )
            continue
        log(f"weekly {purpose} ok {week_key} events={len(blocks)}")
        return blocks, False, []

    if agreement_failed:
        log(
            f"weekly {purpose} agreement exhausted {week_key} — no write / skip analysts"
        )
        return [], False, list(last_errors)

    if not days:
        log(f"weekly {purpose} empty partition exhausted {week_key} — no fallback days")
        return [], True, list(last_errors)

    # Legacy text path unused; keep call_llm param for signature compat with tests.
    _ = call_llm

    log(
        f"weekly {purpose} fallback after failures {week_key} "
        f"days={[d.isoformat() for d in days]} errors={last_errors[:3]}"
    )
    return _fallback_events_from_dailies(by_day, days, purpose=purpose), True, list(
        last_errors
    )


def merge_event_blocks(
    week_dates: Sequence[date],
    worker_blocks: Sequence[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Merge event blocks in Monday–Sunday order by valid_from, then normalize cites."""
    day_rank = {d: i for i, d in enumerate(week_dates)}

    def _sort_key(block: dict[str, Any]) -> tuple[int, str]:
        fm = block.get("frontmatter") or {}
        vf = str(fm.get("valid_from") or "").strip()
        try:
            d = date.fromisoformat(vf)
            rank = day_rank.get(d, 99)
        except ValueError:
            rank = 99
        return rank, str(fm.get("id") or "")

    flat = [b for group in worker_blocks for b in group]
    flat.sort(key=_sort_key)
    return normalize_event_citations(flat)


def attach_claim_sentences(
    week_dates: Sequence[date],
    by_day: dict[date, Path],
    legend: dict[int, str],
    event_blocks: Sequence[dict[str, Any]],
) -> list[DayBrief]:
    """Deterministically attach fact/procedure/decision sentences to each day.

    Kept for tests/compat; the Worker 1 coordinator no longer uses this path.
    """
    mem_to_cite = {mem: n for n, mem in legend.items()}
    events_by_day = _events_by_day(week_dates, event_blocks)

    sentences_by_day: dict[date, list[ClaimSentence]] = {d: [] for d in week_dates}
    for day in week_dates:
        path = by_day.get(day)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _line_no, fm, body in _frontmatter_blocks(text):
            if not isinstance(fm, dict) or fm.get("__yaml_error__"):
                continue
            if str(fm.get("status") or "").strip() in {
                "approved",
                "rejected",
                "dropped",
            }:
                continue
            kind = str(fm.get("type") or "").strip().casefold()
            if kind not in _CLAIM_KINDS:
                continue
            body_text = (body or "").strip()
            if not body_text:
                continue
            # One concise sentence: first line / first sentence.
            first = re.split(r"(?<=[.!?])\s+", body_text, maxsplit=1)[0].strip()
            if first and first[-1] not in ".!?":
                first = f"{first}."
            block_id = str(fm.get("id") or "").strip()
            cite = mem_to_cite.get(block_id)
            if cite is None:
                mem_match = _MEM_ID_RE.search(block_id) or _MEM_ID_RE.search(body_text)
                if mem_match:
                    cite = mem_to_cite.get(mem_match.group(1))
            claim_kind = kind if kind in {"fact", "procedure", "decision"} else "fact"
            sentences_by_day[day].append(
                ClaimSentence(text=first, kind=claim_kind, cite=cite)  # type: ignore[arg-type]
            )

    days: list[DayBrief] = []
    for day in week_dates:
        days.append(
            DayBrief(
                day=day,
                events=tuple(events_by_day.get(day, ())),
                sentences=tuple(sentences_by_day.get(day, ())),
            )
        )
    return days


def _events_by_day(
    week_dates: Sequence[date],
    event_blocks: Sequence[dict[str, Any]],
) -> dict[date, list[EventCite]]:
    """Collect week-local event cites keyed by valid_from day."""
    events_by_day: dict[date, list[EventCite]] = {d: [] for d in week_dates}
    for block in event_blocks:
        fm = block.get("frontmatter") or {}
        if str(fm.get("type") or "").strip().casefold() != "event":
            continue
        vf = str(fm.get("valid_from") or "").strip()
        try:
            day = date.fromisoformat(vf)
        except ValueError:
            continue
        if day not in events_by_day:
            continue
        related = fm.get("related") or []
        if not isinstance(related, list):
            related = [related]
        for entry in related:
            match = re.match(
                r"^\[(\d+)\]\s+(mem-\S+)\s*$",
                str(entry).strip(),
                flags=re.IGNORECASE,
            )
            if match:
                events_by_day[day].append(
                    EventCite(int(match.group(1)), match.group(2))
                )
    return events_by_day


def _event_body_narrative(body: str) -> str:
    """Event paragraph text with ``[N]`` markers stripped (cites live on the date)."""
    text = re.sub(r"\s*\[\d+\]", "", body or "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if text[-1] not in ".!?":
        text = f"{text}."
    return text


_STAGE_LABEL_RE = re.compile(
    r"(?i)\b(Beginning|Course|Outcome)\s*:\s*"
)


def _split_event_stages(body: str) -> dict[str, str]:
    """Parse Beginning/Course/Outcome clauses from a Distill event body."""
    text = re.sub(r"\s*\[\d+\]", "", body or "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return {}
    parts = _STAGE_LABEL_RE.split(text)
    # split keeps delimiters: [pre, Label, content, Label, content, ...]
    out: dict[str, str] = {}
    if len(parts) == 1:
        return {}
    i = 1
    while i + 1 < len(parts):
        label = parts[i].strip().casefold()
        content = parts[i + 1].strip(" ;")
        # Truncate at next stage boundary already handled by split.
        content = content.strip()
        if content.endswith(";"):
            content = content[:-1].strip()
        if label in {"beginning", "course", "outcome"} and content:
            out[label] = content
        i += 2
    return out


def _humanize_predicate(predicate: str) -> str:
    raw = (predicate or "").strip().replace("-", "_")
    if not raw:
        return ""
    words = [w for w in raw.split("_") if w]
    if not words:
        return ""
    return " ".join(words).capitalize()


def _compress_phrase(text: str, *, max_words: int = 10) -> str:
    """Cut on a clause boundary so a 10-word headline cannot land mid-clause.

    Blind word slices produced titles like 'Kimi quota exhaustion diagnosed provider'
    when the source continued after a semicolon.
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = cleaned.strip(" .;:")
    if not cleaned:
        return ""
    words = cleaned.split()
    if len(words) <= max_words:
        phrase = " ".join(words)
    else:
        window = words[:max_words]
        cut = None
        for i, word in enumerate(window):
            if re.search(r"[.;:]$", word):
                cut = i + 1
        if cut is None:
            for i, word in enumerate(window):
                if word.endswith(","):
                    cut = i + 1
        phrase = " ".join(window[:cut] if cut else window).rstrip(",;")
    if phrase and phrase[0].islower():
        phrase = phrase[0].upper() + phrase[1:]
    return phrase


def _event_body_plain_brief(body: str) -> str:
    """Plain skim paragraph: no Beginning/Course/Outcome labels."""
    stages = _split_event_stages(body)
    if stages:
        ordered = [
            stages[k]
            for k in ("beginning", "course", "outcome")
            if stages.get(k)
        ]
        chunks: list[str] = []
        for part in ordered:
            bit = part.strip()
            if bit and bit[0].islower():
                bit = bit[0].upper() + bit[1:]
            if bit and bit[-1] not in ".!?":
                bit = f"{bit}."
            chunks.append(bit)
        return " ".join(chunks).strip()
    return _event_body_narrative(body)


def _event_title_summary(
    body: str, *, predicate: str = "", entity: str = ""
) -> str:
    """Short event headline for Worker 2 Brief (summary, not role/predicate slug)."""
    stages = _split_event_stages(body)
    for key in ("outcome", "beginning", "course"):
        if stages.get(key):
            title = _compress_phrase(stages[key], max_words=10)
            if title:
                return title
    plain = _event_body_plain_brief(body)
    if plain:
        # First sentence-ish chunk.
        first = re.split(r"(?<=[.!?])\s+", plain, maxsplit=1)[0]
        title = _compress_phrase(first, max_words=10)
        if title:
            return title
    human = _humanize_predicate(predicate)
    if human:
        return human
    ent = (entity or "").strip()
    if ent:
        return ent
    return "Event"


def _day_briefs_from_events(
    week_dates: Sequence[date],
    event_blocks: Sequence[dict[str, Any]],
) -> list[DayBrief]:
    """Build Mon–Sun DayBriefs: event cites on date; titled plain paragraphs."""
    events_by_day = _events_by_day(week_dates, event_blocks)
    narratives: dict[date, list[ClaimSentence]] = {d: [] for d in week_dates}
    for block in event_blocks:
        fm = block.get("frontmatter") or {}
        if str(fm.get("type") or "").strip().casefold() != "event":
            continue
        vf = str(fm.get("valid_from") or "").strip()
        try:
            day = date.fromisoformat(vf)
        except ValueError:
            continue
        if day not in narratives:
            continue
        raw_body = str(block.get("body") or "")
        plain = _event_body_plain_brief(raw_body)
        if not plain:
            continue
        title = _event_title_summary(
            raw_body,
            predicate=str(fm.get("predicate") or ""),
            entity=str(fm.get("entity") or ""),
        )
        narratives[day].append(
            ClaimSentence(text=plain, kind="fact", cite=None, title=title)
        )
    return [
        DayBrief(
            day=day,
            events=tuple(events_by_day.get(day, ())),
            sentences=tuple(narratives.get(day, ())),
        )
        for day in week_dates
    ]


def _procedure_blocks_from_claims(
    day_briefs: Sequence[DayBrief],
    event_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Lift procedure claim sentences into Distill procedure blocks (legacy bridge)."""
    if not event_ids:
        return []
    anchor = event_ids[0]
    blocks: list[dict[str, Any]] = []
    for day_brief in day_briefs:
        for idx, sentence in enumerate(day_brief.sentences, start=1):
            if sentence.kind != "procedure":
                continue
            related: list[Any] = [anchor]
            if sentence.cite is not None:
                # keep cite number only as body marker; related needs event id
                pass
            body = sentence.text.strip()
            if sentence.cite is not None and f"[{sentence.cite}]" not in body:
                body = f"{body} [{sentence.cite}]"
            blocks.append(
                {
                    "frontmatter": {
                        "id": f"w-proc-{day_brief.day.isoformat()}-{idx}",
                        "type": "procedure",
                        "confidence": "medium",
                        "status": "candidate",
                        "sources": [f"daily:{day_brief.day.isoformat()}"],
                        "related": related,
                    },
                    "body": body,
                }
            )
    return blocks


def _daily_block_ids_for_days(
    by_day: dict[date, Path],
    days: Sequence[date],
) -> set[str]:
    """Collect every frontmatter id from the week's daily staging files."""
    ids: set[str] = set()
    for day in days:
        path = by_day.get(day)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in _parse_blocks(text):
            fm = block.get("frontmatter") or {}
            bid = str(fm.get("id") or "").strip()
            if bid:
                ids.add(bid)
    return ids


def _run_analyst(
    *,
    role: str,
    purpose: str,
    week_key: str,
    merged_events_md: str,
    legend: dict[int, str],
    event_ids: set[str],
    call_llm: CallLlmFn | None = None,
    call_llm_tools: CallLlmToolsFn | None = None,
    log: LogFn,
    extra_context: str = "",
    allowed_span_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[SpanCandidate]]:
    """Force submit then patch so a second submit cannot wipe a half-valid thread."""
    _ = call_llm, allowed_span_ids, role
    last_errors: list[str] = []
    previous_args: dict[str, Any] = {}
    submit_name = "submit_weekly_thread"
    patch_name = "patch_weekly_thread"

    for attempt in range(1, MAX_WORKER_ATTEMPTS + 1):
        force_name = submit_name if attempt == 1 else patch_name
        prompt = _build_analyst_prompt(
            purpose=purpose,
            week_key=week_key,
            role="thread",
            merged_events_md=merged_events_md if attempt == 1 else "",
            legend=legend,
            extra_context=extra_context if attempt == 1 else "",
            attempt=attempt,
            errors=last_errors,
            previous_args=previous_args,
        )
        try:
            log(
                f"weekly {purpose} LLM tool call start {week_key} "
                f"attempt={attempt}/{MAX_WORKER_ATTEMPTS} tool={force_name}"
            )
            result = _invoke_weekly_tools(
                prompt,
                purpose=purpose,
                force_tool_name=force_name,
                call_llm_tools=call_llm_tools,
            )
        except Exception as exc:  # noqa: BLE001
            last_errors = [f"agent error: {exc}"]
            log(f"weekly {purpose} agent failed {week_key}: {exc}")
            continue

        tool_name = str(result.get("tool_name") or "").strip()
        tool_args = result.get("tool_args")
        if not isinstance(tool_args, dict):
            tool_args = {}

        if attempt >= 2 and tool_name == submit_name:
            last_errors = [
                f"attempt 2+ must call {patch_name} only "
                "(no from-scratch submit)"
            ]
            continue

        if attempt == 1:
            if tool_name != submit_name:
                last_errors = [
                    f"expected {submit_name}, got {tool_name or 'none'}"
                ]
                continue
            previous_args = dict(tool_args)
        else:
            if tool_name != patch_name:
                last_errors = [
                    f"expected {patch_name}, got {tool_name or 'none'}"
                ]
                continue
            previous_args = weekly_tools.merge_field_patch(previous_args, tool_args)

        enum_errors = weekly_tools.validate_closed_choice_args(
            previous_args, role="thread"
        )
        if enum_errors:
            last_errors = enum_errors
            continue

        spans, errors = threads_from_tool_args(
            previous_args, event_ids=event_ids, legend=legend
        )
        if not errors:
            log(f"weekly {purpose} ok {week_key} threads={len(spans)}")
            return [], spans
        last_errors = errors
        log(
            f"weekly {purpose} validation failed {week_key} "
            f"attempt={attempt}: {'; '.join(last_errors[:3])}"
        )

    log(f"weekly {purpose} soft-empty after failures {week_key}: {last_errors[:3]}")
    return [], []


def threads_from_tool_args(
    args: Mapping[str, Any],
    *,
    event_ids: set[str],
    legend: dict[int, str],
) -> tuple[list[SpanCandidate], list[str]]:
    """Keep only multi-day chains whose event_ids exist this week, so one-day LLM noise cannot become Chronicle rows."""
    items = args.get("cross-day-thread")
    if not isinstance(items, list):
        return [], []
    cite_by_id = {mem: n for n, mem in legend.items()}
    allowed = set(event_ids) | set(legend.values())
    threads: list[SpanCandidate] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"cross-day-thread[{i}] must be an object")
            continue
        raw_steps = item.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            errors.append(f"cross-day-thread[{i}] needs steps")
            continue
        steps: list[ThreadStep] = []
        dates: set[date] = set()
        skip = False
        for raw in raw_steps:
            if not isinstance(raw, Mapping):
                skip = True
                break
            event_id = str(raw.get("event_id") or "").strip()
            if event_id not in allowed:
                errors.append(
                    f"cross-day-thread[{i}] unknown event_id {event_id}"
                )
                skip = True
                break
            try:
                step_day = date.fromisoformat(str(raw.get("date") or "")[:10])
            except ValueError:
                errors.append(f"cross-day-thread[{i}] bad step date")
                skip = True
                break
            seq = int(raw.get("seq") or len(steps) + 1)
            via = str(raw.get("via") or "").strip() or None
            if seq == 1:
                via = None
            elif via not in {"evolves", "invalidates"}:
                via = "evolves"
            to_seq = raw.get("to_seq")
            cite_n = cite_by_id.get(event_id)
            try:
                steps.append(
                    ThreadStep(
                        seq=seq,
                        date=step_day,
                        event_id=event_id,
                        text=str(raw.get("text") or "").strip(),
                        cite_n=cite_n,
                        via=via,
                        to_seq=int(to_seq) if to_seq is not None else None,
                    )
                )
            except ValueError as exc:
                errors.append(str(exc))
                skip = True
                break
            dates.add(step_day)
        if skip:
            continue
        if len(dates) < 2:
            continue
        ordered = sorted(steps, key=lambda s: s.seq)
        outcome = item.get("outcome")
        try:
            threads.append(
                SpanCandidate(
                    id=str(item.get("id") or f"w-t{i+1}"),
                    label=str(item.get("label") or "thread"),
                    start_date=min(dates),
                    end_date=max(dates),
                    confidence="high",
                    related_event_ids=tuple(s.event_id for s in ordered),
                    steps=tuple(ordered),
                    outcome=dict(outcome) if isinstance(outcome, dict) else None,
                    entity_keys=tuple(
                        str(k) for k in item.get("entity_keys") or () if str(k).strip()
                    ),
                )
            )
        except ValueError as exc:
            errors.append(str(exc))
    return threads, errors


_WEEKDAY_RANK = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}


def _order_weekdays(names: Sequence[Any]) -> tuple[str, ...]:
    """Unique Monday–Sunday order so Chronicle parentheses do not follow LLM shuffle."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in names:
        name = str(raw).strip()
        if name not in _WEEKDAY_RANK or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    ordered.sort(key=lambda n: _WEEKDAY_RANK[n])
    return tuple(ordered)


def summary_from_tool_args(
    args: Mapping[str, Any],
) -> tuple[list[WeeklySummaryItem], list[str]]:
    """Keep only non-empty bullets so Chronicle cannot paint blank hops as summary."""
    items = args.get("summary")
    if not isinstance(items, list):
        return [], ["summary must be an array"]
    out: list[WeeklySummaryItem] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"summary[{i}] must be an object")
            continue
        text = str(item.get("text") or "").strip().lstrip("- ").strip()
        weekdays = _order_weekdays(item.get("weekdays") or ())
        if not text:
            errors.append(f"summary[{i}] text must be non-empty")
            continue
        if not weekdays:
            errors.append(f"summary[{i}] weekdays must list Monday–Sunday names")
            continue
        try:
            out.append(WeeklySummaryItem(text=text, weekdays=weekdays))
        except ValueError as exc:
            errors.append(str(exc))
    return out, errors


def _build_summary_prompt(
    *,
    week_key: str,
    intra: Sequence[IntraDayThread],
    cross: Sequence[SpanCandidate],
    attempt: int = 1,
    errors: Sequence[str] = (),
    previous_args: Mapping[str, Any] | None = None,
) -> str:
    """Stable prefix then thread YAML only — daily bundle would recache the thread prompt."""
    intra_lines: list[str] = []
    for row in intra:
        if row.empty or not str(row.text).strip():
            continue
        intra_lines.append(
            f"- date={row.date.isoformat()} weekday={row.weekday}\n  {row.text.strip()}"
        )
    cross_lines: list[str] = []
    for thread in cross:
        step_bits = ", ".join(
            f"{step.date.isoformat()} {step.text.strip()}" for step in thread.steps
        )
        cross_lines.append(
            f"- id={thread.id} label={thread.label} {thread.start_date}..{thread.end_date}\n"
            f"  steps: {step_bits}"
        )
    rules = (
        "You are weekly Worker 1 summary analyst (worker1_summary).\n\n"
        f"Week: {week_key}\n\n"
        "Call submit_weekly_summary with summary. Each item needs text "
        "(one sentence, no leading dash) and weekdays (Monday..Sunday).\n"
        "One row per non-empty intra-day wrap-up and per cross-day-thread.\n"
        "Sort by earliest date. Same earliest day: intra before cross.\n"
        "weekdays: unique names in Monday-Sunday order for dates that item covers.\n"
        "Do not emit legend, cite_n, wrap-up copies, or new events.\n"
    )
    if attempt <= 1:
        return (
            rules
            + "\nINTRA-DAY-THREAD:\n"
            + ("\n".join(intra_lines) or "(none)")
            + "\n\nCROSS-DAY-THREAD:\n"
            + ("\n".join(cross_lines) or "(none)")
            + "\n"
        )
    return rules + "\n" + weekly_tools.failed_fields_teach(
        errors,
        previous_args or {},
        role="summary",
        patch_tool="patch_weekly_summary",
        attempt=attempt,
        max_attempts=MAX_WORKER_ATTEMPTS,
    )


def _run_summary_worker(
    *,
    week_key: str,
    intra: Sequence[IntraDayThread],
    cross: Sequence[SpanCandidate],
    call_llm_tools: CallLlmToolsFn | None,
    log: LogFn,
) -> tuple[WeeklySummaryItem, ...]:
    """Force submit then patch for summary; empty tuple on exhaustion so generate still dumps."""
    last_errors: list[str] = []
    previous_args: dict[str, Any] = {}
    submit_name = "submit_weekly_summary"
    patch_name = "patch_weekly_summary"
    for attempt in range(1, MAX_WORKER_ATTEMPTS + 1):
        force_name = submit_name if attempt == 1 else patch_name
        prompt = _build_summary_prompt(
            week_key=week_key,
            intra=intra,
            cross=cross,
            attempt=attempt,
            errors=last_errors,
            previous_args=previous_args,
        )
        try:
            log(
                f"weekly worker1_summary LLM tool call start {week_key} "
                f"attempt={attempt}/{MAX_WORKER_ATTEMPTS} tool={force_name}"
            )
            result = _invoke_weekly_tools(
                prompt,
                purpose="worker1_summary",
                force_tool_name=force_name,
                call_llm_tools=call_llm_tools,
            )
        except Exception as exc:  # noqa: BLE001
            last_errors = [f"agent error: {exc}"]
            log(f"weekly worker1_summary agent failed {week_key}: {exc}")
            continue
        tool_name = str(result.get("tool_name") or "").strip()
        tool_args = result.get("tool_args")
        if not isinstance(tool_args, dict):
            tool_args = {}
        if attempt >= 2 and tool_name == submit_name:
            last_errors = [
                f"attempt 2+ must call {patch_name} only "
                "(no from-scratch submit)"
            ]
            continue
        if attempt == 1:
            if tool_name != submit_name:
                last_errors = [
                    f"expected {submit_name}, got {tool_name or 'none'}"
                ]
                continue
            previous_args = dict(tool_args)
        else:
            if tool_name != patch_name:
                last_errors = [
                    f"expected {patch_name}, got {tool_name or 'none'}"
                ]
                continue
            previous_args = weekly_tools.merge_field_patch(previous_args, tool_args)
        enum_errors = weekly_tools.validate_closed_choice_args(
            previous_args, role="summary"
        )
        if enum_errors:
            last_errors = enum_errors
            continue
        items, errors = summary_from_tool_args(previous_args)
        if not errors:
            log(f"weekly worker1_summary ok {week_key} rows={len(items)}")
            return tuple(items)
        last_errors = errors
        log(
            f"weekly worker1_summary validation failed {week_key} "
            f"attempt={attempt}: {'; '.join(last_errors[:3])}"
        )
    log(f"weekly worker1_summary soft-empty after failures {week_key}: {last_errors[:3]}")
    return ()


def _daily_supersedes_map(by_day: Mapping[date, Path]) -> dict[str, list[str]]:
    """Read daily supersedes so disk, not the model, closes a prior step."""
    out: dict[str, list[str]] = {}
    for path in by_day.values():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in _parse_blocks(text):
            fm = block.get("frontmatter") or {}
            bid = str(fm.get("id") or "").strip()
            sup = fm.get("supersedes")
            if not bid or not isinstance(sup, list):
                continue
            out[bid] = [str(x).strip() for x in sup if str(x).strip()]
    return out


def _stamp_invalidates_from_supersedes(
    threads: Sequence[SpanCandidate],
    by_day: Mapping[date, Path],
) -> list[SpanCandidate]:
    """Force via=invalidates when a later step's daily card supersedes an earlier step."""
    mapping = _daily_supersedes_map(by_day)
    stamped: list[SpanCandidate] = []
    for thread in threads:
        id_to_seq = {step.event_id: step.seq for step in thread.steps}
        new_steps: list[ThreadStep] = []
        for step in thread.steps:
            targets = mapping.get(step.event_id) or []
            hit = next((tid for tid in targets if tid in id_to_seq and id_to_seq[tid] < step.seq), None)
            if hit is None:
                new_steps.append(step)
                continue
            new_steps.append(
                replace(step, via="invalidates", to_seq=id_to_seq[hit])
            )
        stamped.append(replace(thread, steps=tuple(new_steps)))
    return stamped


def _fill_thread_entity_keys(
    threads: Sequence[SpanCandidate],
    entities: Sequence[WeeklyEntity],
) -> list[SpanCandidate]:
    """Join thread steps to roster keys so the LLM cannot invent a second entity spelling."""
    key_by_block: dict[str, str] = {}
    for entity in entities:
        for bid in entity.week_blocks:
            key_by_block[bid] = entity.key
    filled: list[SpanCandidate] = []
    for thread in threads:
        keys = tuple(
            dict.fromkeys(
                key_by_block[step.event_id]
                for step in thread.steps
                if step.event_id in key_by_block
            )
        )
        filled.append(replace(thread, entity_keys=keys or thread.entity_keys))
    return filled


_WRAPUP_HEADING_RE = re.compile(r"(?m)^## Day wrap-up[ \t]*\n?")


def _copy_daily_wrapup_trailer(content: str) -> str:
    """Copy digest.split_daily_wrapup without importing digest.py, which path-shadows as a non-package module in weekly tests."""
    text = content or ""
    matches = list(_WRAPUP_HEADING_RE.finditer(text))
    if not matches:
        return ""
    rest = text[matches[-1].end() :].strip()
    return "\n".join(ln.rstrip() for ln in rest.splitlines() if ln.strip())


def _intra_day_from_dailies(
    week_dates: Sequence[date], by_day: Mapping[date, Path]
) -> tuple[IntraDayThread, ...]:
    """Copy each day's wrap-up trailer so Chronicle does not invent leftover Outcomes."""
    rows: list[IntraDayThread] = []
    for day in week_dates:
        path = by_day.get(day)
        text = ""
        if path is not None and path.is_file():
            text = _copy_daily_wrapup_trailer(path.read_text(encoding="utf-8"))
        rows.append(
            IntraDayThread(
                date=day,
                weekday=weekday_label(day),
                source_field="day_wrapup",
                text=text,
                empty=not bool(text.strip()),
            )
        )
    return tuple(rows)


def _entities_from_event_blocks(
    blocks: Sequence[dict[str, Any]],
) -> tuple[WeeklyEntity, ...]:
    """Fold original-language entity_aliases onto the English weekly key so a Chinese surface cannot mint a second roster row."""
    try:
        from .weekly_json import normalize_entity_key
    except ImportError:  # pragma: no cover
        from weekly_json import normalize_entity_key  # type: ignore

    claims: dict[str, set[str]] = {}
    english_canonical: dict[str, str] = {}
    for block in blocks:
        fm = block.get("frontmatter") or {}
        surface = str(fm.get("entity") or "").strip()
        key = normalize_entity_key(surface)
        if not key:
            continue
        english_canonical.setdefault(key, surface)
        raw_aliases = fm.get("entity_aliases")
        if not isinstance(raw_aliases, list):
            continue
        for alias in raw_aliases:
            ak = normalize_entity_key(str(alias or "").strip())
            if ak and ak != key:
                claims.setdefault(ak, set()).add(key)
    unique_redirect = {
        ak: next(iter(eks)) for ak, eks in claims.items() if len(eks) == 1
    }

    buckets: dict[str, dict[str, Any]] = {}
    for block in blocks:
        fm = block.get("frontmatter") or {}
        surface = str(fm.get("entity") or "").strip()
        if not surface:
            continue
        raw_key = normalize_entity_key(surface)
        if not raw_key:
            continue
        key = unique_redirect.get(raw_key, raw_key)
        vf = str(fm.get("valid_from") or "").strip()[:10]
        bid = str(fm.get("id") or "").strip()
        related = fm.get("related") or []
        mems = []
        if bid.startswith("mem-"):
            mems.append(bid)
        mems.extend(
            str(r).strip()
            for r in related
            if isinstance(related, list) and str(r).strip().startswith("mem-")
        )
        bucket = buckets.setdefault(
            key,
            {
                "canonical": english_canonical.get(key) or surface,
                "aliases": set(),
                "dates": [],
                "blocks": [],
            },
        )
        preferred = english_canonical.get(key)
        if preferred:
            bucket["canonical"] = preferred
        bucket["aliases"].add(surface)
        raw_aliases = fm.get("entity_aliases")
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                text = str(alias or "").strip()
                if text:
                    bucket["aliases"].add(text)
        if vf:
            bucket["dates"].append(vf)
        bucket["blocks"].extend(mems)
        if surface.casefold() == bucket["canonical"].casefold() and "-" in surface:
            bucket["canonical"] = surface
    entities: list[WeeklyEntity] = []
    for key, bag in buckets.items():
        dates = sorted(bag["dates"])
        aliases = tuple(
            sorted(a for a in bag["aliases"] if a != bag["canonical"])
        )
        first = date.fromisoformat(dates[0]) if dates else None
        last = date.fromisoformat(dates[-1]) if dates else None
        entities.append(
            WeeklyEntity(
                key=key,
                canonical=str(bag["canonical"]),
                aliases=aliases,
                first_seen=first,
                last_seen=last,
                week_blocks=tuple(dict.fromkeys(bag["blocks"])),
                embedding=None,
            )
        )
    return tuple(entities)


def _entities_from_dailies(
    week_dates: Sequence[date], by_day: Mapping[date, Path]
) -> tuple[WeeklyEntity, ...]:
    """Roster from daily entity: fields so weekly JSON does not depend on Distill fences."""
    blocks: list[dict[str, Any]] = []
    for day in week_dates:
        path = by_day.get(day)
        if path is None or not path.is_file():
            continue
        try:
            blocks.extend(_parse_blocks(path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return _entities_from_event_blocks(blocks)


def _blocks_to_conflict_items(
    blocks: Sequence[dict[str, Any]],
) -> tuple[ConflictItem, ...]:
    items: list[ConflictItem] = []
    for block in blocks:
        fm = block.get("frontmatter") or {}
        related = fm.get("related") or ()
        if isinstance(related, list):
            related_ids = tuple(
                str(r).strip()
                for r in related
                if str(r).strip() and not str(r).strip().startswith("[")
            )
        else:
            related_ids = ()
        try:
            items.append(
                ConflictItem(
                    id=str(fm.get("id") or f"cfl-{len(items)+1}"),
                    text=str(block.get("body") or "").strip() or "Conflict.",
                    related_event_ids=related_ids,
                )
            )
        except ValueError:
            continue
    return tuple(items)


def _blocks_to_hypothesis_items(
    blocks: Sequence[dict[str, Any]],
) -> tuple[HypothesisItem, ...]:
    items: list[HypothesisItem] = []
    for block in blocks:
        fm = block.get("frontmatter") or {}
        related = fm.get("related") or ()
        if isinstance(related, list):
            related_ids = tuple(
                str(r).strip()
                for r in related
                if str(r).strip() and not str(r).strip().startswith("[")
            )
        else:
            related_ids = ()
        try:
            items.append(
                HypothesisItem(
                    id=str(fm.get("id") or f"hyp-{len(items)+1}"),
                    text=str(block.get("body") or "").strip() or "Hypothesis.",
                    related_event_ids=related_ids,
                )
            )
        except ValueError:
            continue
    return tuple(items)


def run_parallel_worker1(
    week_key: str,
    files: Sequence[Path],
    *,
    call_llm: CallLlmFn | None = None,
    call_llm_tools: CallLlmToolsFn | None = None,
    log: LogFn | None = None,
    reason: str = "",
) -> Worker1Result:
    """Coordinate one event worker then analysts; return Distill blocks + payload."""
    _log: LogFn = log or (lambda _msg: None)
    reason_bit = f" reason={reason}" if reason else ""
    week_dates = iso_week_dates(week_key)
    by_day = _files_by_day(files)
    active_days = [d for d in week_dates if d in by_day]
    _log(
        f"weekly worker1 parallel start {week_key} active_days="
        f"{[d.isoformat() for d in active_days]}{reason_bit}"
    )

    purposes_called: list[str] = []
    fallback_days: list[date] = []
    purpose = EVENT_WORKER_PURPOSES[0]
    purposes_called.append(purpose)
    try:
        event_worker_blocks, used_fallback, worker_errs = _run_event_worker(
            week_key=week_key,
            purpose=purpose,
            days=active_days,
            by_day=by_day,
            call_llm=call_llm,
            call_llm_tools=call_llm_tools,
            log=_log,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"weekly {purpose} future error {week_key}: {exc}")
        event_worker_blocks = _fallback_events_from_dailies(
            by_day, active_days, purpose=purpose
        )
        used_fallback = True
        worker_errs = [str(exc)]

    # Agreement validator exhausted: no write / skip analysts.
    if (
        not event_worker_blocks
        and worker_errs
        and not used_fallback
        and active_days
    ):
        empty_days = [
            DayBrief(day=d, events=(), sentences=()) for d in week_dates
        ]
        return Worker1Result(
            blocks=[],
            legend={},
            payload=WeeklyReviewPayload(
                days=tuple(empty_days),
                conflicts=(),
                hypotheses=(),
                span_candidates=(),
                legend={},
            ),
            errors=list(worker_errs),
            fallback_days=(),
            purposes_called=tuple(purposes_called),
        )

    if used_fallback:
        fallback_days.extend(active_days)

    event_blocks, legend = merge_event_blocks(week_dates, [event_worker_blocks])

    # Never silently drop an active day: cover gaps with bounded fallback events.
    covered_days: set[date] = set()
    for block in event_blocks:
        fm = block.get("frontmatter") or {}
        vf = str(fm.get("valid_from") or "").strip()
        try:
            covered_days.add(date.fromisoformat(vf))
        except ValueError:
            continue
    missing_days = [d for d in active_days if d not in covered_days]
    if missing_days:
        _log(
            f"weekly worker1 covering missing active days {week_key}: "
            f"{[d.isoformat() for d in missing_days]}{reason_bit}"
        )
        gap_blocks = _fallback_events_from_dailies(
            by_day, missing_days, purpose="worker1_day_cover"
        )
        event_blocks, legend = merge_event_blocks(
            week_dates, [event_blocks, gap_blocks]
        )
        fallback_days.extend(missing_days)

    day_briefs = _day_briefs_from_events(week_dates, event_blocks)
    event_ids = [
        str((b.get("frontmatter") or {}).get("id") or "").strip()
        for b in event_blocks
        if str((b.get("frontmatter") or {}).get("id") or "").strip()
    ]
    event_id_set = set(event_ids)
    merged_events_md = "\n\n".join(_render_block(b) for b in event_blocks) or "(no events)"

    # Possible overdue UI comes from memory-digest validate_weekly_spans only.
    # Do not invent Worker 1 span candidates into the Brief.
    span_candidates: list[SpanCandidate] = []
    cross_day: list[SpanCandidate] = []

    with ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="memory-weekly-analyst"
    ) as pool:
        fut_thread = pool.submit(
            _run_analyst,
            role="thread",
            purpose="worker1_thread",
            week_key=week_key,
            merged_events_md=merged_events_md,
            legend=legend,
            event_ids=event_id_set | set(legend.values()),
            call_llm=call_llm,
            call_llm_tools=call_llm_tools,
            log=_log,
        )
        purposes_called.append("worker1_thread")
        try:
            _blocks, cross_day = fut_thread.result()
        except Exception as exc:  # noqa: BLE001
            _log(f"weekly worker1_thread future error {week_key}: {exc}")
            cross_day = []

    procedure_blocks = _procedure_blocks_from_claims(day_briefs, event_ids)
    distill_blocks = list(event_blocks) + procedure_blocks
    intra = _intra_day_from_dailies(week_dates, by_day)
    entities = _entities_from_dailies(week_dates, by_day)
    cross_day = _stamp_invalidates_from_supersedes(cross_day, by_day)
    cross_day = _fill_thread_entity_keys(cross_day, entities)

    payload = WeeklyReviewPayload(
        days=tuple(day_briefs),
        conflicts=(),
        hypotheses=(),
        span_candidates=tuple(span_candidates),
        legend=dict(legend),
        week_key=week_key,
        cross_day_thread=tuple(cross_day),
        intra_day_thread=intra,
        entities=entities,
    )

    summary_items: tuple[WeeklySummaryItem, ...] = ()
    has_intra = any(not row.empty and str(row.text).strip() for row in intra)
    if has_intra or cross_day:
        purposes_called.append("worker1_summary")
        try:
            summary_items = _run_summary_worker(
                week_key=week_key,
                intra=intra,
                cross=tuple(cross_day),
                call_llm_tools=call_llm_tools,
                log=_log,
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"weekly worker1_summary future error {week_key}: {exc}")
            summary_items = ()
        payload = replace(payload, summary=summary_items)

    errors: list[str] = []
    if not event_blocks:
        errors.append("no event blocks after parallel Worker 1")
    if fallback_days:
        _log(
            f"weekly worker1 bounded fallback days "
            f"{[d.isoformat() for d in sorted(set(fallback_days))]}{reason_bit}"
        )

    return Worker1Result(
        blocks=distill_blocks,
        legend=legend,
        payload=payload,
        errors=errors,
        fallback_days=tuple(sorted(set(fallback_days))),
        purposes_called=tuple(purposes_called),
        cross_day_thread=tuple(cross_day),
        entities=entities,
        summary=summary_items,
    )
