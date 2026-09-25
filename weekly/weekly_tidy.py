"""Weekly ledger parse, Brief-cite candidates, and reopen for closed weeks."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import yaml

try:
    from . import weekly
except ImportError:  # pragma: no cover
    import sys

    _plugin_dir = Path(__file__).resolve().parent
    if str(_plugin_dir) not in sys.path:
        sys.path.insert(0, str(_plugin_dir))
    _module_path = Path(__file__).with_name("weekly.py")
    _spec = importlib.util.spec_from_file_location("memory_weekly_core", _module_path)
    if _spec is None or _spec.loader is None:
        raise
    weekly = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(weekly)

_plugins_root = Path(__file__).resolve().parent.parent.parent
_mymemory = Path(__file__).resolve().parent.parent
for _p in (_mymemory, _plugins_root):
    if str(_p) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(_p))

from memory_staging import (
    BLOCK_ID_RE,
    patch_daily_block_status,
    weekly_reviewed_path,
    weekly_staging_path,
)

_SECTION_HEADERS = (
    (1, re.compile(r"^##\s*1\.\s*Proposed additions", re.IGNORECASE)),
    (2, re.compile(r"^##\s*2\.\s*Hypotheses", re.IGNORECASE)),
    (3, re.compile(r"^##\s*3\.\s*Reinforced procedures", re.IGNORECASE)),
)
_LEDGER_HEADER = re.compile(r"^##\s*8\.\s*Action ledger", re.IGNORECASE)
_VALID_ACTIONS = frozenset({"promote", "discard", "skip"})
_STRUCTURED_FIELD_KEYS = frozenset(
    {"record_id", "block_ids", "target", "text", "valid_to", "reason"}
)
_SUBSECTION_HEADER_RE = re.compile(r"^###\s*(\d+\.\d+)", re.IGNORECASE)
_PROSE_RECORD_RE = re.compile(r"^(F|H|P)(\d+)\.\s+(.+)$", re.IGNORECASE)
_LEVEL_TWO_HEADER_RE = re.compile(r"^##(?!#)\s*")
_DISTILL_HEADER_RE = re.compile(r"^##\s*Distill\s*$", re.IGNORECASE | re.MULTILINE)
_LEVEL_TWO_ANYWHERE_RE = re.compile(r"^##(?!#)\s+", re.MULTILINE)
# Distill related / sources may use dated or compact mem-ids.
_MEM_ID_FIND_RE = re.compile(
    r"\b(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)\b",
    re.IGNORECASE,
)
# Four-part Brief Cite map: "- [N] event mem-…"
_FOUR_PART_CITE_MAP_EVENT_RE = re.compile(
    r"^-\s*\[(\d+)\]\s+event\s+(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CITE_MAP_SECTION_RE = re.compile(
    r"^Cite map\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DISTILL_PROMOTE_TIERS = {
    "hypothesis": "hypothesis",
    "procedure": "procedure",
}


def _tier_for(subsection: str, section: int) -> str:
    if subsection == "1.1":
        return "proposed"
    if subsection == "1.2":
        return "not_proposed"
    if section == 2:
        return "hypothesis"
    if section == 3:
        return "procedure"
    return ""


def _parse_structured_fields(first_stripped: str, lines: list[str], start: int) -> tuple[dict[str, str], int]:
    fields: dict[str, str] = {}
    body = first_stripped[2:].strip()
    if ":" in body:
        key, _, val = body.partition(":")
        fields[key.strip()] = val.strip()
    idx = start + 1
    while idx < len(lines):
        raw = lines[idx]
        if not raw or not raw[:1].isspace():
            break
        cont = raw.strip()
        if not cont or cont.startswith("- "):
            break
        if ":" in cont:
            key, _, val = cont.partition(":")
            fields[key.strip()] = val.strip()
        idx += 1
    return fields, idx


def _parse_indented_fields(lines: list[str], start: int) -> tuple[dict[str, str], int]:
    fields: dict[str, str] = {}
    idx = start + 1
    while idx < len(lines):
        raw = lines[idx]
        if not raw or not raw[:1].isspace():
            break
        cont = raw.strip()
        if cont and ":" in cont:
            key, _, val = cont.partition(":")
            fields[key.strip()] = val.strip()
        idx += 1
    return fields, idx


def _block_id_from_fields(fields: dict[str, str]) -> str:
    block_ids_raw = fields.get("block_ids", "")
    block_match = BLOCK_ID_RE.search(block_ids_raw)
    if block_match:
        return block_match.group(1)
    for value in fields.values():
        match = BLOCK_ID_RE.search(value)
        if match:
            return match.group(1)
    return ""


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().casefold()).strip("-")
    return slug[:48] or "record"


def _weekly_file_for_tidy(week_key: str) -> Path | None:
    parsed = weekly._parse_week_key(week_key)
    if parsed is None:
        return None
    year, week = parsed
    reviewed = weekly_reviewed_path(weekly._hermes_home(), year, week)
    if reviewed.exists():
        return reviewed
    return weekly._resolve_weekly_read_path(week_key)


def _distill_region(text: str) -> str:
    match = _DISTILL_HEADER_RE.search(text or "")
    if not match:
        return ""
    start = match.end()
    next_h = _LEVEL_TWO_ANYWHERE_RE.search(text, start)
    end = next_h.start() if next_h else len(text)
    return text[start:end].strip()


def _iter_distill_yaml_blocks(region: str) -> list[tuple[dict[str, Any], str]]:
    """Parse ``---`` YAML fences in Distill region into (frontmatter, body)."""
    lines = (region or "").splitlines()
    blocks: list[tuple[dict[str, Any], str]] = []
    idx = 0
    while idx < len(lines):
        if lines[idx].strip() != "---":
            idx += 1
            continue
        idx += 1
        frontmatter_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            frontmatter_lines.append(lines[idx])
            idx += 1
        if idx >= len(lines):
            raw = "\n".join(frontmatter_lines)
            try:
                parsed = yaml.safe_load(raw) if raw.strip() else {}
            except yaml.YAMLError:
                break
            if isinstance(parsed, dict):
                blocks.append((parsed, ""))
            break
        idx += 1
        body_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            body_lines.append(lines[idx])
            idx += 1
        raw = "\n".join(frontmatter_lines)
        try:
            parsed = yaml.safe_load(raw) if raw.strip() else {}
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        blocks.append((parsed, "\n".join(body_lines).strip()))
    return blocks


def _first_mem_id(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                found = _first_mem_id(item)
                if found:
                    return found
            continue
        text = str(value).strip()
        if not text:
            continue
        match = _MEM_ID_FIND_RE.search(text)
        if match:
            return match.group(1)
        match = BLOCK_ID_RE.search(text)
        if match:
            return match.group(1)
    return ""


def _parse_distill_yaml_candidates(text: str) -> list[dict[str, str]]:
    """Map Distill YAML blocks to tidy candidates (hypothesis/procedure only)."""
    region = _distill_region(text)
    if not region:
        return []

    candidates: list[dict[str, str]] = []
    for fm, body in _iter_distill_yaml_blocks(region):
        block_type = str(fm.get("type") or "").strip().casefold()
        tier = _DISTILL_PROMOTE_TIERS.get(block_type)
        if not tier:
            # event / conflict / unknown — not auto-promote candidates
            continue
        record_id = str(fm.get("id") or "").strip()
        if not record_id:
            continue
        block_id = _first_mem_id(fm.get("related"), fm.get("sources"))
        proposed = (body or "").strip()
        label = proposed.splitlines()[0].strip() if proposed else record_id
        entry: dict[str, str] = {
            "record_id": record_id,
            "label": label or record_id,
            "section": "Distill",
            "block_id": block_id,
            "source": "Distill",
            "tier": tier,
        }
        if proposed:
            entry["proposed_text"] = proposed
        valid_to = str(fm.get("valid_to") or "").strip()
        if valid_to:
            entry["valid_to"] = valid_to
        candidates.append(entry)
    return candidates


def parse_weekly_candidates(week_key: str) -> list[dict[str, str]]:
    """Extract actionable rows from Distill YAML or legacy §1–§3."""
    path = _weekly_file_for_tidy(week_key)
    if path is None or not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    if _DISTILL_HEADER_RE.search(text):
        return _parse_distill_yaml_candidates(text)

    lines = text.splitlines()
    current_section = 0
    current_subsection = ""
    candidates: list[dict[str, str]] = []
    current_label = ""

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if _LEDGER_HEADER.match(stripped):
            break
        matched_section = False
        for section_no, pattern in _SECTION_HEADERS:
            if pattern.match(stripped):
                current_section = section_no
                current_subsection = ""
                current_label = ""
                matched_section = True
                break
        if _LEVEL_TWO_HEADER_RE.match(stripped):
            if not matched_section:
                current_section = 0
                current_subsection = ""
                current_label = ""
            i += 1
            continue
        if stripped.startswith("### ") and current_section == 1:
            sub_match = _SUBSECTION_HEADER_RE.match(stripped)
            if sub_match:
                current_subsection = sub_match.group(1)
            current_label = stripped[4:].strip()
            i += 1
            continue
        if not current_section:
            i += 1
            continue
        prose_match = _PROSE_RECORD_RE.match(stripped)
        if prose_match:
            prefix, number, body = prose_match.groups()
            prefix = prefix.upper()
            is_expected_record = (
                (prefix == "F" and current_section == 1 and current_subsection == "1.1")
                or (prefix == "H" and current_section == 2)
                or (prefix == "P" and current_section == 3)
            )
            if not is_expected_record:
                i += 1
                continue
            fields, i = _parse_indented_fields(lines, i)
            record_id = f"{prefix}{number}"
            block_id = _block_id_from_fields(fields)
            source = f"§{current_subsection}" if current_subsection else f"§{current_section}"
            entry = {
                "record_id": record_id,
                "label": body,
                "section": f"§{current_section}",
                "block_id": block_id,
                "source": source,
                "proposed_text": body,
            }
            tier = _tier_for(current_subsection, current_section)
            if tier:
                entry["tier"] = tier
            if fields.get("target"):
                entry["hot_target"] = fields["target"]
            if fields.get("valid_to"):
                entry["valid_to"] = fields["valid_to"]
            candidates.append(entry)
            continue
        if not stripped.startswith("- "):
            i += 1
            continue
        body = stripped[2:].strip()
        if body.casefold().startswith("defer"):
            i += 1
            continue

        structured_key = body.split(":", 1)[0].strip().casefold()
        if structured_key in _STRUCTURED_FIELD_KEYS:
            fields, i = _parse_structured_fields(stripped, lines, i)
            block_id = _block_id_from_fields(fields)
            record_id = fields.get("record_id", "").strip() or block_id or _slugify(current_label or body)
            label = fields.get("record_id", "").strip() or current_label or record_id
            tier = _tier_for(current_subsection, current_section)
            entry: dict[str, str] = {
                "record_id": record_id,
                "label": label,
                "section": f"§{current_section}",
                "block_id": block_id,
                "source": f"§{current_section}"
                + (f".{current_subsection}" if current_subsection else ""),
            }
            if tier:
                entry["tier"] = tier
            if fields.get("text"):
                entry["proposed_text"] = fields["text"]
            if fields.get("target"):
                entry["hot_target"] = fields["target"]
            if fields.get("valid_to"):
                entry["valid_to"] = fields["valid_to"]
            candidates.append(entry)
            continue

        block_match = BLOCK_ID_RE.search(body)
        block_id = block_match.group(1) if block_match else ""
        label = current_label or body.split(":", 1)[0].strip()
        if not label:
            label = body[:80]
        record_id = block_id or _slugify(label)
        entry = {
            "record_id": record_id,
            "label": label,
            "section": f"§{current_section}",
            "block_id": block_id,
            "source": f"§{current_section}",
        }
        tier = _tier_for(current_subsection, current_section)
        if tier:
            entry["tier"] = tier
        candidates.append(entry)
        i += 1

    return candidates


def _load_sibling_module(module_name: str, filename: str) -> Any:
    """Load a same-package module under package or flat test import."""
    try:
        return importlib.import_module(f".{module_name}", package=__package__)
    except Exception:
        pass
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(
        f"memory_weekly_{module_name}_tidy", path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {filename}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_four_part_cite_map_event_candidates(week_key: str) -> list[dict[str, Any]]:
    """Quoted Events from four-part ``Cite map`` (``- [N] event mem-…``).

    Event-First briefs put day-header cites + Cite map entries here. Those mem-ids
    are Approval Hub cards even when daily staging ``type`` is fact/decision —
    they were quoted as Events in the Weekly Brief.
    """
    path = _weekly_file_for_tidy(week_key)
    if path is None or not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    section = _CITE_MAP_SECTION_RE.search(text)
    if not section:
        return []
    body = text[section.end() :]
    next_header = re.search(r"^##\s+\S+", body, flags=re.MULTILINE)
    if next_header:
        body = body[: next_header.start()]

    cite = _load_sibling_module("weekly_cite", "weekly_cite.py")
    find_staging_block = cite.find_staging_block

    seen_mem: set[str] = set()
    out: list[dict[str, Any]] = []
    for match in _FOUR_PART_CITE_MAP_EVENT_RE.finditer(body):
        n = int(match.group(1))
        mem = str(match.group(2) or "").strip()
        if not mem or mem in seen_mem:
            continue
        seen_mem.add(mem)
        staging = find_staging_block(mem) or {}
        body_text = str(staging.get("body") or "").strip()
        label = body_text.splitlines()[0].strip() if body_text else mem
        out.append(
            {
                "record_id": f"cite-{n}",
                "block_id": mem,
                "label": label,
                "section": "Cite map",
                "source": "Cite map",
                "tier": "cited",
                "proposed_text": body_text or mem,
                "cite_n": str(n),
                # Hub treats Brief-quoted Events as event cards for review actions.
                "type": "event",
            }
        )
    return out


def parse_brief_cite_candidates(week_key: str) -> list[dict[str, str]]:
    """Brief [N] → Distill related mem → Approval candidate rows.

    Each row includes at least:
      record_id, block_id, label, source, tier, proposed_text, cite_n (str)
    Order: first appearance of [N] in ## Brief. One card per mem-id (first N wins).
    """
    path = _weekly_file_for_tidy(week_key)
    if path is None or not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    cite = _load_sibling_module("weekly_cite", "weekly_cite.py")
    citations = _load_sibling_module("weekly_citations", "weekly_citations.py")
    extract_brief = cite.extract_brief
    find_staging_block = cite.find_staging_block
    parse_related = getattr(cite, "_parse_related_cite_entry", None)
    extract_cite_numbers = citations.extract_cite_numbers

    brief = extract_brief(text)
    if not brief.strip():
        return []

    # Build N → mem from Distill related (first wins per N)
    n_to_mem: dict[int, str] = {}
    region = _distill_region(text)
    for fm, _body in _iter_distill_yaml_blocks(region or ""):
        related = fm.get("related") or []
        if not isinstance(related, list):
            related = [related]
        for entry in related:
            pair = None
            if callable(parse_related):
                pair = parse_related(entry)
            if pair is None:
                raw = str(entry or "").strip()
                m = re.match(
                    r"^\[(\d+)\]\s+(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)\s*$",
                    raw,
                    re.IGNORECASE,
                )
                if not m:
                    continue
                n, mem = int(m.group(1)), m.group(2)
            else:
                n, mem = pair
            if n not in n_to_mem:
                n_to_mem[n] = mem

    seen_mem: set[str] = set()
    out: list[dict[str, str]] = []
    for n in extract_cite_numbers(brief):
        mem = n_to_mem.get(n)
        if not mem or mem in seen_mem:
            continue
        seen_mem.add(mem)
        staging = find_staging_block(mem) or {}
        body = str(staging.get("body") or "").strip()
        label = body.splitlines()[0].strip() if body else mem
        row: dict[str, str] = {
            "record_id": f"cite-{n}",
            "block_id": mem,
            "label": label,
            "section": "Brief",
            "source": "Brief",
            "tier": "cited",
            "proposed_text": body or mem,
            "cite_n": str(n),
        }
        block_type = str(staging.get("type") or "").strip()
        if block_type:
            row["type"] = block_type
        out.append(row)
    return out


def filter_approval_hub_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep Approval Hub rows whose staging block is ``type: event``.

    Non-event types (fact / procedure / decision_constraint / hypothesis) are
    excluded. Rows already tagged ``type: event`` are kept without re-lookup.
    """
    cite = _load_sibling_module("weekly_cite", "weekly_cite.py")
    find_staging_block = cite.find_staging_block
    out: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        block_id = str(raw.get("block_id") or "").strip()
        if not block_id:
            continue
        typ = str(raw.get("type") or "").strip().casefold()
        if typ != "event":
            staging = find_staging_block(block_id) or {}
            typ = str(staging.get("type") or "").strip().casefold()
        if typ != "event":
            continue
        row = dict(raw)
        row["type"] = "event"
        out.append(row)
    return out


