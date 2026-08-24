"""Memory digest — append typed blocks to memories/staging/daily/YYYY-MM-DD.md.

Plugin hooks (CLI + gateway):
  - on_session_end — after each completed agent turn, digest when the
    undigested window past the session bookmark reaches ≥12 user messages
    (assistant replies between those 12 user turns are included; extra user
    turns wait; there is no assistant floor)
  - on_session_finalize — digest session-boundary leftovers when the same
    user-count floor is met
  -     plugin clock — sleep until next_clock_at (08:00/12:00/16:00/20:00/23:55
    in config.yaml ``timezone:``). Day ticks Phase-2 only if today's file has
    more than 25 cards. Leftover force-extract runs when last_nightly is
    behind (including morning catch-up); that path also Phase-2's any card
    count ≥ 1 then refreshes ``## Day wrap-up`` (and yesterday if it has
    cards and no trailer). Day ticks only re-attach the trailer.

Progress is one cursor: the session bookmark (``last_digest_message_id``).
Never calls the memory tool — write_file/patch to staging only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import math
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

import yaml

_plugin_dir = Path(__file__).resolve().parent
_plugin_dir_str = str(_plugin_dir)
if _plugin_dir_str not in sys.path:
    sys.path.insert(0, _plugin_dir_str)

_mymemory = _plugin_dir.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = _plugin_dir.parent.parent
_plugins_root_str = str(_plugins_root)
if _plugins_root_str not in sys.path:
    sys.path.insert(0, _plugins_root_str)

from memory_staging import (
    daily_staging_dir,
    daily_staging_path,
    hermes_local_now,
    hermes_local_today,
    hermes_local_today_str,
    iter_daily_staging_files,
    migrate_legacy_daily_yaml,
    parse_week_key,
    patch_daily_block_valid_to,
)
from worker_llm import (
    in_worker_llm,
    run_worker_llm,
    run_worker_llm_oneshot,
    run_worker_llm_tools,
)
import operations as digest_operations
import operation_log as digest_operation_log
import dedup_prompt as digest_dedup_prompt
import composition as digest_composition
import digest_tools
import digest_clock

logger = logging.getLogger("plugins.memory-digest")

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover
    import os

    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val).resolve() if val else (Path.home() / ".hermes").resolve()


def _hermes_home() -> Path:
    return get_hermes_home()


BATCH_USER_MESSAGES = 12
RETRIEVAL_ID_CAP = 8
RETRIEVAL_TTL_SECONDS = 30 * 60
USER_CORRECTION_REASON = "rejected by user's correction"
_USER_DATED_RE = re.compile(
    r"\b(dated|outdated|wrong|incorrect|not true)\b",
    re.I,
)
# Trigger reason when the undigested window past the bookmark hits the batch floor.
BOOKMARK_TRIGGER_REASON = "bookmark_ready"
BATCH_TRIGGER_REASON = BOOKMARK_TRIGGER_REASON  # deprecated alias
ALLOWED_TYPES = {
    "fact",
    "procedure",
    "decision",
    "decision_constraint",
    "event",
    "entity",
}
NEW_OUTPUT_TYPES = {"fact", "procedure", "decision", "event"}
LEGACY_TYPE_ALIASES = {"decision_constraint": "decision"}
ALLOWED_CONFIDENCE = {"explicit", "high", "medium", "low"}
# "dropped" is a transient operator marker: apply_operation sets it and
# purge_dropped_blocks removes the block in the same commit, so it is valid
# mid-commit but never reaches disk. Human review writes "rejected", which is
# never auto-purged.
ALLOWED_STATUS = {"candidate", "approved", "rejected", "dropped"}
REQUIRED_FRONTMATTER = {"id", "type", "confidence", "status", "sources"}

# Light digest caps — daily staging is index cards, not transcripts.
MAX_BODY_CHARS = digest_operations.MAX_BODY_CHARS
MAX_BODY_LINES = 1
# Validation retry — feed validator errors back to the worker before giving up.
MAX_VALIDATION_ATTEMPTS = 5
MAX_WORKER_VALIDATION_ATTEMPTS = 5
MAX_COMMIT_ATTEMPTS = 3
# prepare_operations retries the proposer up to MAX_PROPOSER_VALIDATION_ATTEMPTS;
# on exhaust it hands in the last proposal. Invocation budget stays bounded.
MAX_PROPOSER_VALIDATION_ATTEMPTS = 5
MAX_PROPOSER_INVOCATIONS = 9
MAX_ERRORS_IN_PROMPT = 8
MAX_RETRY_ERROR_CHARS = 800
# Clear a stuck in-flight flag if the worker never finished (e.g. gateway crash).
IN_FLIGHT_STALE_SECONDS = 600
# Phase-2 Pearson/MI prefilter (ES-Mem eqs 1–2). Lazy MiniLM; in-memory only.
PHASE2_MINILM_MODEL = "all-MiniLM-L6-v2"
PHASE2_MI_QUANTILE = 0.35
_PHASE2_MINILM: Any = None
_PHASE2_EMBED_CACHE: dict[str, list[float]] = {}

# Civil-day event bullets after YAML fences — not a fifth memory type.
DAY_WRAPUP_HEADING = "## Day wrap-up"
MAX_WRAPUP_CHARS = 200
_WRAPUP_HEADING_RE = re.compile(r"(?m)^## Day wrap-up[ \t]*\n?")

# Cap on blocks in a committed daily staging file.
MAX_BLOCKS_PER_FILE = 30
# Importance (0–5 stored): digest write assigns 1–5 at create time; 0 is decay/drop.
IMPORTANCE_MIN = 0
IMPORTANCE_MAX = 5
IMPORTANCE_DEFAULT = 3
IMPORTANCE_WRITE_MIN = 1
# Recent staging window for inject and week-alive related scans.
RECALL_DAYS = 3
RECALL_EXTENDED_DAY_START = 4
RECALL_EXTENDED_DAY_END = 7
RECENT_CONTEXT_TYPES = {
    "fact",
    "procedure",
    "decision",
    "decision_constraint",
    "event",
}
SPAN_CONFIDENCES = frozenset({"explicit", "high", "medium", "low"})
# fact / event must carry an entity anchor so weekly review can group them.
# Hypothesis is owned by the weekly worker, not by digest.
ENTITY_REQUIRED_TYPES = {"fact", "event"}
# entity is a tag (entity: frontmatter), never a standalone block type in digest output.
# hypothesis is owned by memory-weekly and is not a digest type.
BANNED_TYPES = {"entity", "hypothesis"}
# Optional temporal span keys; ISO YYYY-MM-DD, valid_to may also be "open".
OPTIONAL_TEMPORAL_KEYS = ("valid_from", "valid_to")
OPEN_SPAN = "open"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MEM_ID_RE = re.compile(r"^mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+$")
_PREDICATE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PHASE_TOKEN_RE = re.compile(r"^(?:v\d+|draft|final|wip|rev\d+)$", re.I)
_DECISION_SUBJECT_RE = re.compile(
    r"^(?:Preference|Decision):\s*(\S+)",
    re.IGNORECASE,
)
_DECISION_OBSERVATIONAL_RE = re.compile(
    r"\b(?:stated|said|mentioned)\b",
    re.IGNORECASE,
)
_DECISION_CONSTRAINT_CUE_RE = re.compile(
    r"\b(?:ruled|must not|must|wants|instruct|prefer)\b",
    re.IGNORECASE,
)
_DECISION_USER_SUBJECTS_BASE = frozenset({"user"})
_DECISION_USER_SUBJECTS = _DECISION_USER_SUBJECTS_BASE
_NARRATION_PREFIX = "Narration:"
_FACTUAL_PREFIX = "Factual:"
_DECISION_OWNERSHIP_TEACH = digest_tools.DECISION_OWNERSHIP_TEACH
_FACT_NARRATION_TEACH = digest_tools.FACT_NARRATION_TEACH


def _decision_user_subjects() -> frozenset[str]:
    """Allowlisted decision subjects: user + tokens derived from USER.md."""
    subjects = set(_DECISION_USER_SUBJECTS_BASE)
    try:
        user_path = _hermes_home() / "memories" / "USER.md"
        if user_path.is_file():
            text_u = user_path.read_text(encoding="utf-8")
            for match in re.finditer(
                r"(?im)^(?:#+\s*)?(?:name|preferred name|aka|alias)\s*[:：]\s*(.+)$",
                text_u,
            ):
                raw = match.group(1).strip()
                for part in re.split(r"[,/|，、]+", raw):
                    token = part.strip().split()[0] if part.strip() else ""
                    if token:
                        subjects.add(token.casefold())
            for match in re.finditer(r"(?m)^#\s+(\S+)", text_u):
                subjects.add(match.group(1).casefold())
                break
    except OSError:
        pass
    return frozenset(subjects)

MAX_INVOLVES = 5
MAX_RELATED = 10
MAX_SUPERSEDES = 8
# Phase-2 oneshot dumps four typed op slots in one tool call; 2048 truncates.
ONESHOT_DIGEST_MAX_TOKENS = 8192
# Wrap-up is one short phrase; keep reasoning room without a 8k completion.
ONESHOT_WRAPUP_MAX_TOKENS = 2048
_WRAPUP_SENTENCE_END = re.compile(r"[.!?]")
WORKER_ARTIFACT_FILENAMES = {
    "event": "event-result.json",
    "fact": "fact-result.json",
    "procedure": "procedure-result.json",
    "decision": "decision-result.json",
}
WORKER_FAILURE_FILENAMES = {
    "event": "event-failures.jsonl",
    "fact": "fact-failures.jsonl",
    "procedure": "procedure-failures.jsonl",
    "decision": "decision-failures.jsonl",
    "phase1": "phase1-failures.jsonl",
}
LIST_FRONTMATTER_KEYS = frozenset({"sources", "related", "supersedes", "entity_aliases"})
CLOCK_FRONTMATTER_KEYS = frozenset(
    {"user_message_at", "assistant_response_at", "generated_at"}
)
# Body keywords implying a time-bound fact — both span keys then required.
# Narrow hints — avoid broad Chinese substrings (e.g. 之前) that fire on venting.
TEMPORAL_REQUIRED_HINTS = (
    "deadline",
    "due ",
    "until ",
    "during ",
    "applying",
    "application",
    "expires",
    "申请",
    "截止",
)
_STATUS_ALIASES = {
    "confirmed": "candidate",
    "active": "candidate",
    "draft": "candidate",
    "pending": "candidate",
    "discarded": "rejected",
    "reject": "rejected",
}
_FRONTMATTER_KEY_ORDER = (
    "id",
    "type",
    "entity",
    "entity_aliases",
    "predicate",
    "participants",
    "involves",
    "related",
    "supersedes",
    "valid_from",
    "valid_to",
    "confidence",
    "importance",
    "status",
    "rejected_reason",
    "promoted_at",
    "discarded_at",
    "superseded_at",
    "sources",
    "user_message_at",
    "assistant_response_at",
    "generated_at",
    "strength",
    "recall_n",
    "last_recall_at",
    "first_seen",
)


@dataclass(frozen=True)
class ValidatedWorkerResult:
    """Worker result; ``blocks`` is authoritative, ``content`` is derived YAML."""

    worker_type: str
    session_id: str
    run_id: str
    attempts: int
    content: str
    blocks: tuple[dict[str, Any], ...]
    path: Path | None = None
    accepted_dirty: bool = False


@dataclass(frozen=True)
class WorkerFailure:
    """A worker that exhausted validation or invocation attempts."""

    worker_type: str
    session_id: str
    run_id: str
    attempts: int
    errors: tuple[str, ...]

DIGEST_POLICY = f"""Memory digest policy (index cards, not transcripts):
- Return only markdown to append to YYYY-MM-DD.md. Never write files, MEMORY.md, USER.md, or use the memory tool.
- Caps: one body sentence ≤{MAX_BODY_CHARS} chars; no newlines, bullets, tables, or fences; no session-summary; outcomes not drafts.
- importance: {IMPORTANCE_WRITE_MIN}–{IMPORTANCE_MAX} on every new block (do not write 0 at create).

Stage when durable (prefer staging over skip): named person/class/project/doc; decision/constraint/deliverable; user remember/correction; repeatable pattern; venting with real anchors. Skip only Hermes infra with zero anchors. Stage all durable items (no per-run cap).

