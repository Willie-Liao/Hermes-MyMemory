"""Public monthly ops for the weekly bridge and recall lookups."""

from __future__ import annotations

import re
import sys
from datetime import date
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
_GUIDANCE_GATE = 0.30
_BAND_DECISIONS = 4
_BAND_PROCEDURES = 3
_BAND_SUMMARY = 8
_HELP = (
    "/monthly update [YYYY-MM]  refresh that month (default: current)\n"
    "/monthly show [YYYY-MM]    print story bullets and D/P guidance"
)


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
    slices = week_slices(key, types=frozenset({"decision", "procedure"}))
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


def _month_folder(staging: Path | None) -> Path:
    if staging is not None:
        return Path(staging) / "monthly"
    return month_file_path("x").parent


def _iter_payloads(staging: Path | None = None):
    folder = _month_folder(staging)
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("????-??.md"), reverse=True):
        try:
            yield loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, FileNotFoundError):
            continue


def _is_active_decision(row, superseded: set[str]) -> bool:
    if row.id in superseded:
        return False
    valid = str(row.valid_to or "").strip().casefold()
    return valid in {"", "open"}


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9]+", (text or "").casefold()) if tok}


def _lexical_sim(query: str, candidate: str) -> float:
    qt, ct = _tokens(query), _tokens(candidate)
    if not qt or not ct:
        return 0.0
    return len(qt & ct) / len(qt | ct)


def _guidance_sim(query: str, candidate: str) -> float:
    """Lexical overlap first; GTE cosine only if lexical misses the 0.30 gate."""
    lexical = _lexical_sim(query, candidate)
    if lexical >= _GUIDANCE_GATE:
        return lexical
    try:
        from recall.embed import _cosine, _encode_texts

        vecs = _encode_texts([query, candidate])
        if vecs and len(vecs) == 2:
            return float(_cosine(vecs[0], vecs[1]))
    except Exception:
        pass
    return lexical


def rank_monthly_guidance(
    query: str,
    *,
    staging: Path | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Rank active monthly D/P for a task-shaped query so prefetch does not scan daily events first.

    Strength may reorder inside the admitted set; it cannot rescue a row below the 0.30 gate.
    """
    q = str(query or "").strip()
    if not q:
        return []
    admitted: list[dict[str, Any]] = []
    for payload in _iter_payloads(staging):
        superseded = {sid for row in payload.key_decisions for sid in row.supersedes}
        for row in payload.key_decisions:
            if not _is_active_decision(row, superseded):
                continue
            sim = _guidance_sim(q, row.lookup_text())
            if sim < _GUIDANCE_GATE:
                continue
            rank = 0.8 * sim + 0.2 * (float(row.strength) / 10.0)
            admitted.append(
                {
                    "kind": "decision",
                    "month": payload.key,
                    "sim": sim,
                    "rank": rank,
                    "row": row,
                }
            )
        for row in payload.key_procedures:
            sim = _guidance_sim(q, row.lookup_text())
            if sim < _GUIDANCE_GATE:
                continue
            rank = 0.8 * sim + 0.2 * (float(row.strength) / 10.0)
            admitted.append(
                {
                    "kind": "procedure",
                    "month": payload.key,
                    "sim": sim,
                    "rank": rank,
                    "row": row,
                }
            )
    prefs = [h for h in admitted if h["kind"] == "decision"]
    procs = [h for h in admitted if h["kind"] == "procedure"]
    prefs.sort(key=lambda h: (-h["rank"], h["row"].id))
    procs.sort(key=lambda h: (-h["rank"], h["row"].id))
    ordered = prefs + procs
    return ordered[: max(1, int(limit))]


def format_guidance_hits(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    lines = ["## Memory / recall  channel=monthly_guidance"]
    for hit in hits:
        row = hit["row"]
        if hit["kind"] == "decision":
            extra = f" except {row.exceptions}" if row.exceptions else ""
            lines.append(
                f"- preference {row.id}  context={row.context or '-'}  "
                f"{row.text}{extra}  strength={row.strength:.2f}"
            )
        else:
            obs = "; ".join(row.obstacles) if row.obstacles else ""
            lines.append(
                f"- procedure {row.id}  trigger={row.trigger or '-'}  "
                f"obstacles={obs or '-'}  solution={row.solution}  strength={row.strength:.2f}"
            )
    return "\n".join(lines)


def _paint_payload(payload) -> list[str]:
    start = str(payload.range.start or "").strip()[:10]
    end = str(payload.range.end or "").strip()[:10]
    lines = [f"### {payload.key}  {start}..{end}".rstrip()]
    for row in payload.summary[:_BAND_SUMMARY]:
        weeks = ", ".join(row.weeks) if row.weeks else ""
        suffix = f" ({weeks})" if weeks else ""
        lines.append(f"- {row.text}{suffix}")
    superseded = {sid for row in payload.key_decisions for sid in row.supersedes}
    decisions = [row for row in payload.key_decisions if _is_active_decision(row, superseded)]
    decisions.sort(key=lambda r: (-float(r.strength), r.id))
    for row in decisions[:_BAND_DECISIONS]:
        extra = f" except {row.exceptions}" if row.exceptions else ""
        lines.append(f"- preference {row.id}  {row.context or '-'} — {row.text}{extra}")
    procs = sorted(payload.key_procedures, key=lambda r: (-float(r.strength), r.id))
    for row in procs[:_BAND_PROCEDURES]:
        obs = "; ".join(row.obstacles) if row.obstacles else "-"
        lines.append(f"- procedure {row.id}  {row.trigger or '-'} — {obs} — {row.solution}")
    return lines


def month_band(limit: int = 8, staging: Path | None = None) -> str:
    """Paint month stories like weekly Band C, then bounded active D/P for first-turn guidance."""
    lines: list[str] = []
    for payload in list(_iter_payloads(staging))[:limit]:
        painted = _paint_payload(payload)
        if len(painted) == 1 and not payload.summary and not payload.key_decisions and not payload.key_procedures:
            continue
        lines.extend(painted)
    if not lines:
        return ""
    return "## Month summaries\n" + "\n".join(lines)


def handle_monthly(raw_args: str) -> str:
    """Slash/CLI dispatcher for update/show only — monthly has no UI/close lifecycle."""
    tokens = str(raw_args or "").strip().split()
    if not tokens or tokens[0] in {"help", "-h", "--help"}:
        return _HELP
    cmd = tokens[0]
    rest = tokens[1] if len(tokens) > 1 else ""
    if cmd == "update":
        key = parse_month_key(rest) if rest else date.today().strftime("%Y-%m")
        if rest and key is None:
            return f"bad month {rest!r}; expected YYYY-MM"
        result = generate_month(key, reason="slash", force_refresh=True)
        if result.get("outcome") == "bad_month":
            return f"bad month {rest!r}; expected YYYY-MM"
        return f"updated {result.get('month')} ({result.get('outcome')})"
    if cmd == "show":
        key = parse_month_key(rest) if rest else date.today().strftime("%Y-%m")
        if rest and key is None:
            return f"bad month {rest!r}; expected YYYY-MM"
        try:
            payload = load_month(key)
        except FileNotFoundError:
            return f"missing {key}"
        return "## Month summaries\n" + "\n".join(_paint_payload(payload))
    return _HELP


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
