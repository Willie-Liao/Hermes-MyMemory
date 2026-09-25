"""Chat cite helpers: Brief extract, Distill cite-map, staging block load.

Non-LLM helpers for UI cite and soft locate. Reuses Phase 1 ``weekly_citations``
legend parsing when that module is present; otherwise parses Distill ``related``
entries shaped as ``"[N] mem-…"``.

Also owns dig-in state for **hot** MEMORY/USER Edit/Delete/Recall
(``arm_hot_action`` → ``arm_dig_in`` / ``set_dig_in_progress``), plus staging
recall snapshots used by weekly action flows. There is no compulsory cite
pipeline or ``pre_tool_call`` cite gate.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from memory_staging import (
    daily_staging_dir,
    iter_daily_staging_files,
    patch_daily_block_status,
)

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover
    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val).resolve() if val else (Path.home() / ".hermes").resolve()

_BRIEF_HEADER_RE = re.compile(r"^##\s+Brief\s*$", re.IGNORECASE | re.MULTILINE)
_DISTILL_HEADER_RE = re.compile(r"^##\s+Distill\s*$", re.IGNORECASE | re.MULTILINE)
# Brief may contain theme ## headings (Events / Hypothesis / …). Only stop at
# sibling top-level sections — not every level-2 header.
_BRIEF_SIBLING_END_RE = re.compile(
    r"^##\s+(?:Distill|(?:\d+\.\s*)?Action ledger)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_LEVEL_TWO_HEADER_RE = re.compile(r"^##(?!#)\s+", re.MULTILINE)
_RELATED_CITE_RE = re.compile(
    r"^\[(\d+)\]\s+(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)\s*$",
    re.IGNORECASE,
)
_TYPED_CITE_MAP_RE = re.compile(
    r"^-\s+\[(\d+)\]\s+(conflict|hypothesis|span)\s+(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_CITE_DIG_IN_KEY = "cite_dig_in"
_STAGING_RECALL_KEY = "staging_recall"
_CITE_STEPS = ("resolve", "load_block", "session_lookup", "answer")
STAGING_RECALL_MAX = 3
STAGING_RECALL_TTL_SECONDS = 24 * 3600
STAGING_RECALL_LIMIT_MESSAGE = "you can only recall last 3 actions!"


def _section_body(md: str, header_re: re.Pattern[str]) -> str:
    """Return text under a ``## Name`` header until the next level-2 header."""
    match = header_re.search(md)
    if not match:
        return ""
    start = match.end()
    rest = md[start:]
    next_header = _LEVEL_TWO_HEADER_RE.search(rest)
    if next_header:
        rest = rest[: next_header.start()]
    return rest.strip()


def extract_brief(md: str) -> str:
    """Return Brief presentation prose (no Distill YAML, no ``## Brief`` header).

    For Event-First weekly files this is the four-part container (Weekly Brief /
    Conflict / Hypothesis / Possible overdue report). For legacy files it is
    the Worker 2 theme Brief. Theme ``##`` headings under Brief (e.g.
    ``## Events``) are kept — only ``## Distill`` / ``## Action ledger``
    (and numbered ledger variants) end the section.
    """
    text = md or ""
    match = _BRIEF_HEADER_RE.search(text)
    if not match:
        try:
            try:
                from .weekly_event_schema import is_four_part_brief
            except ImportError:
                from weekly_event_schema import is_four_part_brief
        except ImportError:
            return ""
        return text.strip() if is_four_part_brief(text) else ""
    rest = text[match.end() :]
    end = _BRIEF_SIBLING_END_RE.search(rest)
    if end:
        rest = rest[: end.start()]
    return rest.strip()


def load_typed_cite_map(md: str) -> dict[int, dict[str, str]]:
    """Parse typed (non-event) cites from the Brief ``Cite map`` section.

    Returns ``{N: {"kind": "conflict"|"hypothesis"|"span", "id": "…"}}``.
    Event citations stay in ``load_cite_map`` / Distill related and are never
    renumbered here.
    """
    brief = extract_brief(md)
    if not brief:
        return {}
    out: dict[int, dict[str, str]] = {}
    for match in _TYPED_CITE_MAP_RE.finditer(brief):
        out[int(match.group(1))] = {
            "kind": match.group(2).casefold(),
            "id": match.group(3),
        }
    return out