_RESOLVED_EVENT_STATUSES = frozenset(
    {"approved", "rejected", "discarded", "dropped"}
)


def list_week_daily_event_candidates(week_key: str) -> list[dict[str, Any]]:
    """All ``type: event`` daily staging blocks for the ISO week.

    Used so Approval Hub lists every week event, not only those already cited
    in the Brief (worker1 may cite facts as narrative evidence while real
    event blocks sit uncited in daily files).
    """
    parsed = weekly._parse_week_key(week_key)
    if parsed is None:
        return []
    year, week_num = parsed
    files = weekly._daily_files_for_week(year, week_num)
    cite = _load_sibling_module("weekly_cite", "weekly_cite.py")
    frontmatter_blocks = cite._frontmatter_blocks
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _line_no, raw_frontmatter, body in frontmatter_blocks(text):
            try:
                fm = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(fm, dict):
                continue
            if str(fm.get("type") or "").strip().casefold() != "event":
                continue
            block_id = str(fm.get("id") or "").strip()
            if not block_id or block_id in seen:
                continue
            status = str(fm.get("status") or "").strip().casefold()
            if status in _RESOLVED_EVENT_STATUSES:
                continue
            seen.add(block_id)
            body_s = str(body or "").strip()
            label = body_s.splitlines()[0].strip() if body_s else block_id
            out.append(
                {
                    "record_id": f"event-{block_id}",
                    "block_id": block_id,
                    "label": label,
                    "section": "Daily",
                    "source": "Daily",
                    "tier": "event",
                    "proposed_text": body_s or block_id,
                    "type": "event",
                }
            )
    return out


