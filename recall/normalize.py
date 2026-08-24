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
    """Merge bilingual aliases onto one English key so a Chinese query cannot mint a second Band B row.

    Ambiguous alias claims stay unmerged so two English entities that share a surface do not silently fuse.
    """
    root = staging_root(staging)
    records = list(blocks) if blocks is not None else load_blocks(root)
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    claims: dict[str, set[str]] = defaultdict(set)
    english_canonical: dict[str, str] = {}
    live: list[BlockRecord] = []
    for rec in records:
        if str(rec.parsed.get("status") or "").strip() == "rejected":
            continue
        live.append(rec)
        ek = entity_key(rec.entity)
        if ek:
            english_canonical.setdefault(ek, rec.entity)
        raw_aliases = rec.parsed.get("entity_aliases")
        if not ek or not isinstance(raw_aliases, list):
            continue
        for alias in raw_aliases:
            ak = entity_key(str(alias or "").strip())
            if ak and ak != ek:
                claims[ak].add(ek)
    unique_redirect = {
        ak: next(iter(eks)) for ak, eks in claims.items() if len(eks) == 1
    }

    def _touch(surface: str, rec: BlockRecord, *, as_member: bool) -> None:
        key = entity_key(surface) or (entity_key(rec.block_id) if as_member else "")
        if key in unique_redirect:
            key = unique_redirect[key]
        if not key:
            return
        if key not in groups:
            groups[key] = {
                "canonical": english_canonical.get(key) or surface or key,
                "aliases": [],
                "mem_ids": [],
                "days": [],
                "weeks": [],
                "first_seen": rec.day,
                "last_seen": rec.day,
            }
            order.append(key)
        node = groups[key]
        preferred = english_canonical.get(key)
        if preferred and node["canonical"] != preferred:
            old = node["canonical"]
            node["canonical"] = preferred
            if old and old not in node["aliases"] and old != preferred:
                node["aliases"].append(old)
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

    for rec in live:
        if rec.entity:
            _touch(rec.entity, rec, as_member=True)
        else:
            _touch(rec.block_id, rec, as_member=True)
        for extra in rec.involves:
            _touch(extra, rec, as_member=True)
        raw_aliases = rec.parsed.get("entity_aliases")
        if not isinstance(raw_aliases, list):
            continue
        ek = unique_redirect.get(entity_key(rec.entity), entity_key(rec.entity))
        for alias in raw_aliases:
            text = str(alias or "").strip()
            if not text:
                continue
            ak = entity_key(text)
            if ak in unique_redirect:
                _touch(text, rec, as_member=True)
            elif ek and ek in groups:
                node = groups[ek]
                if text not in node["aliases"] and text != node["canonical"]:
                    node["aliases"].append(text)
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
    """Resolve English and original-language queries onto one English key when the match is unique.

    Ambiguous alias hits return None so Channel 2 cannot fuse two entities; the ladder falls through.
    """
    q = entity_key(query)
    if not q:
        return None
    idx = index if index is not None else load_entity_index()
    if q in idx:
        return q
    hits: dict[str, int] = {}
    for k, node in idx.items():
        surfaces = [k, entity_key(str(node.get("canonical") or ""))]
        for alias in node.get("aliases") or []:
            surfaces.append(entity_key(str(alias)))
        for surface in dict.fromkeys(s for s in surfaces if s):
            if surface != q and surface not in q:
                continue
            if surface != q:
                ascii_only = all(ord(ch) < 128 for ch in surface)
                if ascii_only and len(surface) < 4:
                    continue
                if not ascii_only and len(surface) < 2:
                    continue
            if len(surface) > hits.get(k, 0):
                hits[k] = len(surface)
    if not hits:
        return None
    best = max(hits.values())
    winners = [k for k, length in hits.items() if length == best]
    if len(winners) != 1:
        return None
    return winners[0]


def multi_day_keys(index: dict[str, dict[str, Any]]) -> list[str]:
    """Keys whose members span more than one civil day — the cross-window join set."""
    return [k for k, node in index.items() if len(node.get("days") or []) > 1]
