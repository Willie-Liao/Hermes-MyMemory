"""Resolve every daily and weekly id schema so Channel 1 cannot silently drop pre-August cards.

A resolver that only handles mem-YYYY-MM-DD-type-HEX12 misses 42% of the corpus
and the two 08-17 ids that live in 2026-08-16.md.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import yaml

_ID_A = re.compile(
    r"^mem-(\d{4}-\d{2}-\d{2})-([a-z][a-z0-9_]*)-([0-9A-Fa-f]{12})$"
)
_ID_B = re.compile(r"^mem-(\d{4}-\d{2}-\d{2})-([A-Za-z0-9][A-Za-z0-9\-]*)$")
_ID_C = re.compile(r"^mem-(\d{8})-(\d{4})-(.+)$")
_ID_D = re.compile(r"^mem-(\d{8})-(.+)$")
_WEEK_EVT = re.compile(r"^w-evt-(\d{4}-\d{2}-\d{2})-(\d+)$")
_WEEK_SLUG = re.compile(r"^w(\d{1,2})-e(\d+)-(.+)$", re.I)
_FRONTMATTER_RE = re.compile(r"(?ms)^---\n(.*?)\n---\n(.*?)(?=^---|\Z)")


@dataclass
class BlockRecord:
    """One daily YAML card plus the file that actually contains it (not the id date hint)."""

    block_id: str
    path: Path
    parsed: dict[str, Any]
    body: str
    day: str
    item_type: str
    entity: str
    related: list[str] = field(default_factory=list)
    involves: list[str] = field(default_factory=list)


def staging_root(explicit: Path | str | None = None) -> Path:
    """Hermes staging root: explicit, HERMES_HOME, else this pack's hermes-home."""
    if explicit is not None:
        return Path(explicit)
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        return Path(env) / "memories" / "staging"
    return Path(__file__).resolve().parents[3] / "memories" / "staging"


def hermes_home(explicit: Path | str | None = None) -> Path:
    """Plugin pack home so L1 can open state.db without a writable migrate."""
    if explicit is not None:
        return Path(explicit)
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3]


def classify_daily_id(mem_id: str) -> str | None:
    """Return A/B/C/D so tests can lock the four schemas the corpus actually uses."""
    text = str(mem_id or "").strip()
    if _ID_A.match(text):
        return "A"
    if _ID_B.match(text):
        return "B"
    if _ID_C.match(text):
        return "C"
    if _ID_D.match(text):
        return "D"
    return None


def classify_weekly_id(mem_id: str) -> str | None:
    """Accept both w-evt- date ids and the older w25-e1- slug form."""
    text = str(mem_id or "").strip()
    if _WEEK_EVT.match(text):
        return "w-evt"
    if _WEEK_SLUG.match(text):
        return "w-slug"
    return None