Types: fact | procedure | decision | event. Never type: entity or hypothesis (entity is a frontmatter tag; hypothesis belongs to the weekly worker).
- User messages are the primary evidence; preserve the user's goal, wording, preferences, and corrections over assistant/tool narration.
- event: user-driven causal chain and the user-request skeleton with exactly three stages — Beginning, Course, Outcome. Each of beginning, course, and outcome is one concise sentence (not a paragraph). Own the user goal, important phases, and current result/status; do not duplicate detailed facts, preferences, speculation, or tool logs. Required: entity; predicate snake_case user intent (e.g. user_requested_*) — not a tool-step name; participants MUST include {{entity: User, role: requester}} and {{entity: Assistant, role: executor}} (≤5 total; role optional on secondary; confidence: medium if any role missing); valid_from + valid_to (open if ongoing); no file paths / message ids / byte sizes (→ sources:); no 【过程性参考】.
- fact: stable, observable observations; do not contain agent process or user preference. Two peer forms: Factual (kind=Factual + content; involves optional/single) or Narration (kind=Narration + content + involves cast `{{entity: Name, role?: optional}}`, role only when clear). Prefer one Narration card over N cast shards for the same story.
- procedure: agent process for the user-request task — an abstract reusable process used by the agent. Own course obstacles and abstract solutions, not raw tool logs or the event outcome. Not object documentation; do not become object/API documentation.
- decision: user-only rulings and preferences for agent behavior (must/must-not), including decision_constraint: user feedback on that procedure, corrections, and standing prefs. Scan the transcript for the user's must / must-not / standing prefs and emit them as subject=user plus a predicate ruling so Preference:/Decision: {{subject}} {{ruling}} is one clause. First subject after Preference:/Decision: must be user/User (plus USER.md aliases). Third-party traits/living/likes → fact (`Narration:` / kind=Narration), even when the user reported them. Do not duplicate the full procedure or event summary. Corrections use supersedes: + confidence: explicit. `decision_constraint` is accepted only as a legacy input alias and is normalized to canonical `decision`.
- Legacy input alias detail: decision_constraint: user feedback on that procedure; it is never emitted as the canonical output type.
- Episode-first: one outcome event per completed user request (not one per tool step). Grade/scrape snapshot → fact; multi-file deliverable arc → one event.
- entity: required on fact and event (English canonical). entity_aliases: optional original-language surfaces; omit when identical to entity. involves: optional on non-event only (max {MAX_INVOLVES}; omit primary; entity collection with optional role). Do NOT use involves on event — use participants instead.
- related: optional mem-ids (max {MAX_RELATED}); associative only — event→fact/procedure/decision only. NEVER put another event id in an event's related:. related: is associative only and NEVER deletes or supersedes a block.
- supersedes: correction only (max {MAX_SUPERSEDES}); requires confidence: explicit. User correction: MUST set confidence: explicit and supersedes: [mem-…] from EXISTING BLOCK IDS (today's daily file / earlier batches on today). Prefer decision or corrected fact; never guess ids.
- Filename ≠ entity (paths in sources:). Roster >5: collective entity + ≤5 participants + related roster fact. Unconfirmed roles are not participants.

Spans: absolute ISO dates only; time-bound facts need valid_from/valid_to (open ok); events always both; never `as of` in staging.
Required keys: id, type, confidence, status, sources. status: candidate | approved | rejected (default candidate); `dropped` is reserved for the update operator and must never be written by a worker. Detail worker results are temporary envelopes only; they are not a persistent fifth memory type.
If nothing durable: one line saying nothing durable to stage. English dominant; Chinese only for names, titles, or quotes.
"""

_digest_lock = threading.RLock()
# Marks the current thread as the digest worker so its child agent session
# (AIAgent.run_conversation fires on_agent_end) does not pollute .digest-state.json.
_digest_worker_active = threading.local()
_clock_stop = threading.Event()
_clock_thread: threading.Thread | None = None
_clock_action_lock = threading.Lock()


def _role_counts(messages: list[dict[str, Any]]) -> tuple[int, int]:
    user_count = sum(1 for m in messages if m["role"] == "user")
    assistant_count = sum(1 for m in messages if m["role"] == "assistant")
    return user_count, assistant_count


def _batch_ready(messages: list[dict[str, Any]]) -> bool:
    """True once the undigested window has enough user turns to extract.

    Assistant replies stay in the transcript; they must not delay the batch
    or a quiet model could strand user messages indefinitely.
    """
    user_count, _assistant_count = _role_counts(messages)
    return user_count >= BATCH_USER_MESSAGES


def _staging_daily() -> Path:
    return daily_staging_dir(_hermes_home())


def _state_file() -> Path:
    return _hermes_home() / "memories" / "staging" / ".digest-state.json"


def _log_file() -> Path:
    return _hermes_home() / "logs" / "memory-digest.log"


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
        return {"sessions": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"sessions": {}}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _today_block_count(date_str: str | None = None) -> tuple[Path, int]:
    """Count YAML cards on today's daily file for the Phase-2 size gate."""
    day = date_str or hermes_local_today_str()
    path = daily_staging_path(_hermes_home(), day)
    if not path.exists():
        return path, 0
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return path, 0
    return path, len(_daily_blocks(content))


def maybe_run_digest_clock(
    now: datetime | None = None,
    *,
    sync: bool = False,
    tz: Any = None,
) -> dict[str, Any]:
    """Catch up leftover extract / gated merge when a civil tick is due.

    Chat hooks call this asynchronously so a consolidate cannot freeze the turn.
    The daemon clock thread uses ``sync=True``. Pytest runs outside
    ``test_digest_clock`` return idle so ImmediateThread stubs cannot leftover
    against a real or tmp ``HERMES_HOME``.
    """
    test_name = os.environ.get("PYTEST_CURRENT_TEST") or ""
    if test_name and "test_digest_clock" not in test_name:
        return {"outcome": "idle"}
    if not sync:
        threading.Thread(
            target=maybe_run_digest_clock,
            kwargs={"now": now, "sync": True, "tz": tz},
            name="memory-digest-clock-catchup",
            daemon=True,
        ).start()
        return {"outcome": "scheduled"}
    if not _clock_action_lock.acquire(blocking=False):
        return {"outcome": "busy"}
    try:
        return _run_digest_clock_once(now=now, tz=tz)
    finally:
        _clock_action_lock.release()


def _weekly_clock_leftover_flag(local: datetime, need_leftover: bool) -> bool:
    """Pass leftover_ran after 23:55 Sunday so close can retry once leftover already ran."""
    sunday_close = local.weekday() == 6 and (local.hour, local.minute) >= (23, 55)
    return bool(need_leftover) or sunday_close


def _maybe_run_monthly_clock(local: datetime) -> dict[str, Any]:
    """Month rollover after weekly so a weekly exception cannot skip L4 generation."""
    try:
        monthly_dir = _mymemory / "monthly"
        if str(monthly_dir) not in sys.path:
            sys.path.insert(0, str(monthly_dir))
        import monthly_clock as _monthly_clock

        return _monthly_clock.maybe_run(local)
    except Exception as exc:
        _log(f"monthly clock failed: {exc}")
        return {"outcome": "error", "error": str(exc)}


def _maybe_run_weekly_clock(local: datetime, leftover_ran: bool = False) -> dict[str, Any]:
    """Sunday generate/close after leftover so stale cron next_run cannot empty-close Monday."""
    try:
        weekly_dir = _mymemory / "weekly"
        if str(weekly_dir) not in sys.path:
            sys.path.insert(0, str(weekly_dir))
        import weekly_clock as _weekly_clock

        return _weekly_clock.maybe_run(local, leftover_ran=leftover_ran)
    except Exception as exc:
        _log(f"weekly clock failed: {exc}")
        return {"outcome": "error", "error": str(exc)}


def _run_digest_clock_once(
    now: datetime | None = None,
    tz: Any = None,
) -> dict[str, Any]:
    """Fire leftover extract and/or gated merge, then the Sunday weekly clock.

    23:55 leftover writes today's file; 08:00 catch-up writes yesterday only
    when that night never stamped, so morning cannot spend tonight's slot.
    Nightly/catch-up Phase 2 ignores the 25-card noon gate.
    """
    zone = tz or digest_clock.digest_clock_tz()
    local = digest_clock._as_local(now or datetime.now(zone), zone)
    today = local.date().isoformat()
    on_grid = (local.hour, local.minute) in digest_clock.PHASE2_TICKS
    with _digest_lock:
        state = _load_state()
        last_nightly = state.get("last_nightly_date")
        last_p2 = digest_clock.parse_aware(state.get("last_phase2_at"), zone)
        stored_next = digest_clock.parse_aware(state.get("next_clock_at"), zone)
        session_keys = list(state.get("sessions", {}).keys())
        in_flight = bool(state.get("phase2_in_flight"))
    _kind, upcoming = digest_clock.next_deadline(local, zone)
    past_tick = stored_next is not None and stored_next <= local
    first_boot_on_grid = stored_next is None and on_grid
    need_leftover = digest_clock.should_run_nightly_leftover(
        None if last_nightly is None else str(last_nightly),
        local,
        zone,
    )
    payload: dict[str, Any] = {
        "outcome": "idle",
        "next_clock_at": upcoming.isoformat(),
        "leftover": 0,
        "phase2": False,
    }
    if not past_tick and not first_boot_on_grid and not need_leftover:
        with _digest_lock:
            state = _load_state()
            state["next_clock_at"] = upcoming.isoformat()
            _save_state(state)
        payload["weekly"] = _maybe_run_weekly_clock(
            local, leftover_ran=_weekly_clock_leftover_flag(local, bool(need_leftover))
        )
        payload["monthly"] = _maybe_run_monthly_clock(local)
        return payload

    leftover_n = 0
    payload["wrapup"] = False
    leftover_date = str(need_leftover) if need_leftover else None
    if leftover_date:
        for session_key in session_keys:
            _maybe_run_digest(
                session_key,
                reason="nightly_leftover",
                force=True,
                sync=True,
                date_str=leftover_date,
            )
            leftover_n += 1
        with _digest_lock:
            state = _load_state()
            state["last_nightly_date"] = leftover_date
            _save_state(state)

    board_date = leftover_date or today
    daily_path, block_count = _today_block_count(board_date)
    want_phase2 = (past_tick or first_boot_on_grid or bool(leftover_date)) and (
        digest_clock.should_run_phase2_tick(
            block_count,
            last_p2,
            local,
            ignore_block_gate=bool(leftover_date),
        )
    )
    if want_phase2 and in_flight:
        want_phase2 = False
    if want_phase2:
        with _digest_lock:
            state = _load_state()
            state["phase2_in_flight"] = True
            _save_state(state)
        try:
            run_manual_phase2(daily_path, date_str=board_date)
            payload["phase2"] = True
            with _digest_lock:
                state = _load_state()
                state["last_phase2_at"] = local.isoformat()
                state["phase2_in_flight"] = False
                _save_state(state)
        except Exception as exc:
            _log(f"digest clock phase2 failed: {exc}")
            with _digest_lock:
                state = _load_state()
                state["phase2_in_flight"] = False
                _save_state(state)

    if need_leftover and block_count > 0:
        try:
            run_day_wrapup(daily_path)
            payload["wrapup"] = True
        except Exception as exc:
            _log(f"digest clock wrapup failed: {exc}")
        yesterday = (local.date() - timedelta(days=1)).isoformat()
        ypath = daily_staging_path(_hermes_home(), yesterday)
        if ypath.exists():
            try:
                ytext = ypath.read_text(encoding="utf-8")
            except OSError:
                ytext = ""
            if (
                ytext
                and DAY_WRAPUP_HEADING not in ytext
                and _daily_blocks(ytext)
            ):
                try:
                    run_day_wrapup(ypath)
                    payload["wrapup_yesterday"] = True
                except Exception as exc:
                    _log(f"digest clock wrapup yesterday failed: {exc}")

    with _digest_lock:
        state = _load_state()
        state["next_clock_at"] = upcoming.isoformat()
        _save_state(state)
    payload["leftover"] = leftover_n
    payload["outcome"] = "ran"
    payload["block_count"] = block_count
    payload["weekly"] = _maybe_run_weekly_clock(
        local, leftover_ran=_weekly_clock_leftover_flag(local, bool(need_leftover))
    )
    payload["monthly"] = _maybe_run_monthly_clock(local)
    return payload


def start_digest_clock_thread() -> None:
    """Arm the civil clock and record clock_alive so a dead worker is visible.

    Exclusive plugins skip manager boot; the first primary initialize is what
    starts this thread. State stamps exist because idle sleeps write no digest log.
    """
    global _clock_thread
    if _clock_thread is not None and _clock_thread.is_alive():
        return
    _clock_stop.clear()
    _clock_thread = threading.Thread(
        target=_digest_clock_loop,
        name="memory-digest-clock",
        daemon=True,
    )
    _clock_thread.start()
    with _digest_lock:
        state = _load_state()
        state["clock_alive"] = True
        state["clock_started_at"] = datetime.now(timezone.utc).isoformat()
        state["clock_error"] = ""
        state["clock_stopped_at"] = ""
        _save_state(state)
    _log("digest clock started")


def stop_digest_clock_thread() -> None:
    """Tests / plugin unload: wake the sleep loop so the thread can exit."""
    _clock_stop.set()


def _digest_clock_loop() -> None:
    """Sleep until next_clock_at; a 60s poll hid a sleeping thread from state.

    Fire the slept-for tick as ``now`` so a wake a few minutes past midnight
    still leftover-extracts that 23:55 civil day instead of skipping until 08:00.
    """
    global _clock_thread
    while not _clock_stop.is_set():
        try:
            zone = digest_clock.digest_clock_tz()
            local = datetime.now(zone)
            with _digest_lock:
                stored = digest_clock.parse_aware(
                    _load_state().get("next_clock_at"), zone
                )
            if stored is None:
                _kind, stored = digest_clock.next_deadline(local, zone)
                with _digest_lock:
                    state = _load_state()
                    state["next_clock_at"] = stored.isoformat()
                    _save_state(state)
            delay = max(0.0, (stored - local).total_seconds())
            with _digest_lock:
                state = _load_state()
                state["clock_alive"] = True
                state["clock_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
                _save_state(state)
            _log(f"digest clock waiting until {stored.isoformat()}")
            if _clock_stop.wait(delay):
                with _digest_lock:
                    state = _load_state()
                    state["clock_alive"] = False
                    state["clock_stopped_at"] = datetime.now(timezone.utc).isoformat()
                    _save_state(state)
                break
            maybe_run_digest_clock(now=stored, sync=True)
        except Exception as exc:
            _log(f"digest clock died: {exc}")
            with _digest_lock:
                state = _load_state()
                state["clock_alive"] = False
                state["clock_error"] = str(exc)
                state["clock_stopped_at"] = datetime.now(timezone.utc).isoformat()
                _save_state(state)
            _clock_thread = None
            start_digest_clock_thread()
            return


def _session_key(context: dict) -> str:
    return str(context.get("session_key") or context.get("session_id") or "unknown")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _seconds_since(ts: datetime | None) -> float:
    if ts is None:
        return float("inf")
    now = datetime.now(ts.tzinfo or timezone.utc)
    return (now - ts).total_seconds()


def _fetch_messages(session_id: str, after_id: int = 0) -> list[dict[str, Any]]:
    """Load active user/assistant rows including timestamp for source-window clocks."""
    db_path = _hermes_home() / "state.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, role, content, timestamp
            FROM messages
            WHERE session_id = ? AND id > ? AND active = 1
            ORDER BY id ASC
            """,
            (session_id, after_id),
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        _log(f"sqlite error session={session_id}: {exc}")
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        role = row["role"] or ""
        content = (row["content"] or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        out.append(
            {
                "id": row["id"],
                "role": role,
                "content": content[:2000],
                "timestamp": row["timestamp"],
            }
        )
    return out


def _iso_from_message_ts(raw: Any) -> str | None:
    """Turn SQLite messages.timestamp into offset ISO-8601 seconds."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, (int, float)):
        dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
    else:
        text = str(raw).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat(timespec="seconds")


def _iso_clocks_for_window(
    session_id: str,
    start_id: int | None,
    end_id: int | None,
    *,
    on_day: date | None = None,
) -> tuple[str | None, str | None]:
    """First user / last assistant clocks in the cited message id range."""
    if start_id is None or end_id is None:
        return None, None
    lo, hi = (start_id, end_id) if start_id <= end_id else (end_id, start_id)
    user_at: str | None = None
    assistant_at: str | None = None
    for row in _fetch_messages(session_id, after_id=max(0, lo - 1)):
        try:
            mid = int(row["id"])
        except (TypeError, ValueError, KeyError):
            continue
        if mid < lo or mid > hi:
            continue
        stamp = _iso_from_message_ts(row.get("timestamp"))
        if not stamp:
            continue
        parsed_stamp = _parse_frontmatter_clock(stamp)
        if on_day is not None and (
            parsed_stamp is None or parsed_stamp.astimezone().date() != on_day
        ):
            continue
        if row.get("role") == "user" and user_at is None:
            user_at = stamp
        if row.get("role") == "assistant":
            assistant_at = stamp
    return user_at, assistant_at


def _parse_frontmatter_clock(value: Any) -> datetime | None:
    """Parse an optional wall-clock frontmatter value; None if blank."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        text = str(value).strip().strip("'\"")
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _civil_noon_iso(day: date) -> str:
    """Fallback wall clock when state.db has no row for the cited window."""
    tz = datetime.now().astimezone().tzinfo or timezone.utc
    return datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=tz).isoformat(
        timespec="seconds"
    )


def _clocks_from_source_tags(
    sources: Any, day: date
) -> tuple[str | None, str | None]:
    """Earliest user / latest assistant ISO from session source tags via state.db."""
    if isinstance(sources, list):
        tags = [str(item).strip() for item in sources if str(item).strip()]
    elif sources in (None, ""):
        tags = []
    else:
        tags = [str(sources).strip()]
    user_dt: datetime | None = None
    asst_dt: datetime | None = None
    for tag in tags:
        session_id = digest_tools.session_id_from_source_tag(tag)
        if not session_id:
            continue
        rng = digest_tools.message_range_from_source_tag(tag)
        if rng:
            user_iso, asst_iso = _iso_clocks_for_window(
                session_id, rng[0], rng[1]
            )
        else:
            user_iso, asst_iso = None, None
        if not user_iso and not asst_iso:
            user_iso, asst_iso = _iso_clocks_for_window(
                session_id, 1, 2_147_483_647, on_day=day
            )
        user_parsed = _parse_frontmatter_clock(user_iso)
        asst_parsed = _parse_frontmatter_clock(asst_iso)
        if user_parsed is not None and (user_dt is None or user_parsed < user_dt):
            user_dt = user_parsed
        if asst_parsed is not None and (asst_dt is None or asst_parsed > asst_dt):
            asst_dt = asst_parsed
    user_out = user_dt.isoformat(timespec="seconds") if user_dt else None
    asst_out = asst_dt.isoformat(timespec="seconds") if asst_dt else None
    return user_out, asst_out


def _stamp_block_clocks(parsed: dict[str, Any], day: date) -> bool:
    """Fill missing message clocks from state.db; civil noon when lookup misses."""
    changed = False
    user_iso, asst_iso = _clocks_from_source_tags(parsed.get("sources"), day)
    noon = _civil_noon_iso(day)
    wanted = {
        "user_message_at": user_iso or noon,
        "assistant_response_at": asst_iso or noon,
        "generated_at": asst_iso or noon,
    }
    for key, stamp in wanted.items():
        current = parsed.get(key)
        if _parse_frontmatter_clock(current) == _parse_frontmatter_clock(stamp):
            continue
        parsed[key] = stamp
        changed = True
    return changed


def backfill_daily_file_clocks(path: Path | str) -> int:
    """Rewrite one daily staging file with plugin-stamped clocks. Returns cards changed."""
    file_path = Path(path)
    try:
        day = date.fromisoformat(file_path.stem)
    except ValueError:
        return 0
    try:
        original = file_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    _fences, wrapup = split_daily_wrapup(original)
    blocks = _frontmatter_blocks(original)
    if not blocks:
        return 0
    stamped: list[str] = []
    changed_n = 0
    for _line_no, raw_frontmatter, body in blocks:
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            stamped.append(
                _render_digest_block({"id": f"mem-{day.isoformat()}-digest"}, body)
            )
            continue
        if not isinstance(parsed, dict):
            continue
        if _stamp_block_clocks(parsed, day):
            changed_n += 1
        stamped.append(_render_digest_block(parsed, body))
    if changed_n == 0:
        return 0
    new_fences = "\n\n".join(stamped).rstrip() + "\n"
    updated = join_daily_wrapup(new_fences, wrapup) if wrapup else new_fences
    if updated != original:
        file_path.write_text(updated, encoding="utf-8")
    return changed_n


def backfill_daily_dir_clocks(daily_dir: Path | str) -> dict[str, int]:
    """Walk YYYY-MM-DD.md files and stamp clocks. No LLM."""
    root = Path(daily_dir)
    files = 0
    cards = 0
    if not root.is_dir():
        return {"files": 0, "cards": 0}
    for path in sorted(root.glob("*.md")):
        n = backfill_daily_file_clocks(path)
        if n:
            files += 1
            cards += n
    return {"files": files, "cards": cards}


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for msg in messages[-40:]:
        lines.append(f"[{msg['role']}] {msg['content']}")
    return "\n".join(lines)


def _clean_digest_response(response: str) -> str:
    content = response.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content


def _format_sources(value: Any) -> str:
    if isinstance(value, list):
        inner = ", ".join(str(item) for item in value)
        return f"[{inner}]"
    return str(value)


def _involves_entity_name(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("entity", "")).strip()
    if isinstance(item, str):
        return item.strip()
    return ""


def _normalize_involves_entry(item: Any) -> dict[str, str] | None:
    """Coerce legacy string or map involves item to ``{entity, role?}``."""
    if isinstance(item, str):
        ent = item.strip()
        return {"entity": ent} if ent else None
    if isinstance(item, Mapping):
        ent = str(item.get("entity", "")).strip()
        if not ent:
            return None
        entry: dict[str, str] = {"entity": ent}
        role = str(item.get("role", "")).strip()
        if role:
            entry["role"] = role
        return entry
    return None


def _format_entity_collection_lines(key: str, items: Any) -> list[str]:
    lines = [f"{key}:"]
    if not isinstance(items, list):
        return lines
    for part in items:
        if not isinstance(part, Mapping):
            continue
        ent = str(part.get("entity", "")).strip()
        if not ent:
            continue
        role = str(part.get("role", "")).strip()
        if role:
            lines.append(f"  - {{entity: {ent}, role: {role}}}")
        else:
            lines.append(f"  - {{entity: {ent}}}")
    return lines


def _render_digest_block(parsed: dict[str, Any], body: str) -> str:
    """Keep entity_aliases in list form after entity so a rewrite cannot drop the original-language surface."""
    lines = ["---"]
    seen: set[str] = set()
    for key in _FRONTMATTER_KEY_ORDER:
        if key not in parsed:
            continue
        seen.add(key)
        if key in LIST_FRONTMATTER_KEYS:
            lines.append(f"{key}: {_format_sources(parsed[key])}")
        elif key in {"participants", "involves"}:
            lines.extend(_format_entity_collection_lines(key, parsed[key]))
        elif key in CLOCK_FRONTMATTER_KEYS:
            lines.append(f"{key}: {digest_tools._yaml_scalar(parsed[key])}")
        else:
            lines.append(f"{key}: {parsed[key]}")
    for key, value in parsed.items():
        if key in seen:
            continue
        if key in LIST_FRONTMATTER_KEYS:
            lines.append(f"{key}: {_format_sources(value)}")
        elif key in {"participants", "involves"}:
            lines.extend(_format_entity_collection_lines(key, value))
        elif key in CLOCK_FRONTMATTER_KEYS:
            lines.append(f"{key}: {digest_tools._yaml_scalar(value)}")
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", body])
    return "\n".join(lines)


def _body_needs_temporal_span(body: str) -> bool:
    lowered = body.lower()
    return any(hint in lowered for hint in TEMPORAL_REQUIRED_HINTS)


def _truncate_body(body: str) -> str:
    if len(body) <= MAX_BODY_CHARS:
        return body
    trimmed = body[: MAX_BODY_CHARS - 1].rstrip()
    return f"{trimmed}…"


def _normalize_importance(value: Any) -> int:
    """Clamp importance to 0–5; missing/invalid → IMPORTANCE_DEFAULT (3)."""
    if value is None or value == "":
        return IMPORTANCE_DEFAULT
    try:
        n = int(value)
    except (TypeError, ValueError):
        return IMPORTANCE_DEFAULT
    if n < IMPORTANCE_MIN or n > IMPORTANCE_MAX:
        return IMPORTANCE_DEFAULT
    return n


def _normalize_digest_content(
    content: str,
    *,
    session_id: str,
    message_start_id: int | None = None,
    message_end_id: int | None = None,
) -> str:
    """Repair common LLM frontmatter mistakes before validation (retry handles the rest)."""
    if _is_skip_only_content(content):
        return content

    blocks = _frontmatter_blocks(content)
    if not blocks:
        return content

    today = hermes_local_today_str()
    rendered: list[str] = []
    for _line_no, raw_frontmatter, body in blocks:
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            rendered.append(_render_digest_block({"id": f"mem-{today}-digest"}, body))
            continue
        if not isinstance(parsed, dict):
            continue

        status = str(parsed.get("status", "")).strip()
        if status not in ALLOWED_STATUS:
            parsed["status"] = _STATUS_ALIASES.get(status.casefold(), "candidate")

        confidence = str(parsed.get("confidence", "")).strip()
        if confidence not in ALLOWED_CONFIDENCE:
            parsed["confidence"] = "medium"

        parsed["importance"] = _normalize_importance(parsed.get("importance"))

        locator = digest_tools.format_session_source(
            session_id,
            message_start_id=message_start_id,
            message_end_id=message_end_id,
        )
        extras = digest_tools._extra_file_sources(parsed.get("sources"))
        parsed["sources"] = [locator, *extras]
        user_at, assistant_at = _iso_clocks_for_window(
            session_id, message_start_id, message_end_id
        )
        if user_at:
            parsed["user_message_at"] = user_at
        if assistant_at:
            parsed["assistant_response_at"] = assistant_at
        parsed.setdefault(
            "generated_at",
            datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        )

        body = _truncate_body(body.strip())
        item_type = str(parsed.get("type", "")).strip()
        if item_type in LEGACY_TYPE_ALIASES:
            item_type = LEGACY_TYPE_ALIASES[item_type]
            parsed["type"] = item_type
        if _body_needs_temporal_span(body):
            parsed.setdefault("valid_from", today)
            parsed.setdefault("valid_to", OPEN_SPAN)
        if item_type == "event":
            parsed.setdefault("valid_from", today)
            parsed.setdefault("valid_to", OPEN_SPAN)

        if not str(parsed.get("id", "")).strip():
            slug = str(parsed.get("entity", "fact")).lower().replace(" ", "-")[:24]
            parsed["id"] = f"mem-{today}-{slug}"

        primary_entity = str(parsed.get("entity", "")).strip()
        involves_raw = parsed.get("involves")
        if involves_raw is not None:
            items = (
                [involves_raw]
                if isinstance(involves_raw, (str, Mapping))
                else involves_raw
                if isinstance(involves_raw, list)
                else []
            )
            cleaned_involves: list[dict[str, str]] = []
            seen_involves: set[str] = set()
            for item in items:
                entry = _normalize_involves_entry(item)
                if entry is None:
                    continue
                ent = entry["entity"]
                if primary_entity and ent == primary_entity:
                    continue
                if ent in seen_involves:
                    # Prefer keeping a non-empty role when deduping.
                    for existing in cleaned_involves:
                        if existing["entity"] == ent:
                            if "role" not in existing and "role" in entry:
                                existing["role"] = entry["role"]
                            break
                    continue
                seen_involves.add(ent)
                cleaned_involves.append(entry)
            if cleaned_involves:
                parsed["involves"] = cleaned_involves[:MAX_INVOLVES]
            else:
                parsed.pop("involves", None)

        for list_key, max_len, id_filter in (
            ("related", MAX_RELATED, True),
            ("supersedes", MAX_SUPERSEDES, True),
        ):
            raw = parsed.get(list_key)
            if raw is None:
                continue
            items = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
            cleaned: list[str] = []
            seen_item: set[str] = set()
            for item in items:
                tag = str(item).strip()
                if not tag or tag in seen_item:
                    continue
                if id_filter and not _MEM_ID_RE.match(tag):
                    continue
                seen_item.add(tag)
                cleaned.append(tag)
            if cleaned:
                parsed[list_key] = cleaned[:max_len]
            else:
                parsed.pop(list_key, None)

        if item_type == "event":
            parsed.pop("involves", None)
            pred_raw = parsed.get("predicate")
            if pred_raw is not None:
                pred_norm = str(pred_raw).strip().lower().replace("-", "_").replace(" ", "_")
                if _PREDICATE_RE.match(pred_norm):
                    parsed["predicate"] = pred_norm
                else:
                    parsed.pop("predicate", None)
            raw_parts = parsed.get("participants")
            if raw_parts is not None:
                items = raw_parts if isinstance(raw_parts, list) else [raw_parts]
                cleaned_parts: list[dict[str, str]] = []
                seen_part: set[str] = set()
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    ent = str(item.get("entity", "")).strip()
                    if not ent or ent in seen_part:
                        continue
                    if primary_entity and ent == primary_entity:
                        continue
                    seen_part.add(ent)
                    entry: dict[str, str] = {"entity": ent}
                    role = str(item.get("role", "")).strip()
                    if role:
                        entry["role"] = role
                    cleaned_parts.append(entry)
                if cleaned_parts:
                    parsed["participants"] = cleaned_parts[:MAX_INVOLVES]
                else:
                    parsed.pop("participants", None)

        rendered.append(_render_digest_block(parsed, body))

    return "\n\n".join(rendered)


def _frontmatter_blocks(content: str) -> list[tuple[int, str, str]]:
    fences, _phrase = split_daily_wrapup(content)
    lines = fences.splitlines()
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


def _validate_temporal(line_no: int, parsed: dict, body: str) -> list[str]:
    errors: list[str] = []
    present: dict[str, str] = {}
    for key in OPTIONAL_TEMPORAL_KEYS:
        if key not in parsed:
            continue
        value = str(parsed.get(key, ""))
        present[key] = value
        if key == "valid_to" and value == OPEN_SPAN:
            continue
        if not _DATE_RE.match(value):
            errors.append(f"line {line_no}: {key} must be YYYY-MM-DD or 'open', got {value!r}")

    valid_from = present.get("valid_from")
    valid_to = present.get("valid_to")
    if (
        valid_from
        and valid_to
        and valid_to != OPEN_SPAN
        and _DATE_RE.match(valid_from)
        and _DATE_RE.match(valid_to)
        and valid_to < valid_from
    ):
        errors.append(f"line {line_no}: valid_to {valid_to} is before valid_from {valid_from}")

    if _body_needs_temporal_span(body):
        if "valid_from" not in present or "valid_to" not in present:
            errors.append(
                f"line {line_no}: time-bound body requires both valid_from and valid_to"
            )

    item_type = str(parsed.get("type", ""))
    if item_type == "event":
        if "valid_from" not in present or "valid_to" not in present:
            errors.append(
                f"line {line_no}: type event requires both valid_from and valid_to"
            )
    return errors


def _validate_block(
    line_no: int,
    raw_frontmatter: str,
    body: str,
    seen_ids: set[str],
) -> list[str]:
    """Reject malformed cards so bad YAML cannot enter daily staging.

    Optional wall clocks are format-checked here; missing clocks stay valid so
    older cards and pre-stamp worker YAML do not fail.
    """
    errors: list[str] = []
    try:
        parsed = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        return [f"line {line_no}: invalid YAML frontmatter: {exc}"]

    if not isinstance(parsed, dict):
        return [f"line {line_no}: frontmatter must be a mapping"]

    missing = sorted(REQUIRED_FRONTMATTER - set(parsed))
    if missing:
        errors.append(f"line {line_no}: missing required keys: {', '.join(missing)}")

    block_id = str(parsed.get("id", ""))
    if block_id:
        if block_id in seen_ids:
            errors.append(f"line {line_no}: duplicate id {block_id!r} in this file")
        seen_ids.add(block_id)

    item_type = str(parsed.get("type", ""))
    if item_type in BANNED_TYPES:
        errors.append(
            f"line {line_no}: type {item_type!r} is banned — use entity: frontmatter instead"
        )
    elif item_type not in ALLOWED_TYPES:
        errors.append(f"line {line_no}: invalid type {item_type!r}")

    if item_type in ENTITY_REQUIRED_TYPES and not str(parsed.get("entity", "")).strip():
        errors.append(f"line {line_no}: type {item_type!r} requires an entity: tag")

    confidence = str(parsed.get("confidence", ""))
    if confidence not in ALLOWED_CONFIDENCE:
        errors.append(f"line {line_no}: invalid confidence {confidence!r}")

    if "importance" in parsed:
        raw_imp = parsed.get("importance")
        try:
            imp = int(raw_imp)
        except (TypeError, ValueError):
            errors.append(f"line {line_no}: importance must be an integer 0–5")
        else:
            if imp < IMPORTANCE_MIN or imp > IMPORTANCE_MAX:
                errors.append(
                    f"line {line_no}: importance {imp} out of range "
                    f"[{IMPORTANCE_MIN}, {IMPORTANCE_MAX}]"
                )

    status = str(parsed.get("status", ""))
    if status not in ALLOWED_STATUS:
        errors.append(f"line {line_no}: invalid status {status!r}")

    if "superseded_at" in parsed:
        stamp = str(parsed.get("superseded_at", "")).strip()
        if not _DATE_RE.match(stamp):
            errors.append(
                f"line {line_no}: superseded_at must be YYYY-MM-DD, got {stamp!r}"
            )

    clock_values: dict[str, datetime] = {}
    for key in ("user_message_at", "assistant_response_at", "generated_at"):
        if key not in parsed:
            continue
        raw = parsed.get(key)
        if raw in (None, ""):
            continue
        parsed_clock = _parse_frontmatter_clock(raw)
        if parsed_clock is None:
            errors.append(
                f"line {line_no}: {key} must be ISO-8601, got {raw!r}"
            )
        else:
            clock_values[key] = parsed_clock
    user_clock = clock_values.get("user_message_at")
    assistant_clock = clock_values.get("assistant_response_at")
    if user_clock is not None and assistant_clock is not None:
        if user_clock > assistant_clock:
            errors.append(
                f"line {line_no}: user_message_at must be <= assistant_response_at"
            )

    sources = parsed.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append(f"line {line_no}: sources must be a non-empty list")

    primary_entity = str(parsed.get("entity", "")).strip()
    involves = parsed.get("involves")
    if item_type == "event":
        predicate = str(parsed.get("predicate", "")).strip()
        if not predicate:
            errors.append(f"line {line_no}: type event requires predicate")
        elif not _PREDICATE_RE.match(predicate):
            errors.append(f"line {line_no}: invalid predicate {predicate!r}")
        if involves is not None:
            errors.append(
                f"line {line_no}: type event must use participants, not involves"
            )
        participants = parsed.get("participants")
        if participants is not None:
            if not isinstance(participants, list) or not participants:
                errors.append(f"line {line_no}: participants must be a non-empty list")
            else:
                if len(participants) > MAX_INVOLVES:
                    errors.append(
                        f"line {line_no}: participants has {len(participants)} items "
                        f"(max {MAX_INVOLVES})"
                    )
                for idx, part in enumerate(participants):
                    if not isinstance(part, dict):
                        errors.append(
                            f"line {line_no}: participants[{idx}] must be a mapping"
                        )
                        continue
                    ent = str(part.get("entity", "")).strip()
                    if not ent:
                        errors.append(
                            f"line {line_no}: participants[{idx}] requires non-empty entity"
                        )
                    elif primary_entity and ent == primary_entity:
                        errors.append(
                            f"line {line_no}: participants must not repeat primary "
                            f"entity {primary_entity!r}"
                        )
    elif involves is not None:
        if not isinstance(involves, list) or not involves:
            errors.append(f"line {line_no}: involves must be a non-empty list")
        else:
            if len(involves) > MAX_INVOLVES:
                errors.append(
                    f"line {line_no}: involves has {len(involves)} items (max {MAX_INVOLVES})"
                )
            for idx, item in enumerate(involves):
                if isinstance(item, Mapping):
                    tag = str(item.get("entity", "")).strip()
                    if not tag:
                        errors.append(
                            f"line {line_no}: involves[{idx}] requires non-empty entity"
                        )
                    elif primary_entity and tag == primary_entity:
                        errors.append(
                            f"line {line_no}: involves must not repeat primary entity "
                            f"{primary_entity!r}"
                        )
                    role_raw = item.get("role", None)
                    if role_raw is not None and not str(role_raw).strip():
                        errors.append(
                            f"line {line_no}: involves[{idx}] role must be non-empty when present"
                        )
                elif isinstance(item, str):
                    tag = item.strip()
                    if not tag:
                        errors.append(
                            f"line {line_no}: involves[{idx}] must be a non-empty string"
                        )
                    elif primary_entity and tag == primary_entity:
                        errors.append(
                            f"line {line_no}: involves must not repeat primary entity "
                            f"{primary_entity!r}"
                        )
                else:
                    errors.append(
                        f"line {line_no}: involves[{idx}] must be a string or "
                        "{entity} map"
                    )

    related = parsed.get("related")
    if related is not None:
        if not isinstance(related, list) or not related:
            errors.append(f"line {line_no}: related must be a non-empty list")
        else:
            if len(related) > MAX_RELATED:
                errors.append(
                    f"line {line_no}: related has {len(related)} items (max {MAX_RELATED})"
                )
            for idx, ref in enumerate(related):
                ref_id = str(ref).strip()
                if not ref_id:
                    errors.append(f"line {line_no}: related[{idx}] must be a non-empty string")
                elif not _MEM_ID_RE.match(ref_id):
                    errors.append(
                        f"line {line_no}: related[{idx}] must match mem-id pattern, got {ref_id!r}"
                    )

    supersedes = parsed.get("supersedes")
    if supersedes is not None:
        if not isinstance(supersedes, list) or not supersedes:
            errors.append(f"line {line_no}: supersedes must be a non-empty list")
        else:
            if len(supersedes) > MAX_SUPERSEDES:
                errors.append(
                    f"line {line_no}: supersedes has {len(supersedes)} items "
                    f"(max {MAX_SUPERSEDES})"
                )
            for idx, ref in enumerate(supersedes):
                ref_id = str(ref).strip()
                if not ref_id:
                    errors.append(
                        f"line {line_no}: supersedes[{idx}] must be a non-empty string"
                    )
                elif not _MEM_ID_RE.match(ref_id):
                    errors.append(
                        f"line {line_no}: supersedes[{idx}] must match mem-id pattern, "
                        f"got {ref_id!r}"
                    )
            if confidence != "explicit":
                errors.append(
                    f"line {line_no}: supersedes links require confidence: explicit"
                )

    if not body:
        errors.append(f"line {line_no}: body is empty")
        return errors

    if len(body) > MAX_BODY_CHARS:
        errors.append(
            f"line {line_no}: body too long ({len(body)} > {MAX_BODY_CHARS} chars)"
        )
    if "\n" in body:
        errors.append(f"line {line_no}: body must be a single line")
    if body.lstrip().startswith("#"):
        errors.append(f"line {line_no}: body must not start with a heading")
    if "|" in body:
        errors.append(f"line {line_no}: body must not contain table syntax")
    if "```" in body:
        errors.append(f"line {line_no}: body must not contain code fences")

    errors.extend(_validate_temporal(line_no, parsed, body))
    return errors


def _validate_digest_content(
    content: str,
    *,
    extra_type_map: dict[str, str] | None = None,
) -> list[str]:
    if "## session summary" in content.lower():
        return ["session summary section is not allowed in daily staging"]

    blocks = _frontmatter_blocks(content)
    if not blocks:
        lowered = content.lower()
        if "nothing durable" in lowered or "no durable" in lowered or "skip" in lowered:
            return []
        return ["no typed YAML frontmatter blocks found"]

    errors: list[str] = []
    seen_ids: set[str] = set()
    for line_no, raw_frontmatter, body in blocks:
        errors.extend(_validate_block(line_no, raw_frontmatter, body, seen_ids))

    errors.extend(
        _validate_related_target_types(content, extra_type_map=extra_type_map)
    )
    return errors


def _tool_args_from_parsed_block(
    worker_type: str,
    parsed: Mapping[str, Any],
    body: str,
) -> dict[str, Any]:
    """Map a YAML worker block back to tool-arg shape for shared semantics."""
    bag: dict[str, Any] = {}
    slots = digest_tools.parse_rendered_body_slots(worker_type, body)
    bag.update(slots)
    for key in (
        "participants",
        "involves",
        "entity",
        "predicate",
        "confidence",
        "importance",
        "sources",
        "valid_from",
        "valid_to",
        "related",
        "supersedes",
        "id",
        "status",
    ):
        if key in parsed and parsed[key] not in (None, "", []):
            bag[key] = parsed[key]
    return bag


def _smoke_rendered_worker_yaml(worker_type: str, content: str) -> list[str]:
    """Post-render smoke: frontmatter must parse; type + body slots present."""
    text = str(content or "").strip()
    if not text or text == "skip" or _is_skip_only_content(text):
        return []
    assigned = LEGACY_TYPE_ALIASES.get(worker_type, worker_type)
    blocks = list(_frontmatter_blocks(text))
    if not blocks:
        return ["renderer smoke: no YAML frontmatter blocks"]
    errors: list[str] = []
    for _line_no, raw_frontmatter, body in blocks:
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError as exc:
            return [f"renderer smoke: invalid YAML frontmatter: {exc}"]
        if not isinstance(parsed, dict):
            return ["renderer smoke: frontmatter is not a mapping"]
        actual = str(parsed.get("type", "")).strip()
        canonical = LEGACY_TYPE_ALIASES.get(actual, actual)
        if canonical != assigned:
            errors.append(
                f"renderer smoke: type {actual or '<missing>'!r} != {assigned!r}"
            )
        for err in digest_tools.validate_rendered_body_slots(assigned, body):
            errors.append(f"renderer smoke: {err}")
    return list(dict.fromkeys(errors))


def _validate_worker_output(
    content: str,
    worker_type: str,
    *,
    extra_type_map: dict[str, str] | None = None,
) -> list[str]:
    """Legacy YAML-worker gate. Type-A Phase-1 uses ``accept_phase1_args`` only.

    File-format errors come from ``_validate_digest_content``. Semantic rules
    (participants, ownership, Narration) reuse ``validate_worker_tool_args``
    after a successful YAML parse so dict and string paths stay single-sourced.
    """
    raw_worker_type = str(worker_type).strip()
    assigned = LEGACY_TYPE_ALIASES.get(raw_worker_type, raw_worker_type)
    if assigned not in NEW_OUTPUT_TYPES:
        return [f"unknown assigned worker type {worker_type!r}"]

    errors = list(_validate_digest_content(content, extra_type_map=extra_type_map))
    blocks = _frontmatter_blocks(content)
    user_subjects = _decision_user_subjects()
    for line_no, raw_frontmatter, body in blocks:
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        actual = str(parsed.get("type", "")).strip()
        canonical_actual = LEGACY_TYPE_ALIASES.get(actual, actual)
        if canonical_actual != assigned:
            errors.append(
                f"line {line_no}: assigned worker type {assigned} returned type "
                f"{actual or '<missing>'}"
            )
            continue

        bag = _tool_args_from_parsed_block(assigned, parsed, body)
        for err in digest_tools.validate_worker_tool_args(
            assigned, bag, user_subjects=user_subjects
        ):
            errors.append(f"line {line_no}: {err}")

    return list(dict.fromkeys(errors))


def _week_alive_block_ids() -> set[str]:
    """Mem-ids from the last seven daily staging files (related window)."""
    alive: set[str] = set()
    for path in _daily_files_for_tier():
        try:
            text_d = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for _line_no, raw_frontmatter, _body in _frontmatter_blocks(text_d):
            try:
                parsed = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(parsed, dict):
                continue
            block_id = str(parsed.get("id", "")).strip()
            if block_id:
                alive.add(block_id)
    return alive


def _validate_digest_file(
    content: str,
    *,
    alive_ids: set[str] | None = None,
) -> list[str]:
    """Validate a whole daily staging file (no hard block-count cap)."""
    if "## session summary" in content.lower():
        return ["session summary section is not allowed in daily staging"]

    blocks = _frontmatter_blocks(content)
    if not blocks:
        return ["candidate produced no typed YAML frontmatter blocks"]

    errors: list[str] = []
    seen_ids: set[str] = set()
    for line_no, raw_frontmatter, body in blocks:
        errors.extend(_validate_block(line_no, raw_frontmatter, body, seen_ids))

    errors.extend(_validate_related_target_types(content))
    ids = set(_id_type_map_from_content(content))
    allowed_related = set(ids)
    if alive_ids is not None:
        allowed_related |= set(alive_ids)
    else:
        allowed_related |= _week_alive_block_ids()
    for line_no, raw_frontmatter, _body in blocks:
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        block_id = str(parsed.get("id", "")).strip() or f"line {line_no}"
        related = parsed.get("related")
        if isinstance(related, list):
            for ref in related:
                ref_id = str(ref).strip()
                if ref_id and ref_id not in allowed_related:
                    errors.append(
                        f"line {line_no}: dangling related reference "
                        f"{block_id} -> {ref_id}"
                    )
    return errors


def _scrub_dangling_related(
    content: str,
    *,
    extra_keep_ids: set[str] | None = None,
    retiring_ids: set[str] | None = None,
) -> str:
    """Remove related targets that are neither on-file nor week-alive."""
    parsed_blocks: list[tuple[dict[str, Any], str]] = []
    for _line_no, raw_frontmatter, body in _frontmatter_blocks(content):
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict):
            parsed_blocks.append((dict(parsed), body))
    ids = {
        str(parsed.get("id", "")).strip()
        for parsed, _body in parsed_blocks
        if str(parsed.get("id", "")).strip()
    }
    keep = set(ids)
    if extra_keep_ids:
        keep |= {str(item).strip() for item in extra_keep_ids if str(item).strip()}
    if retiring_ids:
        keep -= {str(item).strip() for item in retiring_ids if str(item).strip()}
    rendered: list[str] = []
    for parsed, body in parsed_blocks:
        related = parsed.get("related")
        if isinstance(related, list):
            parsed["related"] = [
                ref for ref in related if str(ref).strip() in keep
            ]
            if not parsed["related"]:
                parsed.pop("related", None)
        rendered.append(_render_digest_block(parsed, body))
    return "\n\n".join(rendered).rstrip() + "\n" if rendered else ""


def _legal_source_tags(sources: Any) -> list[str]:
    """Keep session locators, sheet:, and non-staging file: tags."""
    if not isinstance(sources, list):
        return []
    kept: list[str] = []
    seen: set[str] = set()
    for item in sources:
        tag = str(item).strip()
        if not tag or tag in seen:
            continue
        if digest_tools.is_memory_staging_source_tag(tag):
            continue
        if digest_tools.session_id_from_source_tag(tag):
            kept.append(tag)
            seen.add(tag)
            continue
        if tag.startswith("sheet:") or tag.startswith("file:"):
            kept.append(tag)
            seen.add(tag)
    return kept


def _scrub_illegal_sources(content: str) -> str:
    """Drop sources that cite memory staging files (same fail-open as related)."""
    rendered: list[str] = []
    for _line_no, raw_frontmatter, body in _frontmatter_blocks(content):
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        parsed = dict(parsed)
        if "sources" in parsed:
            kept = _legal_source_tags(parsed.get("sources"))
            if kept:
                parsed["sources"] = kept
            else:
                parsed.pop("sources", None)
        rendered.append(_render_digest_block(parsed, body))
    return "\n\n".join(rendered).rstrip() + "\n" if rendered else ""


def _id_type_map_from_content(content: str) -> dict[str, str]:
    """Map mem-id → type for every parseable frontmatter block in content."""
    type_map: dict[str, str] = {}
    for _line_no, raw_frontmatter, _body in _frontmatter_blocks(content):
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        block_id = str(parsed.get("id", "")).strip()
        item_type = str(parsed.get("type", "")).strip()
        if block_id and item_type:
            type_map[block_id] = item_type
    return type_map


def _validate_related_target_types(
    content: str,
    *,
    extra_type_map: dict[str, str] | None = None,
) -> list[str]:
    """Reject event.related that points at another event (episode-merge instead)."""
    type_map = dict(extra_type_map or {})
    type_map.update(_id_type_map_from_content(content))
    errors: list[str] = []
    for line_no, raw_frontmatter, _body in _frontmatter_blocks(content):
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("type", "")).strip() != "event":
            continue
        block_id = str(parsed.get("id", "")).strip() or f"line {line_no}"
        related = parsed.get("related")
        if not isinstance(related, list):
            continue
        for ref in related:
            rid = str(ref).strip()
            if not rid:
                continue
            if type_map.get(rid) == "event":
                errors.append(
                    f"line {line_no}: event {block_id} related must not point at "
                    f"event {rid} — episode-merge instead"
                )
    return errors


def split_daily_wrapup(content: str) -> tuple[str, str]:
    """Peel ``## Day wrap-up`` so fence parsers never treat bullets as a body.

    Last heading wins. Trailer is every following non-empty line (catalog
    bullets as ``- …``). Missing trailer → empty string.
    """
    text = content or ""
    matches = list(_WRAPUP_HEADING_RE.finditer(text))
    if not matches:
        return text, ""
    mark = matches[-1]
    fences = text[: mark.start()].rstrip()
    if fences:
        fences += "\n"
    rest = text[mark.end() :].strip()
    lines = [ln.rstrip() for ln in rest.splitlines() if ln.strip()]
    return fences, "\n".join(lines)


def format_wrapup_body(raw: str | Iterable[Any]) -> str:
    """Turn model output into markdown bullets so events stay independent sentences."""
    if isinstance(raw, str):
        items = [ln for ln in raw.splitlines() if ln.strip()]
        if len(items) <= 1 and raw.strip() and "\n" not in raw:
            items = [raw]
    else:
        items = list(raw)
    bullets: list[str] = []
    for item in items:
        cleaned = clamp_wrapup_phrase(str(item or "").lstrip("-").strip())
        if cleaned:
            bullets.append(f"- {cleaned}")
    return "\n".join(bullets)


def join_daily_wrapup(fences: str, phrase: str) -> str:
    """Re-attach event bullets after fences so Phase-2 rewrite cannot drop them."""
    body = (fences or "").rstrip()
    cleaned = format_wrapup_body(phrase)
    if not cleaned:
        return body + ("\n" if body else "")
    if body:
        return f"{body}\n\n{DAY_WRAPUP_HEADING}\n{cleaned}\n"
    return f"{DAY_WRAPUP_HEADING}\n{cleaned}\n"


def clamp_wrapup_phrase(raw: str) -> str:
    """Keep one catalog bullet without splitting on '.' so mimo-v2.5 stays intact.

    Period-splitting cut worker_llm.py and model names; length is per bullet.
    """
    cleaned = " ".join(str(raw or "").split())
    if not cleaned:
        return ""
    if len(cleaned) > MAX_WRAPUP_CHARS:
        cleaned = cleaned[:MAX_WRAPUP_CHARS].rstrip()
    return cleaned


def run_day_wrapup(daily_path: Path | str) -> dict[str, Any]:
    """Refresh ``## Day wrap-up`` once at 23:55 from typed JSON cards, never YAML."""
    path = Path(daily_path)
    if not path.exists():
        return {"outcome": "skipped", "reason": "missing"}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"outcome": "skipped", "reason": str(exc)}
    blocks = _daily_blocks(text)
    if not blocks:
        return {"outcome": "skipped", "reason": "empty"}
    prompt = digest_dedup_prompt.build_wrapup_prompt(blocks)
    capture = _invoke_digest_oneshot_tool(
        prompt,
        "cli",
        purpose="digest-wrapup",
        force_tool_name="submit_day_wrapup",
    )
    if capture.get("failed"):
        return {
            "outcome": "failed",
            "error": str(capture.get("error") or capture.get("final_response") or ""),
        }
    _name, args = digest_tools.parse_tool_args_from_result(capture)
    raw: str | list[Any] = ""
    if isinstance(args, Mapping):
        listed = args.get("phrases")
        if isinstance(listed, list) and listed:
            raw = listed
        else:
            raw = str(args.get("phrase") or "")
    phrase = format_wrapup_body(raw)
    if not phrase:
        return {"outcome": "failed", "error": "blank wrap-up phrase"}
    fences, _old = split_daily_wrapup(text)
    _rewrite_daily_file(path, join_daily_wrapup(fences, phrase))
    extra = {
        k: capture.get(k)
        for k in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
            "finish_reason",
            "max_tokens",
        )
        if k in capture
    }
    return {"outcome": "written", "phrase": phrase, **extra}


