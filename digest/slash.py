"""``/digest`` slash command — parse args, call digest_run, format text.

Subcommands (single flat ``/digest`` command, parsed from ``raw_args``):

    /digest --all                rewrite today's blocks from the full transcript
    /digest --batch              digest today's messages after the bookmark
    /digest YYYY-MM-DD           rewrite a past day, including the wrap-up
    /digest status               bookmark + undigested counts + in-flight + log
    /digest bookmark show        current bookmark id
    /digest bookmark set <id>    set bookmark to an absolute message id
    /digest bookmark rewind <n>  move bookmark back n message ids
    /digest bookmark reset --yes clear bookmark (re-digest whole session)
    /digest history              estimate 1d / 7d / 30d / all (read-only)
    /digest history <preset>     print one estimate; add --yes to run
    /digest history status|resume --yes|stop --yes

Every handler returns plain text and never raises.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from memory_staging import resolve_session
from worker_llm import format_token_line, sum_usage_after, usage_ledger_offset


def _digest_state_has(session_key: str) -> bool:
    """True when digest state already tracks this key, so slash must not remap it."""
    with digest._digest_lock:
        state = digest._load_state()
    entry = (state.get("sessions") or {}).get(session_key)
    return isinstance(entry, dict) and bool(entry)


def _transcript_id_for_gateway_key(session_key: str) -> str | None:
    """Map a WeChat routing key to the open Hermes transcript id.

    Plugin slash dispatch binds HERMES_SESSION_KEY and leaves HERMES_SESSION_ID
    empty, so a lookup on the routing address misses digest state and returns
    zero tokens.
    """
    if not session_key:
        return None
    db_path = digest._hermes_home() / "state.db"
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                """
                SELECT id FROM sessions
                WHERE session_key = ?
                ORDER BY (ended_at IS NULL) DESC, started_at DESC
                LIMIT 1
                """,
                (session_key,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    return str(row[0])


def _active_session() -> tuple[str, str] | None:
    """Resolve the current Hermes session from gateway context only.

    Digest slash commands never accept ``--session``; they always target the
    session that invoked the slash command. A routing key with no digest row
    is replaced by the open transcript id, which is what hooks actually store.
    """
    resolved = resolve_session("")
    if resolved is None:
        return None
    session_key, session_id = resolved
    if _digest_state_has(session_key):
        return session_key, session_id or session_key
    if session_id and session_id != session_key and _digest_state_has(session_id):
        return session_id, session_id
    mapped = _transcript_id_for_gateway_key(session_key)
    if mapped:
        return mapped, mapped
    return session_key, session_id or session_key


try:  # package import (normal plugin load)
    from . import digest, digest_run
except ImportError:  # pragma: no cover - direct pytest collection path
    def _load(name: str):
        path = Path(__file__).with_name(f"{name}.py")
        mod_name = f"memory_digest_{name}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    digest = _load("digest")
    digest_run = _load("digest_run")


def handle_digest(raw_args: str, *, history_sync: bool = False) -> str:
    """Entry point registered as the ``/digest`` slash command handler.

    ``history_sync`` is True for the CLI process so a confirmed backfill cannot
    exit when the parent command returns; chat keeps the run in-process.
    """
    try:
        tokens = shlex.split(raw_args or "")
    except ValueError:
        tokens = (raw_args or "").split()

    sub = tokens[0].lower() if tokens else ""

    if sub == "status":
        return _status(raw_args)
    if sub == "bookmark":
        return _bookmark(tokens[1:], raw_args)
    if sub == "history":
        return _history(tokens[1:], history_sync=history_sync)
    if sub in ("help", "?"):
        return _help()
    if _DATE_RE.match(sub):
        return _past_day(sub)
    if sub in ("--all", "--batch"):
        if len(tokens) != 1:
            return _run_usage()
        return _force_run(sub[2:])
    if sub and not sub.startswith("-"):
        return f"Unknown /digest subcommand: {sub}\n\n{_help()}"

    return _run_usage()


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _run_usage() -> str:
    """Bare /digest and mixed flags must not start a run the user did not name."""
    return (
        "Usage:\n"
        "  /digest --all\n"
        "  /digest --batch\n"
        "  /digest YYYY-MM-DD\n"
    )


def _past_day(day_text: str) -> str:
    """Rewrite one past civil day, including wrap-up, without moving today's bookmark."""
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        return _run_usage()
    if day >= digest.hermes_local_today():
        return f"/digest {day_text}: past civil date only.\n\n{_run_usage()}"
    return _force_run("date", date_str=day_text)


