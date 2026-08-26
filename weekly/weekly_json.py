"""Persist the weekly schema as YAML inside YYYY-Www.md so Chronicle has one file.

A JSON or YAML sidecar would become a second source of truth next to week_status.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

try:
    from memory_staging import _split_week_doc_frontmatter
except ImportError:  # pragma: no cover - packaged import
    from ..memory_staging import _split_week_doc_frontmatter

try:
    from .weekly_event_schema import (
        IntraDayThread,
        SpanCandidate,
        ThreadStep,
        WeeklyEntity,
        WeeklyReviewPayload,
        WeeklySummaryItem,
    )
except ImportError:  # pragma: no cover - flat pytest load
    from weekly_event_schema import (  # type: ignore[no-redef]
        IntraDayThread,
        SpanCandidate,
        ThreadStep,
        WeeklyEntity,
        WeeklyReviewPayload,
        WeeklySummaryItem,
    )

JSON_CROSS = "cross-day-thread"
JSON_INTRA = "intra-day-thread"
JSON_SUMMARY = "summary"
SCHEMA_VERSION = 2
_KEY_ORDER = (
    "schema_version",
    "cycle",
    "week_key",
    "belongs_to",
    "range",
    "generated_at",
    "generator",
    "entities",
    JSON_CROSS,
    JSON_INTRA,
    JSON_SUMMARY,
)


def normalize_entity_key(surface: str) -> str:
    """Join alias spellings so Memory Digest and MemoryDigest share one roster node.

    Hyphens and spaces must not mint a second key; CJK stays because isalnum keeps it.
    """
    return "".join(ch for ch in (surface or "").casefold() if ch.isalnum())


def dumps(payload: WeeklyReviewPayload, *, generated_at: str | None = None) -> str:
    """Keep an in-memory JSON twin of the MD schema for tests and the HTTP bridge.

    This string is not written next to YYYY-Www.md; a sidecar would race week_status.
    """
    return json.dumps(_to_dict(payload, generated_at=generated_at), ensure_ascii=False, indent=2) + "\n"


def dump_yaml(payload: WeeklyReviewPayload, *, generated_at: str | None = None) -> str:
    """YAML body of YYYY-Www.md so recall can parse threads without a second file.

    Wrap-up text often starts with ``- `` bullets; a quoted folded scalar can
    look like a nested list to strict parsers. Literal blocks stay one string.
    """

    class _Dumper(yaml.SafeDumper):
        """Private dumper so wrap-up literal style cannot leak onto global SafeDumper."""

        pass

    def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
        if "\n" in data or data.lstrip().startswith("- "):
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    _Dumper.add_representer(str, _str_representer)
    return yaml.dump(
        _to_dict(payload, generated_at=generated_at),
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
    )


def loads(raw: str) -> WeeklyReviewPayload:
    """Re-coerce ISO date strings so callers get dataclasses, not leftover JSON/YAML primitives."""
    text = raw or ""
    stripped = text.lstrip()
    if stripped.startswith("---"):
        _fm, rest = _split_week_doc_frontmatter(text)
        text = rest if _fm is not None else text
        stripped = text.lstrip()
    if stripped.startswith("{"):
        obj = json.loads(text)
    else:
        obj = yaml.safe_load(text)
    if not isinstance(obj, dict):
        raise ValueError("weekly schema root must be an object")
    return _from_dict(obj)


def write_sidecars(
    weekly_md_path: Path,
    payload: WeeklyReviewPayload | None = None,
    **_: Any,
) -> None:
    """Delete leftover .json/.yaml so Chronicle cannot load a second copy of the week."""
    del payload
    for suffix in (".json", ".yaml"):
        extra = weekly_md_path.with_suffix(suffix)
        extra.unlink(missing_ok=True)


def load_sidecar(weekly_path: Path) -> dict[str, Any]:
    """Read week_status-stripped YAML from YYYY-Www.md so UI and recall share one file."""
    path = weekly_path if weekly_path.suffix == ".md" else weekly_path.with_suffix(".md")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    text = path.read_text(encoding="utf-8")
    fm, rest = _split_week_doc_frontmatter(text)
    body = rest if fm is not None else text
    obj = yaml.safe_load(body)
    if not isinstance(obj, dict):
        raise ValueError("weekly md body must be a YAML object")
    return obj


def _week_bounds(week_key: str) -> tuple[date, date]:
    year_s, _, week_s = (week_key or "").partition("-W")
    start = date.fromisocalendar(int(year_s), int(week_s), 1)
    return start, start + timedelta(days=6)


def _to_dict(payload: WeeklyReviewPayload, *, generated_at: str | None) -> dict[str, Any]:
    week_key = (payload.week_key or "").strip()
    start, end = _week_bounds(week_key) if week_key else (None, None)
    stamp = generated_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "cycle": "weekly",
        "week_key": week_key,
        "belongs_to": start.strftime("%Y-%m") if start else "",
        "range": (
            {"start": start.isoformat(), "end": end.isoformat()}
            if start and end
            else {"start": "", "end": ""}
        ),
        "generated_at": stamp,
        "generator": {
            "model": "",
            "authored": [
                key
                for key, present in (
                    ("cross-day-thread", bool(payload.cross_day_thread)),
                )
                if present
            ],
        },
        "entities": [_entity_dict(e) for e in payload.entities],
        JSON_CROSS: [_thread_dict(t) for t in payload.cross_day_thread],
        JSON_INTRA: [_intra_dict(row) for row in payload.intra_day_thread],
        JSON_SUMMARY: [_summary_dict(row) for row in payload.summary],
    }
    return {key: data[key] for key in _KEY_ORDER}


def _entity_dict(entity: WeeklyEntity) -> dict[str, Any]:
    return {
        "key": entity.key,
        "canonical": entity.canonical,
        "aliases": list(entity.aliases),
        "first_seen": entity.first_seen.isoformat() if entity.first_seen else None,
        "last_seen": entity.last_seen.isoformat() if entity.last_seen else None,
        "week_blocks": list(entity.week_blocks),
        "embedding": None,
    }


def _thread_dict(thread: SpanCandidate) -> dict[str, Any]:
    outcome = thread.outcome
    return {
        "id": thread.id,
        "label": thread.label,
        "start_date": thread.start_date.isoformat(),
        "end_date": thread.end_date.isoformat(),
        "entity_keys": list(thread.entity_keys),
        "steps": [_step_dict(step) for step in thread.steps],
        "outcome": dict(outcome) if outcome else None,
    }


def _step_dict(step: ThreadStep) -> dict[str, Any]:
    row: dict[str, Any] = {
        "seq": step.seq,
        "date": step.date.isoformat(),
        "event_id": step.event_id,
        "text": step.text,
    }
    if step.via is not None:
        row["via"] = step.via
    if step.to_seq is not None:
        row["to_seq"] = step.to_seq
    return row


def _intra_dict(row: IntraDayThread) -> dict[str, Any]:
    return {
        "date": row.date.isoformat(),
        "weekday": row.weekday,
        "source_field": row.source_field,
        "text": row.text,
        "empty": row.empty,
    }


def _summary_dict(row: WeeklySummaryItem) -> dict[str, Any]:
    """Dump Chronicle bullets last so YAML order matches generate (threads then summary)."""
    return {
        "text": row.text,
        "weekdays": list(row.weekdays),
    }


def _from_dict(obj: dict[str, Any]) -> WeeklyReviewPayload:
    legend_raw = obj.get("legend") or {}
    legend = {int(k): str(v) for k, v in legend_raw.items()}
    return WeeklyReviewPayload(
        days=(),
        legend=legend,
        week_key=str(obj.get("week_key") or ""),
        cross_day_thread=tuple(_thread_from(item) for item in obj.get(JSON_CROSS) or ()),
        intra_day_thread=tuple(_intra_from(item) for item in obj.get(JSON_INTRA) or ()),
        entities=tuple(_entity_from(item) for item in obj.get("entities") or ()),
        summary=tuple(
            _summary_from(item)
            for item in obj.get(JSON_SUMMARY) or ()
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ),
    )


def _summary_from(item: dict[str, Any]) -> WeeklySummaryItem:
    weekdays = tuple(
        str(name) for name in (item.get("weekdays") or ()) if str(name).strip()
    )
    return WeeklySummaryItem(text=str(item.get("text") or "").strip(), weekdays=weekdays)


def _parse_day(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _thread_from(item: dict[str, Any]) -> SpanCandidate:
    steps = tuple(_step_from(step) for step in item.get("steps") or ())
    outcome = item.get("outcome")
    return SpanCandidate(
        id=str(item.get("id") or ""),
        label=str(item.get("label") or ""),
        start_date=_parse_day(item.get("start_date")),
        end_date=_parse_day(item.get("end_date")),
        confidence=str(item.get("confidence") or "high"),
        steps=steps,
        outcome=dict(outcome) if isinstance(outcome, dict) else None,
        entity_keys=tuple(str(k) for k in item.get("entity_keys") or ()),
    )


def _step_from(item: dict[str, Any]) -> ThreadStep:
    via = item.get("via")
    to_seq = item.get("to_seq")
    cite_n = item.get("cite_n")
    return ThreadStep(
        seq=int(item.get("seq") or 1),
        date=_parse_day(item.get("date")),
        event_id=str(item.get("event_id") or ""),
        text=str(item.get("text") or ""),
        cite_n=int(cite_n) if cite_n is not None else None,
        via=str(via) if via else None,
        to_seq=int(to_seq) if to_seq is not None else None,
    )


def _intra_from(item: dict[str, Any]) -> IntraDayThread:
    day = _parse_day(item.get("date"))
    return IntraDayThread(
        date=day,
        weekday=str(item.get("weekday") or ""),
        source_field=str(item.get("source_field") or "day_wrapup"),
        text=str(item.get("text") or ""),
        empty=bool(item.get("empty")),
    )


def _entity_from(item: dict[str, Any]) -> WeeklyEntity:
    first = item.get("first_seen")
    last = item.get("last_seen")
    return WeeklyEntity(
        key=str(item.get("key") or ""),
        canonical=str(item.get("canonical") or ""),
        aliases=tuple(str(a) for a in item.get("aliases") or ()),
        first_seen=_parse_day(first) if first else None,
        last_seen=_parse_day(last) if last else None,
        week_blocks=tuple(str(b) for b in item.get("week_blocks") or ()),
        embedding=None,
    )
