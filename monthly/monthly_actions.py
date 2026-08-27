"""Public monthly ops for the weekly bridge and recall lookups."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_monthly = Path(__file__).resolve().parent
_mymemory = _monthly.parent
for path in (_monthly, _mymemory):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from monthly_notes import map_batch  # noqa: E402
from monthly_slice import (  # noqa: E402
    calendar_range,
    carry_card,
    mechanical_facts,
    pack_batches,
    previous_month_key,
    week_slices,
)
from monthly_state import month_file_path  # noqa: E402
from monthly_synth import synthesize_month  # noqa: E402
from monthly_writer import load_month, write_month, loads  # noqa: E402

_MONTH_KEY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def parse_month_key(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not _MONTH_KEY_RE.match(text):
        return None
    return text


def generate_month(
    month_key: str | None,
    *,
    reason: str = "bridge",
    call_oneshot=None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Run map-reduce for one calendar month and persist YYYY-MM.md."""
    del reason
    key = parse_month_key(month_key)
    if key is None:
        return {"outcome": "bad_month", "month": month_key or ""}
    slices = week_slices(key)
    batches = pack_batches(slices)
    notes = [
        map_batch(key, batch, call_oneshot=call_oneshot, force_refresh=force_refresh)
        for batch in batches
    ]
    facts = mechanical_facts(key)
    start, _end = calendar_range(key)
    carry = carry_card(previous_month_key(start))
    payload, usage = synthesize_month(
        key,
        notes,
        call_oneshot=call_oneshot,
        carry=carry,
        facts=facts,
    )
    path = write_month(payload)
    return {
        "outcome": "ok",
        "month": key,
        "path": str(path),
        "map_calls": sum(0 if n.get("cache_hit") else 1 for n in notes),
        "usage": usage,
        "payload": payload.to_dict(),
    }


def load_monthly_yaml(month_key: str | None = None) -> dict[str, Any]:
    """Return parsed YAML-as-JSON; missing file stays 404-equivalent."""
    key = parse_month_key(month_key)
    if key is None:
        return {"outcome": "bad_month", "month": month_key or ""}
    try:
        payload = load_month(key)
    except FileNotFoundError:
        return {"outcome": "missing", "month": key}
    return {"outcome": "ok", "month": key, "payload": payload.to_dict()}


def month_band(limit: int = 8, staging: Path | None = None) -> str:
    """Index last months as summary plus ISO range so Band D can pick a month window.

    Optional staging points at a sandbox root so tests do not read live HERMES_HOME.
    Prefetch passes limit=4; other callers keep the default eight.
    """
    folder = Path(staging) / "monthly" if staging is not None else month_file_path("x").parent
    if not folder.is_dir():
        return ""
    lines: list[str] = []
    for path in sorted(folder.glob("????-??.md"), reverse=True)[:limit]:
        try:
            payload = loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, FileNotFoundError):
            continue
        summary = (payload.summary or "").strip()
        if not summary:
            continue
        start = str(payload.range.start or "").strip()[:10]
        end = str(payload.range.end or "").strip()[:10]
        if start or end:
            lines.append(f"{payload.key} {start}..{end}: {summary}")
        else:
            lines.append(f"{payload.key}: {summary}")
    if not lines:
        return ""
    return "## Month summaries\n" + "\n".join(lines)


def lookup_by_id(mem_id: str) -> dict[str, Any] | None:
    """Find a stored decision, procedure, or progress item by id for expand_memory."""
    folder = month_file_path("x").parent
    if not folder.is_dir():
        return None
    needle = str(mem_id or "")
    for path in sorted(folder.glob("????-??.md")):
        try:
            payload = load_month(path.stem)
        except (OSError, ValueError, FileNotFoundError):
            continue
        for row in payload.key_decisions:
            if row.id == needle:
                return {"month": payload.key, "kind": "decision", "item": row}
        for row in payload.key_procedures:
            if row.id == needle:
                return {"month": payload.key, "kind": "procedure", "item": row}
        for row in payload.core_progress:
            if row.id == needle or needle in row.evidence:
                return {"month": payload.key, "kind": "progress", "item": row}
    return None


def lookup_by_entity(entity_key: str) -> list[dict[str, Any]]:
    """Return month rows for a canonical key so Channel 2 can hop across months."""
    folder = month_file_path("x").parent
    if not folder.is_dir():
        return []
    key = str(entity_key or "")
    hits: list[dict[str, Any]] = []
    for path in sorted(folder.glob("????-??.md")):
        try:
            payload = load_month(path.stem)
        except (OSError, ValueError, FileNotFoundError):
            continue
        for ent in payload.entities:
            if ent.key == key:
                hits.append({"month": payload.key, "entity": ent, "payload": payload})
                break
    return hits


def expand_from_block(mem_id: str) -> dict[str, Any]:
    """Depth-2 walk: daily id → core_progress → sibling week procedure ids."""
    hit = lookup_by_id(mem_id)
    if hit is None:
        folder = month_file_path("x").parent
        if folder.is_dir():
            for path in sorted(folder.glob("????-??.md")):
                try:
                    payload = load_month(path.stem)
                except (OSError, ValueError, FileNotFoundError):
                    continue
                for row in payload.core_progress:
                    if mem_id in row.evidence:
                        hit = {"month": payload.key, "kind": "progress", "item": row, "payload": payload}
                        break
                if hit:
                    break
    if hit is None:
        return {"ok": False, "error": "not_found"}
    item = hit["item"]
    sibling_ids: list[str] = []
    if getattr(item, "evidence", None):
        sibling_ids.extend(list(item.evidence))
    return {"ok": True, "progress": hit, "sibling_ids": sibling_ids}