def _frontmatter_blocks(content: str) -> list[tuple[int, str, str]]:
    """Parse ``---`` YAML + body blocks (digest-compatible shape)."""
    lines = content.splitlines()
    blocks: list[tuple[int, str, str]] = []
    idx = 0

    while idx < len(lines):
        if lines[idx].strip() != "---":
            idx += 1
            continue

        start_line = idx + 1
        idx += 1
        frontmatter: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            frontmatter.append(lines[idx])
            idx += 1
        if idx >= len(lines):
            blocks.append((start_line, "\n".join(frontmatter), ""))
            break

        idx += 1
        body: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            body.append(lines[idx])
            idx += 1
        blocks.append((start_line, "\n".join(frontmatter), "\n".join(body).strip()))

    return blocks


def _parse_related_cite_entry(entry: Any) -> tuple[int, str] | None:
    if not isinstance(entry, str):
        return None
    match = _RELATED_CITE_RE.match(entry.strip())
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def _cite_map_from_distill_related(md: str) -> dict[int, str]:
    distill = _section_body(md or "", _DISTILL_HEADER_RE)
    if not distill:
        return {}

    legend: dict[int, str] = {}
    for _line_no, raw_frontmatter, _body in _frontmatter_blocks(distill):
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        related = parsed.get("related")
        if not isinstance(related, list):
            continue
        for entry in related:
            pair = _parse_related_cite_entry(entry)
            if pair is None:
                continue
            n, mem_id = pair
            legend[n] = mem_id
    return legend


def load_cite_map(md: str) -> dict[int, str]:
    """Build continuous ``[N] → mem-…`` map from Distill ``related`` entries.

    Prefer Phase 1 ``weekly_citations`` when available; otherwise parse Distill
    YAML ``related: ["[N] mem-…"]`` markers directly.
    """
    try:
        citations_path = Path(__file__).with_name("weekly_citations.py")
        if citations_path.is_file():
            spec = importlib.util.spec_from_file_location(
                "memory_weekly_citations_cite", citations_path
            )
            if spec is not None and spec.loader is not None:
                citations = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(citations)
                loader = getattr(citations, "load_cite_map_from_md", None)
                if callable(loader):
                    result = loader(md)
                    if isinstance(result, dict):
                        return {int(k): str(v) for k, v in result.items()}
    except Exception:
        pass

    return _cite_map_from_distill_related(md)


