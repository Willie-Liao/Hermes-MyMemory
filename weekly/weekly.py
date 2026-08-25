"""Generate-only weekly memory consolidation from daily staging files.

Runs on plugin load and session lifecycle hooks. Scans staging/daily/ for
complete ISO weeks missing a staging review file (backlog catch-up). Writes
review files under memories/staging/weekly/ and never calls the memory tool.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_section_dir = Path(__file__).resolve().parent
_section_dir_str = str(_section_dir)
if _section_dir_str not in sys.path:
    sys.path.insert(0, _section_dir_str)

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
_plugins_root_str = str(_plugins_root)
if _plugins_root_str not in sys.path:
    sys.path.insert(0, _plugins_root_str)

from memory_staging import (
    daily_staging_dir,
    eligibility_iso_week,
    hermes_local_today,
    iter_daily_staging_files,
    mark_week_reviewed,
    migrate_all_weekly_files,
    patch_daily_block_status,
    read_week_status,
    week_blocks_backlog_regenerate,
    week_is_reviewed,
    weekly_reviewed_path,
    write_week_status,
    WEEK_STATUS_PENDING,
    WEEK_STATUS_REVIEWED,
)
from worker_llm import in_worker_llm as _in_weekly_worker_llm
from worker_llm import run_worker_llm, run_worker_llm_tools
from worker_llm import worker_llm_scope as _weekly_worker_llm_scope

try:
    from .weekly_brief_validate import format_brief_for_chat
    from .weekly_citations import normalize_event_citations
    from . import hot_health
    from .weekly_cite import (
        clear_dig_in,
        extract_brief,
        find_staging_block,
        get_dig_in,
        pop_staging_recall,
        push_staging_recall,
        set_dig_in_progress,
    )
    from .weekly_distill_validate import (
        _distill_region,
        _frontmatter_blocks,
        validate_weekly_distill,
    )
    from .weekly_event_schema import (
        WeeklyReviewPayload,
        assign_typed_citations,
        render_weekly_review_brief,
        validate_weekly_review_payload,
    )
    from .weekly_event_workers import run_parallel_worker1
    from . import weekly_json
except ImportError:
    from weekly_brief_validate import format_brief_for_chat  # type: ignore[no-redef]
    from weekly_citations import normalize_event_citations  # type: ignore[no-redef]
    import hot_health  # type: ignore[no-redef]
    from weekly_cite import (  # type: ignore[no-redef]
        clear_dig_in,
        extract_brief,
        find_staging_block,
        get_dig_in,
        pop_staging_recall,
        push_staging_recall,
        set_dig_in_progress,
    )
    from weekly_distill_validate import (  # type: ignore[no-redef]
        _distill_region,
        _frontmatter_blocks,
        validate_weekly_distill,
    )
    from weekly_event_schema import (  # type: ignore[no-redef]
        WeeklyReviewPayload,
        assign_typed_citations,
        render_weekly_review_brief,
        validate_weekly_review_payload,
    )
    from weekly_event_workers import run_parallel_worker1  # type: ignore[no-redef]
    import weekly_json  # type: ignore[no-redef]

logger = logging.getLogger("plugins.memory-weekly")

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover
    import os

    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val).resolve() if val else (Path.home() / ".hermes").resolve()


MAX_DAILY_CHARS = 28000
MAX_WEEKS_PER_RUN = 1
MAX_GENERATION_ATTEMPTS = 3
MAX_WEEKLY_RETRY_CHARS = 4000
MAX_ERRORS_IN_PROMPT = 8
_DISTILL_HEADER_RE = re.compile(r"^##\s+Distill\s*$", re.IGNORECASE | re.MULTILINE)
_LEVEL_TWO_HEADER_RE = re.compile(r"^##(?!#)\s+", re.MULTILINE)
WEEKLY_ERROR_MARKERS = (
    "api call failed",
    "connection error",
    "client has been closed",
    "cannot send a request",
    "max retries",
)
SNOOZE_SECONDS = 3600
# How long "Review proposed additions now" keeps the hot-promotion
# window open so approved §-entries can be written via the memory tool.
HOT_PROMOTION_SECONDS = 7200
WEEK_KEY_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{2})$")
CHECK_LATER_RESPONSES = ("2", "check later", "later", "稍后", "等一下")
COMPLETE_WEEK_RESPONSES = (
    "3",
    "skip this week",
    "skip week",
    "skip",  # legacy; gated by WEEKLY_REVIEW_QUESTION on clarify
    "close week",
    "mark complete",
    "review finished",
    "完成了",
    "本周完成",
    "关闭本周",
)
SKIP_RESPONSES = COMPLETE_WEEK_RESPONSES  # backward-compat alias
WEEKLY_REVIEW_QUESTION = "Staging memory review"
# Slash /weekly update has no session_id from Hermes; bind on first chat turn.
SLASH_STAGING_SESSION = "__slash__"
WEEKLY_ACTION_EDIT = "Edit"
WEEKLY_ACTION_DELETE = "Delete"
WEEKLY_ACTION_SOMETHING_ELSE = "Something else…"
WEEKLY_EDIT_OPEN_QUESTION = (
    "How should we edit this staging memory? Describe the change in your own words."
)
WEEKLY_EDIT_CONFIRM_QUESTION = "Apply this staging edit draft?"
WEEKLY_EDIT_AGREE_RESPONSES = ("A · Agree", "Agree")
WEEKLY_EDIT_OTHER_RESPONSES = ("B · Other thought", "Other thought")
WEEKLY_DELETE_QUESTION = "Delete this staging memory?"
WEEKLY_HOT_DELETE_QUESTION = "Delete this hot memory entry?"
WEEKLY_DELETE_YES_RESPONSES = ("Yes",)
WEEKLY_DELETE_LATER_RESPONSES = ("Later", "Check later", "2")
WEEKLY_SOMETHING_ELSE_OPEN_QUESTION = "What should we do with this staging memory?"
WEEKLY_RECALL_QUESTION = "Undo the last staging Edit/Delete?"
WEEKLY_RECALL_YES_RESPONSES = ("Yes", "Recall", "Undo")
WEEKLY_RECALL_NO_RESPONSES = ("No", "Later", "Keep")
# Internal generate turns (AIAgent.run_conversation) — must not get chat review inject
# or schedule backlog via on_session_* hooks. Prefer a broad shared prefix so prompt
# wording tweaks cannot re-enable inject; keep role-specific lines as belt-and-suspenders.
_WEEKLY_WORKER_PROMPT_MARKERS = (
    "weekly memory consolidation Worker",
    "You are the weekly memory consolidation Worker 1 (Distill).",
)
# Process-wide worker flag lives in shared ``worker_llm`` (see imports above).
_run_lock = threading.Lock()

WEEKLY_POLICY = """Weekly memory consolidation policy (Worker 1 — Distill only):
- Generate a review file only. Never call the memory tool.
- Do not write MEMORY.md or USER.md.
- Do not auto-promote staging items to hot memory.
- Use daily typed staging blocks as source material.
- Blocks already marked status: approved or rejected are excluded from sources — do not re-propose them.
- Emit ONLY four block types: event | hypothesis | procedure | conflict.
- Align frontmatter with daily digest contracts where types overlap (id, type, sources, related, confidence, status, …).
- Prefer daily `participants` on events (not `involves`); note `predicate` when present.
- Every block needs non-empty `sources` and `related`.
- Non-events must `related` to at least one week event id from this Distill section.
- conflict is weekly-only: id, type, confidence, status, sources, related (≥1 event id); body = short tension.
- Event bodies use week-global continuous citation markers [1]…[N] after each staging-derived piece (do not restart per event).
- Event `related` entries use the same markers: "[N] mem-…".
- On event participants: include `role` only when clear from sources; otherwise `{entity: …}` only — never guess a role.
- Boil the week into a small set of occasions (events); merge across dates when appropriate.
- Do NOT write Proposed additions tables, Staging over 7 days, State snapshots, Capacity, or Action ledger rows.
- Do NOT write ## Brief (deterministic Worker 2 formats Brief from Distill events).
- Output ONLY a markdown document with ## Distill containing YAML frontmatter + body blocks.
"""

DISTILL_SHAPE_EXAMPLE = """EXAMPLE ## Distill schema (include all required keys per type; invent values from this week’s sources):

## Distill

---
id: wNN-e1-…
type: event
entity: …
predicate: …
participants:
- entity: …
  role: …          # only when role is clear from sources
- entity: …        # omit role when not confident — never guess
valid_from: YYYY-MM-DD
valid_to: YYYY-MM-DD
confidence: high
status: candidate
sources:
- session:…
related:
- '[1] mem-…'
---
Event body [1].

---
id: wNN-h1-…
type: hypothesis
entity: …
valid_from: YYYY-MM-DD
confidence: medium
status: candidate
sources:
- session:…
related:
- '[2] mem-…'
- wNN-e1-…
---
Hypothesis body [2].

---
id: wNN-p1-…
type: procedure
confidence: explicit
status: candidate
sources:
- session:…
related:
- '[3] mem-…'
- wNN-e1-…
---
Procedure body [3].