def _force_run(scope: str, date_str: str | None = None) -> str:
    """Start a day digest even when the gateway bound no transcript id.

    ``--all``, ``--batch``, and a past date scan state.db themselves. Refusing
    here made a WeChat slash return zero tokens before that scan could run.
    The past-date wrap-up runs after that scan's bill is saved, so its tokens
    are added onto the same reply.
    """
    resolved = _active_session()
    session_key = resolved[0] if resolved else ""

    result = digest_run.request_digest(
        session_key,
        reason="slash_force",
        scope=scope,
        date_str=date_str,
    )
    outcome = result.get("outcome")
    sid = result.get("session_id", session_key)

    messages = {
        "appended": "Digest run complete — staged new block(s).",
        "skip": "Digest run complete — nothing durable to stage (bookmark advanced).",
        "failed": "Digest ran but validation failed after retries; nothing staged. See log.",
        "empty": "No undigested messages — bookmark is already current.",
        "in_flight": "A digest for this session is already running; try again shortly.",
        "no_state": "No digest state for this session yet (no completed turns recorded).",
        "no_session": "Could not resolve a session id for the digest run.",
    }
    line = messages.get(outcome, f"Digest outcome: {outcome}")
    counts = ""
    if result.get("user") is not None:
        counts = f" [window: {result.get('user')} user / {result.get('assistant')} assistant]"
    usage = dict(result.get("usage") or {}) if isinstance(result.get("usage"), dict) else {}
    seconds = float(result.get("seconds") or 0)
    if scope == "date" and result.get("path") and outcome in {"appended", "skip"}:
        wrap_offset = usage_ledger_offset()
        wrap_started = time.monotonic()
        digest.run_day_wrapup(result["path"])
        extra = sum_usage_after(wrap_offset, keep_boundary_line=True)
        usage["input_tokens"] = int(usage.get("input_tokens") or 0) + int(
            extra.get("input_tokens") or 0
        )
        usage["output_tokens"] = int(usage.get("output_tokens") or 0) + int(
            extra.get("output_tokens") or 0
        )
        seconds += max(0.0, time.monotonic() - wrap_started)
    token_line = format_token_line(usage, seconds)
    return f"/digest ({sid}): {line}{counts}\n{token_line}"


def _status(raw_args: str) -> str:
    resolved = _active_session()
    if resolved is None:
        return _no_session()
    session_key, session_id = resolved
    info = digest_run.get_digest_status(session_key, session_id)

    if not info["has_state"]:
        return (
            f"/digest status ({info['session_id'] or session_key}): "
            "no digest state recorded yet."
        )

    lines = [
        f"/digest status ({info['session_id']})",
        f"  bookmark: message id {info['bookmark']}",
        f"  undigested: {info['undigested_user']} user / "
        f"{info['undigested_assistant']} assistant",
        f"  in flight: {'yes' if info['in_flight'] else 'no'}",
    ]
    if info.get("last_digest_at"):
        lines.append(f"  last digest: {info['last_digest_at']}")
    if info.get("last_failure_at"):
        lines.append(f"  last failure: {info['last_failure_at']}")
    if info.get("last_log"):
        lines.append(f"  last log: {info['last_log']}")
    return "\n".join(lines)