def _append_daily_digest(daily_path: Path, content: str) -> None:
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if daily_path.exists():
        existing = daily_path.read_text(encoding="utf-8")
    fences, phrase = split_daily_wrapup(existing)
    chunk = content.rstrip() + "\n"
    if fences.strip():
        combined = fences.rstrip() + "\n\n" + chunk
    else:
        combined = chunk
    _rewrite_daily_file(daily_path, join_daily_wrapup(combined, phrase))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rewrite_daily_file(daily_path: Path, content: str) -> None:
    """Atomically replace the day's staging file."""
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(daily_path.parent), suffix=".tmp", prefix=".digest_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, daily_path)
        try:
            directory_fd = os.open(str(daily_path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # The replacement is still atomic; some filesystems reject
            # directory fsync, so treat this as a durability limitation.
            pass
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _staging_output_example() -> str:
    return (
        "EXAMPLE (fact with span):\n"
        "---\n"
        "id: mem-20260617-casey-canteen\n"
        "type: fact\n"
        "entity: Casey\n"
        "valid_from: 2026-06-14\n"
        "valid_to: open\n"
        "confidence: high\n"
        f"importance: {IMPORTANCE_DEFAULT}\n"
        "status: candidate\n"
        "sources: [session example]\n"
        "---\n"
        "Casey (student): parents chose home-packed lunch; school card is binary canteen vs gate-only.\n"
        "\n"
        "EXAMPLE (event + linked procedure):\n"
        "---\n"
        "id: mem-20260725-lit-review-chapter\n"
        "type: event\n"
        "entity: 论文文献综述\n"
        "predicate: user_requested_lit_review_chapter\n"
        "participants:\n"
        "  - {entity: User, role: requester}\n"
        "  - {entity: Assistant, role: executor}\n"
        "related: [mem-20260725-ilink-file-push]\n"
        "valid_from: 2026-07-25\n"
        "valid_to: 2026-07-25\n"
        "confidence: explicit\n"
        f"importance: {IMPORTANCE_DEFAULT}\n"
        "status: candidate\n"
        "sources: [session example, file:/tmp/章节稿.html]\n"
        "---\n"
        "Beginning: user requested the chapter; Course: assistant reviewed sources; Outcome: chapter pushed to WeChat.\n"
        "\n"
        "---\n"
        "id: mem-20260725-ilink-file-push\n"
        "type: procedure\n"
        "entity: Hermes\n"
        "confidence: explicit\n"
        f"importance: {IMPORTANCE_DEFAULT}\n"
        "status: candidate\n"
        "sources: [session example, file:gateway/platforms/weixin.py]\n"
        "---\n"
        "Obstacle: the delivery path lacked a direct file flag; Solution: Assistant delivered the chapter file for this request using an abstract file-delivery procedure, with the tool recorded in sources.\n"
        "\n"
        "EXAMPLE (user correction / feedback with supersedes):\n"
        "---\n"
        "id: mem-20260725-resume-push-correction\n"
        "type: decision\n"
        "entity: User\n"
        "confidence: explicit\n"
        f"importance: {IMPORTANCE_DEFAULT}\n"
        "status: candidate\n"
        "supersedes: [mem-20260725-user-deliverables]\n"
        "sources: [session example]\n"
        "---\n"
        "Decision: user corrected that 简历 was pushed separately on the evening of 7-25, not in the same batch as 技术成果证明.\n"
    )


def _staging_output_contract(daily_path: Path) -> str:
    """Teach the worker the bilingual entity field so original-language names are not lost on English canonicalization."""
    return (
        "OUTPUT CONTRACT (reference / legacy text path):\n"
        "- Prefer forced tool calls (submit_*/patch_*/skip_digest_worker) for workers.\n"
        "- If emitting text: return ONLY markdown YAML frontmatter blocks, or one skip line.\n"
        "- Use this skeleton for every block (fill in <> only; keep keys and order):\n"
        "  ---\n"
        "  id: mem-<YYYY-MM-DD>-<slug>\n"
        "  type: fact | procedure | decision | event\n"
        "  entity: <Person|Class|Project>  # required on fact and event; English canonical\n"
        "  entity_aliases: [<original-language surface>]  # optional; omit when identical to entity\n"
        "  predicate: <snake_case>  # required on event only (e.g. grade_dispute)\n"
        "  participants:\n"
        "    - {entity: <Name>, role: <optional>}  # event only; max 5; role optional\n"
        "  involves:\n"
        "    - {entity: <OtherEntity>, role: <optional>}  # non-event cast; max 5; role only when clear; omit primary\n"
        f"  related: [mem-<YYYY-MM-DD>-<slug>]  # optional; max {MAX_RELATED}; event→non-event only\n"
        f"  supersedes: [mem-<YYYY-MM-DD>-<slug>]  # correction only; max {MAX_SUPERSEDES}; requires confidence: explicit\n"
        "  valid_from: <YYYY-MM-DD>  # required on event; on time-bound facts; else omit both span keys\n"
        "  valid_to: open | <YYYY-MM-DD>  # required on event\n"
        "  confidence: explicit | high | medium | low\n"
        f"  importance: {IMPORTANCE_WRITE_MIN}–{IMPORTANCE_MAX}  "
        f"# required on new blocks; 5=most important\n"
        "  status: candidate\n"
        "  sources: [session <session_id>#<start>-<end>]  # code-stamped window; optional file:/sheet: (not staging dailies)\n"
        "  ---\n"
        f"  <one sentence body, <= {MAX_BODY_CHARS} chars>\n"
        "- Target file (must be .md, never .yaml): "
        f"{daily_path}\n"
        "- type must be one of: fact | procedure | decision | event. "
        "Do NOT emit type: entity (entity is a frontmatter tag, not a block type). "
        "Do NOT emit type: hypothesis (owned by the weekly worker).\n"
        "- `decision_constraint` is accepted only as a legacy input alias; emit canonical `decision`.\n"
        "- User messages are the primary evidence. Event output has exactly three stages: "
        "Beginning, Course, Outcome, in that order and no additional stages.\n"
        "- Event slots: beginning, course, outcome — each one concise sentence "
        "(code renders Beginning:/Course:/Outcome:).\n"
        "- event owns the user goal, important phases, and current result/status; it must not "
        "duplicate detailed facts, preferences, speculation, or raw tool logs.\n"
        "- fact owns stable observations and must not contain agent process or user preference. "
        "Use kind=Factual + content for one observation, or kind=Narration + content + involves cast "
        "`{entity, role?}` (role optional) for a multi-person/multi-facet story.\n"
        "- procedure owns course obstacles and abstract solutions, not raw tool logs or the event outcome.\n"
        "- Procedure slots: obstacle, solution (code renders Obstacle:/Solution:); keep raw tool logs in sources only.\n"
        "- decision owns user-only rulings/preferences for agent behavior (first subject user/User + USER.md aliases), "
        "not third-party traits and not the full procedure or event summary. "
        "Look in the transcript for the user's must / must-not / standing prefs; skip if the user made no ruling.\n"
        "- Decision slots: kind=Preference|Decision, subject=user (USER.md aliases ok), "
        "ruling=predicate for that subject (e.g. must not auto-drop events); "
        "assembled `{kind}: {subject} {ruling}` is one clause.\n"
        "- Each worker result is a temporary worker result envelope for the organizer; it is not a persistent memory type.\n"
        "- Every block needs frontmatter keys: id, type, confidence, status, sources.\n"
        f"- New blocks: set importance: integer {IMPORTANCE_WRITE_MIN}–{IMPORTANCE_MAX} "
        f"(5=most important). Do not invent 0 at create time.\n"
        "- `entity:` frontmatter is REQUIRED on fact and event.\n"
        f"- One sentence per body, <= {MAX_BODY_CHARS} characters, no line breaks, no bullets, no tables.\n"
        "- status must be candidate, approved, or rejected; use candidate by default. "
        "`dropped` belongs to the update operator — never emit it.\n"
        "- Time-bound facts: add `valid_from` and `valid_to` (YYYY-MM-DD; use `valid_to: open` if the "
        "end is unknown). event blocks always require both span keys. Use absolute ISO dates only. "
        "Do NOT use `as of` here (promotion-only).\n"
        "- Deliverables (xlsx, drafts sent) → event with predicate; stable truths → fact; "
        "multi-person background → one Narration fact with involves cast.\n"
        "- event blocks use participants (not involves); role optional — omit when ambiguous.\n"
        "- involves on non-event is an entity collection like participants: `{entity, role?}`; "
        "legacy bare strings are accepted on read and coerced to `{entity}`.\n"
        "- Roster overflow (>5 people): collective entity + key participants + related roster fact; "
        "sources may include file: or sheet: tags.\n"
        "- User correction blocks: confidence: explicit and supersedes: [mem-…-prior] required; "
        "related: is associative and never deletes; event.related must not point at another event.\n"
        "- Outcomes, not drafts: never paste full WeChat/email text.\n"
        "- English dominant in body text; Chinese only for names/titles/quotes.\n"
        "- Do NOT use the memory tool. Do NOT write MEMORY.md or USER.md.\n"
        "- Do NOT add any session-summary heading or recap section.\n"
        "- If nothing durable happened, return a single line saying nothing durable to stage.\n"
        f"{_staging_output_example()}"
    )


def _session_id_from_sources(sources: Any) -> str:
    if not isinstance(sources, list):
        return ""
    for item in sources:
        sid = digest_tools.session_id_from_source_tag(str(item))
        if sid:
            return sid
    return ""


def _validation_failure_note(session_id: str, errors: list[str]) -> str:
    joined = "; ".join(errors[:5])
    return (
        f"digest validation failed session={session_id}; "
        f"no staging items appended; errors: {joined}"
    )


def _build_retry_section(
    attempt: int,
    errors: list[str],
    previous_output: str,
    *,
    max_attempts: int | None = None,
) -> str:
    max_a = max_attempts or MAX_VALIDATION_ATTEMPTS
    error_lines = "\n".join(f"- {err}" for err in errors[:MAX_ERRORS_IN_PROMPT])
    previous = previous_output[:MAX_RETRY_ERROR_CHARS] if previous_output else "(empty response)"
    format_nudge = ""
    if any("no typed YAML frontmatter blocks" in err for err in errors):
        format_nudge = (
            "\nFormat reminder: output must be ONLY `---` YAML frontmatter blocks "
            "with a one-line body after each closing `---`. No prose, headings, or markdown wrappers.\n"
        )
    return (
        f"\n\nVALIDATION FAILED (attempt {attempt} of {max_a}).\n"
        "Your previous output did not pass the plugin validator. Fix ONLY the listed issues.\n"
        "Do not add new facts. Re-emit corrected blocks OR the skip line if nothing durable.\n"
        f"{format_nudge}\n"
        "Validator errors:\n"
        f"{error_lines}\n\n"
        f"Your previous output (truncated to {MAX_RETRY_ERROR_CHARS} chars):\n"
        f"{previous}\n"
    )


def _build_commit_retry_section(
    attempt: int,
    errors: list[str],
    assembled_snippet: str,
    *,
    max_attempts: int | None = None,
) -> str:
    """Prompt suffix when the assembled candidate fails commit validation."""
    max_a = max_attempts or MAX_COMMIT_ATTEMPTS
    error_lines = "\n".join(f"- {err}" for err in errors[:MAX_ERRORS_IN_PROMPT])
    previous = (
        assembled_snippet[:MAX_RETRY_ERROR_CHARS]
        if assembled_snippet
        else "(empty candidate)"
    )
    return (
        f"\n\nCOMMIT VALIDATION FAILED (attempt {attempt} of {max_a}).\n"
        "The assembled daily candidate did not pass the plugin validator. "
        "Fix ONLY the listed issues in your worker output.\n"
        "Do not add new facts. Re-emit corrected blocks OR the skip line if nothing durable.\n\n"
        "Validator errors:\n"
        f"{error_lines}\n\n"
        f"Assembled candidate (truncated to {MAX_RETRY_ERROR_CHARS} chars):\n"
        f"{previous}\n"
    )


def _expand_commit_errors(errors: list[str]) -> list[str]:
    expanded: list[str] = []
    for err in errors:
        parts = [part.strip() for part in str(err).split("; ") if part.strip()]
        expanded.extend(parts or [str(err)])
    return expanded


def _block_type_at_line(content: str, line_no: int) -> str | None:
    """Return the frontmatter type owning 1-based ``line_no``, if any."""
    for start_line, raw_frontmatter, _body in _frontmatter_blocks(content):
        if line_no < start_line:
            continue
        try:
            parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        item_type = str(parsed.get("type", "")).strip()
        if item_type:
            return LEGACY_TYPE_ALIASES.get(item_type, item_type)
    return None


def _worker_types_for_commit_errors(
    errors: list[str],
    workers: Sequence[ValidatedWorkerResult],
) -> set[str]:
    """Map commit validation errors to worker types; fall back to all workers."""
    all_types = {w.worker_type for w in workers} or set(NEW_OUTPUT_TYPES)
    expanded = _expand_commit_errors(errors)
    if any("artifact" in err.lower() for err in expanded):
        return set(all_types)

    types: set[str] = set()
    if any("body too long" in err for err in expanded):
        for worker in workers:
            for block in worker.blocks:
                if len(str(block.get("body", ""))) > MAX_BODY_CHARS:
                    types.add(worker.worker_type)

    combined = "\n\n".join(worker.content for worker in workers)
    for err in expanded:
        match = re.match(r"line (\d+):", err)
        if not match:
            continue
        block_type = _block_type_at_line(combined, int(match.group(1)))
        if block_type in NEW_OUTPUT_TYPES:
            types.add(block_type)

    types = {t for t in types if t in NEW_OUTPUT_TYPES}
    return types or set(all_types)


def _is_skip_only_content(content: str) -> bool:
    if _frontmatter_blocks(content):
        return False
    lowered = content.lower()
    return "nothing durable" in lowered or "no durable" in lowered or "skip" in lowered


def _event_first_is_nothing_durable(
    event_result: ValidatedWorkerResult,
    detail_results: Sequence[ValidatedWorkerResult],
) -> bool:
    """True when all workers returned skip prose with no typed blocks to stage."""
    combined = "\n\n".join(
        [event_result.content, *[result.content for result in detail_results]]
    ).strip()
    if not combined:
        return True
    if _daily_blocks(combined):
        return False
    return _is_skip_only_content(combined)


def _format_existing_id_snippet(body: str, block_type: str) -> str:
    """Truncate body for EXISTING BLOCK IDS while preserving validator prefixes."""
    snippet = body.strip()
    if len(snippet) > 60:
        snippet = snippet[:59] + "…"
    canonical = LEGACY_TYPE_ALIASES.get(block_type, block_type)
    lowered = snippet.lstrip()
    if canonical == "event" and not lowered.startswith("Beginning:"):
        return f"[needs Beginning:/Course:/Outcome:] {snippet}"
    if canonical == "procedure" and not lowered.startswith("Obstacle:"):
        return f"[needs Obstacle:/Solution:] {snippet}"
    if canonical == "decision" and not (
        lowered.startswith("Decision:") or lowered.startswith("Preference:")
    ):
        return f"[needs Decision: or Preference: prefix] {snippet}"
    if canonical == "fact" and not (
        lowered.startswith("Narration:") or lowered.startswith("Factual:")
    ):
        # Legacy unprefixed facts are still valid; no rewrite hint required.
        return snippet
    return snippet


def _build_existing_ids_section(date_str: str | None = None) -> str:
    """List this board day's mem-ids so leftover catch-up cannot supersede wall-today cards.

    related: may still cite week-alive ids; only this catalogue is the leftover
    civil day. Missing that file stays empty rather than falling back to wall today.
    """
    id_lines: list[str] = []
    seen_ids: set[str] = set()
    today_path = daily_staging_path(
        _hermes_home(), date_str or hermes_local_today_str()
    )
    paths = [today_path] if today_path.exists() else []
    for path in paths:
        try:
            existing_text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not existing_text.strip():
            continue
        for _line_no, raw_frontmatter, body in _frontmatter_blocks(existing_text):
            try:
                parsed = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(parsed, dict):
                continue
            block_id = str(parsed.get("id", "")).strip()
            if not block_id or block_id in seen_ids:
                continue
            seen_ids.add(block_id)
            entity = str(parsed.get("entity", "")).strip() or "(no entity)"
            block_type = str(parsed.get("type", "")).strip() or "(no type)"
            snippet = _format_existing_id_snippet(body, block_type)
            id_lines.append(
                f"- type: {block_type} | {block_id} | file: {path.name} | "
                f"entity: {entity} | {snippet}"
            )
    if not id_lines:
        return ""
    return (
        "EXISTING BLOCK IDS (current-day staging for supersedes; "
        "related: may cite week-alive tier-1+tier-2 ids):\n"
        "Snippets may be truncated; fill tool-call slots with full "
        "validator-compliant values.\n"
        + "\n".join(id_lines)
        + "\n\n"
    )


def _build_digest_shared_context(
    session_id: str,
    platform: str,
    reason: str,
    *,
    user_count: int,
    assistant_count: int,
    daily_path: Path,
    run_id: str = "",
) -> str:
    """Policy + metadata + existing IDs shared by all event-first workers."""
    run_line = f"Run: {run_id}\n" if run_id else ""
    return (
        f"Session: {session_id}\n"
        f"Platform: {platform}\n"
        f"Trigger: {reason}\n"
        f"{run_line}"
        f"User messages in window: {user_count}\n"
        f"Assistant messages in window: {assistant_count}\n\n"
        "DIGEST POLICY:\n"
        f"{DIGEST_POLICY}\n\n"
        f"{_build_existing_ids_section(date_str=daily_path.stem)}"
        f"Target daily file (must be .md, never .yaml): {daily_path}\n"
    )


def _build_prompt(
    session_id: str,
    platform: str,
    messages: list[dict[str, Any]],
    reason: str,
    *,
    worker_type: str = "event",
) -> str:
    """Legacy-compatible full prompt (tests + diagnostics). Production uses
    ``_format_transcript`` + ``_worker_prompt`` instead of nesting this blob.
    """
    today = hermes_local_today_str()
    daily_path = daily_staging_path(_hermes_home(), today)
    transcript = _format_transcript(messages)
    user_count, assistant_count = _role_counts(messages)
    shared = _build_digest_shared_context(
        session_id,
        platform,
        reason,
        user_count=user_count,
        assistant_count=assistant_count,
        daily_path=daily_path,
    )
    return (
        "You are the memory digest worker. Stage durable observations as light index cards.\n\n"
        f"{_staging_output_contract(daily_path)}\n\n"
        f"{shared}"
        + f"Assigned worker type: {worker_type} "
        f"(return {worker_type} blocks only; detail types are not accepted here).\n\n"
        "TRANSCRIPT (recent user/assistant messages):\n"
        f"{transcript}\n"
    )


def _invoke_digest_llm(prompt: str, platform: str, *, purpose: str = "digest") -> str:
    """Text-only worker invoke (non-tool helpers). Workers prefer tool path."""
    return _clean_digest_response(
        run_worker_llm(
            prompt,
            plugin="memory-digest",
            purpose=purpose,
            platform=platform or "cli",
            max_iterations=15,
        )
    )


def _invoke_digest_worker_tool(
    prompt: str,
    platform: str,
    *,
    purpose: str,
    force_tool_name: str = "",
    allowed_tool_names: list[str] | None = None,
    max_iterations: int = 2,
) -> dict[str, Any]:
    """Digest tool call capture; empty capture on failure for legacy fallback.

    Phase-1 type A passes ``allowed_tool_names`` (submit|patch|skip) with a
    higher ``max_iterations`` budget. Phase-2/weekly keep ``force_tool_name``.
    """
    try:
        digest_tools.ensure_digest_tools_registered()
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "plugin": "memory-digest",
            "purpose": purpose,
            "platform": platform or "cli",
            "max_iterations": max_iterations,
            "enabled_toolsets": [digest_tools.DIGEST_TOOLSET],
        }
        if allowed_tool_names:
            kwargs["allowed_tool_names"] = list(allowed_tool_names)
        elif force_tool_name:
            kwargs["force_tool_name"] = force_tool_name
        return run_worker_llm_tools(**kwargs)
    except Exception as exc:
        return {
            "final_response": "",
            "tool_name": None,
            "tool_args": None,
            "tool_calls": [],
            "messages": [],
            "failed": True,
            "error": str(exc),
        }


def _invoke_digest_oneshot_tool(
    prompt: str,
    platform: str,
    *,
    purpose: str,
    force_tool_name: str = "",
    allowed_tool_names: list[str] | None = None,
    max_iterations: int = 2,
) -> dict[str, Any]:
    """One OpenAI-compatible tool call. No Hermes AIAgent / run_worker_llm_tools.

    Raise completion budget past the shared oneshot default so a four-type
    ``submit_operations`` payload is not cut off at 2048 tokens.
    """
    del platform, allowed_tool_names, max_iterations
    name = str(force_tool_name or "").strip()
    schema = None
    budget = ONESHOT_DIGEST_MAX_TOKENS
    if name == "submit_operations":
        schema = digest_tools.submit_operations_schema()
    elif name == "patch_operations":
        schema = digest_tools.patch_operations_schema()
    elif name == "submit_day_wrapup":
        schema = digest_tools.submit_day_wrapup_schema()
        budget = ONESHOT_WRAPUP_MAX_TOKENS
    try:
        return run_worker_llm_oneshot(
            prompt,
            plugin="memory-digest",
            purpose=purpose or "digest-dedup",
            force_tool_name=name or None,
            tool_schema=schema,
            max_tokens=budget,
        )
    except Exception as exc:
        return {
            "final_response": str(exc),
            "tool_name": None,
            "tool_args": None,
            "tool_calls": [],
            "messages": [],
            "failed": True,
            "error": str(exc),
        }


def _finalize_digest_success(
    session_key: str,
    batch_end_id: int | None,
    *,
    session_id: str | None = None,
) -> None:
    """Advance the session bookmark after a committed digest (or durable skip).

    Always upserts state: a successful daily write must move the bookmark even
    if the session entry was missing/cleared mid-run.
    """
    with _digest_lock:
        state = _load_state()
        sessions = state.setdefault("sessions", {})
        entry = sessions.get(session_key)
        if entry is None:
            entry = {}
            _log(
                f"bookmark advance recreating missing state "
                f"session_key={session_key} batch_end_id={batch_end_id}"
            )
        if session_id and not entry.get("session_id"):
            entry["session_id"] = session_id
        if batch_end_id is not None:
            entry["last_digest_message_id"] = batch_end_id
        entry["last_digest_at"] = datetime.now(timezone.utc).isoformat()
        entry["digest_in_flight"] = False
        entry["in_flight_batch_end_id"] = None
        entry.pop("last_digest_failure_at", None)
        entry["last_digest_attempts"] = 0
        sessions[session_key] = entry
        _save_state(state)


def _finalize_digest_failure(session_key: str, session_id: str, errors: list[str]) -> None:
    with _digest_lock:
        state = _load_state()
        entry = state.get("sessions", {}).get(session_key)
        if entry is not None:
            entry["digest_in_flight"] = False
            entry["in_flight_batch_end_id"] = None
            entry["last_digest_failure_at"] = datetime.now(timezone.utc).isoformat()
            entry["last_digest_attempts"] = MAX_VALIDATION_ATTEMPTS
            state["sessions"][session_key] = entry
            _save_state(state)
    _log(_validation_failure_note(session_id, errors))


def _worker_result_blocks(content: str) -> tuple[dict[str, Any], ...]:
    blocks: list[dict[str, Any]] = []
    for _line_no, raw_frontmatter, body in _frontmatter_blocks(content):
        parsed = yaml.safe_load(raw_frontmatter)
        if isinstance(parsed, dict):
            block = dict(parsed)
            block["body"] = body.strip()
            blocks.append(block)
    return tuple(blocks)


def _content_from_blocks(blocks: Sequence[Mapping[str, Any]]) -> str:
    """Render block dicts to daily-style YAML markdown (derived view)."""
    rendered: list[str] = []
    for raw in blocks:
        parsed = dict(raw)
        body = str(parsed.pop("body", "")).strip()
        rendered.append(_render_digest_block(parsed, body))
    return "\n\n".join(rendered).rstrip() + ("\n" if rendered else "")


def _flatten_worker_blocks(
    *results: ValidatedWorkerResult,
) -> list[dict[str, Any]]:
    """Deep-copy blocks from validated workers for proposer / ops input."""
    out: list[dict[str, Any]] = []
    for result in results:
        for block in result.blocks:
            out.append(copy.deepcopy(dict(block)))
    return out


_artifact_manifest_lock = threading.Lock()


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _store_validated_worker_result(result: ValidatedWorkerResult) -> ValidatedWorkerResult:
    directory = _hermes_home() / "memories" / "staging" / ".tmp_mem_files" / result.session_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / WORKER_ARTIFACT_FILENAMES[result.worker_type]
    # blocks are authoritative; content is not persisted (derive when needed)
    payload = {
        "status": "validated",
        "session_id": result.session_id,
        "run_id": result.run_id,
        "worker_type": result.worker_type,
        "attempts": result.attempts,
        "blocks": list(result.blocks),
        "accepted_dirty": bool(result.accepted_dirty),
    }
    _atomic_json_write(path, payload)
    manifest_path = directory / "worker-manifest.json"
    with _artifact_manifest_lock:
        manifest = {}
        if manifest_path.exists():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    isinstance(loaded, dict)
                    and loaded.get("session_id") == result.session_id
                    and loaded.get("run_id") == result.run_id
                ):
                    manifest = loaded
            except (OSError, json.JSONDecodeError):
                manifest = {}
        workers = (
            dict(manifest.get("workers", {}))
            if isinstance(manifest.get("workers"), dict)
            else {}
        )
        workers[result.worker_type] = path.name
        manifest.update(
            {
                "session_id": result.session_id,
                "run_id": result.run_id,
                "status": "validated",
                "workers": workers,
            }
        )
        _atomic_json_write(manifest_path, manifest)
    return ValidatedWorkerResult(**{**result.__dict__, "path": path})


