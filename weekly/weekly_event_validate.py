"""Post-event-worker validator: mem-id agreement with daily F/P/D.

After the single ``worker1_event`` LLM groups events by date with mem ids,
this module resolves each cited ``mem-…`` id in daily staging and checks that
the weekly event text is supported by the cited fact / procedure / decision
blocks (no invented ids; type must be in {fact, procedure, decision}).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Sequence

try:
    from .weekly_distill_validate import _frontmatter_blocks
except ImportError:  # pragma: no cover
    from weekly_distill_validate import _frontmatter_blocks  # type: ignore[no-redef]

_CLAIM_KINDS = frozenset({"fact", "procedure", "decision"})
_MEM_ID_RE = re.compile(
    r"(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)",
    re.IGNORECASE,
)
_RELATED_CITE_RE = re.compile(
    r"^\[(\d+)\]\s+(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)\s*$",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)


def _norm_tokens(text: str) -> set[str]:
    return {t.casefold() for t in _TOKEN_RE.findall(text or "")}


def _mem_ids_from_event_block(block: dict[str, Any]) -> list[str]:
    fm = block.get("frontmatter") or {}
    ids: list[str] = []
    related = fm.get("related") or []
    if not isinstance(related, list):
        related = [related]
    for entry in related:
        raw = str(entry).strip()
        match = _RELATED_CITE_RE.match(raw)
        if match:
            ids.append(match.group(2))
            continue
        mem = _MEM_ID_RE.search(raw)
        if mem:
            ids.append(mem.group(1))
    body = str(block.get("body") or "")
    for mem in _MEM_ID_RE.findall(body):
        if mem not in ids:
            ids.append(mem)
    return ids


def index_daily_claim_blocks(
    files: Sequence[Path],
) -> dict[str, dict[str, Any]]:
    """Map mem-id → {type, body, path, id} for claim kinds in daily files."""
    out: dict[str, dict[str, Any]] = {}
    for path in files:
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
            block_id = str(fm.get("id") or "").strip()
            if not block_id:
                continue
            out[block_id] = {
                "id": block_id,
                "type": kind,
                "body": (body or "").strip(),
                "path": str(path),
            }
    return out


def claims_support_event(event_body: str, claim_bodies: Sequence[str]) -> bool:
    """True if event tokens substantially overlap union of cited claim bodies.

    Empty event body with cites is treated as weak-ok (structure-only event).
    """
    event_tokens = _norm_tokens(event_body)
    if not event_tokens:
        return True
    claim_tokens: set[str] = set()
    for body in claim_bodies:
        claim_tokens |= _norm_tokens(body)
    if not claim_tokens:
        return False
    overlap = event_tokens & claim_tokens
    # At least 2 shared content tokens, or ≥30% of event tokens.
    if len(overlap) >= 2:
        return True
    return len(overlap) / max(len(event_tokens), 1) >= 0.3


def validate_event_blocks_against_dailies(
    event_blocks: Sequence[dict[str, Any]],
    daily_files: Sequence[Path],
) -> list[str]:
    """Return validator errors (empty list = pass)."""
    errors: list[str] = []
    claims = index_daily_claim_blocks(daily_files)
    for block in event_blocks:
        fm = block.get("frontmatter") or {}
        if str(fm.get("type") or "").strip().casefold() != "event":
            continue
        evt_id = str(fm.get("id") or "").strip() or "(missing-id)"
        mem_ids = _mem_ids_from_event_block(block)
        if not mem_ids:
            errors.append(f"event {evt_id}: missing mem id reference in related/body")
            continue
        claim_bodies: list[str] = []
        for mem_id in mem_ids:
            hit = claims.get(mem_id)
            if hit is None:
                # Also try case-insensitive
                hit = next(
                    (v for k, v in claims.items() if k.casefold() == mem_id.casefold()),
                    None,
                )
            if hit is None:
                errors.append(
                    f"event {evt_id}: mem id {mem_id!r} not found in daily "
                    "fact/procedure/decision blocks"
                )
                continue
            if hit["type"] not in _CLAIM_KINDS:
                errors.append(
                    f"event {evt_id}: mem id {mem_id!r} has type {hit['type']!r}, "
                    "expected fact|procedure|decision"
                )
                continue
            claim_bodies.append(str(hit["body"]))
        if claim_bodies and not claims_support_event(
            str(block.get("body") or ""), claim_bodies
        ):
            errors.append(
                f"event {evt_id}: weekly event text does not agree with cited "
                "fact/procedure/decision bodies"
            )
    return errors


__all__ = [
    "claims_support_event",
    "index_daily_claim_blocks",
    "validate_event_blocks_against_dailies",
]