def _normalize_sources(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return [str(raw)]


def find_staging_block(mem_id: str) -> dict[str, Any] | None:
    """Load a daily staging block by ``mem-…`` id.

    Returns ``{"id", "body", "sources", ...}`` or ``None`` when missing.
    """
    target = (mem_id or "").strip()
    if not target:
        return None

    home = get_hermes_home()
    daily_dir = daily_staging_dir(home)
    for path in iter_daily_staging_files(daily_dir, migrate_legacy=False):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _line_no, raw_frontmatter, body in _frontmatter_blocks(text):
            try:
                parsed = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(parsed, dict):
                continue
            if str(parsed.get("id", "")).strip() != target:
                continue
            result = dict(parsed)
            result["id"] = target
            result["body"] = body
            result["sources"] = _normalize_sources(parsed.get("sources"))
            return result
    return None


# --- Dig-in state (hot actions) ------------------------------------------------


def _weekly_state_path() -> Path:
    return get_hermes_home() / "memories" / "staging" / ".weekly-state.json"


def _load_weekly_state() -> dict[str, Any]:
    path = _weekly_state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_weekly_state(state: dict[str, Any]) -> None:
    path = _weekly_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _sync_presentation_dig_in(
    state: dict[str, Any],
    *,
    active: bool | None = None,
    week_key: str | None = None,
    session_id: str | None = None,
    attempt_count: int | None = None,
) -> None:
    """Keep presentation dig_in_* fields aligned with dig-in state."""
    presentation = state.get("presentation")
    if not isinstance(presentation, dict):
        presentation = {}
        state["presentation"] = presentation
    if active is not None:
        presentation["dig_in_active"] = bool(active)
    if week_key is not None:
        presentation["dig_in_week"] = str(week_key)
    if session_id is not None:
        if session_id:
            presentation["dig_in_session_id"] = str(session_id)
        else:
            presentation.pop("dig_in_session_id", None)
    if attempt_count is not None:
        presentation["dig_in_attempts"] = int(attempt_count)


def get_dig_in() -> dict[str, Any] | None:
    """Return active dig-in state dict, or None when inactive/absent."""
    state = _load_weekly_state()
    raw = state.get(_CITE_DIG_IN_KEY)
    if not isinstance(raw, dict):
        return None
    if not raw.get("active"):
        return None
    return raw


def arm_dig_in(*, session_id: str = "", week_key: str = "") -> dict[str, Any]:
    """Arm dig-in state for hot actions (``arm_hot_action`` calls this when inactive)."""
    state = _load_weekly_state()
    dig: dict[str, Any] = {
        "active": True,
        "session_id": str(session_id or ""),
        "week_key": str(week_key or ""),
        "attempt_count": 0,
        "step": "resolve",
        "resolved_mem_id": None,
        "allowed_session_ids": [],
        "action_pending": False,
        "action_block_id": None,
        "action_cite": None,
        "target_kind": "staging",
        "action_file": None,
        "action_index": None,
        "action_before": None,
    }
    state[_CITE_DIG_IN_KEY] = dig
    _sync_presentation_dig_in(
        state,
        active=True,
        week_key=str(week_key or ""),
        session_id=str(session_id or ""),
        attempt_count=0,
    )
    _save_weekly_state(state)
    return dig


def clear_dig_in() -> None:
    """Disarm dig-in and drop dig-in state."""
    state = _load_weekly_state()
    state.pop(_CITE_DIG_IN_KEY, None)
    _sync_presentation_dig_in(state, active=False, attempt_count=0)
    _save_weekly_state(state)


def set_dig_in_progress(
    *,
    step: str | None = None,
    resolved_mem_id: str | None = None,
    allowed_session_ids: list[str] | None = None,
    action_pending: bool | None = None,
    action_block_id: str | None = None,
    action_cite: int | None = None,
    action_phase: str | None = None,
    edit_draft: str | None = None,
    recall_offer: bool | None = None,
    target_kind: str | None = None,
    action_file: str | None = None,
    action_index: int | None = None,
    action_before: str | None = None,
    clear_action: bool = False,
) -> dict[str, Any] | None:
    """Update dig-in progress (hot/staging action fields) without resetting attempts."""
    state = _load_weekly_state()
    raw = state.get(_CITE_DIG_IN_KEY)
    if not isinstance(raw, dict) or not raw.get("active"):
        return None
    if step is not None:
        raw["step"] = step if step in _CITE_STEPS else raw.get("step", "resolve")
    if resolved_mem_id is not None:
        raw["resolved_mem_id"] = resolved_mem_id
    if allowed_session_ids is not None:
        raw["allowed_session_ids"] = [str(s) for s in allowed_session_ids if str(s).strip()]
    if clear_action:
        raw["action_pending"] = False
        raw["action_block_id"] = None
        raw["action_cite"] = None
        raw["action_phase"] = None
        raw["edit_draft"] = None
        raw["recall_offer"] = False if recall_offer is None else bool(recall_offer)
        raw["target_kind"] = "staging"
        raw["action_file"] = None
        raw["action_index"] = None
        raw["action_before"] = None
    else:
        if action_pending is not None:
            raw["action_pending"] = bool(action_pending)
        if action_block_id is not None:
            raw["action_block_id"] = str(action_block_id)
        if action_cite is not None:
            raw["action_cite"] = int(action_cite)
        if action_phase is not None:
            raw["action_phase"] = str(action_phase) if action_phase else None
        if edit_draft is not None:
            raw["edit_draft"] = str(edit_draft) if edit_draft else None
        if recall_offer is not None:
            raw["recall_offer"] = bool(recall_offer)
        if target_kind is not None:
            kind = str(target_kind).strip().lower()
            raw["target_kind"] = kind if kind in ("staging", "hot") else "staging"
        if action_file is not None:
            raw["action_file"] = str(action_file) if action_file else None
        if action_index is not None:
            raw["action_index"] = int(action_index)
        if action_before is not None:
            raw["action_before"] = str(action_before) if action_before else None
    state[_CITE_DIG_IN_KEY] = raw
    _save_weekly_state(state)
    return raw


def arm_hot_action(
    *,
    file: str,
    index: int,
    before: str,
    session_id: str = "",
) -> dict[str, Any] | None:
    """Ensure dig-in is active, then arm a hot MEMORY/USER action target."""
    if get_dig_in() is None:
        arm_dig_in(session_id=session_id)
    return set_dig_in_progress(
        target_kind="hot",
        action_pending=True,
        action_file=file,
        action_index=index,
        action_before=before,
    )


def _parse_recall_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _prune_staging_recall(entries: list[Any], *, now: datetime | None = None) -> list[dict[str, Any]]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=STAGING_RECALL_TTL_SECONDS)
    kept: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        at = _parse_recall_at(item.get("at") or item.get("savedAt"))
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at >= cutoff:
            kept.append(item)
    return kept