def _append_worker_failure_record(
    *,
    worker_type: str,
    session_id: str,
    run_id: str,
    attempt: int,
    max_attempts: int,
    errors: list[str],
    content: str,
    exhausted: bool,
) -> Path:
    directory = _hermes_home() / "memories" / "staging" / ".tmp_mem_files" / session_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / WORKER_FAILURE_FILENAMES[worker_type]
    record = {
        "status": "exhausted" if exhausted else "validation_failed",
        "session_id": session_id,
        "run_id": run_id,
        "worker_type": worker_type,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "errors": list(errors),
        "content": content,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with _artifact_manifest_lock:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return path


def _mark_worker_failed_in_manifest(
    *,
    worker_type: str,
    session_id: str,
    run_id: str,
    attempts: int,
) -> None:
    directory = _hermes_home() / "memories" / "staging" / ".tmp_mem_files" / session_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "worker-manifest.json"
    failure_name = WORKER_FAILURE_FILENAMES[worker_type]
    with _artifact_manifest_lock:
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    isinstance(loaded, dict)
                    and loaded.get("session_id") == session_id
                    and loaded.get("run_id") == run_id
                ):
                    manifest = loaded
            except (OSError, json.JSONDecodeError):
                manifest = {}
        workers = (
            dict(manifest.get("workers", {}))
                        if isinstance(manifest.get("workers"), dict)
                        else {}
        )
        has_validated_peer = any(
            (isinstance(value, str) and value.endswith("-result.json"))
            or (
                isinstance(value, Mapping)
                and str(value.get("status", "")).strip() == "validated"
            )
            for key, value in workers.items()
            if key != worker_type
        )
        workers[worker_type] = {
            "artifact": failure_name,
            "status": "failed",
            "attempts": attempts,
        }
        manifest.update(
            {
                "session_id": session_id,
                "run_id": run_id,
                "status": "partial" if has_validated_peer else "failed",
                "workers": workers,
            }
        )
        _atomic_json_write(manifest_path, manifest)


def _daily_blocks(content: str) -> list[dict[str, Any]]:
    """Parse a validated daily snapshot into operation-protocol block mappings."""
    return list(_worker_result_blocks(content))


def _load_validated_worker_artifact(
    artifact: Path | ValidatedWorkerResult,
    *,
    session_id: str,
    run_id: str,
    expected_worker_type: str,
) -> dict[str, Any]:
    path = artifact.path if isinstance(artifact, ValidatedWorkerResult) else artifact
    if path is None:
        raise ValueError("worker result is not stored in a temporary artifact")
    if expected_worker_type not in {"event", "fact", "procedure", "decision"}:
        raise ValueError(f"unknown expected worker type: {expected_worker_type!r}")
    path = Path(path)
    session_root = (
        _hermes_home() / "memories" / "staging" / ".tmp_mem_files" / session_id
    ).resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(session_root)
    except ValueError as exc:
        raise ValueError("worker artifact is outside its temporary session directory") from exc
    if resolved_path == session_root:
        raise ValueError("worker artifact path is not a file")
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid worker artifact: {exc}") from exc
    if payload.get("status") != "validated":
        raise ValueError("worker artifact is not validated")
    if payload.get("session_id") != session_id:
        raise ValueError(
            f"worker artifact session mismatch: {payload.get('session_id')!r} != {session_id!r}"
        )
    if payload.get("run_id") != run_id:
        raise ValueError(
            f"worker artifact run mismatch: {payload.get('run_id')!r} != {run_id!r}"
        )
    if payload.get("worker_type") != expected_worker_type:
        raise ValueError(
            f"worker type mismatch: {payload.get('worker_type')!r} != "
            f"{expected_worker_type!r}"
        )
    if resolved_path.name != WORKER_ARTIFACT_FILENAMES[expected_worker_type]:
        raise ValueError("worker artifact filename is not canonical")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not all(isinstance(block, dict) for block in blocks):
        raise ValueError("worker artifact blocks are not a validated list")
    for block in blocks:
        block_session = _session_id_from_sources(block.get("sources"))
        if block_session and block_session != session_id:
            raise ValueError(
                f"worker artifact block session mismatch: {block_session!r} != {session_id!r}"
            )
    return payload