---
id: wNN-c1-…
type: conflict
confidence: high
status: candidate
sources:
- session:…
related:
- '[4] mem-…'
- wNN-e1-…
---
Short tension body [4].
"""


def _event_ids_from_previous(previous_output: str) -> list[str]:
    region = _distill_region(previous_output) or previous_output
    ids: list[str] = []
    for _line_no, fm, _body in _frontmatter_blocks(region):
        if fm.get("__yaml_error__"):
            continue
        if str(fm.get("type") or "").strip().casefold() != "event":
            continue
        block_id = str(fm.get("id") or "").strip()
        if block_id:
            ids.append(block_id)
    return ids


def format_distill_retry_guidance(
    errors: tuple[str, ...] | list[str],
    previous_output: str = "",
) -> str:
    """Actionable Fix hints for Worker 1 Distill validation retries."""
    joined = "\n".join(errors)
    hints: list[str] = []
    lower = joined.casefold()

    if "no yaml frontmatter" in lower or "missing ## distill" in lower:
        hints.append(
            "Re-emit ## Distill with full schema fences (see EXAMPLE below): "
            "event needs entity/predicate/participants/valid_from/valid_to/"
            "confidence/status/sources/related."
        )
    if "week event id" in lower:
        available = _event_ids_from_previous(previous_output)
        avail_bit = (
            f" Available event ids: {', '.join(available)}."
            if available
            else " Add a bare event id from this Distill’s event blocks."
        )
        hints.append(
            "Non-event related must include a bare week event id (not only mem-…)."
            + avail_bit
        )
    if "event missing" in lower or "participants must be" in lower:
        hints.append(
            "Fill required event fields: entity, predicate, participants "
            "(non-empty; each needs entity; omit role when unsure), "
            "valid_from, valid_to, confidence, status."
        )
    if "hypothesis missing" in lower:
        hints.append(
            "Fill required hypothesis fields: entity, valid_from, confidence, status."
        )
    if "procedure missing" in lower:
        hints.append("Fill required procedure fields: confidence, status.")
    if "conflict missing" in lower:
        hints.append(
            "Fill required conflict fields: confidence, status "
            "(plus id/type/sources/related)."
        )
    if "do not match" in lower or "body cites" in lower:
        hints.append(
            "Align event body [N] cites with related '[N] mem-…' markers (same multiset)."
        )
    if "contiguous" in lower:
        hints.append("Renumber event cites to continuous week-global [1]…[N].")

    if not hints:
        hints.append("Fix ONLY the listed validator errors; re-emit full ## Distill.")

    lines = ["Fix hints:"] + [f"- {h}" for h in hints]
    lines.append(
        "Non-events: related must include ≥1 bare event id from this Distill."
    )
    lines.append(DISTILL_SHAPE_EXAMPLE)
    return "\n".join(lines)


def _hermes_home() -> Path:
    return get_hermes_home()


def _staging_daily() -> Path:
    return daily_staging_dir(_hermes_home())


def _staging_weekly() -> Path:
    return _hermes_home() / "memories" / "staging" / "weekly"


def _state_file() -> Path:
    return _hermes_home() / "memories" / "staging" / ".weekly-state.json"


def _log_file() -> Path:
    return _hermes_home() / "logs" / "memory-weekly.log"


def _log(msg: str) -> None:
    logger.info(msg)
    try:
        path = _log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except OSError:
        pass


def _load_state() -> dict[str, Any]:
    path = _state_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _presentation_state(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get("presentation")
    if not isinstance(raw, dict):
        raw = {}
        state["presentation"] = raw
    completed = raw.get("completed_weeks")
    if not isinstance(completed, list):
        raw["completed_weeks"] = []
    tidy_done = raw.get("tidy_completed_weeks")
    if not isinstance(tidy_done, list):
        raw["tidy_completed_weeks"] = []
    return raw


def _week_open_marks(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return mutable top-level week_open_marks map (create if missing)."""
    raw = state.get("week_open_marks")
    if not isinstance(raw, dict):
        raw = {}
        state["week_open_marks"] = raw
    return raw