def _bookmark(args: list[str], raw_args: str) -> str:
    resolved = _active_session()
    if resolved is None:
        return _no_session()
    session_key, _session_id = resolved

    action = args[0].lower() if args else "show"

    if action == "show":
        current = digest_run.get_bookmark(session_key)
        return f"/digest bookmark ({session_key}): message id {current}"

    if action == "set":
        if len(args) < 2 or not args[1].lstrip("-").isdigit():
            return "Usage: /digest bookmark set <message-id>"
        result = digest_run.set_bookmark(session_key, int(args[1]))
        return _bookmark_result(session_key, result)

    if action == "rewind":
        if len(args) < 2 or not args[1].lstrip("-").isdigit():
            return "Usage: /digest bookmark rewind <count>"
        result = digest_run.rewind_bookmark(session_key, int(args[1]))
        return _bookmark_result(session_key, result)

    if action == "reset":
        if "--yes" not in args:
            return (
                "Refusing to reset the bookmark without confirmation. "
                "Run: /digest bookmark reset --yes"
            )
        result = digest_run.reset_bookmark(session_key)
        return _bookmark_result(session_key, result)

    return "Usage: /digest bookmark show | set <id> | rewind <n> | reset --yes"


def _bookmark_result(session_key: str, result: dict) -> str:
    if result.get("outcome") == "no_state":
        return f"/digest bookmark ({session_key}): no digest state to edit."
    return (
        f"/digest bookmark ({session_key}): "
        f"{result.get('previous')} -> {result.get('bookmark')}"
    )


def _history(args: list[str], *, history_sync: bool) -> str:
    yes = "--yes" in args
    tokens = [t for t in args if t != "--yes"]
    action = tokens[0].lower() if tokens else ""
    if action in ("", "matrix"):
        payload = digest_run.estimate_history()
        return digest_run.format_history_matrix(payload)
    if action == "status":
        info = digest_run.get_history_status()
        if not info:
            return "/digest history: no backfill recorded."
        return (
            f"/digest history status: {info.get('status') or 'unknown'} "
            f"preset={info.get('preset')} "
            f"batches={len(info.get('completed_batches') or [])} "
            f"days={len(info.get('completed_days') or [])}"
        )
    if action == "stop":
        result = digest_run.stop_history(yes=yes)
        if result.get("outcome") == "needs_confirm":
            return "Refusing to stop without confirmation. Run: /digest history stop --yes"
        return f"/digest history stop: {result.get('outcome')}"
    if action == "resume":
        result = digest_run.resume_history(yes=yes, sync=history_sync)
        if result.get("outcome") == "needs_confirm":
            return "Refusing to resume without confirmation. Run: /digest history resume --yes"
        if result.get("outcome") == "started":
            return "/digest history resume started in the background. Check with /digest history status"
        return f"/digest history resume: {result.get('outcome')}"
    preset = action
    plan = digest_run.plan_history(preset)
    if not yes:
        return digest_run.format_history_plan(plan)
    result = digest_run.request_history_run(preset, yes=True, sync=history_sync)
    if result.get("outcome") == "started":
        return (
            f"/digest history {preset} started in the background. "
            "Check with /digest history status"
        )
    if result.get("outcome") == "needs_confirm":
        return digest_run.format_history_plan(result)
    return f"/digest history {preset}: {result.get('outcome')}"


def _help() -> str:
    return (
        "/digest commands:\n"
        "  /digest --all                rewrite today from the full transcript\n"
        "  /digest --batch              digest today's messages after the bookmark\n"
        "  /digest YYYY-MM-DD           rewrite a past day, including wrap-up\n"
        "  /digest status               bookmark, undigested counts, in-flight\n"
        "  /digest bookmark show        current bookmark id\n"
        "  /digest bookmark set <id>    set bookmark to a message id\n"
        "  /digest bookmark rewind <n>  move bookmark back n ids\n"
        "  /digest bookmark reset --yes clear bookmark (re-digest all)\n"
        "  /digest history              estimate 1d / 7d / 30d / all\n"
        "  /digest history <preset>     one estimate; add --yes to run\n"
        "  /digest history status       backfill progress\n"
        "  /digest history resume --yes continue an interrupted run\n"
        "  /digest history stop --yes   halt between batches"
    )


def _no_session() -> str:
    return (
        "Could not resolve the active session. Run /digest from inside a "
        "Hermes chat session (gateway sets HERMES_SESSION_ID for the current turn)."
    )