def _load_validated_operation_artifact(
    artifact: Path | Mapping[str, Any],
    *,
    session_id: str,
    run_id: str,
) -> list[digest_operations.Operation]:
    if isinstance(artifact, Path):
        session_root = (
            _hermes_home() / "memories" / "staging" / ".tmp_mem_files" / session_id
        ).resolve()
        resolved_path = artifact.resolve()
        try:
            resolved_path.relative_to(session_root)
        except ValueError as exc:
            raise ValueError(
                "operation artifact is outside its temporary session directory"
            ) from exc
        if resolved_path == session_root:
            raise ValueError("operation artifact path is not a file")
        if resolved_path.name != "operations.json":
            raise ValueError("operation artifact filename is not canonical")
        try:
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid operation artifact: {exc}") from exc
    elif isinstance(artifact, Mapping):
        payload = dict(artifact)
    else:
        raise ValueError("operations must come from a validated artifact")
    if payload.get("status") != "validated":
        raise ValueError("operation artifact is not validated")
    if payload.get("session_id") != session_id:
        raise ValueError("operation artifact session mismatch")
    if payload.get("run_id") != run_id:
        raise ValueError("operation artifact run mismatch")
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list):
        raise ValueError("operation artifact does not contain a list")
    return [digest_operations.normalize_operation(item) for item in raw_operations]


def _candidate_invariant_errors(
    before: str,
    after: str,
    operations: list[digest_operations.Operation],
) -> list[str]:
    before_blocks = _daily_blocks(before)
    after_blocks = _daily_blocks(after)
    before_by_id = {str(block.get("id")): block for block in before_blocks}
    after_by_id = {str(block.get("id")): block for block in after_blocks}
    # Every way a block may legally leave the file: merge absorption, an
    # explicit drop, or a supersede helper whose body moved into its target.
    # Anything else disappearing is still a silent deletion.
    authorized = {
        item
        for operation in operations
        if operation.operation == "merge"
        for item in operation.absorbed_ids
    }
    authorized |= {
        str(operation.id)
        for operation in operations
        if operation.operation == "drop" and operation.id
    }
    authorized |= {
        str(operation.helper_id)
        for operation in operations
        if operation.operation == "supersede" and operation.helper_id
    }
    errors: list[str] = []
    for block_id, block in before_by_id.items():
        if (
            str(block.get("type", "")).strip() == "event"
            and block_id not in after_by_id
            and block_id not in authorized
        ):
            errors.append(f"candidate changed stable event id: {block_id}")
        elif block_id not in after_by_id and block_id not in authorized:
            errors.append(f"candidate silently deleted durable id: {block_id}")
        elif (
            block_id in after_by_id
            and str(block.get("type", "")).strip()
            != str(after_by_id[block_id].get("type", "")).strip()
        ):
            errors.append(f"candidate changed immutable type for id: {block_id}")
    return errors


def _commit_candidate_once(
    daily_path: Path,
    worker_artifacts: Iterable[Path | ValidatedWorkerResult],
    operation_artifact: Path | Mapping[str, Any] | Sequence[Any],
    *,
    session_id: str,
    run_id: str,
    base_content: str | None = None,
) -> tuple[bool, list[str]]:
    """Assemble and atomically commit one candidate from in-memory ops (+ optional workers).

    Worker tmp / operations.json are audit-only. Happy path uses in-memory
    ``ValidatedWorkerResult`` identity checks and an in-memory ops list/mapping.
    No final ``_validate_digest_file`` / invariant hard-reject before write.
    """
    execution_log: digest_operation_log.ExecutionLog | None = None
    replacement_succeeded = False
    try:
        with _digest_lock:
            before = daily_path.read_text(encoding="utf-8") if daily_path.exists() else ""
            if base_content is not None and before != base_content:
                raise ValueError("stale candidate base; daily file changed before commit")
            execution_log = digest_operation_log.ExecutionLog.create(
                daily_path.parent.parent / ".tmp_mem_files" / session_id,
                session_id=session_id,
                run_id=run_id,
                base_content=before,
                base_version=digest_operation_log.file_version(daily_path),
                operations=[],
            )
            for artifact in worker_artifacts:
                if isinstance(artifact, ValidatedWorkerResult):
                    if artifact.session_id != session_id:
                        raise ValueError(
                            f"worker session mismatch: {artifact.session_id!r} != {session_id!r}"
                        )
                    if artifact.run_id != run_id:
                        raise ValueError(
                            f"worker run mismatch: {artifact.run_id!r} != {run_id!r}"
                        )
                    if artifact.worker_type not in WORKER_ARTIFACT_FILENAMES:
                        raise ValueError(
                            f"unknown worker type: {artifact.worker_type!r}"
                        )
                    continue
                # Legacy Path inputs: identity-check via load helper (tests/fixtures).
                expected_type = next(
                        (
                            worker_type
                            for worker_type, filename in WORKER_ARTIFACT_FILENAMES.items()
                            if Path(artifact).name == filename
                        ),
                        "",
                )
                if not expected_type:
                    raise ValueError("worker artifact filename is not canonical")
                _load_validated_worker_artifact(
                    artifact,
                    session_id=session_id,
                    run_id=run_id,
                    expected_worker_type=expected_type,
                )
            operations = _resolve_commit_operations(
                operation_artifact, session_id=session_id, run_id=run_id
            )
            execution_log.set_operations(operations)
            existing_blocks = _daily_blocks(before)
            execution_log.transition("validated")
            execution_log.transition("executing")
            candidate_blocks = existing_blocks
            for operation in operations:
                candidate_blocks = digest_operations.apply_operation(
                    operation, candidate_blocks
                )
            candidate_blocks, purged_ids = digest_operations.purge_dropped_blocks(
                candidate_blocks
            )
            if purged_ids and not candidate_blocks:
                raise ValueError("purge would empty the daily file; refusing to commit")
            if purged_ids:
                _log(
                    f"purged dropped blocks session={session_id} run={run_id}: "
                    + ", ".join(purged_ids)
                )
            rendered_blocks: list[str] = []
            for block in candidate_blocks:
                parsed = dict(block)
                body = _truncate_body(str(parsed.pop("body", "")).strip())
                rendered_blocks.append(_render_digest_block(parsed, body))
            rendered = "\n\n".join(rendered_blocks)
            candidate = rendered.rstrip() + "\n" if rendered.strip() else ""
            candidate = _scrub_dangling_related(
                candidate,
                extra_keep_ids=_week_alive_block_ids(),
                retiring_ids=set(purged_ids or ()),
            )
            candidate = _scrub_illegal_sources(candidate)
            # Soft log only — no hard reject (workers/ops already validated upstream).
            soft_errors = _validate_digest_file(candidate)
            soft_errors.extend(
                _candidate_invariant_errors(before, candidate, operations)
            )
            if soft_errors:
                _log(
                    f"commit soft validation notes session={session_id} run={run_id}: "
                    + "; ".join(soft_errors[:5])
                )
            execution_log.transition("candidate_validated")
            _fences, wrap_phrase = split_daily_wrapup(before)
            del _fences
            _rewrite_daily_file(daily_path, join_daily_wrapup(candidate, wrap_phrase))
            replacement_succeeded = True
            try:
                execution_log.transition("committed")
            except (OSError, ValueError) as log_error:
                execution_log.mark_uncertain(log_error)
                _log(
                    f"candidate replacement succeeded but commit log is uncertain "
                    f"session={session_id} run={run_id}: {log_error}"
                )
                try:
                    digest_operation_log.cleanup_terminal_artifacts(
                        execution_log.path,
                        session_id=session_id,
                        run_id=run_id,
                    )
                except (OSError, ValueError, TypeError) as cleanup_error:
                    _log(
                        f"execution artifact cleanup failed session={session_id} "
                        f"run={run_id}: {cleanup_error}"
                    )
                return False, [
                    f"candidate replacement succeeded but commit log is uncertain: {log_error}"
                ]
            try:
                digest_operation_log.cleanup_terminal_artifacts(
                    execution_log.path,
                    session_id=session_id,
                    run_id=run_id,
                )
            except (OSError, ValueError, TypeError) as cleanup_error:
                _log(
                    f"execution artifact cleanup failed session={session_id} "
                    f"run={run_id}: {cleanup_error}"
                )
        return True, []
    except (OSError, ValueError, TypeError) as exc:
        if execution_log is not None:
            try:
                if replacement_succeeded:
                    execution_log.mark_uncertain(exc)
                else:
                    execution_log.fail(exc)
            except (OSError, ValueError):
                _log(
                    f"execution log failure session={session_id} run={run_id}: {exc}"
                )
            try:
                digest_operation_log.cleanup_terminal_artifacts(
                    execution_log.path,
                    session_id=session_id,
                    run_id=run_id,
                )
            except (OSError, ValueError, TypeError) as cleanup_error:
                _log(
                    f"execution artifact cleanup failed session={session_id} "
                    f"run={run_id}: {cleanup_error}"
                )
        _log(f"candidate commit rejected session={session_id} run={run_id}: {exc}")
        return False, [str(exc)]


def _resolve_commit_operations(
    operation_artifact: Path | Mapping[str, Any] | Sequence[Any],
    *,
    session_id: str,
    run_id: str,
) -> list[digest_operations.Operation]:
    """Normalize in-memory ops (preferred) or legacy path/mapping artifact."""
    if isinstance(operation_artifact, Path):
        return _load_validated_operation_artifact(
            operation_artifact, session_id=session_id, run_id=run_id
        )
    if isinstance(operation_artifact, Mapping):
        if (
            "session_id" in operation_artifact
            and operation_artifact.get("session_id") != session_id
        ):
            raise ValueError(
                f"operation artifact session mismatch: "
                f"{operation_artifact.get('session_id')!r} != {session_id!r}"
            )
        if (
            "run_id" in operation_artifact
            and operation_artifact.get("run_id") != run_id
        ):
            raise ValueError(
                f"operation artifact run mismatch: "
                f"{operation_artifact.get('run_id')!r} != {run_id!r}"
            )
        raw_ops = operation_artifact.get("operations")
        if raw_ops is None:
            raise ValueError("operation artifact missing operations list")
        if not isinstance(raw_ops, Sequence) or isinstance(raw_ops, (str, bytes)):
            raise ValueError("operations must be a list")
        return [digest_operations.normalize_operation(op) for op in raw_ops]
    if isinstance(operation_artifact, Sequence) and not isinstance(
        operation_artifact, (str, bytes)
    ):
        return [
            digest_operations.normalize_operation(op) for op in operation_artifact
        ]
    raise ValueError("unsupported operation artifact type")


def _commit_candidate(
    daily_path: Path,
    worker_artifacts: Iterable[Path | ValidatedWorkerResult],
    operation_artifact: Path | Mapping[str, Any] | Sequence[Any],
    *,
    session_id: str,
    run_id: str,
    base_content: str | None = None,
    max_attempts: int = 1,
) -> bool:
    """Commit a candidate, retrying transient rejections up to ``max_attempts`` times."""
    pinned_base = base_content
    last_errors: list[str] = []
    for attempt in range(1, max(1, max_attempts) + 1):
        attempt_base = pinned_base if attempt == 1 else None
        ok, errors = _commit_candidate_once(
            daily_path,
            worker_artifacts,
            operation_artifact,
            session_id=session_id,
            run_id=run_id,
            base_content=attempt_base,
        )
        if ok:
            return True
        last_errors = errors
        if attempt < max_attempts:
            _log(
                f"candidate commit attempt {attempt}/{max_attempts} rejected "
                f"session={session_id} run={run_id}: {'; '.join(errors[:3])}"
            )
    if max_attempts > 1 and last_errors:
        _log(
            f"candidate commit failed after {max_attempts} attempts "
            f"session={session_id} run={run_id}: {'; '.join(last_errors[:5])}"
        )
    return False


def _run_update_operator(
    existing_blocks: list[dict[str, Any]],
    validated_new_blocks: list[dict[str, Any]],
    *,
    session_id: str,
    run_id: str,
    proposer: Any = None,
    max_attempts: int | None = None,
) -> tuple[list[digest_operations.Operation], Path]:
    """Compare validated blocks and store validated operations without file mutation."""
    operation_dir = _hermes_home() / "memories" / "staging" / ".tmp_mem_files" / session_id
    return digest_operations.prepare_operations(
        existing_blocks,
        validated_new_blocks,
        session_id=session_id,
        run_id=run_id,
        session_dir=operation_dir,
        proposer=proposer,
        alive_ids=_week_alive_block_ids(),
        max_attempts=(
            max_attempts
            if max_attempts is not None
            else MAX_PROPOSER_VALIDATION_ATTEMPTS
        ),
    )


def _fallback_proposal(
    existing_blocks: list[dict[str, Any]],
    new_blocks: list[dict[str, Any]],
    why: str,
    *,
    session_id: str,
    run_id: str,
) -> list[digest_operations.Operation]:
    """Commit without consolidation rather than lose the session's memory."""
    _log(
        f"digest dedup proposer fell back to the deterministic builder "
        f"session={session_id} run={run_id}: {why}"
    )
    return digest_operations.build_update_operations(existing_blocks, new_blocks)


def _phase2_prompt_and_tool(
    existing_blocks: list[dict[str, Any]],
    new_blocks: list[dict[str, Any]],
    *,
    errors: tuple[str, ...],
    attempt: int,
    previous_ops: list[dict[str, Any]],
    max_attempts: int,
) -> tuple[str, str, str]:
    """Build Phase-2 submit/patch prompt after a local Pearson/MI pair filter.

    Without a same-type MI gate every daily card would enter the LLM context.
    Embed or math failure fail-opens to the full board so merge quality is not
    lost. Attempt ≥2 still shrinks to foul-touched cards only.
    """
    global _PHASE2_MINILM
    mode = "submit" if attempt == 1 or not previous_ops else "patch"
    force_name = (
        "submit_operations" if mode == "submit" else "patch_operations"
    )
    teach = ""
    if errors:
        teach = digest_tools.operations_failed_teach(
            errors,
            attempt=attempt,
            max_attempts=max_attempts,
        )
    prompt_existing = existing_blocks
    prompt_new = new_blocks
    pending_ids: list[str] | None = None
    previous_for_prompt: list[dict[str, Any]] | None = None
    candidate_pairs: list[tuple[str, str]] | None = None
    if attempt >= 2 and previous_ops:
        closure = digest_dedup_prompt.foul_touched_block_ids(
            errors, previous_ops, existing_blocks, new_blocks
        )
        if closure:
            by_id = {
                str(b.get("id")): b
                for b in list(existing_blocks) + list(new_blocks)
                if b.get("id")
            }
            existing_ids = {
                str(b.get("id")) for b in existing_blocks if b.get("id")
            }
            new_ids = {str(b.get("id")) for b in new_blocks if b.get("id")}
            prompt_existing = [
                by_id[i] for i in closure if i in existing_ids and i in by_id
            ]
            prompt_new = [
                by_id[i] for i in closure if i in new_ids and i in by_id
            ]
            pending_ids = digest_dedup_prompt.pending_new_ids(
                new_blocks, closure
            )
            previous_for_prompt = list(previous_ops)
    elif attempt == 1:
        try:
            board = [
                dict(b)
                for b in list(existing_blocks) + list(new_blocks)
                if str(b.get("id") or "").strip()
            ]
            grouped = digest_dedup_prompt._group_blocks_by_type(board)
            texts: list[str] = []
            keys: list[str] = []
            for block in board:
                bits = [str(block.get("type") or "").strip()]
                for field in ("entity", "predicate", "body"):
                    val = str(block.get(field) or "").strip()
                    if val:
                        bits.append(val)
                text = " ".join(bits)
                keys.append(
                    hashlib.sha256(text.encode("utf-8")).hexdigest()
                )
                texts.append(text)
            missing_i = [
                i for i, key in enumerate(keys) if key not in _PHASE2_EMBED_CACHE
            ]
            if missing_i:
                if _PHASE2_MINILM is None:
                    from sentence_transformers import SentenceTransformer

                    _PHASE2_MINILM = SentenceTransformer(PHASE2_MINILM_MODEL)
                encoded = _PHASE2_MINILM.encode(
                    [texts[i] for i in missing_i],
                    normalize_embeddings=True,
                )
                for pos, idx in enumerate(missing_i):
                    row = encoded[pos]
                    _PHASE2_EMBED_CACHE[keys[idx]] = [
                        float(v) for v in row
                    ]
            vectors = [_PHASE2_EMBED_CACHE[key] for key in keys]
            by_id = {
                str(block.get("id")): (block, vectors[i])
                for i, block in enumerate(board)
            }
            kept: list[tuple[str, str]] = []
            for kind in digest_dedup_prompt.BLOCK_TYPE_ORDER:
                cards = [
                    str(b.get("id"))
                    for b in grouped.get(kind, [])
                    if str(b.get("id") or "").strip()
                ]
                if len(cards) < 2:
                    continue
                scores: list[tuple[str, str, float]] = []
                for i, left in enumerate(cards):
                    for right in cards[i + 1 :]:
                        x = by_id[left][1]
                        y = by_id[right][1]
                        n = min(len(x), len(y))
                        if n < 2:
                            rho = 0.0
                        else:
                            mx = sum(x[:n]) / n
                            my = sum(y[:n]) / n
                            num = sum(
                                (x[j] - mx) * (y[j] - my) for j in range(n)
                            )
                            vx = sum((x[j] - mx) ** 2 for j in range(n))
                            vy = sum((y[j] - my) ** 2 for j in range(n))
                            den = math.sqrt(vx * vy)
                            rho = 0.0 if den == 0.0 else num / den
                        rho = max(-1.0, min(1.0, rho))
                        rho2 = min(rho * rho, 1.0 - 1e-12)
                        mi = -0.5 * math.log(1.0 - rho2)
                        scores.append((left, right, mi))
                if len(scores) == 1:
                    kept.append((scores[0][0], scores[0][1]))
                    continue
                values = sorted(item[2] for item in scores)
                pos = PHASE2_MI_QUANTILE * (len(values) - 1)
                lo = int(pos)
                hi = min(lo + 1, len(values) - 1)
                frac = pos - lo
                threshold = values[lo] * (1.0 - frac) + values[hi] * frac
                for left, right, mi in scores:
                    if mi > threshold:
                        kept.append((left, right))
            if not kept:
                _log(
                    "phase2 Pearson/MI gate: no candidate pairs; skip LLM"
                )
                return ("", force_name, "skip")
            keep_ids = {a for a, _b in kept} | {b for _a, b in kept}
            prompt_existing = [
                b
                for b in existing_blocks
                if str(b.get("id") or "") in keep_ids
            ]
            prompt_new = [
                b for b in new_blocks if str(b.get("id") or "") in keep_ids
            ]
            candidate_pairs = kept
        except Exception as exc:
            _log(
                f"phase2 Pearson/MI gate failed; full-board prompt: {exc}"
            )
            candidate_pairs = None
    prompt = digest_dedup_prompt.build_proposer_prompt(
        prompt_existing,
        prompt_new,
        errors=errors,
        attempt=attempt,
        previous_operations=previous_for_prompt,
        pending_account_ids=pending_ids,
        already_persisted_new=True,
        candidate_pairs=candidate_pairs,
    )
    if teach:
        prompt = f"{prompt}\n\n{teach}"
    return prompt, force_name, mode


def make_llm_proposer(
    platform: str,
    *,
    session_id: str,
    run_id: str,
    invoke: Any = None,
    max_attempts: int = MAX_PROPOSER_VALIDATION_ATTEMPTS,
) -> Any:
    """Build the duplicate-consolidation proposer for one digest.

    Skip-mode from the Pearson/MI gate returns no ops without an LLM call so
    empty-candidate boards do not burn tokens, while prepare_operations still
    runs decay and validation.

    Preferred I/O: forced ``submit_operations`` (attempt 1) then
    ``patch_operations`` (attempts 2–5) via ``run_worker_llm_tools``. Text
    re-propose remains as fallback when tools fail (unit tests / legacy).

    ``prepare_operations`` retries up to ``MAX_PROPOSER_VALIDATION_ATTEMPTS``;
    past ``MAX_PROPOSER_INVOCATIONS`` the deterministic builder takes over.
    """
    caller = invoke or (
        lambda prompt: _invoke_digest_llm(prompt, platform, purpose="digest-dedup")
    )
    cache: dict[str, list[dict[str, Any]]] = {}
    stats = {"invocations": 0, "prompt_chars": 0, "latency_ms": 0.0}
    previous_ops: list[dict[str, Any]] = []

    def llm_proposer(
        existing_blocks: list[dict[str, Any]],
        new_blocks: list[dict[str, Any]],
        *,
        errors: tuple[str, ...] = (),
        attempt: int = 1,
    ) -> list[Any]:
        nonlocal previous_ops
        proposed: list[Any] | None = None
        try:
            prompt, force_name, mode = _phase2_prompt_and_tool(
                existing_blocks,
                new_blocks,
                errors=errors,
                attempt=attempt,
                previous_ops=previous_ops,
                max_attempts=max_attempts,
            )
            if mode == "skip":
                return []
        except Exception as exc:
            if attempt < max_attempts:
                raise
            return _fallback_proposal(
                existing_blocks,
                new_blocks,
                f"prompt could not be built: {exc}",
                session_id=session_id,
                run_id=run_id,
            )

        key = hashlib.sha256(
            f"{mode}:{force_name}:{prompt}".encode("utf-8")
        ).hexdigest()
        if key in cache:
            proposed = list(cache[key])
        elif stats["invocations"] >= MAX_PROPOSER_INVOCATIONS:
            return _fallback_proposal(
                existing_blocks,
                new_blocks,
                f"invocation budget of {MAX_PROPOSER_INVOCATIONS} exhausted",
                session_id=session_id,
                run_id=run_id,
            )
        else:
            started = time.monotonic()
            try:
                capture = _invoke_digest_worker_tool(
                    prompt,
                    platform,
                    purpose=f"digest-dedup-{mode}",
                    force_tool_name=force_name,
                )
                tool_name, tool_args = digest_tools.parse_tool_args_from_result(
                    capture
                )
                if (
                    tool_name == "submit_operations"
                    and isinstance(tool_args, dict)
                    and isinstance(tool_args.get("operations"), list)
                ):
                    proposed = [
                        dict(op) if isinstance(op, Mapping) else op
                        for op in tool_args["operations"]
                    ]
                    previous_ops = [dict(op) for op in proposed if isinstance(op, Mapping)]
                elif tool_name == "patch_operations" and isinstance(tool_args, dict):
                    merged = digest_tools.merge_operations_patch(
                        previous_ops, tool_args
                    )
                    proposed = [
                        dict(op) if isinstance(op, Mapping) else op for op in merged
                    ]
                    previous_ops = [
                        dict(op) for op in proposed if isinstance(op, Mapping)
                    ]
                else:
                    # Text fallback (tests stub _invoke_digest_llm + stub_dedup).
                    reply = caller(prompt)
                    proposed = digest_dedup_prompt.parse_proposal(reply)
                    previous_ops = [
                        dict(op) for op in proposed if isinstance(op, Mapping)
                    ]
                stats["invocations"] += 1
                stats["prompt_chars"] += len(prompt)
                stats["latency_ms"] += (time.monotonic() - started) * 1000
            except Exception as exc:
                if attempt < max_attempts:
                    raise
                return _fallback_proposal(
                    existing_blocks,
                    new_blocks,
                    f"proposal unusable after {attempt} attempts: {exc}",
                    session_id=session_id,
                    run_id=run_id,
                )
            if proposed is not None:
                cache[key] = [
                    dict(item) if isinstance(item, Mapping) else item
                    for item in proposed
                ]

        if proposed is None:
            if attempt < max_attempts:
                raise ValueError("proposer returned no operations")
            return _fallback_proposal(
                existing_blocks,
                new_blocks,
                f"proposal empty after {attempt} attempts",
                session_id=session_id,
                run_id=run_id,
            )

        finalized = digest_operations.finalize_operations(existing_blocks, proposed)
        merges = sum(1 for op in finalized if op.operation == "merge")
        drops = sum(1 for op in finalized if op.operation == "drop")
        _log(
            f"digest dedup proposer session={session_id} run={run_id} "
            f"attempt={attempt} mode={mode} invocations={stats['invocations']} "
            f"prompt_tokens~={stats['prompt_chars'] // 4} "
            f"latency_ms={stats['latency_ms']:.0f} "
            f"merges={merges} drops={drops}"
        )
        return finalized

    return llm_proposer


