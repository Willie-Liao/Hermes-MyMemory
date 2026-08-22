"""Continuous week-global citation numbering for Worker 1 distill events.

Non-LLM helper: ``related`` list (file order) is SoT for ``mem-…`` ids; bodies
are rewritten to contiguous ``[1]…[N]`` across all events. Non-event blocks pass
through unchanged.

Typed (conflict / hypothesis / span) citations are assigned separately by
``weekly_event_schema.assign_typed_citations`` and must never renumber the
event legend produced here.
"""

from __future__ import annotations

import copy
import re
from typing import Any

_CITE_RE = re.compile(r"\[(\d+)\]")
_RELATED_PREFIX_RE = re.compile(
    r"^\[(\d+)\]\s+(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)\s*$",
    re.IGNORECASE,
)
_MEM_ID_RE = re.compile(
    r"^(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)$",
    re.IGNORECASE,
)


def extract_cite_numbers(text: str) -> list[int]:
    """Return citation numbers in order of appearance in ``text``."""
    return [int(m.group(1)) for m in _CITE_RE.finditer(text or "")]


def next_cite_after_legend(legend: dict[int, str]) -> int:
    """First free citation number after the event legend (typed cites start here)."""
    return max(legend.keys(), default=0) + 1


def _mem_id_from_related_entry(entry: Any) -> str | None:
    raw = str(entry or "").strip()
    if not raw:
        return None
    prefixed = _RELATED_PREFIX_RE.match(raw)
    if prefixed:
        return prefixed.group(2)
    if _MEM_ID_RE.match(raw):
        return raw
    # Allow bare mem-… with trailing junk stripped loosely
    mem = re.search(
        r"(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)",
        raw,
        re.IGNORECASE,
    )
    return mem.group(1) if mem else None


def _rewrite_body_cites(body: str, numbers: list[int]) -> str:
    """Replace existing ``[n]`` markers in order, then pad if related has more."""
    text = body or ""
    idx = 0

    def _sub(_match: re.Match[str]) -> str:
        nonlocal idx
        if idx >= len(numbers):
            return ""
        replacement = f"[{numbers[idx]}]"
        idx += 1
        return replacement

    rewritten = _CITE_RE.sub(_sub, text)
    # Drop leftover empty double spaces from truncated markers
    rewritten = re.sub(r" +", " ", rewritten).rstrip()

    if idx < len(numbers):
        extras = " ".join(f"[{n}]" for n in numbers[idx:])
        if rewritten and not rewritten.endswith((".", "!", "?", "\n")):
            rewritten = f"{rewritten} {extras}"
        elif rewritten:
            rewritten = f"{rewritten} {extras}"
        else:
            rewritten = extras
    return rewritten


def normalize_event_citations(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Normalize event ``related`` + body cites to one contiguous ``[1]…[N]`` map.

    Returns updated blocks (shallow-copied) and legend ``{N: mem-id}``.
    """
    out: list[dict[str, Any]] = []
    legend: dict[int, str] = {}
    next_n = 1

    for block in blocks:
        item = copy.deepcopy(block)
        fm = item.get("frontmatter")
        if not isinstance(fm, dict):
            fm = {}
            item["frontmatter"] = fm
        if str(fm.get("type") or "").strip().casefold() != "event":
            out.append(item)
            continue

        related_raw = fm.get("related") or []
        if not isinstance(related_raw, list):
            related_raw = [related_raw]

        mem_ids: list[str] = []
        for entry in related_raw:
            mid = _mem_id_from_related_entry(entry)
            if mid:
                mem_ids.append(mid)

        event_numbers: list[int] = []
        related_out: list[str] = []
        for mid in mem_ids:
            n = next_n
            next_n += 1
            event_numbers.append(n)
            legend[n] = mid
            related_out.append(f"[{n}] {mid}")

        fm["related"] = related_out
        item["body"] = _rewrite_body_cites(str(item.get("body") or ""), event_numbers)
        out.append(item)

    return out, legend