def date_hint(mem_id: str) -> date | None:
    """Date encoded in the id — a hint, not a guarantee of the containing file."""
    text = str(mem_id or "").strip()
    m = _ID_A.match(text) or _ID_B.match(text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    m = _WEEK_EVT.match(text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    m = _ID_C.match(text) or _ID_D.match(text)
    if m:
        raw = m.group(1)
        try:
            return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            return None
    return None


def iso_week(day: str | date | None) -> str:
    """ISO week label for Band C and entity_index weeks[]."""
    if day is None or day == "":
        return ""
    if isinstance(day, str):
        try:
            d = date.fromisoformat(day[:10])
        except ValueError:
            return ""
    else:
        d = day
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _parse_related(parsed: dict[str, Any]) -> list[str]:
    raw = parsed.get("related")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _parse_involves(parsed: dict[str, Any]) -> list[str]:
    """Involves surfaces join the entity index; participants stay off Band B."""
    raw = parsed.get("involves")
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                tag = str(item.get("entity") or "").strip()
                if tag:
                    names.append(tag)
    elif isinstance(raw, str) and raw.strip():
        names.append(raw.strip())
    return names


def iter_frontmatter_blocks(text: str) -> Iterable[tuple[dict[str, Any], str]]:
    """Yield YAML fence pairs so inline and block-list related: both survive."""
    for match in _FRONTMATTER_RE.finditer(text or ""):
        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        yield parsed, match.group(2).strip()


def load_blocks(staging: Path | None = None) -> list[BlockRecord]:
    """Scan daily/*.md into records keyed by the file that actually holds the id."""
    root = staging_root(staging)
    daily = root / "daily"
    out: list[BlockRecord] = []
    if not daily.is_dir():
        return out
    for path in sorted(daily.glob("*.md")):
        try:
            file_day = date.fromisoformat(path.stem).isoformat()
        except ValueError:
            file_day = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for parsed, body in iter_frontmatter_blocks(text):
            block_id = str(parsed.get("id") or "").strip()
            if not block_id:
                continue
            day = file_day
            out.append(
                BlockRecord(
                    block_id=block_id,
                    path=path,
                    parsed=parsed,
                    body=body,
                    day=day[:10] if day else file_day,
                    item_type=str(parsed.get("type") or "").strip(),
                    entity=str(parsed.get("entity") or "").strip(),
                    related=_parse_related(parsed),
                    involves=_parse_involves(parsed),
                )
            )
    return out


class BlockIndex:
    """In-memory id → record map so Channel 1 and the edge builder share one scan."""

    def __init__(self, staging: Path | None = None) -> None:
        self.root = staging_root(staging)
        self.records = load_blocks(self.root)
        self.by_id: dict[str, BlockRecord] = {r.block_id: r for r in self.records}
        self.by_file: dict[str, list[BlockRecord]] = {}
        for rec in self.records:
            self.by_file.setdefault(rec.path.name, []).append(rec)

    def get(self, mem_id: str) -> BlockRecord | None:
        return self.by_id.get(str(mem_id or "").strip())


def _ids_in_file(path: Path) -> dict[str, tuple[dict[str, Any], str]]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, tuple[dict[str, Any], str]] = {}
    for parsed, body in iter_frontmatter_blocks(text):
        block_id = str(parsed.get("id") or "").strip()
        if block_id:
            found[block_id] = (parsed, body)
    return found


def _record_from_file(
    path: Path, mem_id: str, parsed: dict[str, Any], body: str
) -> BlockRecord:
    try:
        file_day = date.fromisoformat(path.stem).isoformat()
    except ValueError:
        file_day = path.stem
    day = file_day
    return BlockRecord(
        block_id=mem_id,
        path=path,
        parsed=parsed,
        body=body,
        day=day[:10] if day else file_day,
        item_type=str(parsed.get("type") or "").strip(),
        entity=str(parsed.get("entity") or "").strip(),
        related=_parse_related(parsed),
        involves=_parse_involves(parsed),
    )


def resolve_id(
    mem_id: str,
    staging: Path | None = None,
    index: BlockIndex | None = None,
    entity_index: dict[str, Any] | None = None,
) -> BlockRecord | None:
    """Predicted file, then adjacent civil day, then entity_index, then full scan."""
    text = str(mem_id or "").strip()
    if not text:
        return None
    if index is not None:
        hit = index.get(text)
        if hit:
            return hit
    root = staging_root(staging)
    daily = root / "daily"
    hint = date_hint(text)
    candidates: list[Path] = []
    if hint:
        candidates.append(daily / f"{hint.isoformat()}.md")
        candidates.append(daily / f"{(hint - timedelta(days=1)).isoformat()}.md")
        candidates.append(daily / f"{(hint + timedelta(days=1)).isoformat()}.md")
    for path in candidates:
        found = _ids_in_file(path)
        if text in found:
            parsed, body = found[text]
            return _record_from_file(path, text, parsed, body)
    if entity_index:
        for node in entity_index.values():
            if not isinstance(node, dict):
                continue
            if text in (node.get("mem_ids") or []):
                full = index or BlockIndex(root)
                return full.get(text)
    full = index or BlockIndex(root)
    hit = full.get(text)
    if hit:
        return hit
    return _near_id(text, full)


def _near_id(text: str, store: BlockIndex) -> BlockRecord | None:
    """One-character id typos should not fall off Channel 1 onto an empty FTS miss."""
    if not classify_daily_id(text):
        return None
    best: BlockRecord | None = None
    best_d = 99
    for rec in store.records:
        other = rec.block_id
        if len(other) != len(text):
            continue
        dist = sum(a != b for a, b in zip(other, text))
        if dist == 0:
            return rec
        if dist < best_d:
            best_d = dist
            best = rec
    if best is not None and best_d == 1:
        return best
    return None


def one_line(body: str, limit: int = 120) -> str:
    """Single routing line so tool output never dumps Beginning/Course/Outcome."""
    text = " ".join((body or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