def make_oneshot_proposer(
    *,
    session_id: str,
    run_id: str,
    invoke_tool: Any = None,
    max_attempts: int = 2,
) -> Any:
    """Phase-2 oneshot proposer: same teach/patch as Hermes, no AIAgent.

    Skip-mode from the Pearson/MI gate returns no ops without an LLM call so
    empty-candidate boards still reach prepare_operations decay.
    """
    caller = invoke_tool or _invoke_digest_oneshot_tool
    previous_ops: list[dict[str, Any]] = []

    def oneshot_proposer(
        existing_blocks: list[dict[str, Any]],
        new_blocks: list[dict[str, Any]],
        *,
        errors: tuple[str, ...] = (),
        attempt: int = 1,
    ) -> list[Any]:
        nonlocal previous_ops
        prompt, force_name, mode = _phase2_prompt_and_tool(
            existing_blocks,
            new_blocks,
            errors=errors,
            attempt=attempt,
            previous_ops=previous_ops,
            max_attempts=max_attempts,
        )
        if mode == "skip":
            return []
        capture = caller(
            prompt,
            "cli",
            purpose=f"digest-dedup-{mode}",
            force_tool_name=force_name,
        )
        if capture.get("failed"):
            raise ValueError(
                str(capture.get("final_response") or capture.get("error") or "oneshot failed")
            )
        tool_name, tool_args = digest_tools.parse_tool_args_from_result(capture)
        proposed: list[Any] | None = None
        if (
            tool_name == "submit_operations"
            and isinstance(tool_args, dict)
            and isinstance(tool_args.get("operations"), list)
        ):
            proposed = [
                dict(op) if isinstance(op, Mapping) else op
                for op in tool_args["operations"]
            ]
            previous_ops = [dict(op) for op in proposed if isinstance(op, Mapping)]
        elif tool_name == "patch_operations" and isinstance(tool_args, dict):
            merged = digest_tools.merge_operations_patch(previous_ops, tool_args)
            proposed = [
                dict(op) if isinstance(op, Mapping) else op for op in merged
            ]
            previous_ops = [dict(op) for op in proposed if isinstance(op, Mapping)]
        if proposed is None:
            raise ValueError("oneshot returned no operations")
        _log(
            f"digest oneshot proposer session={session_id} run={run_id} "
            f"attempt={attempt} mode={mode} ops={len(proposed)}"
        )
        return digest_operations.finalize_operations(existing_blocks, proposed)

    return oneshot_proposer


def _worker_type_body_reminder(worker_type: str) -> str:
    assigned = LEGACY_TYPE_ALIASES.get(worker_type, worker_type)
    if assigned == "event":
        return (
            "Slot reminder: beginning, course, and outcome are each one "
            "concise sentence (code renders Beginning:/Course:/Outcome:).\n"
        )
    if assigned == "procedure":
        return (
            "Slot reminder: fill obstacle and solution "
            "(code renders Obstacle:/Solution:).\n"
        )
    if assigned == "decision":
        return (
            "Slot reminder: kind=Preference|Decision, subject=user "
            "(or USER.md aliases), ruling=predicate for that subject "
            "(must/must-not/standing pref; do not repeat subject). "
            "Look in the transcript for the user's rulings. "
            "Third-party traits → fact "
            "with kind=Narration + involves cast, not decision.\n"
        )
    if assigned == "fact":
        return (
            "Slot reminder: kind=Factual|Narration + content. "
            "Factual = one observation (involves optional/single); "
            "Narration = multi-person/multi-facet story with involves cast "
            "[{entity, role?}] (role only when clear).\n"
        )
    return ""


def _worker_prompt(
    worker_type: str,
    transcript: str,
    *,
    session_id: str,
    platform: str,
    run_id: str,
    event_skeleton: str = "",
    reason: str = "digest",
    user_count: int = 0,
    assistant_count: int = 0,
    mode: str = "submit",
    teach: str = "",
) -> str:
    today = hermes_local_today_str()
    daily_path = daily_staging_path(_hermes_home(), today)
    shared = _build_digest_shared_context(
        session_id,
        platform,
        reason,
        user_count=user_count,
        assistant_count=assistant_count,
        daily_path=daily_path,
        run_id=run_id,
    )
    skeleton = ""
    if event_skeleton:
        skip_note = ""
        if _is_skip_only_content(event_skeleton):
            skip_note = (
                f"EVENT SKELETON may be skip-only; still extract {worker_type} "
                "blocks from TRANSCRIPT if durable.\n"
            )
        skeleton = (
            f"\n\nEVENT SKELETON (use as context; do not re-emit it unless assigned event):\n"
            f"{skip_note}"
            f"{event_skeleton}\n"
        )
    tool_hint = (
        f"You MUST call exactly one tool. Mode={mode}. "
        f"Assigned type={worker_type}. "
        "Use the submit_* tool for a full fill, patch_* for sparse field fixes, "
        "or skip_digest_worker if nothing durable. "
        "Do not emit free-form YAML/JSON in assistant text.\n"
        f"Closed enums: confidence={list(digest_tools.CONFIDENCE_ENUM)}; "
        f"fact kind={list(digest_tools.FACT_KIND_ENUM)}; "
        f"decision kind={list(digest_tools.DECISION_KIND_ENUM)}; "
        f"importance={digest_tools.IMPORTANCE_WRITE_MIN}–{digest_tools.IMPORTANCE_MAX}.\n"
    )
    teach_block = f"\n{teach}\n" if teach else ""
    return (
        "You are an independently validated memory digest worker.\n\n"
        f"{tool_hint}"
        f"{teach_block}"
        f"{shared}"
        f"Assigned worker type: {worker_type} (emit {worker_type} slots only).\n"
        f"{_worker_type_body_reminder(worker_type)}"
        f"{skeleton}\nTRANSCRIPT:\n{transcript}\n"
    )


def _run_validated_worker(
    worker_type: str,
    prompt: str,
    *,
    session_id: str,
    run_id: str,
    platform: str,
    transcript: str | None = None,
    event_skeleton: str = "",
    reason: str = "digest",
    user_count: int = 0,
    assistant_count: int = 0,
    message_start_id: int | None = None,
    message_end_id: int | None = None,
) -> ValidatedWorkerResult | WorkerFailure:
    """Validate worker output via forced submit/patch tool calls + code render."""
    last_content = ""
    last_errors: list[str] = []
    previous_args: dict[str, Any] = {}
    active_type = worker_type
    use_rebuild = transcript is not None
    user_at, assistant_at = _iso_clocks_for_window(
        session_id, message_start_id, message_end_id
    )

    for attempt in range(1, MAX_WORKER_VALIDATION_ATTEMPTS + 1):
        mode = "submit" if attempt == 1 or not previous_args else "patch"
        if last_errors and any("assigned worker type" in e for e in last_errors):
            mode = "submit"
            previous_args = {}
        force_name = (
            f"submit_{active_type}_block"
            if mode == "submit"
            else f"patch_{active_type}_block"
        )
        teach = ""
        if attempt > 1:
            teach = digest_tools.failed_fields_teach(
                last_errors,
                previous_args,
                worker_type=active_type,
                attempt=attempt,
                max_attempts=MAX_WORKER_VALIDATION_ATTEMPTS,
            )
        if use_rebuild:
            retry_prompt = _worker_prompt(
                active_type,
                transcript or "",
                session_id=session_id,
                platform=platform,
                run_id=run_id,
                event_skeleton=event_skeleton,
                reason=reason,
                user_count=user_count,
                assistant_count=assistant_count,
                mode=mode,
                teach=teach,
            )
        else:
            retry_prompt = prompt + ("\n" + teach if teach else "")

        content = ""
        used_tool_args = False
        try:
            capture = _invoke_digest_worker_tool(
                retry_prompt,
                platform,
                purpose=f"digest-{active_type}-{mode}",
                force_tool_name=force_name,
            )
            tool_name, tool_args = digest_tools.parse_tool_args_from_result(capture)
            if isinstance(capture, dict) and capture.get("legacy_yaml"):
                content = str(capture["legacy_yaml"])
            elif tool_name and digest_tools.is_skip_tool(tool_name, tool_args or {}):
                content = "skip"
            elif tool_name and tool_args is not None:
                if mode == "patch" and previous_args:
                    tool_args = digest_tools.merge_field_patch(previous_args, tool_args)
                previous_args = dict(tool_args)
                used_tool_args = True
            else:
                try:
                    try:
                        legacy = _invoke_digest_llm(
                            retry_prompt,
                            platform,
                            purpose=f"digest-{active_type}-legacy",
                        )
                    except TypeError:
                        legacy = _invoke_digest_llm(retry_prompt, platform)
                except Exception as legacy_exc:
                    legacy = ""
                    last_errors = [f"agent error: {legacy_exc}"]
                content = legacy or ""
                if not content:
                    last_errors = last_errors or [
                        "no tool call returned (fail closed)"
                    ]
        except Exception as exc:
            content = ""
            last_errors = [f"agent error: {exc}"]

        errors: list[str] = []
        if used_tool_args:
            # Primary gate: tool-arg dict (model intent). YAML is serialization.
            errors = digest_tools.validate_worker_tool_args(
                active_type,
                previous_args,
                user_subjects=_decision_user_subjects(),
            )
            if not errors:
                try:
                    content = digest_tools.render_worker_yaml_from_args(
                        active_type,
                        previous_args,
                        session_id=session_id,
                        today=hermes_local_today_str(),
                        message_start_id=message_start_id,
                        message_end_id=message_end_id,
                        user_message_at=user_at,
                        assistant_response_at=assistant_at,
                    )
                except Exception as render_exc:
                    content = ""
                    _log(
                        f"renderer defect type={active_type} session={session_id} "
                        f"run={run_id}: {render_exc}"
                    )
                    errors = [f"renderer defect: {render_exc}"]
                else:
                    content = _normalize_digest_content(
                        content,
                        session_id=session_id,
                        message_start_id=message_start_id,
                        message_end_id=message_end_id,
                    )
                    smoke = _smoke_rendered_worker_yaml(active_type, content)
                    if smoke:
                        # Do not teach the model to "fix YAML" — log and accept
                        # when args already passed (serializer bug is ours).
                        _log(
                            f"renderer smoke type={active_type} session={session_id} "
                            f"run={run_id}: {'; '.join(smoke[:3])}"
                        )
            else:
                # Keep a rendered snapshot for failure records / dirty accept.
                try:
                    content = digest_tools.render_worker_yaml_from_args(
                        active_type,
                        previous_args,
                        session_id=session_id,
                        today=hermes_local_today_str(),
                        message_start_id=message_start_id,
                        message_end_id=message_end_id,
                        user_message_at=user_at,
                        assistant_response_at=assistant_at,
                    )
                    content = _normalize_digest_content(
                        content,
                        session_id=session_id,
                        message_start_id=message_start_id,
                        message_end_id=message_end_id,
                    )
                except Exception:
                    content = content or ""
        elif content:
            content = _normalize_digest_content(
                content,
                session_id=session_id,
                message_start_id=message_start_id,
                message_end_id=message_end_id,
            )
            errors = list(_validate_worker_output(content, active_type))
            for err in errors:
                m = re.search(r"returned type (\S+)", err)
                if m:
                    other = m.group(1).strip().strip("'\"")
                    other = LEGACY_TYPE_ALIASES.get(other, other)
                    if other in NEW_OUTPUT_TYPES and other != active_type:
                        active_type = other
                        previous_args = {}
                        break
        else:
            errors = ["empty response"] if not last_errors else last_errors

        if used_tool_args:
            for err in errors:
                m = re.search(r"returned type (\S+)", err)
                if m:
                    other = m.group(1).strip().strip("'\"")
                    other = LEGACY_TYPE_ALIASES.get(other, other)
                    if other in NEW_OUTPUT_TYPES and other != active_type:
                        active_type = other
                        previous_args = {}
                        break

        if not errors:
            result = ValidatedWorkerResult(
                worker_type=worker_type,
                session_id=session_id,
                run_id=run_id,
                attempts=attempt,
                content=content,
                blocks=_worker_result_blocks(content),
            )
            return _store_validated_worker_result(result)
        last_content = content
        last_errors = errors
        exhausted = attempt >= MAX_WORKER_VALIDATION_ATTEMPTS
        _append_worker_failure_record(
            worker_type=worker_type,
            session_id=session_id,
            run_id=run_id,
            attempt=attempt,
            max_attempts=MAX_WORKER_VALIDATION_ATTEMPTS,
            errors=errors,
            content=content,
            exhausted=exhausted,
        )
        if exhausted:
            _mark_worker_failed_in_manifest(
                worker_type=worker_type,
                session_id=session_id,
                run_id=run_id,
                attempts=MAX_WORKER_VALIDATION_ATTEMPTS,
            )
    # Dirty accept: hand in last usable output instead of aborting the digest.
    dirty_content = last_content
    if not dirty_content.strip() and previous_args:
        try:
            dirty_content = digest_tools.render_worker_yaml_from_args(
                active_type,
                previous_args,
                session_id=session_id,
                today=hermes_local_today_str(),
                message_start_id=message_start_id,
                message_end_id=message_end_id,
                user_message_at=user_at,
                assistant_response_at=assistant_at,
            )
        except Exception:
            dirty_content = ""
    if dirty_content.strip():
        dirty_content = _normalize_digest_content(
            dirty_content,
            session_id=session_id,
            message_start_id=message_start_id,
            message_end_id=message_end_id,
        )
        blocks = _worker_result_blocks(dirty_content)
        usable = bool(blocks) or _is_skip_only_content(dirty_content)
        if usable:
            _log(
                f"worker_accepted_dirty type={worker_type} session={session_id} "
                f"run={run_id} attempts={MAX_WORKER_VALIDATION_ATTEMPTS} "
                f"errors={'; '.join(last_errors[:3])}"
            )
            result = ValidatedWorkerResult(
                worker_type=worker_type,
                session_id=session_id,
                run_id=run_id,
                attempts=MAX_WORKER_VALIDATION_ATTEMPTS,
                content=dirty_content,
                blocks=blocks,
                accepted_dirty=True,
            )
            return _store_validated_worker_result(result)
    return WorkerFailure(
        worker_type=worker_type,
        session_id=session_id,
        run_id=run_id,
        attempts=MAX_WORKER_VALIDATION_ATTEMPTS,
        errors=tuple(last_errors),
    )


def run_event_worker(
    session_id: str,
    platform: str,
    transcript: str,
    *,
    run_id: str | None = None,
    reason: str = "digest",
    user_count: int = 0,
    assistant_count: int = 0,
) -> ValidatedWorkerResult | WorkerFailure:
    run_id = run_id or uuid.uuid4().hex
    prompt = _worker_prompt(
        "event",
        transcript,
        session_id=session_id,
        platform=platform,
        run_id=run_id,
        reason=reason,
        user_count=user_count,
        assistant_count=assistant_count,
    )
    return _run_validated_worker(
        "event",
        prompt,
        session_id=session_id,
        run_id=run_id,
        platform=platform,
        transcript=transcript,
        reason=reason,
        user_count=user_count,
        assistant_count=assistant_count,
    )


def run_detail_worker(
    worker_type: str,
    event_skeleton: str,
    transcript: str,
    *,
    session_id: str,
    platform: str,
    run_id: str | None = None,
    reason: str = "digest",
    user_count: int = 0,
    assistant_count: int = 0,
) -> ValidatedWorkerResult | WorkerFailure:
    run_id = run_id or uuid.uuid4().hex
    prompt = _worker_prompt(
        worker_type,
        transcript,
        session_id=session_id,
        platform=platform,
        run_id=run_id,
        event_skeleton=event_skeleton,
        reason=reason,
        user_count=user_count,
        assistant_count=assistant_count,
    )
    return _run_validated_worker(
        worker_type,
        prompt,
        session_id=session_id,
        run_id=run_id,
        platform=platform,
        transcript=transcript,
        event_skeleton=event_skeleton,
        reason=reason,
        user_count=user_count,
        assistant_count=assistant_count,
    )


def _rerun_workers_after_commit_failure(
    *,
    event_result: ValidatedWorkerResult,
    detail_results: Sequence[ValidatedWorkerResult],
    commit_errors: list[str],
    candidate_snippet: str,
    commit_attempt: int,
    transcript: str,
    session_id: str,
    platform: str,
    run_id: str,
    reason: str,
    user_count: int = 0,
    assistant_count: int = 0,
) -> tuple[ValidatedWorkerResult | WorkerFailure, list[ValidatedWorkerResult | WorkerFailure]]:
    """Thin commit repair: compact field teach only — no full transcript re-feed."""
    workers = [event_result, *detail_results]
    target_types = _worker_types_for_commit_errors(commit_errors, workers)
    if "event" in target_types:
        target_types = set(NEW_OUTPUT_TYPES)
    teach = (
        f"COMMIT VALIDATION FAILED (attempt {commit_attempt}/{MAX_COMMIT_ATTEMPTS}).\n"
        "Fix ONLY the listed issues via patch/submit tool args. "
        "Do not resend unchanged fields. Do not emit free-form YAML.\n"
        "Errors:\n"
        + "\n".join(
            f"- {e}" for e in _expand_commit_errors(commit_errors)[:MAX_ERRORS_IN_PROMPT]
        )
        + "\n"
    )
    _log(
        f"commit repair (thin) workers={sorted(target_types)} "
        f"session={session_id} run={run_id} attempt={commit_attempt}"
    )
    new_event: ValidatedWorkerResult | WorkerFailure = event_result
    if "event" in target_types:
        prompt = _worker_prompt(
            "event", "", session_id=session_id, platform=platform, run_id=run_id,
            reason=reason, user_count=user_count, assistant_count=assistant_count,
            mode="patch", teach=teach,
        )
        new_event = _run_validated_worker(
            "event", prompt, session_id=session_id, run_id=run_id, platform=platform,
            transcript="", reason=reason, user_count=user_count, assistant_count=assistant_count,
        )
        if isinstance(new_event, WorkerFailure):
            return new_event, list(detail_results)
    event_skeleton = new_event.content
    detail_order = ("fact", "procedure", "decision")
    by_type = {result.worker_type: result for result in detail_results}
    refreshed: dict[str, ValidatedWorkerResult | WorkerFailure] = {}
    to_rerun = [wt for wt in detail_order if wt in target_types]
    if to_rerun:
        with ThreadPoolExecutor(
            max_workers=len(to_rerun), thread_name_prefix="memory-digest-repair"
        ) as pool:
            futures = {
                worker_type: pool.submit(
                    _run_validated_worker,
                    worker_type,
                        _worker_prompt(
                        worker_type, "", session_id=session_id, platform=platform,
                        run_id=run_id, event_skeleton=event_skeleton, reason=reason,
                        user_count=user_count, assistant_count=assistant_count,
                        mode="patch", teach=teach,
                    ),
                    session_id=session_id, run_id=run_id, platform=platform,
                    transcript="", event_skeleton=event_skeleton, reason=reason,
                    user_count=user_count, assistant_count=assistant_count,
                )
                for worker_type in to_rerun
            }
            for worker_type, future in futures.items():
                try:
                    refreshed[worker_type] = future.result()
                except Exception as exc:
                    refreshed[worker_type] = WorkerFailure(
                        worker_type=worker_type, session_id=session_id, run_id=run_id,
                        attempts=MAX_WORKER_VALIDATION_ATTEMPTS,
                        errors=(f"{worker_type} worker error: {exc}",),
                    )
    new_details: list[ValidatedWorkerResult | WorkerFailure] = []
    for worker_type in detail_order:
        if worker_type in refreshed:
            new_details.append(refreshed[worker_type])
        else:
            new_details.append(by_type[worker_type])
    return new_event, new_details


def _phase1_prompt(
    transcript: str,
    *,
    session_id: str,
    platform: str,
    run_id: str,
    reason: str = "digest",
    user_count: int = 0,
    assistant_count: int = 0,
    mode: str = "submit",
    teach: str = "",
    date_str: str | None = None,
) -> str:
    """Point Phase-1 at the leftover board date so 08:00 catch-up cannot target wall today."""
    board_date = date_str or hermes_local_today_str()
    daily_path = daily_staging_path(_hermes_home(), board_date)
    shared = _build_digest_shared_context(
            session_id,
            platform,
        reason,
            user_count=user_count,
            assistant_count=assistant_count,
        daily_path=daily_path,
        run_id=run_id,
    )
    del mode  # type A: one prompt; model may submit then patch in-turn
    tool_hint = (
        "You MUST call a Phase-1 tool. Tools: submit_digest_blocks (full batch), "
        "patch_digest_blocks (same-turn fix after teach), or skip_digest_worker. "
        "Each blocks[] item is FLAT: type const + sibling fields (no nested "
        "event/fact/procedure/decision objects). Put event cards first; details "
        "may related: same-batch temp_id or week-alive mem-ids. Event related "
        "is fact/procedure/decision only (never another event id). Slot strings "
        "have maxLength; assembled body is <= 500 chars (code truncates). Do not emit "
        "free-form YAML/JSON in assistant text.\n"
        f"Closed enums: confidence={list(digest_tools.CONFIDENCE_ENUM)}; "
        f"fact kind={list(digest_tools.FACT_KIND_ENUM)}; "
        f"decision kind={list(digest_tools.DECISION_KIND_ENUM)}; "
        f"importance={digest_tools.IMPORTANCE_WRITE_MIN}–{digest_tools.IMPORTANCE_MAX}.\n"
    )
    teach_block = f"\n{teach}\n" if teach else ""
    return (
        "You are the Phase-1 memory digest extractor for one transcript batch.\n\n"
        f"{tool_hint}"
        f"{teach_block}"
        f"{shared}"
        "Emit all durable event/fact/procedure/decision cards via "
        "submit_digest_blocks (flat oneOf). On validation teach, call "
        "patch_digest_blocks in this same turn.\n"
        f"{_worker_type_body_reminder('event')}"
        f"{_worker_type_body_reminder('fact')}"
        f"{_worker_type_body_reminder('procedure')}"
        f"{_worker_type_body_reminder('decision')}"
        f"\nTRANSCRIPT:\n{transcript}\n"
    )


def _mint_phase1_block_id(
    block_type: str, occupied: set[str], date_str: str | None = None
) -> str:
    """Mint on the leftover/board civil day so a midnight-crossing extract cannot stamp tomorrow."""
    board = date_str or hermes_local_today_str()
    type_seg = digest_operations._id_type_segment(block_type)

    def _factory() -> str:
        return f"mem-{board}-{type_seg}-{uuid.uuid4().hex[:12].upper()}"

    return digest_operations._new_id(_factory, occupied, block_type)


def _resolve_temp_ids_in_refs(
    value: Any, mapping: Mapping[str, str]
) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_resolve_temp_ids_in_refs(item, mapping) for item in value]
    return value