def ensure_week_open_mark(
    state: dict[str, Any], week_key: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Idempotent: status=open, set opened_at if new. Returns the mark dict."""
    marks = _week_open_marks(state)
    existing = marks.get(week_key)
    if isinstance(existing, dict):
        existing["status"] = "open"
        marks[week_key] = existing
        return existing
    stamp = (now or _now()).isoformat()
    mark: dict[str, Any] = {
        "status": "open",
        "opened_at": stamp,
        "closed_at": None,
        "ask_pending": False,
        "ask_resolved": None,
        "generate_in_flight": False,
        "reorganise_in_flight": False,
    }
    marks[week_key] = mark
    return mark


def mark_week_closed_in_state(
    state: dict[str, Any],
    week_key: str,
    *,
    ask_pending: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Set status=closed, closed_at; clear ask_pending (chat A/B ask removed)."""
    marks = _week_open_marks(state)
    mark = marks.get(week_key)
    if not isinstance(mark, dict):
        mark = ensure_week_open_mark(state, week_key, now=now)
    stamp = (now or _now()).isoformat()
    mark["status"] = "closed"
    mark["closed_at"] = stamp
    # Chat overdue A/B ask is retired; never leave ask_pending sticky on close.
    mark["ask_pending"] = False
    mark["generate_in_flight"] = False
    mark["reorganise_in_flight"] = False
    marks[week_key] = mark
    return mark


def resolve_week_ask(
    state: dict[str, Any],
    week_key: str,
    resolution: str,  # "reopen" | "keep_closed"
) -> dict[str, Any]:
    """Clear ask_pending; set ask_resolved; if reopen leave caller to flip status open."""
    marks = _week_open_marks(state)
    mark = marks.get(week_key)
    if not isinstance(mark, dict):
        mark = ensure_week_open_mark(state, week_key)
    mark["ask_pending"] = False
    mark["ask_resolved"] = resolution
    marks[week_key] = mark
    return mark


def _current_iso_week(today: date | None = None) -> tuple[int, int]:
    base = today or hermes_local_today()
    iso = base.isocalendar()
    return iso.year, iso.week


def _week_key(year: int, week: int) -> str:
    return f"{year}-W{week:02d}"


def _parse_week_key(value: str) -> tuple[int, int] | None:
    match = WEEK_KEY_RE.match(value.strip())
    if not match:
        return None
    year = int(match.group("year"))
    week = int(match.group("week"))
    if week < 1 or week > 53:
        return None
    return year, week


def _collect_daily_by_week() -> dict[tuple[int, int], list[Path]]:
    daily = _staging_daily()
    by_week: dict[tuple[int, int], list[Path]] = {}

    for path in iter_daily_staging_files(daily):
        try:
            item_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        iso = item_date.isocalendar()
        key = (iso.year, iso.week)
        by_week.setdefault(key, []).append(path)
    return by_week


def _digest_fingerprint_for_files(files: list[Path]) -> str:
    """Stable hash of daily staging names + file bytes for a week (mtime ignored)."""
    parts: list[bytes] = []
    for path in sorted(files, key=lambda p: p.name):
        try:
            data = path.read_bytes()
            parts.append(f"{path.name}\n".encode("utf-8") + data)
        except OSError:
            parts.append(f"{path.name}:missing".encode("utf-8"))
    joined = b"\n".join(parts)
    return hashlib.sha256(joined).hexdigest()


def _digest_fingerprint_map(presentation: dict[str, Any]) -> dict[str, str]:
    raw = presentation.get("digest_fingerprints")
    if not isinstance(raw, dict):
        raw = {}
        presentation["digest_fingerprints"] = raw
    return raw


def _store_digest_fingerprint(
    presentation: dict[str, Any], week_key: str, fingerprint: str
) -> None:
    _digest_fingerprint_map(presentation)[week_key] = fingerprint


def digest_staleness(
    week_key: str | None = None, today: date | None = None
) -> dict[str, Any]:
    """Compare current daily digest fingerprint to the last draft-generate store.

    Blank ``week_key`` resolves to the current ISO week. No dailies → empty-digest
    state (not stale). Missing prior fingerprint with digests present → stale.
    ``has_weekly_file`` is the canonical ``YYYY-Www.md`` on disk so Re-scan can
    generate a missing schema without treating that as a fingerprint skip.
    """
    if week_key:
        parsed = _parse_week_key(week_key)
        if parsed is None:
            return {
                "outcome": "bad_week",
                "week": week_key,
                "stale": False,
                "empty_digests": False,
                "has_weekly_file": False,
                "fingerprint": "",
                "last_fingerprint": None,
            }
        year, week = parsed
        key = week_key
    else:
        year, week = _current_iso_week(today)
        key = _week_key(year, week)

    has_weekly_file = _weekly_path(year, week).exists()
    files = _usable_daily_files(_daily_files_for_week(year, week))
    if not files:
        state = _load_state()
        presentation = _presentation_state(state)
        last = _digest_fingerprint_map(presentation).get(key)
        return {
            "outcome": "ok",
            "week": key,
            "stale": False,
            "empty_digests": True,
            "has_weekly_file": has_weekly_file,
            "fingerprint": "",
            "last_fingerprint": last,
        }

    fingerprint = _digest_fingerprint_for_files(files)
    state = _load_state()
    presentation = _presentation_state(state)
    last = _digest_fingerprint_map(presentation).get(key)
    return {
        "outcome": "ok",
        "week": key,
        "stale": last != fingerprint,
        "empty_digests": False,
        "has_weekly_file": has_weekly_file,
        "fingerprint": fingerprint,
        "last_fingerprint": last,
    }


def _weeks_needing_report(today: date | None = None) -> list[tuple[int, int]]:
    """Complete ISO weeks with daily staging but no closed weekly review yet.

    A week that already has ``week_status: reviewed`` (or legacy
    ``YYYY-Www reviewed.md``) is never queued — even if a draft is
    missing/invalid. Closed weeks must not be regenerated by backlog /
    plugin_load.
    """
    current = eligibility_iso_week(today)
    by_week = _collect_daily_by_week()
    missing: list[tuple[int, int]] = []

    for week_tuple in sorted(by_week):
        if week_tuple >= current:
            continue
        year, week = week_tuple
        files = _usable_daily_files(by_week[week_tuple])
        if not files:
            continue
        # Hard stop: closed week → never regenerate.
        if week_blocks_backlog_regenerate(_hermes_home(), year, week):
            continue
        draft = _weekly_path(year, week)
        if draft.exists():
            try:
                text = draft.read_text(encoding="utf-8")
            except OSError:
                text = ""
            if text.strip() and _weekly_content_looks_valid(text.strip()):
                continue
        missing.append(week_tuple)

    return missing


def _weekly_content_looks_valid(content: str) -> bool:
    """True when weekly MD has Distill shape and is not an error/fallback stub."""
    if not content.strip():
        return False
    lowered = content.casefold()
    for marker in WEEKLY_ERROR_MARKERS:
        if marker in lowered:
            return False
    if "automatic llm consolidation was not completed" in lowered:
        return False
    if not _DISTILL_HEADER_RE.search(content):
        return False
    region = _distill_region_text(content)
    # At least one YAML frontmatter fence under ## Distill
    return "---" in region


def _collect_weekly_by_week() -> dict[tuple[int, int], Path]:
    """Map ISO week → draft weekly path (``YYYY-Www.md`` only)."""
    weekly = _staging_weekly()
    by_week: dict[tuple[int, int], Path] = {}
    if not weekly.is_dir():
        return by_week

    for path in sorted(weekly.glob("*.md")):
        week_tuple = _parse_week_key(path.stem)
        if week_tuple is None:
            continue
        by_week[week_tuple] = path
    return by_week


def _resolve_weekly_read_path(week_key: str) -> Path | None:
    """Draft if present, else reviewed — for reads during review/tidy."""
    parsed = _parse_week_key(week_key)
    if parsed is None:
        return None
    year, week = parsed
    draft = _weekly_path(year, week)
    if draft.exists():
        return draft
    reviewed = weekly_reviewed_path(_hermes_home(), year, week)
    return reviewed if reviewed.exists() else None


def _tidy_tag(week_key: str, presentation: dict[str, Any]) -> str:
    completed = {str(k) for k in presentation.get("tidy_completed_weeks") or []}
    pending = str(presentation.get("tidy_pending_week") or "")
    if week_key in completed:
        return "done"
    if pending == week_key:
        return "pending"
    return ""


def _weeks_needing_presentation(
    today: date | None = None,
    state: dict[str, Any] | None = None,
) -> list[str]:
    """Complete weekly review files that have not been skipped/completed."""
    current = eligibility_iso_week(today)
    state = state if state is not None else _load_state()
    presentation = _presentation_state(state)
    completed = {str(key) for key in presentation.get("completed_weeks", [])}
    pending: list[str] = []

    for week_tuple in sorted(_collect_weekly_by_week()):
        if week_tuple >= current:
            continue
        key = _week_key(*week_tuple)
        year, week = week_tuple
        if key in completed:
            # Completed + still open (reopened / draft without reviewed status)
            # stays in presentation queue; fully reviewed weeks are skipped.
            if not week_is_reviewed(_hermes_home(), year, week):
                pending.append(key)
            continue
        pending.append(key)

    return pending


def _current_week_key(today: date | None = None) -> str | None:
    """Current ISO week key, but only when its weekly review file exists."""
    year, week = _current_iso_week(today)
    if _weekly_path(year, week).exists():
        return _week_key(year, week)
    return None


def _weeks_status_rows(
    today: date | None = None,
    state: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Filesystem weekly status rows for ``/weekly show`` / UI list.

    Status from document ``week_status`` (``pending`` | ``reviewed``).
    Filename is always ``YYYY-Www.md`` (atomic week file).
    """
    state = state if state is not None else _load_state()
    presentation = _presentation_state(state)

    files_by_key: dict[str, str] = {}
    weekly_dir = _staging_weekly()
    if weekly_dir.is_dir():
        for path in sorted(weekly_dir.glob("*.md")):
            parsed = _parse_week_key(path.stem.replace(" reviewed", ""))
            if parsed is None:
                parsed = _parse_week_key(path.stem)
            if parsed is None:
                continue
            key = _week_key(*parsed)
            # Prefer canonical name when both somehow exist (migrate should remove).
            if key not in files_by_key or not path.stem.endswith(" reviewed"):
                files_by_key[key] = f"{key}.md"

    year, week = _current_iso_week(today)
    current_key = _week_key(year, week)
    all_keys = set(files_by_key) | {current_key}

    rows: list[dict[str, str]] = []
    home = _hermes_home()
    for key in all_keys:
        parsed = _parse_week_key(key)
        filename = files_by_key.get(key, f"{key}.md")
        if parsed is not None and week_is_reviewed(home, *parsed):
            status = "reviewed"
        else:
            status = "pending"
            path = _weekly_path(*parsed) if parsed else None
            if path and path.exists():
                st = read_week_status(path)
                if st == WEEK_STATUS_REVIEWED:
                    status = "reviewed"
        row: dict[str, str] = {
            "week": key,
            "status": status,
            "filename": filename,
        }
        mark = _week_open_marks(state).get(key)
        if isinstance(mark, dict):
            row["generate_in_flight"] = (
                "true" if mark.get("generate_in_flight") else "false"
            )
            row["reorganise_in_flight"] = (
                "true" if mark.get("reorganise_in_flight") else "false"
            )
        else:
            row["generate_in_flight"] = "false"
            row["reorganise_in_flight"] = "false"
        if status == "reviewed":
            tidy = _tidy_tag(key, presentation)
            if tidy:
                row["tidy"] = tidy
        rows.append(row)

    status_rank = {"pending": 0, "reviewed": 1}
    rows.sort(key=lambda row: (status_rank.get(row["status"], 9), row["week"]))
    return rows


def _weeks_for_show(
    today: date | None = None,
    state: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Weekly files for ``/weekly show`` (alias of :func:`_weeks_status_rows`)."""
    return _weeks_status_rows(today=today, state=state)


def _week_reviewable(
    week_key: str,
    *,
    include_current: bool,
    today: date | None = None,
    state: dict[str, Any] | None = None,
) -> Path | None:
    """Path of a reviewable weekly file, or ``None``.

    A week is reviewable when it parses, its file exists and it has not been
    completed. ``include_current=False`` keeps the automatic past-only filter;
    manual review passes ``True`` so the in-progress week can be reviewed early.
    """
    week_tuple = _parse_week_key(week_key)
    if week_tuple is None:
        return None
    if not include_current and week_tuple >= eligibility_iso_week(today):
        return None
    path = _weekly_path(*week_tuple)
    return path if path.exists() else None


def _resolve_manual_review_week(
    week_key: str | None = None,
    today: date | None = None,
    state: dict[str, Any] | None = None,
) -> tuple[str, Path] | None:
    """Pick the week a manual ``/weekly review`` should open.

    Order: explicit ``week_key`` when given, else the current week (if its file
    exists and is not completed), else the oldest past week awaiting approval.
    """
    state = state if state is not None else _load_state()
    if week_key:
        path = _week_reviewable(week_key, include_current=True, today=today, state=state)
        return (week_key, path) if path is not None else None

    current = _current_week_key(today)
    if current is not None:
        path = _week_reviewable(current, include_current=True, today=today, state=state)
        if path is not None:
            return current, path

    pending = _weeks_needing_presentation(today=today, state=state)
    if pending:
        key = pending[0]
        week_tuple = _parse_week_key(key)
        if week_tuple is not None:
            return key, _weekly_path(*week_tuple)
    return None


def _session_presented_map(presentation: dict[str, Any]) -> dict[str, str]:
    raw = presentation.get("session_auto_presented")
    if not isinstance(raw, dict):
        raw = {}
        presentation["session_auto_presented"] = raw
    return raw


def _clear_snooze_tracking(presentation: dict[str, Any]) -> None:
    presentation.pop("snooze_until", None)
    presentation.pop("snoozed_at", None)
    presentation.pop("snooze_session_id", None)
    presentation.pop("snooze_week", None)


def _record_presentation_snooze(
    presentation: dict[str, Any],
    week_key: str,
    *,
    session_id: str = "",
    seconds: int | None = None,
) -> str:
    """Set snooze fields and return ``snooze_until`` ISO timestamp."""
    now = _now()
    window = seconds if seconds is not None else SNOOZE_SECONDS
    until = (now + timedelta(seconds=window)).isoformat()
    presentation["active_week"] = week_key
    presentation["snoozed_at"] = now.isoformat()
    presentation["snooze_until"] = until
    presentation["snooze_week"] = week_key
    if session_id:
        presentation["snooze_session_id"] = session_id
    else:
        presentation.pop("snooze_session_id", None)
    presentation.pop("hot_promotion_allowed", None)
    presentation.pop("hot_promotion_until", None)
    return until


def _snooze_replay_eligible(
    presentation: dict[str, Any],
    session_id: str,
    pending_key: str,
    now: datetime,
) -> bool:
    if not session_id:
        return False
    if str(presentation.get("snooze_session_id") or "") != session_id:
        return False
    if str(presentation.get("snooze_week") or "") != pending_key:
        return False
    snooze_until = _parse_datetime(presentation.get("snooze_until"))
    if snooze_until is None or snooze_until > now:
        return False
    return True


def _should_auto_present(
    session_id: str,
    pending_key: str,
    presentation: dict[str, Any],
    *,
    force: bool,
    is_first_turn: bool,
    now: datetime | None = None,
) -> bool:
    """Whether ``on_pre_llm_call`` should inject the pending week this turn.

    With a session id, present once per (session, week) — this fires on the first
    user message in any session (new or resumed) after the week rolled into the
    queue. The same session may receive one more auto-present after a snooze
    expires when ``snooze_session_id`` matches. Without a session id, fall back
    to ``is_first_turn`` for hook/test parity. Explicit requests (``force``)
    always present.
    """
    if force:
        return True
    if session_id:
        if _session_presented_map(presentation).get(session_id) != pending_key:
            return True
        return _snooze_replay_eligible(
            presentation, session_id, pending_key, now or _now()
        )
    return is_first_turn


def _mark_session_auto_presented(
    session_id: str,
    week_key: str,
    presentation: dict[str, Any],
) -> None:
    if not session_id:
        return
    _session_presented_map(presentation)[session_id] = week_key


def _finalize_week_close(
    presentation: dict[str, Any],
    week_key: str,
    *,
    state: dict[str, Any] | None = None,
    ask_pending: bool = False,
) -> None:
    """Mark week completed and set week_status:reviewed in place.

    When ``state`` is provided, closes the week mark. ``ask_pending`` is ignored
    (chat A/B overdue ask removed); marks always clear ``ask_pending``.
    """
    completed = presentation.setdefault("completed_weeks", [])
    if not isinstance(completed, list):
        completed = []
        presentation["completed_weeks"] = completed
    if week_key not in completed:
        completed.append(week_key)
    presentation["active_week"] = week_key
    _clear_snooze_tracking(presentation)
    presentation.pop("hot_promotion_allowed", None)
    presentation.pop("hot_promotion_until", None)
    presentation["last_completed_at"] = _now().isoformat()

    reviewed = mark_week_reviewed(_hermes_home(), week_key)
    if reviewed is not None:
        _log(f"weekly marked reviewed {week_key} path={reviewed}")

    if state is not None:
        mark_week_closed_in_state(state, week_key, ask_pending=ask_pending)


def _daily_files_for_week(year: int, week: int) -> list[Path]:
    return _collect_daily_by_week().get((year, week), [])


def _file_has_usable_digest_content(path: Path) -> bool:
    """True when a daily file still has unresolved digest content for weekly generate."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.strip():
        return False
    return bool(_exclude_resolved_blocks(text).strip())


def _usable_daily_files(files: list[Path]) -> list[Path]:
    """Drop empty stubs and approved/rejected-only dailies (mtime ignored)."""
    return [path for path in files if _file_has_usable_digest_content(path)]


def _exclude_resolved_blocks(content: str) -> str:
    """Drop approved/rejected staging blocks so weekly review does not re-propose them."""
    if not content.strip():
        return content

    plugins_root = Path(__file__).resolve().parent.parent.parent
    digest_dir = Path(__file__).resolve().parent.parent / "digest"
    digest_path = digest_dir / "digest.py"
    mod_name = "memory_digest_weekly_filter"
    spec = importlib.util.spec_from_file_location(mod_name, digest_path)
    if spec is None or spec.loader is None:
        return content
    digest = importlib.util.module_from_spec(spec)
    # digest.py does `import operations` from its own directory.
    for path_str in (str(digest_dir), str(plugins_root)):
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    # Register before exec_module so digest.py @dataclass decorators resolve
    # cls.__module__ via sys.modules (see digest_run.py import path).
    sys.modules[mod_name] = digest
    spec.loader.exec_module(digest)

    resolved = {"approved", "rejected", "dropped"}
    kept: list[str] = []
    for _line_no, raw_frontmatter, body in digest._frontmatter_blocks(content):
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("status", "")).strip() in resolved:
            continue
        kept.append(digest._render_digest_block(parsed, body))
    return "\n\n".join(kept) if kept else ""


def _read_daily_bundle(files: list[Path]) -> str:
    chunks: list[str] = []
    remaining = MAX_DAILY_CHARS
    for path in files:
        if remaining <= 0:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _log(f"daily read failed {path}: {exc}")
            continue
        filtered = _exclude_resolved_blocks(text)
        if not filtered.strip():
            continue
        snippet = filtered[:remaining]
        remaining -= len(snippet)
        chunks.append(f"# Source: {path.name}\n\n{snippet}")
    return "\n\n---\n\n".join(chunks)


def _weekly_path(year: int, week: int) -> Path:
    return _staging_weekly() / f"{_week_key(year, week)}.md"


def _commit_weekly_outputs(
    target: Path, content: str, payload: WeeklyReviewPayload, week_key: str
) -> None:
    """Dump YAML before replacing the md so a serializer crash cannot clobber last week's file."""
    yaml_text = weekly_json.dump_yaml(payload)
    del content
    target.parent.mkdir(parents=True, exist_ok=True)
    write_week_status(
        target,
        WEEK_STATUS_PENDING,
        week_key_str=week_key,
        content=yaml_text.rstrip() + "\n",
    )
    weekly_json.write_sidecars(target, payload)


def _build_prompt(
    week_key: str,
    daily_bundle: str,
    *,
    attempt: int = 1,
    errors: tuple[str, ...] = (),
    previous_output: str = "",
) -> str:
    base = (
        "You are the weekly memory consolidation Worker 1 (Distill).\n\n"
        f"Week: {week_key}\n\n"
        f"{WEEKLY_POLICY}\n\n"
        f"{DISTILL_SHAPE_EXAMPLE}\n\n"
        "Return ONLY markdown with a ## Distill section of YAML frontmatter + body "
        "blocks (types: event, hypothesis, procedure, conflict). "
        "Use continuous week-global [1]…[N] cites on events. "
        "Do not write ## Brief. Do not call tools. "
        "Do not claim anything not grounded in the daily sources.\n\n"
        "DAILY STAGING SOURCES:\n"
        f"{daily_bundle}\n"
    )
    if attempt <= 1:
        return base
    error_lines = "\n".join(f"- {err}" for err in errors[:MAX_ERRORS_IN_PROMPT])
    previous = (
        previous_output[:MAX_WEEKLY_RETRY_CHARS]
        if previous_output
        else "(empty response)"
    )
    guidance = format_distill_retry_guidance(errors, previous_output)
    return (
        base
        + f"\n\nVALIDATION FAILED (attempt {attempt} of {MAX_GENERATION_ATTEMPTS}).\n"
        "Your previous output did not pass the Distill validator. Fix ONLY the listed issues.\n"
        "Do not add new facts. Re-emit the full ## Distill markdown.\n\n"
        "Validator errors:\n"
        f"{error_lines}\n\n"
        f"{guidance}\n\n"
        f"Your previous output (truncated to {MAX_WEEKLY_RETRY_CHARS} chars):\n"
        f"{previous}\n"
    )


def _fallback_report(week_key: str, files: list[Path], reason: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    source_list = "\n".join(f"- `{path.name}`" for path in files) or "- None"
    return (
        f"# Weekly distill {week_key}\n\n"
        f"Generated at: {now}\n\n"
        "## Source daily files\n\n"
        f"{source_list}\n"
    )


def _distill_region_text(md_text: str) -> str:
    text = md_text or ""
    match = _DISTILL_HEADER_RE.search(text)
    if not match:
        return ""
    start = match.end()
    next_h = _LEVEL_TWO_HEADER_RE.search(text, start)
    end = next_h.start() if next_h else len(text)
    return text[start:end].strip()


def _parse_distill_blocks_for_normalize(md_text: str) -> list[dict[str, Any]]:
    """Parse Distill YAML fences into ``{frontmatter, body}`` for citation normalize."""
    region = _distill_region_text(md_text)
    if not region:
        # Allow bare YAML blocks if the model omitted the ## Distill header
        region = (md_text or "").strip()
    lines = region.splitlines()
    blocks: list[dict[str, Any]] = []
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
                parsed = {}
            if isinstance(parsed, dict):
                blocks.append({"frontmatter": parsed, "body": ""})
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
            parsed = {}
        if not isinstance(parsed, dict):
            continue
        blocks.append(
            {"frontmatter": parsed, "body": "\n".join(body_lines).strip()}
        )
    return blocks


def _render_yaml_frontmatter(fm: dict[str, Any]) -> str:
    dumped = yaml.safe_dump(
        fm,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).rstrip()
    return dumped


def _render_weekly_distill_document(
    week_key: str,
    blocks: list[dict[str, Any]],
    *,
    brief: str = "",
) -> str:
    """Assemble Distill + Brief (+ Action ledger stub) weekly MD."""
    parts: list[str] = [f"# Weekly distill {week_key}", "", "## Distill", ""]
    for block in blocks:
        fm = block.get("frontmatter")
        if not isinstance(fm, dict):
            fm = {}
        body = str(block.get("body") or "").rstrip()
        parts.append("---")
        parts.append(_render_yaml_frontmatter(fm))
        parts.append("---")
        if body:
            parts.append(body)
        parts.append("")
    parts.extend(["## Brief", ""])
    brief_text = (brief or "").strip()
    if brief_text:
        parts.append(brief_text)
        parts.append("")
    else:
        parts.append("")
    parts.extend(
        [
            "## Action ledger",
            "",
            "| Status | Item | Notes |",
            "| --- | --- | --- |",
            "| — | — | stub |",
            "",
        ]
    )
    return "\n".join(parts).rstrip() + "\n"


def _strip_code_fence(content: str) -> str:
    text = (content or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


# Set by `_generate_weekly_content` when four-part Brief assembly fails; cleared each generate.
_last_brief_error: str | None = None
# Last payload that produced Distill+Brief, so the writer can dump JSON/YAML without a second LLM.
_last_weekly_payload: WeeklyReviewPayload | None = None


def _is_weekly_worker_turn(user_message: Any) -> bool:
    """True when this pre_llm turn is a Worker 1 generate prompt, not user chat."""
    text = str(user_message or "")
    return any(marker in text for marker in _WEEKLY_WORKER_PROMPT_MARKERS)


def _call_weekly_llm(prompt: str, *, purpose: str = "weekly_llm") -> str:
    return run_worker_llm(
        prompt,
        plugin="memory-weekly",
        purpose=purpose,
        platform="cli",
        max_iterations=10,
    )


def _call_weekly_llm_tools(
    prompt: str,
    *,
    purpose: str = "weekly_llm",
    force_tool_name: str,
) -> dict:
    """Forced weekly Worker 1 tool call (submit/patch)."""
    try:
        from . import weekly_tools
    except ImportError:  # pragma: no cover
        import weekly_tools  # type: ignore

    weekly_tools.ensure_weekly_tools_registered()
    return run_worker_llm_tools(
        prompt,
        plugin="memory-weekly",
        purpose=purpose,
        platform="cli",
        max_iterations=2,
        enabled_toolsets=[weekly_tools.WEEKLY_TOOLSET],
        force_tool_name=force_tool_name,
    )


def _prepare_distill_content(
    week_key: str, raw: str
) -> tuple[str, list[str], list[dict[str, Any]], dict[int, str]]:
    """Normalize citations and assemble Distill MD.

    Returns ``(md, prep_errors, blocks, legend)``. On prep failure, blocks/legend
    are empty and ``md`` may be the raw/partial content for retry prompts.
    """
    content = _strip_code_fence(raw)
    if not content:
        return "", ["empty response"], [], {}

    lowered = content.casefold()
    for marker in WEEKLY_ERROR_MARKERS:
        if marker in lowered:
            return content, [f"error marker: {marker}"], [], {}
    if "automatic llm consolidation was not completed" in lowered:
        return content, ["fallback stub shape"], [], {}

    blocks = _parse_distill_blocks_for_normalize(content)
    if not blocks:
        if not _DISTILL_HEADER_RE.search(content):
            return content, ["missing ## Distill section"], [], {}
        return content, ["## Distill has no YAML frontmatter blocks"], [], {}

    normalized, legend = normalize_event_citations(blocks)
    prepared = _render_weekly_distill_document(week_key, normalized)
    return prepared, [], normalized, legend


def _generate_weekly_content(
    week_key: str, files: list[Path], *, reason: str = ""
) -> str | None:
    """Worker 1 → JSON sidecar projection as YAML. No weekly Distill document.

    Distill event fences stay on daily files; duplicating them here would make
    Chronicle parse a second copy.
    """
    global _last_brief_error, _last_weekly_payload
    _last_brief_error = None
    _last_weekly_payload = None
    reason_bit = f" reason={reason}" if reason else ""
    try:
        daily_bundle = _read_daily_bundle(files)
    except Exception as exc:
        _log(f"weekly daily bundle failed {week_key}{reason_bit}: {exc}")
        return None
    if not daily_bundle.strip():
        _log(f"weekly daily bundle empty {week_key}{reason_bit}")
        return None

    _log(
        f"weekly daily bundle ready {week_key} sources={len(files)} "
        f"chars={len(daily_bundle)}{reason_bit}"
    )

    try:
        w1 = run_parallel_worker1(
            week_key,
            files,
            call_llm=_call_weekly_llm,
            call_llm_tools=_call_weekly_llm_tools,
            log=_log,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"weekly worker1 parallel failed {week_key}: {exc}{reason_bit}")
        return None

    payload = w1.payload
    legend = dict(w1.legend) or dict(payload.legend)
    if not legend and not payload.intra_day_thread and not payload.cross_day_thread:
        _log(
            f"weekly worker1 produced no payload {week_key} "
            f"errors={w1.errors[:5]}{reason_bit}"
        )
        return None

    payload = replace(
        payload,
        legend=legend,
        week_key=week_key,
        cross_day_thread=w1.cross_day_thread or payload.cross_day_thread,
        entities=w1.entities or payload.entities,
        conflicts=(),
        hypotheses=(),
    )
    assigned = assign_typed_citations(payload)
    assigned = replace(
        assigned,
        cross_day_thread=payload.cross_day_thread,
        intra_day_thread=payload.intra_day_thread,
        entities=payload.entities,
        week_key=week_key,
        summary=payload.summary,
    )
    _last_weekly_payload = assigned
    return weekly_json.dump_yaml(assigned)


def _run_weekly(reason: str) -> None:
    _log(f"weekly run waiting for lock ({reason})")
    try:
        with _run_lock:
            _log(f"weekly run lock acquired ({reason})")
            try:
                touched = migrate_all_weekly_files(_hermes_home())
                if touched:
                    _log(f"weekly migrate atomic files: {len(touched)} week(s)")
            except Exception as exc:  # noqa: BLE001
                _log(f"weekly migrate before backlog failed: {exc}")
            try:
                plugins = Path(__file__).resolve().parent.parent.parent
                path = Path(__file__).resolve().parent.parent / "retention" / "retention.py"
                spec = importlib.util.spec_from_file_location(
                    "memory_retention_orphan_backlog", path
                )
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    plugins_str = str(plugins)
                    if plugins_str not in sys.path:
                        sys.path.insert(0, plugins_str)
                    spec.loader.exec_module(mod)
                    n = int(mod.purge_orphan_daily_blocks() or 0)
                    if n:
                        _log(
                            f"orphan daily purge removed {n} block(s) "
                            f"before backlog generate"
                        )
            except Exception as exc:
                _log(f"orphan daily purge before backlog failed: {exc}")

            pending = _weeks_needing_report()
            if not pending:
                _log(f"weekly backlog empty ({reason})")
                return

            pending_keys = [_week_key(y, w) for y, w in pending[:MAX_WEEKS_PER_RUN]]
            _log(
                f"weekly backlog {len(pending)} week(s) pending, "
                f"processing {', '.join(pending_keys)} ({reason})"
            )

            state = _load_state()
            generated = 0

            for year, week in pending[:MAX_WEEKS_PER_RUN]:
                key = _week_key(year, week)
                if week_blocks_backlog_regenerate(_hermes_home(), year, week):
                    _log(
                        f"weekly skip {key} week closed/legacy-reviewed "
                        f"(no recreate) ({reason})"
                    )
                    continue
                files = _usable_daily_files(_daily_files_for_week(year, week))
                if not files:
                    _log(f"weekly skip {key} no daily files ({reason})")
                    continue

                target = _weekly_path(year, week)
                _log(
                    f"weekly generation started {key} sources={len(files)} "
                    f"reason={reason}"
                )
                content = _generate_weekly_content(key, files, reason=reason)
                if content is None:
                    _log(f"weekly skip write {key} generation failed ({reason})")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = _last_weekly_payload
                if payload is None:
                    payload = WeeklyReviewPayload(days=(), week_key=key)
                _commit_weekly_outputs(target, content, payload, key)

                state["last_generated_week"] = key
                state["last_generated_at"] = datetime.now(timezone.utc).isoformat()
                state["last_reason"] = reason
                fingerprint = _digest_fingerprint_for_files(files)
                presentation = _presentation_state(state)
                _store_digest_fingerprint(presentation, key, fingerprint)
                ensure_week_open_mark(state, key)
                generated += 1
                remaining = len(pending) - generated
                _log(
                    f"weekly generated {key} path={target} sources={len(files)} "
                    f"reason={reason} backlog_remaining={remaining}"
                )

            if len(pending) > generated:
                state["backlog_pending"] = [_week_key(y, w) for y, w in pending[generated:]]
            else:
                state.pop("backlog_pending", None)

            if generated:
                _save_state(state)
            else:
                _log(f"weekly backlog {len(pending)} week(s) pending but none generated ({reason})")
    except Exception as exc:
        _log(f"weekly run failed ({reason}): {exc}")


def _week_key_from_text(text: str) -> str | None:
    match = re.search(r"\b\d{4}-W\d{2}\b", text)
    return match.group(0) if match else None


def _response_matches(response: str, patterns: tuple[str, ...]) -> bool:
    lowered = response.strip().casefold()
    if not lowered:
        return False
    for pattern in patterns:
        folded = pattern.casefold()
        if lowered == folded or folded in lowered:
            return True
    return False


def _parse_clarify_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _read_week_brief(path: Path) -> str:
    try:
        md = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return extract_brief(md).strip()


def _build_brief_paste_context(
    week_key: str,
    path: Path,
    brief: str,
    *,
    force: bool,
) -> str:
    reason = (
        "The user explicitly asked for weekly memory approval."
        if force
        else "A weekly memory review is pending presentation."
    )
    processed = format_brief_for_chat(brief)
    return (
        f"{reason}\n\n"
        f"Paste the following processed Brief to the user (plain theme titles "
        f"Events, Hypothesis, Conflict, Procedure — no # theme markers). "
        f"Do not drop Hypothesis or Conflict. Do not include Distill YAML or "
        f"frontmatter.\n\n"
        f"{processed}\n\n"
        f"Week key: `{week_key}`\n"
        f"Staging review file: `{path}`\n\n"
        "When the user asks for more detail on a Brief item, you may locate "
        "evidence via: Brief [N] → Distill related \"[N] mem-…\" → daily "
        "staging block body (and sources / session if needed). Do not dump "
        "Distill YAML or frontmatter. Do not invent session content. This is "
        "optional guidance — not a required tool pipeline."
    )


def _build_presentation_context(week_key: str, path: Path, *, force: bool) -> str:
    reason = (
        "The user explicitly asked for staging memory approval."
        if force
        else "A staging memory review is pending presentation."
    )
    return (
        f"{reason}\n\n"
        f"Staging review file: `{path}`\n"
        f"Week key: `{week_key}`\n\n"
        "Before answering the user's current request, read the staging review file "
        "and briefly present the review for approval. Summarize Proposed additions, "
        "Hypotheses awaiting confirmation, and Conflicts. Then call the `clarify` "
        "tool with this exact question and choices:\n\n"
        f"Question: {WEEKLY_REVIEW_QUESTION} for {week_key}\n"
        "Choices:\n"
        "- Review proposed additions now\n"
        "- Check later (1 hour)\n"
        "- Close week (mark complete)\n\n"
        "If the user chooses Review proposed additions now, walk the proposed items "
        "with them one at a time. The hot-promotion window is open for "
        "this review: once the user approves an exact compact §-entry, write it with "
        "the `memory` tool — it lands directly in MEMORY.md / USER.md. Never promote "
        "hypotheses without explicit confirmation. Do not run the weekly worker again.\n"
        "After the walkthrough and recap, call the same staging clarify again to close "
        "the week, or the user may run `/weekly close`."
    )


def _build_generation_pending_context(weeks: list[tuple[int, int]], *, force: bool) -> str:
    key = _week_key(*weeks[0])
    reason = (
        "The user explicitly asked for staging memory approval, but the review file "
        "is not ready yet."
        if force
        else "A staging memory review backlog exists, but the review file is not ready yet."
    )
    return (
        f"{reason}\n\n"
        f"Pending week: `{key}`\n"
        "The memory-weekly plugin should generate the review from daily staging on "
        "its lifecycle hook. Tell the user to check again shortly, or run "
        "`/weekly update` (or open the staging review UI) after the file appears. "
        "Do not invent staging review content."
    )


def _migrate_staging_unlock_keys(presentation: dict[str, Any]) -> None:
    """Move legacy vibe_* unlock keys to staging_* (in-place)."""
    if "staging_unlocked" not in presentation and "vibe_unlocked" in presentation:
        presentation["staging_unlocked"] = bool(presentation.get("vibe_unlocked"))
    if "staging_session_id" not in presentation and presentation.get("vibe_session_id"):
        presentation["staging_session_id"] = presentation.get("vibe_session_id")
    presentation.pop("vibe_unlocked", None)
    presentation.pop("vibe_session_id", None)


def _is_staging_unlocked(
    presentation: dict[str, Any],
    session_id: str = "",
) -> bool:
    """Session-scoped unlock. Orphan unlock without session id is locked.

    Slash unlock uses ``SLASH_STAGING_SESSION``; first real session claim rebinds it.
    Accepts legacy ``vibe_*`` keys once, then migrates to ``staging_*``.
    """
    _migrate_staging_unlock_keys(presentation)
    if not bool(presentation.get("staging_unlocked")):
        return False
    locked_sid = str(presentation.get("staging_session_id") or "").strip()
    sid = str(session_id or "").strip()
    if locked_sid == SLASH_STAGING_SESSION:
        if sid:
            presentation["staging_session_id"] = sid
            return True
        return True
    if not locked_sid or not sid:
        return False
    return locked_sid == sid


def _build_action_pending_context(dig: dict[str, Any]) -> str:
    if str(dig.get("target_kind") or "").strip() == "hot":
        file_name = str(dig.get("action_file") or "MEMORY.md").strip() or "MEMORY.md"
        index = dig.get("action_index")
        label = f"`{file_name}` [{index}]"
        return (
            f"The hot memory summary for {label} was shown.\n\n"
            "Before editing or deleting, call the `clarify` tool with this exact question "
            "and these three choices only (do not add a fourth Other):\n\n"
            f"Question: What should we do with hot memory {label}?\n"
            "Choices:\n"
            f"- {WEEKLY_ACTION_EDIT}\n"
            f"- {WEEKLY_ACTION_DELETE}\n"
            f"- {WEEKLY_ACTION_SOMETHING_ELSE}\n"
        )
    block_id = str(
        dig.get("action_block_id") or dig.get("resolved_mem_id") or "this staging memory"
    ).strip()
    return (
        f"The dig-in plain source for staging memory `{block_id}` was shown.\n\n"
        "Before editing or deleting, call the `clarify` tool with this exact question "
        "and these three choices only (do not add a fourth Other):\n\n"
        f"Question: What should we do with staging memory `{block_id}`?\n"
        "Choices:\n"
        f"- {WEEKLY_ACTION_EDIT}\n"
        f"- {WEEKLY_ACTION_DELETE}\n"
        f"- {WEEKLY_ACTION_SOMETHING_ELSE}\n"
    )


def _build_edit_open_context(block_id: str) -> str:
    return (
        f"The user chose Edit for staging memory `{block_id}`.\n\n"
        "Call the `clarify` tool with an open-ended question (no fixed choices):\n\n"
        f"Question: {WEEKLY_EDIT_OPEN_QUESTION}\n"
    )


def _build_edit_confirm_context(block_id: str, draft: str) -> str:
    return (
        f"A staging edit draft is ready for `{block_id}`.\n\n"
        "Show the draft to the user, then call the `clarify` tool with:\n\n"
        f"Question: {WEEKLY_EDIT_CONFIRM_QUESTION}\n"
        "Choices:\n"
        "- A · Agree\n"
        "- B · Other thought\n\n"
        f"Draft body:\n{draft}\n"
    )


def _build_delete_confirm_context(block_id: str) -> str:
    return (
        f"The user chose Delete for staging memory `{block_id}`.\n\n"
        "Call the `clarify` tool with:\n\n"
        f"Question: {WEEKLY_DELETE_QUESTION}\n"
        "Choices:\n"
        "- Yes\n"
        "- Later\n"
    )


def _build_hot_delete_confirm_context(*, file: str = "MEMORY.md", index: int | None = None) -> str:
    label = f"`{file}`" if index is None else f"`{file}` [{index}]"
    return (
        f"The user chose Delete for hot memory {label}.\n\n"
        "Call the `clarify` tool with:\n\n"
        f"Question: {WEEKLY_HOT_DELETE_QUESTION}\n"
        "Choices:\n"
        "- Yes\n"
        "- Later\n"
    )


def _build_something_else_open_context(block_id: str) -> str:
    return (
        f"The user chose Something else… for staging memory `{block_id}`.\n\n"
        "Call the `clarify` tool with an open-ended question (no fixed choices):\n\n"
        f"Question: {WEEKLY_SOMETHING_ELSE_OPEN_QUESTION}\n"
        "Follow the user's free-text instruction for this staging memory. "
        "Do not automatically Edit or Delete.\n"
    )


def _build_recall_offer_context() -> str:
    return (
        "A staging Edit/Delete was applied.\n\n"
        "Offer undo via the `clarify` tool:\n\n"
        f"Question: {WEEKLY_RECALL_QUESTION}\n"
        "Choices:\n"
        "- Yes\n"
        "- No\n"
    )


def _action_block_id(dig: dict[str, Any] | None) -> str:
    if not dig:
        return ""
    return str(dig.get("action_block_id") or dig.get("resolved_mem_id") or "").strip()


def _is_action_menu_question(question: str) -> bool:
    qfold = question.casefold()
    return (
        "what should we do with staging memory" in qfold
        or "what should we do with hot memory" in qfold
    )


def _push_recall_snapshot(*, op: str, block_id: str, before: dict[str, Any] | None) -> dict[str, Any]:
    before = before or {}
    return push_staging_recall(
        {
            "op": op,
            "block_id": block_id,
            "before_body": before.get("body"),
            "before_status": str(before.get("status") or "candidate"),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )


def _open_hot_promotion_window(
    presentation: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Open the weekly hot-promotion window (presentation flag for UI / dig-in)."""
    ts = now or _now()
    presentation["hot_promotion_allowed"] = True
    presentation["hot_promotion_until"] = (
        ts + timedelta(seconds=HOT_PROMOTION_SECONDS)
    ).isoformat()


def _hot_target_fields(dig: dict[str, Any]) -> tuple[str, int | None, str]:
    file_name = str(dig.get("action_file") or "MEMORY.md").strip() or "MEMORY.md"
    raw_index = dig.get("action_index")
    index: int | None
    if isinstance(raw_index, int) and not isinstance(raw_index, bool):
        index = raw_index
    else:
        index = None
    before = str(dig.get("action_before") or "")
    return file_name, index, before


def _apply_hot_write_with_window(file_name: str, entries: list[str]) -> None:
    state = _load_state()
    presentation = _presentation_state(state)
    _open_hot_promotion_window(presentation)
    _save_state(state)
    hot_health.write_hot_entries(file_name, entries)


def _handle_staging_action_clarify(
    *,
    question: str,
    response: str,
    session_id: str = "",
) -> bool:
    """Route Edit/Delete/Something else / recall clarifies. True if handled."""
    dig = get_dig_in()
    if dig is None:
        return False

    phase = str(dig.get("action_phase") or "").strip()
    block_id = _action_block_id(dig)
    qfold = question.casefold()
    is_hot = str(dig.get("target_kind") or "").strip() == "hot"
    hot_file, hot_index, hot_before = _hot_target_fields(dig)

    # Recall offer / confirm
    if WEEKLY_RECALL_QUESTION.casefold() in qfold or dig.get("recall_offer"):
        if WEEKLY_RECALL_QUESTION.casefold() in qfold:
            if _response_matches(response, WEEKLY_RECALL_YES_RESPONSES):
                if is_hot:
                    # Hot recall is UI-only; chat path no longer restores.
                    _log(
                        f"weekly hot recall skipped (ui-only) "
                        f"file={hot_file} session={session_id or '-'}"
                    )
                    set_dig_in_progress(recall_offer=False, clear_action=False)
                    return True
                result = pop_staging_recall()
                set_dig_in_progress(recall_offer=False, clear_action=False)
                if not result.get("ok"):
                    _log(
                        f"weekly staging recall failed "
                        f"err={result.get('error')} session={session_id or '-'}"
                    )
                else:
                    _log(f"weekly staging recall restored session={session_id or '-'}")
                return True
            if _response_matches(response, WEEKLY_RECALL_NO_RESPONSES):
                set_dig_in_progress(recall_offer=False)
                _log(f"weekly staging recall declined session={session_id or '-'}")
                return True
            return True

    # Edit open-ended → store draft
    if phase == "edit_open" or WEEKLY_EDIT_OPEN_QUESTION.casefold() in qfold:
        draft = response.strip()
        if not draft:
            set_dig_in_progress(action_phase="edit_open", edit_draft="")
            return True
        set_dig_in_progress(
            action_pending=False,
            action_phase="edit_confirm",
            edit_draft=draft,
            action_block_id=block_id or None,
        )
        _log(f"weekly edit draft captured block={block_id} session={session_id or '-'}")
        return True

    # Edit confirm Agree / Other
    if phase == "edit_confirm" or WEEKLY_EDIT_CONFIRM_QUESTION.casefold() in qfold:
        if _response_matches(response, WEEKLY_EDIT_AGREE_RESPONSES):
            draft = str(dig.get("edit_draft") or "").strip()
            if is_hot:
                if not draft or hot_index is None:
                    set_dig_in_progress(action_phase="edit_open", edit_draft="")
                    _log(
                        f"weekly hot edit agree missing draft/index "
                        f"session={session_id or '-'}"
                    )
                    return True
                entries = hot_health._split_entries(hot_health._read_hot_file(hot_file))
                if (
                    hot_index < 0
                    or hot_index >= len(entries)
                    or entries[hot_index] != hot_before
                ):
                    _log(
                        f"weekly hot edit abort mismatch file={hot_file} "
                        f"index={hot_index} session={session_id or '-'}"
                    )
                    set_dig_in_progress(clear_action=True)
                    return True
                entries[hot_index] = draft
                _apply_hot_write_with_window(hot_file, entries)
                set_dig_in_progress(clear_action=True, recall_offer=False)
                _log(
                    f"weekly hot edit applied file={hot_file} "
                    f"index={hot_index} session={session_id or '-'}"
                )
                return True
            if not draft or not block_id:
                set_dig_in_progress(action_phase="edit_open", edit_draft="")
                _log(f"weekly edit agree missing draft/block session={session_id or '-'}")
                return True
            before = find_staging_block(block_id)
            status = str((before or {}).get("status") or "candidate")
            push_result = _push_recall_snapshot(op="edit", block_id=block_id, before=before)
            ok = patch_daily_block_status(
                get_hermes_home(),
                block_id,
                status=status,
                timestamp_field="updated_at",
                body=draft,
            )
            if ok:
                offer = bool(push_result.get("ok"))
                set_dig_in_progress(clear_action=True, recall_offer=offer)
                if not offer:
                    _log(
                        f"weekly edit applied without recall "
                        f"err={push_result.get('error')} "
                        f"block={block_id} session={session_id or '-'}"
                    )
                else:
                    _log(f"weekly edit applied block={block_id} session={session_id or '-'}")
            else:
                set_dig_in_progress(action_phase="edit_open", edit_draft="")
                _log(f"weekly edit apply failed block={block_id} session={session_id or '-'}")
            return True
        if _response_matches(response, WEEKLY_EDIT_OTHER_RESPONSES):
            set_dig_in_progress(
                action_phase="edit_open",
                edit_draft="",
                action_pending=False,
                action_block_id=block_id or None,
            )
            _log(f"weekly edit other-thought loop block={block_id} session={session_id or '-'}")
            return True
        return True

    # Delete confirm Yes / Later (staging or hot question)
    if (
        phase == "delete_confirm"
        or WEEKLY_DELETE_QUESTION.casefold() in qfold
        or WEEKLY_HOT_DELETE_QUESTION.casefold() in qfold
    ):
        if _response_matches(response, WEEKLY_DELETE_YES_RESPONSES):
            if is_hot:
                if hot_index is None:
                    set_dig_in_progress(clear_action=True)
                    return True
                entries = hot_health._split_entries(hot_health._read_hot_file(hot_file))
                if (
                    hot_index < 0
                    or hot_index >= len(entries)
                    or entries[hot_index] != hot_before
                ):
                    _log(
                        f"weekly hot delete abort mismatch file={hot_file} "
                        f"index={hot_index} session={session_id or '-'}"
                    )
                    set_dig_in_progress(clear_action=True)
                    return True
                entries.pop(hot_index)
                _apply_hot_write_with_window(hot_file, entries)
                set_dig_in_progress(clear_action=True, recall_offer=False)
                _log(
                    f"weekly hot delete applied file={hot_file} "
                    f"index={hot_index} session={session_id or '-'}"
                )
                return True
            if not block_id:
                set_dig_in_progress(clear_action=True)
                return True
            before = find_staging_block(block_id)
            push_result = _push_recall_snapshot(op="delete", block_id=block_id, before=before)
            ok = patch_daily_block_status(
                get_hermes_home(),
                block_id,
                status="rejected",
                timestamp_field="discarded_at",
            )
            if ok:
                offer = bool(push_result.get("ok"))
                set_dig_in_progress(clear_action=True, recall_offer=offer)
                if not offer:
                    _log(
                        f"weekly delete applied without recall "
                        f"err={push_result.get('error')} "
                        f"block={block_id} session={session_id or '-'}"
                    )
                else:
                    _log(f"weekly delete applied block={block_id} session={session_id or '-'}")
            else:
                set_dig_in_progress(clear_action=True)
                _log(f"weekly delete apply failed block={block_id} session={session_id or '-'}")
            return True
        if _response_matches(response, WEEKLY_DELETE_LATER_RESPONSES):
            set_dig_in_progress(clear_action=True)
            _log(f"weekly delete later cleared block={block_id} session={session_id or '-'}")
            return True
        return True

    # Something else open-ended free-text — no auto write
    if phase == "other_open" or WEEKLY_SOMETHING_ELSE_OPEN_QUESTION.casefold() in qfold:
        # Avoid treating the bare choice label as the instruction.
        if _response_matches(response, (WEEKLY_ACTION_SOMETHING_ELSE,)):
            set_dig_in_progress(
                action_pending=False,
                action_phase="other_open",
                action_block_id=block_id or None,
            )
            return True
        set_dig_in_progress(clear_action=True)
        _log(
            f"weekly something-else handled (no auto write) "
            f"block={block_id} session={session_id or '-'}"
        )
        return True

    # Initial action menu: Edit | Delete | Something else…
    if dig.get("action_pending") and (
        _is_action_menu_question(question) or phase in ("", "await_action")
    ):
        if _response_matches(response, (WEEKLY_ACTION_EDIT,)):
            set_dig_in_progress(
                action_pending=False,
                action_phase="edit_open",
                action_block_id=block_id or None,
                edit_draft="",
            )
            _log(f"weekly action edit opened block={block_id} session={session_id or '-'}")
            return True
        if _response_matches(response, (WEEKLY_ACTION_DELETE,)):
            set_dig_in_progress(
                action_pending=False,
                action_phase="delete_confirm",
                action_block_id=block_id or None,
            )
            _log(f"weekly action delete confirm block={block_id} session={session_id or '-'}")
            return True
        if _response_matches(response, (WEEKLY_ACTION_SOMETHING_ELSE,)):
            set_dig_in_progress(
                action_pending=False,
                action_phase="other_open",
                action_block_id=block_id or None,
            )
            _log(f"weekly action something-else opened block={block_id} session={session_id or '-'}")
            return True
        return False

    return False


def on_pre_llm_call(
    user_message: Any = "",
    is_first_turn: bool = False,
    session_id: str = "",
    platform: str = "",
    **_: Any,
) -> dict[str, str] | None:
    # Worker 1/2 internal generate turns already carry a complete prompt.
    # Chat Brief/clarify inject here pollutes Distill and causes retry storms.
    if _is_weekly_worker_turn(user_message) or _in_weekly_worker_llm():
        _log(
            f"weekly presentation skipped (worker turn) "
            f"session={session_id or '-'}"
        )
        return None
    if (
        str(session_id or "").startswith("cron_")
        or os.environ.get("HERMES_CRON_SESSION") == "1"
        or str(platform or "").casefold() == "cron"
    ):
        _log(f"weekly presentation skipped (cron) session={session_id or '-'}")
        return None

    # Chat NL force-review and hot-retrieval cues removed; Brief only via unlock.
    force = False
    now = _now()
    state = _load_state()
    presentation = _presentation_state(state)
    prior_staging_sid = str(presentation.get("staging_session_id") or "")
    staging_unlocked = _is_staging_unlocked(presentation, session_id)
    if staging_unlocked and str(presentation.get("staging_session_id") or "") != prior_staging_sid:
        _save_state(state)

    # Post dig-in: offer Edit | Delete | Something else… (exactly three).
    if staging_unlocked:
        dig = get_dig_in()
        if dig:
            block_id = _action_block_id(dig)
            phase = str(dig.get("action_phase") or "").strip()
            if dig.get("recall_offer"):
                _log(f"weekly recall offer injected session={session_id or '-'}")
                return {"context": _build_recall_offer_context()}
            is_hot = str(dig.get("target_kind") or "").strip() == "hot"
            hot_file, hot_index, _hot_before = _hot_target_fields(dig)
            hot_label = (
                f"{hot_file} [{hot_index}]" if hot_index is not None else hot_file
            )
            if phase == "edit_open" and (block_id or is_hot):
                return {"context": _build_edit_open_context(block_id or hot_label)}
            if phase == "edit_confirm" and (block_id or is_hot):
                draft = str(dig.get("edit_draft") or "").strip()
                return {
                    "context": _build_edit_confirm_context(block_id or hot_label, draft)
                }
            if phase == "delete_confirm" and (block_id or is_hot):
                if is_hot:
                    return {
                        "context": _build_hot_delete_confirm_context(
                            file=hot_file, index=hot_index
                        )
                    }
                return {"context": _build_delete_confirm_context(block_id)}
            if phase == "other_open" and (block_id or is_hot):
                return {
                    "context": _build_something_else_open_context(block_id or hot_label)
                }
            if dig.get("action_pending") and (
                dig.get("action_block_id")
                or str(dig.get("target_kind") or "").strip() == "hot"
            ):
                summary = str(presentation.pop("hot_summary_inject", "") or "").strip()
                if summary:
                    _save_state(state)
                _log(
                    f"weekly action pending injected "
                    f"block={dig.get('action_block_id') or dig.get('action_file')} "
                    f"session={session_id or '-'}"
                )
                action_ctx = _build_action_pending_context(dig)
                if summary:
                    return {"context": f"{summary}\n\n{action_ctx}"}
                return {"context": action_ctx}

    pending = _weeks_needing_presentation(state=state)

    if pending:
        # Auto-present Brief/legacy clarify only after unlock (slash / UI).
        if not staging_unlocked:
            return None

        key = pending[0]
        if not _should_auto_present(
            session_id,
            key,
            presentation,
            force=force,
            is_first_turn=is_first_turn,
            now=now,
        ):
            return None

        snooze_until = _parse_datetime(presentation.get("snooze_until"))
        if not force and snooze_until is not None and snooze_until > now:
            _log(f"weekly presentation snoozed until {snooze_until.isoformat()}")
            return None

        week_tuple = _parse_week_key(key)
        if week_tuple is None:
            return None
        path = _weekly_path(*week_tuple)
        replay = (
            bool(session_id)
            and _session_presented_map(presentation).get(session_id) == key
            and _snooze_replay_eligible(presentation, session_id, key, now)
        )
        presentation["active_week"] = key
        presentation["last_presented_at"] = now.isoformat()
        if force:
            _clear_snooze_tracking(presentation)
        elif replay:
            presentation.pop("snooze_session_id", None)
            presentation.pop("snooze_week", None)
        _mark_session_auto_presented(session_id, key, presentation)
        brief = _read_week_brief(path)
        if brief:
            _save_state(state)
            _log(
                f"weekly brief paste injected {key} force={force} "
                f"session={session_id}"
            )
            return {
                "context": _build_brief_paste_context(
                    key, path, brief, force=force
                )
            }
        _save_state(state)
        _log(f"weekly presentation injected {key} force={force} session={session_id}")
        return {"context": _build_presentation_context(key, path, force=force)}

    if not (is_first_turn or force):
        return None

    generation_pending = _weeks_needing_report()
    if force and generation_pending:
        return {"context": _build_generation_pending_context(generation_pending, force=True)}

    return None


def on_post_tool_call(
    tool_name: str = "",
    args: dict[str, Any] | None = None,
    result: Any = None,
    status: str = "",
    session_id: str = "",
    **_: Any,
) -> None:
    if tool_name != "clarify" or status == "error":
        return

    args = args if isinstance(args, dict) else {}
    payload = _parse_clarify_result(result)
    question = str(payload.get("question") or args.get("question") or "")
    response = str(payload.get("user_response") or "").strip()
    if not response:
        return

    # Staging Edit / Delete / Something else… / Recall (Task 4b + 5).
    if _handle_staging_action_clarify(
        question=question,
        response=response,
        session_id=session_id,
    ):
        return

    if WEEKLY_REVIEW_QUESTION.casefold() not in question.casefold():
        return

    state = _load_state()
    presentation = _presentation_state(state)
    active_week = _week_key_from_text(question) or str(presentation.get("active_week") or "")
    if _parse_week_key(active_week) is None:
        return

    if _response_matches(response, CHECK_LATER_RESPONSES):
        until = _record_presentation_snooze(
            presentation, active_week, session_id=session_id
        )
        _save_state(state)
        _log(
            f"weekly presentation snoozed {active_week} until {until} "
            f"session={session_id}"
        )
        return

    if _response_matches(response, COMPLETE_WEEK_RESPONSES):
        _finalize_week_close(presentation, active_week, state=state)
        _save_state(state)
        _log(f"weekly presentation completed {active_week}")
        return

    if _response_matches(response, ("review proposed additions now", "review now", "1")):
        now = _now()
        presentation["active_week"] = active_week
        _clear_snooze_tracking(presentation)
        _open_hot_promotion_window(presentation, now=now)
        _save_state(state)
        _log(f"weekly presentation review started {active_week} (hot promotion window open)")


def run_async(reason: str) -> None:
    # Worker AIAgent sessions fire on_session_start/finalize; do not nest backlog
    # generation under an in-flight Distill/Brief LLM call (lock + inject storm).
    if _in_weekly_worker_llm():
        _log(f"weekly run skipped during worker LLM ({reason})")
        return
    _log(f"weekly run scheduled ({reason})")
    thread = threading.Thread(
        target=_run_weekly,
        args=(reason,),
        name="memory-weekly",
        daemon=True,
    )
    thread.start()
