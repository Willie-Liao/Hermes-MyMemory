"""Worker 1 distill YAML validator for ``## Distill`` frontmatter blocks.

After forced Worker 1 tools, field shape / participants / body↔related cite
multisets are owned by tool schema + code render + ``normalize_event_citations``.
This module keeps structural/graph checks only.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

_ALLOWED_TYPES = frozenset({"event", "procedure"})
_DISTILL_HEADER_RE = re.compile(r"^##\s+Distill\s*$", re.IGNORECASE | re.MULTILINE)
_LEVEL_TWO_HEADER_RE = re.compile(r"^##(?!#)\s+", re.MULTILINE)
_RELATED_PREFIX_RE = re.compile(
    r"^\[(\d+)\]\s+(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)\s*$",
    re.IGNORECASE,
)
_MEM_ID_RE = re.compile(
    r"^(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)$",
    re.IGNORECASE,
)


def _distill_region(md_text: str) -> str:
    text = md_text or ""
    match = _DISTILL_HEADER_RE.search(text)
    if not match:
        return ""
    start = match.end()
    next_h = _LEVEL_TWO_HEADER_RE.search(text, start)
    end = next_h.start() if next_h else len(text)
    return text[start:end].strip()


def _frontmatter_blocks(content: str) -> list[tuple[int, dict[str, Any], str]]:
    """Parse ``---`` YAML fences into (line_no, frontmatter_dict, body)."""
    lines = content.splitlines()
    blocks: list[tuple[int, dict[str, Any], str]] = []
    idx = 0
    while idx < len(lines):
        if lines[idx].strip() != "---":
            idx += 1
            continue
        start_line = idx + 1
        idx += 1
        frontmatter_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            frontmatter_lines.append(lines[idx])
            idx += 1
        if idx >= len(lines):
            raw = "\n".join(frontmatter_lines)
            parsed = _safe_yaml(raw)
            blocks.append((start_line, parsed, ""))
            break
        idx += 1
        body_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            body_lines.append(lines[idx])
            idx += 1
        raw = "\n".join(frontmatter_lines)
        parsed = _safe_yaml(raw)
        blocks.append((start_line, parsed, "\n".join(body_lines).strip()))
    return blocks


def _safe_yaml(raw: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError:
        return {"__yaml_error__": True}
    return data if isinstance(data, dict) else {"__yaml_error__": True}


def _related_entries(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    return [s] if s else []


def _parse_related_cite(entry: str) -> tuple[int | None, str | None]:
    m = _RELATED_PREFIX_RE.match(entry)
    if m:
        return int(m.group(1)), m.group(2)
    if _MEM_ID_RE.match(entry):
        return None, entry
    return None, None


def validate_weekly_distill(md_text: str) -> list[str]:
    """Validate Worker 1 Distill section. Empty list means OK.

    Checks: Distill present, parseable blocks, allowed types, sources/related
    present, event ``related`` mem-ids, non-event → week event id, and
    contiguous ``[N]`` when prefixed cites are present. Does **not** re-check
    tool-owned fields (entity/predicate/participants/status) or body↔related
    cite multisets (``normalize_event_citations`` owns those).
    """
    errors: list[str] = []
    region = _distill_region(md_text)
    if not region:
        return ["missing ## Distill section"]

    blocks = _frontmatter_blocks(region)
    if not blocks:
        return ["## Distill has no YAML frontmatter blocks"]

    event_ids: set[str] = set()
    cite_pairs: list[tuple[int, str]] = []

    for line_no, fm, _body in blocks:
        if fm.get("__yaml_error__"):
            errors.append(f"line {line_no}: invalid YAML frontmatter")
            continue

        block_id = str(fm.get("id") or "").strip()
        block_type = str(fm.get("type") or "").strip().casefold()
        if not block_id:
            errors.append(f"line {line_no}: missing id")
        if not block_type:
            errors.append(f"line {line_no}: missing type")
        elif block_type not in _ALLOWED_TYPES:
            errors.append(
                f"line {line_no}: type {block_type!r} not in "
                f"{sorted(_ALLOWED_TYPES)}"
            )

        sources = fm.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"line {line_no}: sources must be a non-empty list")

        related = _related_entries(fm.get("related"))
        if not related:
            errors.append(f"line {line_no}: related is required")

        if block_type == "event" and block_id:
            event_ids.add(block_id)

        if block_type == "event":
            for entry in related:
                n, mem = _parse_related_cite(entry)
                if mem is None:
                    errors.append(
                        f"line {line_no}: related entry must be "
                        f"'[N] mem-…' or mem-…, got {entry!r}"
                    )
                    continue
                if n is not None:
                    cite_pairs.append((n, mem))

    # Non-events must related to an event id present in this Distill section
    for line_no, fm, _body in blocks:
        if fm.get("__yaml_error__"):
            continue
        block_type = str(fm.get("type") or "").strip().casefold()
        if block_type not in ("hypothesis", "procedure", "conflict"):
            continue
        related = _related_entries(fm.get("related"))
        if not related:
            continue  # missing related already reported
        has_event_ref = any(str(entry).strip() in event_ids for entry in related)
        if not has_event_ref:
            errors.append(
                f"line {line_no}: {block_type} related must include a week event id"
            )

    if cite_pairs:
        numbers = sorted(n for n, _ in cite_pairs)
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            errors.append(
                f"citation numbers not contiguous [1]…[N]: got {numbers}"
            )
        by_n: dict[int, str] = {}
        for n, mem in cite_pairs:
            if n in by_n and by_n[n] != mem:
                errors.append(
                    f"citation [{n}] maps to both {by_n[n]!r} and {mem!r}"
                )
            by_n[n] = mem

    return errors