def _blocks_from_digest_args(
    args: Mapping[str, Any],
    *,
    session_id: str,
    message_start_id: int | None = None,
    message_end_id: int | None = None,
    date_str: str | None = None,
) -> list[dict[str, Any]]:
    """Render validated digest_blocks args into block dicts with leftover-day mem-ids."""
    today = date_str or hermes_local_today_str()
    raw_blocks = args.get("blocks") if isinstance(args.get("blocks"), list) else []
    occupied: set[str] = set()
    temp_map: dict[str, str] = {}
    rendered: list[dict[str, Any]] = []
    user_at, assistant_at = _iso_clocks_for_window(
        session_id, message_start_id, message_end_id
    )

    for item in raw_blocks:
        if not isinstance(item, Mapping):
            continue
        wt, flat = digest_tools._flatten_block_item(item)
        if wt not in NEW_OUTPUT_TYPES:
            continue
        temp_id = str(item.get("temp_id") or flat.pop("temp_id", "") or "").strip()
        mem_id = _mint_phase1_block_id(wt, occupied, date_str=today)
        occupied.add(mem_id)
        if temp_id:
            temp_map[temp_id] = mem_id
        yaml_text = digest_tools.render_worker_yaml_from_args(
            wt,
            {**flat, "id": mem_id},
            session_id=session_id,
            today=today,
            message_start_id=message_start_id,
            message_end_id=message_end_id,
            user_message_at=user_at,
            assistant_response_at=assistant_at,
        )
        blocks = list(_worker_result_blocks(yaml_text))
        if not blocks:
            continue
        block = dict(blocks[0])
        block["id"] = mem_id
        rendered.append(block)

    if temp_map:
        for block in rendered:
            for key in ("related", "supersedes"):
                if key in block:
                    block[key] = _resolve_temp_ids_in_refs(block[key], temp_map)

    # Same-batch: link non-event details onto events' related (cap MAX_RELATED).
    detail_ids = [
        str(b["id"])
        for b in rendered
        if str(b.get("type", "")).strip() in {"fact", "procedure", "decision"}
        and b.get("id")
    ]
    for block in rendered:
        if str(block.get("type", "")).strip() != "event":
            continue
        related = block.get("related", [])
        related = list(related) if isinstance(related, list) else []
        related = [str(x) for x in related if str(x).strip()]
        for detail_id in detail_ids:
            if detail_id not in related:
                related.append(detail_id)
        block["related"] = related[:MAX_RELATED]
    return rendered


def run_phase1_digest_blocks(
    session_id: str,
    platform: str,
    transcript: str,
    *,
    run_id: str | None = None,
    reason: str = "digest",
    user_count: int = 0,
    assistant_count: int = 0,
    message_start_id: int | None = None,
    message_end_id: int | None = None,
    date_str: str | None = None,
) -> ValidatedWorkerResult | WorkerFailure:
    """Phase-1 type A: one worker turn (submit|patch|skip) → block list.

    Handler/teach repairs happen in-turn. Does not write daily staging, run
    Phase-2, or advance the bookmark. Returns ``ValidatedWorkerResult``
    (``worker_type='phase1'``) or ``WorkerFailure`` when nothing usable remains.
    After ``PHASE1_MAX_VALIDATION_ATTEMPTS`` failed validations, last args are
    soft-accepted with ``importance=2`` (dirty).
    ``date_str`` is the leftover board civil day so 08:00 catch-up cannot mint
    wall-today ids or point the prompt at tomorrow's file.
    """
    run_id = run_id or uuid.uuid4().hex
    max_attempts = digest_tools.PHASE1_MAX_VALIDATION_ATTEMPTS
    digest_tools.reset_phase1_turn_state(session_id=session_id)
    prompt = _phase1_prompt(
        transcript,
        session_id=session_id,
        platform=platform,
        run_id=run_id,
        reason=reason,
        user_count=user_count,
        assistant_count=assistant_count,
        date_str=date_str,
    )
    previous_args: dict[str, Any] = {}
    last_errors: list[str] = []
    last_blocks: tuple[dict[str, Any], ...] = ()
    fail_count = 0
    content = ""

    try:
        capture = _invoke_digest_worker_tool(
            prompt,
            platform,
            purpose="digest-phase1",
            allowed_tool_names=digest_tools.tool_names_for_phase1(),
            max_iterations=max_attempts,
        )
    except Exception as exc:
        capture = {
            "tool_name": None,
            "tool_args": None,
            "tool_calls": [],
            "failed": True,
            "error": str(exc),
        }
        last_errors = [f"agent error: {exc}"]

    tool_calls = list(capture.get("tool_calls") or [])
    if not tool_calls:
        tool_name, tool_args = digest_tools.parse_tool_args_from_result(capture)
        if tool_name:
            tool_calls = [(tool_name, tool_args or {})]

    accepted_args: dict[str, Any] | None = None
    for tool_name, raw_args in tool_calls:
        args = dict(raw_args or {})
        if digest_tools.is_skip_tool(tool_name, args):
            return ValidatedWorkerResult(
                worker_type="phase1",
                        session_id=session_id,
                        run_id=run_id,
                attempts=max(1, fail_count),
                content="skip",
                blocks=(),
                path=None,
            )
        if tool_name == digest_tools.PHASE1_PATCH_TOOL and previous_args:
            merged = digest_tools.merge_digest_blocks_patch(previous_args, args)
        elif tool_name == digest_tools.PHASE1_SUBMIT_TOOL:
            merged = args
        elif tool_name == digest_tools.PHASE1_PATCH_TOOL:
            merged = args if isinstance(args.get("blocks"), list) else previous_args
        else:
            last_errors = [f"unexpected tool {tool_name!r}"]
            continue
        previous_args = dict(merged)
        accepted, errors, _notes = digest_tools.accept_phase1_args(
            previous_args,
            user_subjects=_decision_user_subjects(),
        )
        previous_args = accepted
        if errors:
            fail_count += 1
            last_errors = errors
            _append_worker_failure_record(
                worker_type="phase1",
                session_id=session_id,
                run_id=run_id,
                attempt=fail_count,
                max_attempts=max_attempts,
                errors=errors,
                content="",
                exhausted=fail_count >= max_attempts,
            )
            continue
        try:
            blocks = _blocks_from_digest_args(
                previous_args,
                session_id=session_id,
                message_start_id=message_start_id,
                message_end_id=message_end_id,
                date_str=date_str,
            )
            content = _content_from_blocks(blocks) if blocks else "skip"
        except Exception as render_exc:
            fail_count += 1
            last_errors = [f"renderer defect: {render_exc}"]
            _append_worker_failure_record(
                worker_type="phase1",
                session_id=session_id,
                run_id=run_id,
                attempt=fail_count,
                max_attempts=max_attempts,
                errors=last_errors,
                content="",
                exhausted=fail_count >= max_attempts,
            )
            continue
        accepted_args = previous_args
        last_blocks = tuple(blocks)
        return ValidatedWorkerResult(
            worker_type="phase1",
            session_id=session_id,
            run_id=run_id,
            attempts=max(1, fail_count + 1),
            content=content or "skip",
            blocks=tuple(blocks),
            path=None,
        )

    # Exhaust / no successful validation in the turn.
    if not last_errors and not previous_args:
        last_errors = ["no tool call returned (fail closed)"]
        _append_worker_failure_record(
            worker_type="phase1",
            session_id=session_id,
            run_id=run_id,
            attempt=max_attempts,
            max_attempts=max_attempts,
            errors=last_errors,
            content="",
            exhausted=True,
        )

    if fail_count >= max_attempts:
        dirty_args = digest_tools.clamp_blocks_importance_dirty(previous_args)
        if isinstance(dirty_args.get("blocks"), list) and dirty_args.get("blocks"):
            try:
                dirty_blocks = _blocks_from_digest_args(
                    dirty_args,
                    session_id=session_id,
                    message_start_id=message_start_id,
                    message_end_id=message_end_id,
                    date_str=date_str,
                )
            except Exception:
                dirty_blocks = list(last_blocks)
            for block in dirty_blocks:
                block["importance"] = digest_tools.IMPORTANCE_DIRTY
            if dirty_blocks:
                _log(
                    f"worker_accepted_dirty type=phase1 session={session_id} "
                    f"run={run_id} attempts={fail_count} "
                    f"importance={digest_tools.IMPORTANCE_DIRTY} "
                    f"errors={'; '.join(last_errors[:3])}"
                )
                return ValidatedWorkerResult(
                    worker_type="phase1",
                    session_id=session_id,
                    run_id=run_id,
                    attempts=fail_count,
                    content=_content_from_blocks(dirty_blocks),
                    blocks=tuple(dirty_blocks),
                    path=None,
                    accepted_dirty=True,
                )
    del accepted_args
    return WorkerFailure(
        worker_type="phase1",
        session_id=session_id,
        run_id=run_id,
        attempts=max(fail_count, 1),
        errors=tuple(last_errors),
    )


def _persist_phase1_candidates(
    daily_path: Path,
    blocks: Sequence[Mapping[str, Any]],
) -> None:
    """Append Phase-1 cards then refresh the entity index so original-language aliases are searchable the same day.

    Index rebuild is fail-open: a digest write must not roll back if recall indexing fails.
    """
    if not blocks:
        return
    content = _content_from_blocks(blocks)
    if content.strip():
        _append_daily_digest(daily_path, content)
        try:
            from recall.normalize import write_entity_index

            write_entity_index(_hermes_home() / "memories" / "staging")
        except Exception as exc:
            _log(f"entity index refresh fail-open: {exc}")


def _filter_redundant_phase1_creates(
    operations: Sequence[digest_operations.Operation],
    board_ids: set[str],
    *,
    phase1_persisted: bool = False,
) -> list[digest_operations.Operation]:
    """Drop Phase-2 create ops (legacy). Cards come from Phase-1 persist.

    Keep merge/update/drop/supersede. ``board_ids`` / ``phase1_persisted`` are
    kept for call-site compatibility.
    """
    del board_ids, phase1_persisted
    return [op for op in operations if op.operation != "create"]


def _run_phase2_consolidate(
    *,
    daily_path: Path,
    existing_blocks: list[dict[str, Any]],
    new_blocks: list[dict[str, Any]],
    session_id: str,
    run_id: str,
    platform: str,
    proposer: Any = None,
    max_attempts: int | None = None,
) -> tuple[bool, list[str]]:
    """Consolidate after Phase-1 persist. Failure leaves daily candidates as-is."""
    if not new_blocks and not existing_blocks:
        return True, []
    try:
        dedup_proposer = proposer or make_llm_proposer(
            platform, session_id=session_id, run_id=run_id
        )
        prepared_operations, _operation_path = _run_update_operator(
                existing_blocks,
                new_blocks,
                session_id=session_id,
                run_id=run_id,
            proposer=dedup_proposer,
            max_attempts=max_attempts,
        )
        before = daily_path.read_text(encoding="utf-8") if daily_path.exists() else ""
        board_ids = {
            str(b.get("id"))
            for b in _daily_blocks(before)
            if b.get("id")
        }
        operations = _filter_redundant_phase1_creates(
            prepared_operations,
            board_ids,
            phase1_persisted=bool(new_blocks),
        )
        ok, errors = _commit_candidate_once(
                daily_path,
            [],
            operations,
                session_id=session_id,
                run_id=run_id,
            base_content=None,
        )
        return ok, errors
    except Exception as exc:
        return False, [f"organizer error: {exc}"]


def run_manual_phase2(
    daily_path: Path,
    *,
    date_str: str,
    retrieval_only: bool = False,
    session_key: str = "",
    user_content: str = "",
) -> dict[str, Any]:
    """Clock/UI Phase-2: leftover same-file overwrite first, then LLM merge.

    Extract already applied most supersedes; this prepend catches pointers that
    landed without an extract-time commit. Tick gates stay in the clock caller.
    Retrieval-grounded past-day rejection runs first so a co-retrieved
    contradiction cannot wait on the merge tick; ``retrieval_only`` skips the
    LLM so chat turns stay non-blocking.
    """
    payload: dict[str, Any] = {
        "outcome": "rewritten",
        "path": str(daily_path),
        "date": date_str,
        "operations": 0,
    }

    def apply_retrieval_grounded_updates() -> None:
        """Close dated past cards from this session's fresh recall set only."""
        import memory_staging as staging_mod
        from recall.ids import BlockIndex
        from recall.lexical import rebuild_lexical
        from recall.normalize import entity_key, write_entity_index

        key = str(session_key or "").strip()
        if not key:
            return
        with _digest_lock:
            state = _load_state()
            entry = (state.get("sessions") or {}).get(key) or {}
            retrieval = dict(entry.get("retrieval") or {})
        ids = [str(item) for item in retrieval.get("ids") or [] if str(item).strip()]
        if not ids or retrieval.get("consumed"):
            return
        recorded_at = float(retrieval.get("recorded_at") or 0)
        if recorded_at <= 0 or (time.time() - recorded_at) > RETRIEVAL_TTL_SECONDS:
            return
        root = _hermes_home() / "memories" / "staging"
        store = BlockIndex(root)
        resolved = []
        for mem_id in ids:
            rec = store.get(mem_id)
            if rec is not None:
                resolved.append(rec)
        if not resolved:
            return
        max_day = max(str(rec.day or "") for rec in resolved)
        today = date_str or hermes_local_today_str()
        user_text = str(user_content or "")
        user_correction = bool(_USER_DATED_RE.search(user_text))
        patches: list[tuple[str, str, str]] = []
        if user_correction:
            older = [
                rec
                for rec in resolved
                if str(rec.parsed.get("status") or "").strip() != "rejected"
                and str(rec.day or "") < max_day
            ]
            if len(older) != 1:
                with _digest_lock:
                    state = _load_state()
                    sessions = state.setdefault("sessions", {})
                    held = dict(sessions.get(key) or {})
                    retrieval = dict(held.get("retrieval") or {})
                    retrieval["consumed"] = True
                    held["retrieval"] = retrieval
                    sessions[key] = held
                    _save_state(state)
                return
            target = older[0]
            valid_from = str(target.parsed.get("valid_from") or target.day or "")[:10]
            valid_to = today if today >= valid_from else valid_from
            patches.append((target.block_id, valid_to, USER_CORRECTION_REASON))
        else:
            groups: dict[tuple[str, str], list] = {}
            for rec in resolved:
                if str(rec.parsed.get("status") or "").strip() == "rejected":
                    continue
                group_key = (
                    str(rec.item_type or "").strip(),
                    entity_key(rec.entity) or str(rec.entity or "").strip().casefold(),
                )
                groups.setdefault(group_key, []).append(rec)
            for _group, recs in groups.items():
                recs = sorted(recs, key=lambda item: str(item.day or ""))
                if len(recs) < 2:
                    continue
                latest = recs[-1]
                for older_rec in recs[:-1]:
                    if str(older_rec.day or "") >= str(latest.day or ""):
                        continue
                    valid_from = str(
                        older_rec.parsed.get("valid_from") or older_rec.day or ""
                    )[:10]
                    valid_to = str(latest.day or today)
                    if valid_from and valid_to < valid_from:
                        continue
                    patches.append(
                        (
                            older_rec.block_id,
                            valid_to,
                            f"rejected by {latest.block_id}",
                        )
                    )
        if not patches:
            with _digest_lock:
                state = _load_state()
                sessions = state.setdefault("sessions", {})
                held = dict(sessions.get(key) or {})
                retrieval = dict(held.get("retrieval") or {})
                retrieval["consumed"] = True
                held["retrieval"] = retrieval
                sessions[key] = held
                _save_state(state)
            return
        wrote = False
        for block_id, valid_to, reason in patches:
            changes = {
                "valid_to": valid_to,
                "status": "rejected",
                "rejected_reason": reason,
            }
            target_rec = store.get(block_id)
            existing_block = (
                {
                    "id": block_id,
                    "valid_from": str(
                        (target_rec.parsed.get("valid_from") if target_rec else "")
                        or (target_rec.day if target_rec else "")
                    )[:10],
                }
                if target_rec is not None
                else {"id": block_id, "valid_from": "1970-01-01"}
            )
            errors = digest_operations.check_type_rules(
                [existing_block],
                [{"operation": "update", "id": block_id, "changes": changes}],
                current_day=today,
                retrieval_ids=ids,
            )
            if errors:
                continue
            ok = staging_mod.patch_daily_block_status(
                _hermes_home(),
                block_id,
                status="rejected",
                timestamp_field="superseded_at",
                timestamp_value=valid_to,
                extra_fields={
                    "valid_to": valid_to,
                    "rejected_reason": reason,
                },
            )
            if not ok:
                raise OSError(f"failed to patch {block_id}")
            wrote = True
        if wrote:
            try:
                rebuild_lexical(root)
                write_entity_index(root)
            except Exception:
                pass
        with _digest_lock:
            state = _load_state()
            sessions = state.setdefault("sessions", {})
            held = dict(sessions.get(key) or {})
            retrieval = dict(held.get("retrieval") or {})
            retrieval["consumed"] = True
            held["retrieval"] = retrieval
            sessions[key] = held
            _save_state(state)

    try:
        apply_retrieval_grounded_updates()
    except Exception as exc:
        _log(f"retrieval-grounded update fail-open: {exc}")
        if retrieval_only:
            return {**payload, "outcome": "failed", "errors": [str(exc)]}
    if retrieval_only:
        return payload
    content = daily_path.read_text(encoding="utf-8") if daily_path.exists() else ""
    existing = list(_daily_blocks(content))
    if not existing:
        return payload
    session_id = "weekly-ui-reorganise"
    leftover_run_id = uuid.uuid4().hex[:12]
    run_id = uuid.uuid4().hex[:12]
    try:
        leftover = [
            op
            for op in digest_operations.build_update_operations(existing, [])
            if op.operation == "supersede"
        ]
        if leftover:
            ok, errors = _commit_candidate_once(
                daily_path,
                [],
                leftover,
                session_id=session_id,
                run_id=leftover_run_id,
                base_content=None,
            )
            if not ok:
                _log(
                    f"digest leftover supersede fail-open session={session_id} "
                    f"run={leftover_run_id} errors={errors}"
                )
            else:
                content = (
                    daily_path.read_text(encoding="utf-8")
                    if daily_path.exists()
                    else ""
                )
                existing = list(_daily_blocks(content))
                if not existing:
                    return payload
        proposer = make_oneshot_proposer(session_id=session_id, run_id=run_id)
        ok, errors = _run_phase2_consolidate(
            daily_path=daily_path,
            existing_blocks=existing,
            new_blocks=[],
                session_id=session_id,
            run_id=run_id,
            platform="cli",
            proposer=proposer,
            max_attempts=2,
        )
    except Exception as exc:
        return {**payload, "outcome": "failed", "errors": [str(exc)]}
    if not ok:
        return {**payload, "outcome": "failed", "errors": errors}
    return payload


def _run_digest_pipeline(
    *,
    session_id: str,
    platform: str,
    transcript: str,
    session_key: str,
    daily_path: Path,
    batch_end_id: int | None,
    run_id: str,
    reason: str,
    user_count: int = 0,
    assistant_count: int = 0,
    batch_start_id: int | None = None,
) -> str:
    """Phase-1 extract → persist → same-day supersede commit → bookmark.

    Merge stays on the 08/12/16/20 clock. Apply overwrite here so a later
    batch in the same civil day does not wait for that tick. Fail-open keeps
    the helper card and still bookmarks so extract cannot stall.
    Passes ``daily_path.stem`` into Phase-1 so leftover persist and minted
    ids stay on that board date when wall-clock today has already rolled.
    """
    try:
        phase1 = run_phase1_digest_blocks(
            session_id,
            platform,
            transcript,
            run_id=run_id,
            reason=reason,
            user_count=user_count,
            assistant_count=assistant_count,
            message_start_id=batch_start_id,
            message_end_id=batch_end_id,
            date_str=daily_path.stem,
        )
    except Exception as exc:
        _finalize_digest_failure(
            session_key, session_id, [f"phase1 worker error: {exc}"]
        )
        return "failed"
    if isinstance(phase1, WorkerFailure):
        _finalize_digest_failure(session_key, session_id, list(phase1.errors))
        return "failed"

    new_blocks = [copy.deepcopy(dict(b)) for b in phase1.blocks]
    if not new_blocks or _is_skip_only_content(phase1.content):
        _log(
            f"digest skip (nothing durable) session={session_id} run={run_id} "
            f"reason={reason}"
        )
        _finalize_digest_success(
            session_key, batch_end_id, session_id=session_id
        )
        return "skip"

    try:
        before_content = (
            daily_path.read_text(encoding="utf-8") if daily_path.exists() else ""
        )
        prior_count = len(_daily_blocks(before_content))
    except OSError:
        prior_count = 0

    try:
        _persist_phase1_candidates(daily_path, new_blocks)
    except Exception as exc:
        _finalize_digest_failure(
            session_key, session_id, [f"phase1 persist error: {exc}"]
        )
        return "failed"

    try:
        after_content = (
            daily_path.read_text(encoding="utf-8") if daily_path.exists() else ""
        )
        on_file = list(_daily_blocks(after_content))
        supersede_ops = [
            op
            for op in digest_operations.build_update_operations(on_file, [])
            if op.operation == "supersede"
        ]
        if supersede_ops:
            ok, errors = _commit_candidate_once(
                daily_path,
                [],
                supersede_ops,
                session_id=session_id,
                run_id=run_id,
                base_content=None,
            )
            if not ok:
                _log(
                    f"digest supersede fail-open session={session_id} "
                    f"run={run_id} errors={errors}"
                )
    except Exception as exc:
        _log(
            f"digest supersede fail-open session={session_id} "
            f"run={run_id} error={exc}"
        )

    _finalize_digest_success(session_key, batch_end_id, session_id=session_id)
    _log(
        f"digest pipeline appended session={session_id} "
        f"run={run_id} reason={reason} prior_cards={prior_count}"
    )
    return "appended"


def _run_event_first_workers(
    *,
    session_id: str,
    platform: str,
    transcript: str,
    session_key: str,
    daily_path: Path,
    batch_end_id: int | None,
    run_id: str,
    reason: str,
    user_count: int = 0,
    assistant_count: int = 0,
    batch_start_id: int | None = None,
) -> str:
    """Compatibility wrapper — delegates to ``_run_digest_pipeline``."""
    return _run_digest_pipeline(
        session_id=session_id,
        platform=platform,
        transcript=transcript,
        session_key=session_key,
        daily_path=daily_path,
        batch_end_id=batch_end_id,
        run_id=run_id,
        reason=reason,
        user_count=user_count,
        assistant_count=assistant_count,
        batch_start_id=batch_start_id,
    )


def _run_digest_pipeline_entry(
    session_key: str,
    session_id: str,
    platform: str,
    daily_path: Path,
    transcript: str,
    batch_end_id: int | None,
    *,
    reason: str = "digest",
    user_count: int = 0,
    assistant_count: int = 0,
    batch_start_id: int | None = None,
) -> str:
    """Run the digest pipeline. Returns appended/skip/failed."""
    _digest_worker_active.active = True
    try:
        return _run_digest_pipeline(
            session_id=session_id,
            platform=platform,
            transcript=transcript,
            session_key=session_key,
            daily_path=daily_path,
            batch_end_id=batch_end_id,
            run_id=uuid.uuid4().hex,
            reason=reason,
            user_count=user_count,
            assistant_count=assistant_count,
            batch_start_id=batch_start_id,
        )
    finally:
        _digest_worker_active.active = False


# Public orchestrator name (locked). Keep legacy alias for hooks/tests.
_run_digest_worker = _run_digest_pipeline_entry