def merge_approval_hub_event_candidates(
    cited_events: list[dict[str, Any]],
    week_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union Brief-cited events with all week daily events; cited rows win on cite_n."""
    by_id: dict[str, dict[str, Any]] = {}
    for raw in week_events:
        if not isinstance(raw, dict):
            continue
        block_id = str(raw.get("block_id") or "").strip()
        if not block_id:
            continue
        by_id[block_id] = dict(raw)
        by_id[block_id]["type"] = "event"
    for raw in cited_events:
        if not isinstance(raw, dict):
            continue
        block_id = str(raw.get("block_id") or "").strip()
        if not block_id:
            continue
        row = dict(raw)
        row["type"] = "event"
        by_id[block_id] = row

    def _sort_key(row: dict[str, Any]) -> tuple[int, str]:
        cite_raw = row.get("cite_n")
        try:
            cite_n = int(str(cite_raw).strip())
        except (TypeError, ValueError):
            cite_n = 10**9
        return (cite_n, str(row.get("block_id") or ""))

    return sorted(by_id.values(), key=_sort_key)


def parse_action_ledger(text: str) -> list[dict[str, str]]:
    """Parse §8 Action ledger rows into ``{record_id, action}`` entries."""
    parts = re.split(
        r"^##\s*8\.\s*Action ledger\s*$",
        text,
        maxsplit=1,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if len(parts) < 2:
        return []

    ledger_body = re.split(r"^##\s+", parts[1], maxsplit=1, flags=re.MULTILINE)[0]
    rows: list[dict[str, str]] = []
    for raw_line in ledger_body.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        id_cell, _source, action = cells[0], cells[1], cells[2].casefold()
        if id_cell.casefold() in {"id / label", "------------"} or set(id_cell) <= {"-"}:
            continue
        if action not in _VALID_ACTIONS:
            continue
        record_id = id_cell.split("/", 1)[0].strip()
        if not record_id:
            continue
        rows.append({"record_id": record_id, "action": action})
    return rows


def _strip_action_ledger(text: str) -> str:
    parts = re.split(
        r"^##\s*8\.\s*Action ledger\s*$",
        text,
        maxsplit=1,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if len(parts) < 2:
        return text
    return parts[0].rstrip() + "\n"


def _unmark_week_reviewed(
    hermes_home: Path, year: int, week: int, content: str
) -> Path:
    """Write content to canonical week path with week_status=pending."""
    from memory_staging import (
        WEEK_STATUS_PENDING,
        week_file_path,
        write_week_status,
        week_key as mk_week_key,
        weekly_reviewed_path,
    )

    draft = week_file_path(hermes_home, year, week)
    legacy = weekly_reviewed_path(hermes_home, year, week)
    key = mk_week_key(year, week)
    write_week_status(
        draft,
        WEEK_STATUS_PENDING,
        week_key_str=key,
        content=content if content.endswith("\n") else content + "\n",
    )
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass
    return draft


def reopen_week(week_key: str) -> dict[str, Any]:
    """Reverse tidy close: restore candidates, strip ledger, set week_status pending."""
    parsed = weekly._parse_week_key(week_key)
    if parsed is None:
        return {"outcome": "bad_week", "week": week_key, "restored_blocks": []}

    year, week = parsed
    hermes_home = weekly._hermes_home()
    from memory_staging import (
        migrate_week_files,
        week_file_path,
        week_is_reviewed,
        weekly_reviewed_path,
    )

    migrate_week_files(hermes_home, year, week)
    path = week_file_path(hermes_home, year, week)
    if not path.exists() and not week_is_reviewed(hermes_home, year, week):
        legacy = weekly_reviewed_path(hermes_home, year, week)
        if not legacy.exists():
            return {
                "outcome": "no_reviewed_file",
                "week": week_key,
                "restored_blocks": [],
            }

    if not path.exists():
        return {"outcome": "no_reviewed_file", "week": week_key, "restored_blocks": []}

    if not week_is_reviewed(hermes_home, year, week):
        return {"outcome": "no_reviewed_file", "week": week_key, "restored_blocks": []}

    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        weekly._log(f"reopen read failed {path}: {exc}")
        return {"outcome": "no_reviewed_file", "week": week_key, "restored_blocks": []}

    ledger_rows = parse_action_ledger(original)
    candidates = parse_brief_cite_candidates(week_key)
    block_by_record = {
        str(c.get("record_id") or "").strip(): str(c.get("block_id") or "").strip()
        for c in candidates
        if str(c.get("record_id") or "").strip()
    }

    restored_blocks: list[str] = []
    for row in ledger_rows:
        action = row["action"]
        if action not in ("promote", "discard"):
            continue
        record_id = row["record_id"]
        block_id = block_by_record.get(record_id, "")
        if not block_id:
            block_match = BLOCK_ID_RE.search(record_id)
            block_id = block_match.group(1) if block_match else ""
        if not block_id:
            continue
        ok = patch_daily_block_status(
            hermes_home,
            block_id,
            status="candidate",
            timestamp_field="reopened_at",
        )
        if ok and block_id not in restored_blocks:
            restored_blocks.append(block_id)

    stripped = _strip_action_ledger(original)
    draft = _unmark_week_reviewed(hermes_home, year, week, stripped)

    state = weekly._load_state()
    presentation = weekly._presentation_state(state)
    for list_key in ("tidy_completed_weeks", "completed_weeks"):
        items = presentation.get(list_key)
        if isinstance(items, list) and week_key in items:
            presentation[list_key] = [k for k in items if str(k) != week_key]
    if str(presentation.get("tidy_pending_week") or "") == week_key:
        presentation.pop("tidy_pending_week", None)
    weekly._save_state(state)

    weekly._log(
        f"weekly reopened {week_key} restored={len(restored_blocks)} path={draft}"
    )
    return {
        "outcome": "reopened",
        "week": week_key,
        "restored_blocks": restored_blocks,
        "path": str(draft),
    }