def list_staging_recall(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """Return non-expired staging recall snapshots (newest last)."""
    state = _load_weekly_state()
    raw = state.get(_STAGING_RECALL_KEY)
    entries = raw if isinstance(raw, list) else []
    pruned = _prune_staging_recall(entries, now=now)
    if pruned != entries:
        state[_STAGING_RECALL_KEY] = pruned
        _save_weekly_state(state)
    return list(pruned)


def push_staging_recall(
    entry: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Push a staging Edit/Delete snapshot. Max 3 live entries; 4th returns limit error."""
    if not isinstance(entry, dict):
        return {"ok": False, "error": "invalid_entry"}
    state = _load_weekly_state()
    raw = state.get(_STAGING_RECALL_KEY)
    entries = _prune_staging_recall(raw if isinstance(raw, list) else [], now=now)
    if len(entries) >= STAGING_RECALL_MAX:
        state[_STAGING_RECALL_KEY] = entries
        _save_weekly_state(state)
        return {"ok": False, "error": STAGING_RECALL_LIMIT_MESSAGE}
    snapshot = dict(entry)
    if not snapshot.get("at"):
        snapshot["at"] = (now or datetime.now(timezone.utc)).isoformat()
    entries.append(snapshot)
    state[_STAGING_RECALL_KEY] = entries
    _save_weekly_state(state)
    return {"ok": True, "count": len(entries)}


def pop_staging_recall(*, now: datetime | None = None) -> dict[str, Any]:
    """Pop and restore the newest staging recall snapshot."""
    state = _load_weekly_state()
    raw = state.get(_STAGING_RECALL_KEY)
    entries = _prune_staging_recall(raw if isinstance(raw, list) else [], now=now)
    if not entries:
        state[_STAGING_RECALL_KEY] = []
        _save_weekly_state(state)
        return {"ok": False, "error": STAGING_RECALL_LIMIT_MESSAGE}

    snapshot = entries.pop()
    state[_STAGING_RECALL_KEY] = entries
    _save_weekly_state(state)

    block_id = str(snapshot.get("block_id") or "").strip()
    if not block_id:
        return {"ok": False, "error": "missing_block_id", "snapshot": snapshot}

    before_status = str(snapshot.get("before_status") or "candidate").strip() or "candidate"
    before_body = snapshot.get("before_body")

    ok = patch_daily_block_status(
        get_hermes_home(),
        block_id,
        status=before_status,
        timestamp_field="updated_at",
        body=None if before_body is None else str(before_body),
    )
    if not ok:
        # Re-push so a failed restore is not lost.
        entries.append(snapshot)
        state = _load_weekly_state()
        state[_STAGING_RECALL_KEY] = entries
        _save_weekly_state(state)
        return {"ok": False, "error": "restore_failed", "snapshot": snapshot}
    return {"ok": True, "snapshot": snapshot}