def _maybe_run_digest(
    session_key: str,
    reason: str,
    *,
    force: bool = False,
    sync: bool = False,
    date_str: str | None = None,
) -> dict[str, Any]:
    """Decide and launch a digest run for ``session_key``.

    Auto runs wait for ``BATCH_USER_MESSAGES`` user turns, then take only
    those turns plus assistant replies between them so leftover user 13+
    cannot inflate one extract. ``force`` skips the floor and that clip
    (still requires undigested messages). ``sync`` runs the worker inline
    and returns its terminal outcome ('appended' / 'skip' / 'failed')
    instead of 'started'. Hook callers ignore the outcome dict; slash inspects it.
    Nightly leftover passes ``date_str`` so a missed 23:55 still writes
    yesterday's file instead of the morning's civil date.
    """
    with _digest_lock:
        state = _load_state()
        entry = state.get("sessions", {}).get(session_key)
        if not entry:
            _log(f"skip digest ({reason}): no state for {session_key}")
            return {"outcome": "no_state", "session_key": session_key}

        session_id = entry.get("session_id")
        if not session_id:
            _log(f"skip digest ({reason}): missing session_id for {session_key}")
            return {"outcome": "no_session", "session_key": session_key}

        if entry.get("digest_in_flight"):
            started = _parse_ts(entry.get("last_digest_attempt_at"))
            if _seconds_since(started) <= IN_FLIGHT_STALE_SECONDS:
                _log(f"skip digest ({reason}): in flight session={session_id}")
                return {"outcome": "in_flight", "session_id": session_id}
            _log(f"recover stale in-flight digest ({reason}) session={session_id}")

        after_id = int(entry.get("last_digest_message_id") or 0)
        messages = _fetch_messages(session_id, after_id=after_id)
        user_count, assistant_count = _role_counts(messages)
        if not messages:
            _log(f"skip digest ({reason}): no undigested messages session={session_id}")
            return {
                "outcome": "empty",
                "session_id": session_id,
                "user": 0,
                "assistant": 0,
            }
        if not force and not _batch_ready(messages):
            _log(
                f"skip digest ({reason}): need {BATCH_USER_MESSAGES} user "
                f"messages, have {user_count} user / {assistant_count} assistant"
            )
            return {
                "outcome": "below_threshold",
                "session_id": session_id,
                "user": user_count,
                "assistant": assistant_count,
            }

        if not force:
            sliced: list[dict[str, Any]] = []
            users_kept = 0
            for msg in messages:
                if msg.get("role") == "user":
                    if users_kept >= BATCH_USER_MESSAGES:
                        break
                    users_kept += 1
                    sliced.append(msg)
                    continue
                sliced.append(msg)
            messages = sliced
            user_count, assistant_count = _role_counts(messages)

        platform = entry.get("platform", "")
        transcript = _format_transcript(messages)
        daily_dir = _staging_daily()
        migrate_legacy_daily_yaml(daily_dir)
        today = date_str or hermes_local_today_str()
        daily_path = daily_staging_path(_hermes_home(), today)
        batch_start_id = messages[0]["id"] if messages else None
        batch_end_id = messages[-1]["id"] if messages else None
        _log(
            f"start digest ({reason}{' force' if force else ''}"
            f"{' sync' if sync else ''}) session={session_id} messages={len(messages)} "
            f"range={batch_start_id}-{batch_end_id}"
        )

        entry["digest_in_flight"] = True
        entry["in_flight_batch_end_id"] = batch_end_id
        entry["last_digest_attempt_at"] = datetime.now(timezone.utc).isoformat()
        state["sessions"][session_key] = entry
        _save_state(state)

    base = {
        "session_id": session_id,
        "path": str(daily_path),
        "batch_end_id": batch_end_id,
        "batch_start_id": batch_start_id,
        "user": user_count,
        "assistant": assistant_count,
    }

    if sync:
        outcome = _run_digest_worker(
            session_key,
            session_id,
            platform,
            daily_path,
            transcript,
            batch_end_id,
            reason=reason,
            user_count=user_count,
            assistant_count=assistant_count,
            batch_start_id=batch_start_id,
        )
        return {"outcome": outcome, **base}

    thread = threading.Thread(
        target=_run_digest_worker,
        args=(
            session_key,
            session_id,
            platform,
            daily_path,
            transcript,
            batch_end_id,
        ),
        kwargs={
            "reason": reason,
            "user_count": user_count,
            "assistant_count": assistant_count,
            "batch_start_id": batch_start_id,
        },
        name=f"memory-digest-{session_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {"outcome": "started", **base}


def on_agent_end(context: dict) -> None:
    """Bookmark-ready extract after a turn; also stamp a dead civil clock.

    Chat is the only observer if the daemon died without an except handler
    writing clock_alive; leftover still runs via maybe_run_digest_clock.
    Schedules retrieval-grounded Phase-2 on a daemon thread so dated
    co-retrieved cards can close without blocking the chat turn.
    """
    if getattr(_digest_worker_active, "active", False):
        return

    if _clock_thread is None or not _clock_thread.is_alive():
        with _digest_lock:
            state = _load_state()
            was_alive = state.get("clock_alive")
            state["clock_alive"] = False
            _save_state(state)
        if was_alive is not False:
            _log("digest clock observed dead")

    session_key = _session_key(context)
    session_id = context.get("session_id")
    if not session_id:
        return

    with _digest_lock:
        state = _load_state()
        sessions = state.setdefault("sessions", {})
        entry = sessions.get(session_key, {})
        entry.update(
            {
                "session_id": session_id,
                "platform": context.get("platform", entry.get("platform", "")),
            }
        )
        sessions[session_key] = entry
        _save_state(state)

        after_id = int(entry.get("last_digest_message_id") or 0)
        messages = _fetch_messages(session_id, after_id=after_id)

    if _batch_ready(messages):
        _maybe_run_digest(session_key, reason=BOOKMARK_TRIGGER_REASON)
    maybe_run_digest_clock(sync=False)
    user_content = str(context.get("user_content") or "")
    today = hermes_local_today_str()
    threading.Thread(
        target=run_manual_phase2,
        args=(),
        kwargs={
            "daily_path": daily_staging_path(_hermes_home(), today),
            "date_str": today,
            "retrieval_only": True,
            "session_key": session_key,
            "user_content": user_content,
        },
        name="memory-retrieval-phase2",
        daemon=True,
    ).start()


def on_session_boundary(context: dict, reason: str) -> None:
    session_key = _session_key(context)
    _maybe_run_digest(session_key, reason=reason)
    maybe_run_digest_clock(sync=False)


# ---------------------------------------------------------------------------
# Last-3-day staging index inject (pre_llm_call).
# ---------------------------------------------------------------------------


@dataclass
class StagingEntry:
    tier: int
    file_date: date | None
    parsed: dict[str, Any]
    body: str
    path: Path


def _recent_daily_files(n: int) -> list[Path]:
    """Newest n daily files so prompt id lists and week-alive can disagree on width."""
    all_files = iter_daily_staging_files(_staging_daily())
    if n <= 0:
        return all_files
    return all_files[-n:] if len(all_files) > n else all_files


def _daily_files_for_tier(tier: int = 1) -> list[Path]:
    """Last seven daily files. The two-tier split is retired; ``tier`` is ignored."""
    del tier
    return _recent_daily_files(7)


def _file_date_for_path(path: Path, tier: int) -> date | None:
    del tier
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None


def _collect_staging_entries(paths: list[Path], tier: int) -> list[StagingEntry]:
    entries: list[StagingEntry] = []
    for path in paths:
        file_date = _file_date_for_path(path, tier)
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
            if str(parsed.get("status", "")).strip() != "candidate":
                continue
            item_type = str(parsed.get("type", "")).strip()
            if item_type not in RECENT_CONTEXT_TYPES:
                continue
            entries.append(
                StagingEntry(
                    tier=tier,
                    file_date=file_date,
                    parsed=parsed,
                    body=body,
                    path=path,
                )
            )
    return entries


def _block_anchor_set(parsed: dict[str, Any]) -> set[str]:
    anchors: set[str] = set()
    entity = str(parsed.get("entity", "")).strip()
    if entity:
        anchors.add(entity)
    involves_raw = parsed.get("involves")
    if isinstance(involves_raw, list):
        for item in involves_raw:
            tag = _involves_entity_name(item)
            if tag:
                anchors.add(tag)
    participants_raw = parsed.get("participants")
    if isinstance(participants_raw, list):
        for item in participants_raw:
            if isinstance(item, dict):
                tag = str(item.get("entity", "")).strip()
                if tag:
                    anchors.add(tag)
    return anchors


def _related_ref_id(ref: Any) -> str:
    """Normalize a related: entry to a bare mem-/w-evt- id (strip [N] markers)."""
    text = str(ref or "").strip()
    if not text:
        return ""
    # Weekly style: "[1] mem-…" or "'[1] mem-…'"
    m = re.search(r"(mem-[A-Za-z0-9_-]+|w-evt-[A-Za-z0-9_-]+)", text)
    return m.group(1) if m else text


def _standalone_block_ids(entries: list[StagingEntry]) -> set[str]:
    """Non-event block ids that no event in ``entries`` points at via related:."""
    referenced: set[str] = set()
    all_ids: set[str] = set()
    event_ids: set[str] = set()
    for entry in entries:
        block_id = str(entry.parsed.get("id", "")).strip()
        if not block_id:
            continue
        all_ids.add(block_id)
        item_type = str(entry.parsed.get("type", "")).strip()
        if item_type == "event":
            event_ids.add(block_id)
            related = entry.parsed.get("related")
            if isinstance(related, list):
                for ref in related:
                    rid = _related_ref_id(ref)
                    if rid:
                        referenced.add(rid)
    return {
        block_id
        for block_id in all_ids
        if block_id not in event_ids and block_id not in referenced
    }


_PATH_SHAPED_ANCHOR_RE = re.compile(
    r"(?:/)|(?:\.(?:md|py|json|ya?ml|txt|sh|ts|js|tsx|jsx)\b)",
    re.IGNORECASE,
)


def _normalize_anchor_key(name: str) -> str:
    """Same join key as Channel 2 so the prefetch index cannot fork aliases."""
    from recall.normalize import entity_key

    return entity_key(name)


def _index_anchor_set(parsed: dict[str, Any]) -> set[str]:
    """Display/index anchors: primary entity + involves; no participants; no paths."""
    anchors: set[str] = set()
    entity = str(parsed.get("entity", "")).strip()
    if entity and not _PATH_SHAPED_ANCHOR_RE.search(entity):
        anchors.add(entity)
    involves_raw = parsed.get("involves")
    if isinstance(involves_raw, list):
        for item in involves_raw:
            tag = _involves_entity_name(item)
            if tag and not _PATH_SHAPED_ANCHOR_RE.search(tag):
                anchors.add(tag)
    return anchors


def _fold_index_anchors(surface_forms: list[str]) -> dict[str, str]:
    """Map normalized key → most frequent surface form (stable on ties: first seen)."""
    counts: dict[str, dict[str, int]] = {}
    order: dict[str, list[str]] = {}
    for name in surface_forms:
        key = _normalize_anchor_key(name)
        if not key:
            continue
        if key not in counts:
            counts[key] = {}
            order[key] = []
        if name not in counts[key]:
            order[key].append(name)
            counts[key][name] = 0
        counts[key][name] += 1
    result: dict[str, str] = {}
    for key, forms in counts.items():
        best = max(order[key], key=lambda n: (forms[n], -order[key].index(n)))
        result[key] = best
    return result


def _recall_bootstrap_map(state: dict[str, Any]) -> dict[str, str]:
    raw = state.get("recall_bootstrap_sessions")
    if not isinstance(raw, dict):
        raw = {}
        state["recall_bootstrap_sessions"] = raw
    legacy = state.get("recall_injected_sessions")
    if isinstance(legacy, dict):
        for key, val in legacy.items():
            if key not in raw and isinstance(val, str) and val not in ("", "done"):
                raw[key] = val
    return raw


def _mark_bootstrap(session_id: str) -> None:
    if not session_id:
        return
    state = _load_state()
    _recall_bootstrap_map(state)[session_id] = hermes_local_today_str()
    _save_state(state)


def _recall_inject_decision(
    session_id: str,
    user_message: Any,
) -> Literal["skip", "bootstrap"]:
    del user_message
    today = hermes_local_today_str()
    state = _load_state()
    if _recall_bootstrap_map(state).get(session_id) == today:
        return "skip"
    return "bootstrap"


def _run_span_validator_llm(
    user_message: str,
    candidates: list[dict],
    conversation_excerpt: str = "",
) -> list[dict]:
    """Compare conversation to open-span candidates; return confidence ladder rows.

    Each row: {block_key, confidence, proposed_valid_to?}. On failure, all low.
    explicit without proposed_valid_to is coerced to high (spec).
    """
    if not candidates:
        return []

    def _fallback_low() -> list[dict]:
        return [
            {
                "block_key": str(c.get("id") or c.get("block_key") or "").strip(),
                "confidence": "low",
            }
            for c in candidates
            if str(c.get("id") or c.get("block_key") or "").strip()
        ]

    cand_lines: list[str] = []
    for c in candidates:
        key = str(c.get("id") or c.get("block_key") or "").strip()
        if not key:
            continue
        entity = str(c.get("entity") or "").strip()
        body = str(c.get("body") or "").strip()[:120]
        cand_lines.append(f"{key} | {entity}: {body}")

    if not cand_lines:
        return []

    prompt = (
        "You are the memory span validator. Return ONLY JSON.\n"
        f"User message: {user_message}\n"
        f"Conversation excerpt: {conversation_excerpt or '(none)'}\n"
        f"Open-span candidates:\n" + "\n".join(cand_lines) + "\n"
        "Output schema (JSON array):\n"
        '[{"block_key":"<id>","confidence":"explicit|high|medium|low",'
        '"proposed_valid_to":"YYYY-MM-DD"?}]\n'
        "Rules: confidence=explicit only when a concrete finish date is clear "
        "enough to propose as proposed_valid_to; otherwise use high/medium/low. "
        "Include one object per candidate block_key listed above."
    )
    try:
        raw = _invoke_digest_llm(prompt, "cli", purpose="span_validator")
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                return _fallback_low()
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                rows = data.get("results") or data.get("candidates") or [data]
            else:
                rows = data
        else:
            rows = json.loads(raw[start : end + 1])
        if not isinstance(rows, list):
            return _fallback_low()

        out: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("block_key") or "").strip()
            if not key or key in seen:
                continue
            confidence = str(row.get("confidence") or "low").strip().lower()
            if confidence not in SPAN_CONFIDENCES:
                confidence = "low"
            proposed = str(row.get("proposed_valid_to") or "").strip()
            if confidence == "explicit" and not (_DATE_RE.match(proposed) if proposed else False):
                confidence = "high"
                proposed = ""
            item: dict[str, Any] = {"block_key": key, "confidence": confidence}
            if proposed and _DATE_RE.match(proposed):
                item["proposed_valid_to"] = proposed
            out.append(item)
            seen.add(key)
        return out or _fallback_low()
    except Exception as exc:
        _log(f"span validator LLM failed, treating candidates as low: {exc}")
        return _fallback_low()


def _hot_memory_text() -> str:
    mem_dir = _hermes_home() / "memories"
    parts: list[str] = []
    for name in ("MEMORY.md", "USER.md"):
        path = mem_dir / name
        if not path.exists():
            continue
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(parts)


def _entity_in_hot_memory(entity: str, hot_text: str) -> bool:
    tag = entity.strip()
    if not tag or not hot_text:
        return False
    return tag.casefold() in hot_text.casefold()


def _entity_filter_match(parsed: dict[str, Any], entities: list[str] | None) -> bool:
    if not entities:
        return True
    anchors = _block_anchor_set(parsed)
    entity_folded = {e.casefold() for e in entities}
    return any(a.casefold() in entity_folded for a in anchors)


def _expiring_blocks(files: list[Path], *, open_only: bool = False) -> list[dict[str, str]]:
    """Return blocks whose valid_to is past today or is 'open' (actionable).

    When open_only=True, keep only valid_to == open (v1 ladder candidates).
    """
    today = hermes_local_today_str()
    out: list[dict[str, str]] = []
    for path in files:
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
            if not _entity_filter_match(parsed, None):
                continue
            valid_to = str(parsed.get("valid_to", "")).strip()
            if not valid_to:
                continue
            is_open = valid_to == OPEN_SPAN
            is_past = bool(_DATE_RE.match(valid_to)) and valid_to < today
            if open_only:
                if not is_open:
                    continue
            elif not (is_open or is_past):
                continue
            primary = str(parsed.get("entity", "")).strip()
            involves_label = ""
            involves_raw = parsed.get("involves")
            if isinstance(involves_raw, list):
                names = [
                    name
                    for name in (_involves_entity_name(x) for x in involves_raw)
                    if name and name != primary
                ]
                involves_label = ", ".join(names)
            else:
                participants_raw = parsed.get("participants")
                if isinstance(participants_raw, list):
                    names = []
                    for item in participants_raw:
                        if isinstance(item, dict):
                            tag = str(item.get("entity", "")).strip()
                            if tag and tag != primary:
                                names.append(tag)
                    involves_label = ", ".join(names)
                if not involves_label:
                    secondary = sorted(a for a in _block_anchor_set(parsed) if a != primary)
                    involves_label = ", ".join(secondary)
            out.append(
                {
                    "file": path.name,
                    "id": str(parsed.get("id", "")),
                    "entity": str(parsed.get("entity", "")),
                    "involves": involves_label,
                    "valid_from": str(parsed.get("valid_from", "")).strip(),
                    "valid_to": valid_to,
                    "state": "open" if is_open else "past",
                    "body": body,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Last-3-day inject index (manifest) + weekly span primitives
# ---------------------------------------------------------------------------


def _is_narration_fact(parsed: dict[str, Any], body: str) -> bool:
    return (
        str(parsed.get("type", "")).strip() == "fact"
        and str(body or "").lstrip().startswith(_NARRATION_PREFIX)
    )


def _collect_printed_ids_and_manifest(
    entries: list[StagingEntry],
    *,
    hot_text: str = "",
    heading: str = "## Recall index",
) -> tuple[str, list[str]]:
    """Return (manifest text, printed searchable mem-ids)."""
    if not entries:
        return "", []

    standalone = _standalone_block_ids(entries)
    # Gather surface forms for folding
    surfaces: list[str] = []
    for entry in entries:
        surfaces.extend(_index_anchor_set(entry.parsed))
    fold_map = _fold_index_anchors(surfaces)  # norm_key -> display
    display_of = {
        _normalize_anchor_key(s): fold_map[_normalize_anchor_key(s)]
        for s in surfaces
        if _normalize_anchor_key(s) in fold_map
    }

    # Per display-anchor aggregate
    class _Agg:
        def __init__(self) -> None:
            self.n = 0
            self.days: set[str] = set()
            self.types: dict[str, int] = {}
            self.event_ids: list[tuple[str, str]] = []  # (id, predicate)
            self.narration_ids: list[str] = []
            self.standalone_ids: list[str] = []

    by_anchor: dict[str, _Agg] = {}
    by_day: dict[str, dict[str, Any]] = {}
    wrap_by_day: dict[str, str] = {}
    printed: list[str] = []
    printed_set: set[str] = set()

    def _add_printed(mid: str) -> None:
        if mid and mid not in printed_set:
            printed_set.add(mid)
            printed.append(mid)

    for entry in entries:
        parsed = entry.parsed
        body = entry.body
        block_id = str(parsed.get("id", "")).strip()
        item_type = str(parsed.get("type", "")).strip() or "?"
        day_key = ""
        if entry.file_date is not None:
            day_key = entry.file_date.isoformat()
        else:
            day_key = entry.path.stem

        if day_key not in wrap_by_day:
            try:
                _fences, wrap_phrase = split_daily_wrapup(
                    entry.path.read_text(encoding="utf-8")
                )
            except OSError:
                wrap_phrase = ""
            wrap_by_day[day_key] = wrap_phrase
        day_row = by_day.setdefault(
            day_key,
            {"n": 0, "types": {}, "event_ids": [], "standalone": []},
        )
        day_row["n"] += 1
        day_row["types"][item_type] = int(day_row["types"].get(item_type) or 0) + 1

        index_anchors = _index_anchor_set(parsed)
        display_anchors: set[str] = set()
        for a in index_anchors:
            display_anchors.add(display_of.get(_normalize_anchor_key(a), a))

        is_event = item_type == "event"
        is_narr = _is_narration_fact(parsed, body)
        is_standalone = block_id in standalone

        if is_event and block_id:
            day_row["event_ids"].append(block_id)
            _add_printed(block_id)
        if is_narr and block_id:
            _add_printed(block_id)
        if is_standalone and block_id:
            day_row["standalone"].append(block_id)
            _add_printed(block_id)

        for display in display_anchors or ({"(unanchored)"} if is_event or is_narr or is_standalone else set()):
            agg = by_anchor.setdefault(display, _Agg())
            agg.n += 1
            if day_key:
                agg.days.add(day_key[-5:] if len(day_key) >= 5 else day_key)
            type_label = "narration" if is_narr else item_type
            agg.types[type_label] = int(agg.types.get(type_label) or 0) + 1
            if is_event and block_id:
                pred = str(parsed.get("predicate") or "").strip() or "?"
                if (block_id, pred) not in agg.event_ids:
                    agg.event_ids.append((block_id, pred))
            if is_narr and block_id and block_id not in agg.narration_ids:
                agg.narration_ids.append(block_id)
            if is_standalone and block_id and block_id not in agg.standalone_ids:
                agg.standalone_ids.append(block_id)

    lines = [heading, "", "### By entity"]
    ranked = sorted(by_anchor.items(), key=lambda kv: (-kv[1].n, kv[0].casefold()))
    for display, agg in ranked[:40]:
        type_bits = " ".join(f"{k}:{v}" for k, v in sorted(agg.types.items()))
        days = ",".join(sorted(agg.days))
        hot = ""
        if display != "(unanchored)" and _entity_in_hot_memory(display, hot_text):
            hot = " [also in hot memory]"
        lines.append(f"- {display} · {agg.n}blk · {days} · {type_bits}{hot}")
        for mid, pred in agg.event_ids:
            lines.append(f"  · [{mid}] {pred}")
        for mid in agg.narration_ids:
            lines.append(f"  · [{mid}] Narration")
        if agg.standalone_ids and not agg.event_ids and not agg.narration_ids:
            # only list standalone under entity when nothing else searchable
            shown = ", ".join(agg.standalone_ids[:8])
            lines.append(f"  · standalone (lower priority): {shown}")

    lines.extend(["", "### By day"])
    for day_key in sorted(by_day.keys(), reverse=True):
        row = by_day[day_key]
        type_bits = " ".join(f"{k}:{v}" for k, v in sorted(row["types"].items()))
        events = ",".join(row["event_ids"][:12]) or "(none)"
        line = f"- {day_key} · {row['n']} blocks · {type_bits} · events={events}"
        wrap_phrase = str(wrap_by_day.get(day_key) or "").strip()
        if wrap_phrase:
            index_bit = " · ".join(
                ln.lstrip("- ").strip()
                for ln in wrap_phrase.splitlines()
                if ln.strip()
            )
            if index_bit:
                line += f" · {index_bit}"
        lines.append(line)
        if row["standalone"]:
            shown = ", ".join(row["standalone"][:12])
            more = "" if len(row["standalone"]) <= 12 else ", …"
            lines.append(f"  standalone (lower priority): {shown}{more}")

    return "\n".join(lines), printed


def _build_recall_manifest_section(
    entries: list[StagingEntry],
    *,
    hot_text: str = "",
    heading: str = "## Recall index",
) -> tuple[str, list[str]]:
    return _collect_printed_ids_and_manifest(entries, hot_text=hot_text, heading=heading)


def build_recall_injection_context(
    **_: Any,
) -> str:
    """Inject Bands A–C (7-day wrap-ups, entity index, week ladder); Band D is empty."""
    from recall.tools import render_bands

    staging = _hermes_home() / "memories" / "staging"
    return render_bands(
        staging, today=hermes_local_today(), hot_text=_hot_memory_text()
    )


def on_pre_llm_call(
    user_message: Any = "",
    is_first_turn: bool = False,
    session_id: str = "",
    turn_id: str = "",
    platform: str = "",
    **_: Any,
) -> dict[str, str] | None:
    del is_first_turn, turn_id
    if in_worker_llm():
        return None
    maybe_run_digest_clock(sync=False)
    if (
        str(session_id or "").startswith("cron_")
        or os.environ.get("HERMES_CRON_SESSION") == "1"
        or str(platform or "").casefold() == "cron"
    ):
        return None
    decision = _recall_inject_decision(session_id, user_message)
    if decision == "skip":
        return None
    text = build_recall_injection_context(
        session_id=session_id,
        user_message=user_message,
    )
    if not text:
        return None
    if decision == "bootstrap":
        _mark_bootstrap(session_id)
    _log(
        f"recall injected session={session_id or '-'} decision={decision} "
        "window=bands-a-c"
    )
    return {"context": text}


def on_transform_session_search_recall(
    tool_name: str = "",
    result: Any = None,
    session_id: str = "",
    turn_id: str = "",
    args: dict[str, Any] | None = None,
    user_message: Any = "",
    **_: Any,
) -> str | None:
    del turn_id, args, user_message
    return digest_tools.transform_phase1_tool_result(
        str(tool_name or ""), result, session_id=session_id
    )
