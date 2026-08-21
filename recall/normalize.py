"""Canonical entity keys so alias spellings cannot fragment the join index.

Without this, Memory Digest / MemoryDigest / memory-digest become five Band B
rows and Channel 2 cannot walk the 14-day concept.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .ids import BlockRecord, iso_week, load_blocks, staging_root


def entity_key(surface: str) -> str:
    """casefold + strip non-alphanumeric; CJK stays because str.isalnum keeps it."""
    return "".join(ch for ch in (surface or "").casefold() if ch.isalnum())


def build_entity_index(
    staging: Path | None = None,
    blocks: Iterable[BlockRecord] | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge every surface into one node per key so synonym hops cannot burn depth-2."""
    root = staging_root(staging)
    records = list(blocks) if blocks is not None else load_blocks(root)
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    def _touch(surface: str, rec: BlockRecord, *, as_member: bool) -> None:
        key = entity_key(surface) or (entity_key(rec.block_id) if as_member else "")
        if not key:
            return
        if key not in groups:
            groups[key] = {
                "canonical": surface or key,
                "aliases": [],
                "mem_ids": [],
                "days": [],
                "weeks": [],
                "first_seen": rec.day,
                "last_seen": rec.day,
            }
            order.append(key)
        node = groups[key]
        if surface and surface not in node["aliases"] and surface != node["canonical"]:
            node["aliases"].append(surface)
        if as_member and rec.block_id and rec.block_id not in node["mem_ids"]:
            node["mem_ids"].append(rec.block_id)
        if rec.day:
            if rec.day not in node["days"]:
                node["days"].append(rec.day)
            if not node["first_seen"] or rec.day < node["first_seen"]:
                node["first_seen"] = rec.day
            if not node["last_seen"] or rec.day > node["last_seen"]:
                node["last_seen"] = rec.day
            week = iso_week(rec.day)
            if week and week not in node["weeks"]:
                node["weeks"].append(week)

    for rec in records:
        if str(rec.parsed.get("status") or "").strip() == "rejected":
            continue
        if rec.entity:
            _touch(rec.entity, rec, as_member=True)
        else:
            _touch(rec.block_id, rec, as_member=True)
        for extra in rec.involves:
            _touch(extra, rec, as_member=True)
    for node in groups.values():
        node["days"].sort()
        node["weeks"].sort()
        node["aliases"].sort()
    return {k: groups[k] for k in order}


def write_entity_index(staging: Path | None = None) -> Path:
    """Persist the join index next to dailies so Channel 2 is a file read."""
    root = staging_root(staging)
    index = build_entity_index(root)
    path = root / "entity_index.json"
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_entity_index(staging: Path | None = None) -> dict[str, dict[str, Any]]:
    """Read the on-disk index, rebuilding if a digest has not written it yet."""
    root = staging_root(staging)
    path = root / "entity_index.json"
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    return build_entity_index(root)


def lookup_key(query: str, index: dict[str, dict[str, Any]] | None = None) -> str | None:
    """Map a sentence onto the longest indexed key so Channel 2 is not exact-match-only.

    Without substring match, 'what did we do about memory digest?' never joins
    `memorydigest` and the ladder falls through to an empty FTS AND query.
    """
    key = entity_key(query)
    if not key:
        return None
    idx = index if index is not None else load_entity_index()
    if key in idx:
        return key
    folded = query.strip()
    for k, node in idx.items():
        if folded == str(node.get("canonical") or ""):
            return k
        aliases = node.get("aliases") or []
        if folded in aliases:
            return k
    best = ""
    for k in idx:
        if not k or k not in key:
            continue
        ascii_only = all(ord(ch) < 128 for ch in k)
        if ascii_only and len(k) < 4:
            continue
        if not ascii_only and len(k) < 2:
            continue
        if len(k) > len(best):
            best = k
    return best or None


def multi_day_keys(index: dict[str, dict[str, Any]]) -> list[str]:
    """Keys whose members span more than one civil day — the cross-window join set."""
    return [k for k, node in index.items() if len(node.get("days") or []) > 1]
