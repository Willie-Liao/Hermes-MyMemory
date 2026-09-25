"""Public weekly actions shared by the ``/weekly`` slash command.

Thin orchestration over :mod:`weekly` internals (status, generate, review,
snooze, skip) so the slash handler never re-implements the worker, presentation,
or state-machine logic that the lifecycle hooks already own.
"""

from __future__ import annotations

import importlib.util
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

try:  # package import (normal plugin load)
    from . import weekly
except ImportError:  # pragma: no cover - direct pytest collection path
    _module_path = Path(__file__).with_name("weekly.py")
    _spec = importlib.util.spec_from_file_location("memory_weekly_core", _module_path)
    if _spec is None or _spec.loader is None:
        raise
    weekly = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(weekly)


def _purge_orphan_daily_blocks_before_generate() -> None:
    """Finish orphan retention before weekly generation (fail soft)."""
    try:
        plugins = Path(__file__).resolve().parent.parent.parent
        path = Path(__file__).resolve().parent.parent / "retention" / "retention.py"
        spec = importlib.util.spec_from_file_location(
            "memory_retention_orphan_weekly", path
        )
        if spec is None or spec.loader is None:
            return
        mod = importlib.util.module_from_spec(spec)
        plugins_str = str(plugins)
        import sys

        if plugins_str not in sys.path:
            sys.path.insert(0, plugins_str)
        spec.loader.exec_module(mod)
        n = int(mod.purge_orphan_daily_blocks() or 0)
        if n:
            weekly._log(f"orphan daily purge removed {n} block(s) before generate")
    except Exception as exc:
        weekly._log(f"orphan daily purge before generate failed: {exc}")


def run_hot_health(*, reason: str = "bridge") -> dict[str, Any]:
    """Refresh and persist LLM-backed hot-memory health suggestions."""
    try:
        from . import hot_health
    except ImportError:
        _module_path = Path(__file__).with_name("hot_health.py")
        _spec = importlib.util.spec_from_file_location(
            "memory_weekly_hot_health", _module_path
        )
        if _spec is None or _spec.loader is None:
            raise ImportError(f"could not load hot_health from {_module_path}")
        hot_health = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(hot_health)
    return hot_health.run_hot_health(reason=reason)


def hot_source_changed() -> dict[str, Any]:
    """Whether MEMORY/USER/HERMES bytes differ from last health source_hash."""
    try:
        from . import hot_health
    except ImportError:
        _module_path = Path(__file__).with_name("hot_health.py")
        _spec = importlib.util.spec_from_file_location(
            "memory_weekly_hot_health_changed", _module_path
        )
        if _spec is None or _spec.loader is None:
            raise ImportError(f"could not load hot_health from {_module_path}")
        hot_health = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(hot_health)
    return {"changed": bool(hot_health.hot_source_changed())}


def tighten_hot_entry(
    *,
    mode: str = "tighten",
    text: str = "",
    guidance: str = "",
    entry_type: str = "",
    source_text: str = "",
    peer_text: str = "",
    peer_entries: list[dict[str, Any]] | None = None,
    reason: str = "",
    actions: list[str] | None = None,
    source_ref: str = "",
    peer_ref: str = "",
    call_llm: Any = None,
    call_tools: Any = None,
) -> dict[str, Any]:
    """Rewrite or merge a hot entry via one-shot JSON polish, then render text."""
    import sys

    plugin_dir = str(Path(__file__).resolve().parent)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    try:
        from . import tighten_tools
    except ImportError:
        import tighten_tools  # type: ignore

    mode_norm = (mode or "tighten").strip().lower() or "tighten"
    current_slots: dict[str, Any] | None = None
    original_body: str | None = None
    action_list = [
        str(a).strip()
        for a in (actions or [])
        if isinstance(a, str) and str(a).strip()
    ]
    guide = (guidance or "").strip() or tighten_tools.DEFAULT_GUIDANCE

    if mode_norm == "merge":
        src = (source_text or "").strip()
        peers: list[tuple[str, str]] = []
        if isinstance(peer_entries, list):
            for raw in peer_entries:
                if not isinstance(raw, dict):
                    continue
                ref = str(raw.get("ref") or "").strip()
                body = str(raw.get("text") or "").strip()
                if ref and body:
                    peers.append((ref, body))
        if not peers:
            peer = (peer_text or "").strip()
            if peer:
                peers.append(((peer_ref or "peer").strip() or "peer", peer))
        if not src or not peers:
            raise ValueError(
                "source_text and peer_text or peer_entries are required for merge"
            )
        steps = (
            "\n".join(f"{i}. {a}" for i, a in enumerate(action_list, start=1))
            if action_list
            else "(none)"
        )
        source_label = (source_ref or "source").strip()
        lines = [
            (
                "You are merging multiple hot-memory entries. Combine them into ONE concise entry."
                if len(peers) > 1
                else "You are merging two hot-memory entries. Combine them into ONE concise entry."
            ),
            "Preserve names, paths, dates, and factual accuracy. Do not invent facts.",
            "Call submit_tighten_text with the merged entry in text.",
            "",
            "WHY MERGE (from hot-health worker):",
            (reason or "").strip(),
            "",
            "SUGGESTED STEPS (operator hints — follow when compatible with the texts):",
            steps,
            "",
            f"SOURCE — {source_label}:",
            src,
            "",
        ]
        for i, (label, body) in enumerate(peers, start=1):
            peer_label = label or f"peer {i}"
            heading = (
                f"PEER — {peer_label}:"
                if len(peers) == 1
                else f"PEER {i} — {peer_label}:"
            )
            lines.extend([heading, body, ""])
        hermes_touch = source_label.startswith("HERMES.md") or any(
            label.startswith("HERMES.md") for label, _ in peers
        )
        if hermes_touch:
            lines.extend(
                [
                    "HERMES.md FORMAT (required — cards re-split on lines that start with \"## \"):",
                    "- Keep exactly ONE top-level ## heading (the parent / SOURCE section title).",
                    "- Nest peer content under the parent using ### (and #### if needed).",
                    "- Never emit a second ## heading for the peer — that recreates a second card.",
                    "- Regenerate a clean heading hierarchy inside the parent; do not paste two sibling ## sections.",
                    "",
                ]
            )
        kind = "text"
        force_tool = tighten_tools.force_tool_for_kind(kind)
        prompt = "\n".join(lines)
    else:
        body = (text or "").strip()
        if not body:
            raise ValueError("text is required")
        original_body = body
        kind = tighten_tools.infer_tighten_kind(body, entry_type)
        force_tool = tighten_tools.force_tool_for_kind(kind)
        current_slots = (
            {"text": body}
            if kind == "text"
            else tighten_tools.parse_body_slots(kind, body)
        )
        slot_hint = {
            "event": "Call submit_tighten_event with beginning, course, outcome.",
            "fact": "Call submit_tighten_fact with kind (Factual|Narration) and content.",
            "procedure": "Call submit_tighten_procedure with obstacle and solution.",
            "decision": (
                "Call submit_tighten_decision with kind (Preference|Decision), "
                "subject, and ruling."
            ),
            "text": "Call submit_tighten_text with the revised entry in text.",
        }[kind]
        prompt = "\n".join(
            [
                "Rewrite CURRENT_JSON. Those keys are Phase-1 body slots "
                "(same shape as digest submit_event/fact/procedure/decision).",
                "Returning CURRENT_JSON unchanged is a failure.",
                "OPERATOR GUIDANCE is authoritative and highest priority.",
                "Apply every correction or form change it requests "
                "(wording, numbers, names, facts, cuts, restructuring).",
                "When guidance conflicts with CURRENT_JSON, follow the guidance.",
                "If guidance is only to make it concise, cut filler and shorten each value; "
                "keep names, paths, dates, and facts.",
                "Do not invent facts beyond the guidance and CURRENT_JSON.",
                slot_hint,
                "Return the same key names with rewritten values. "
                "Values are prose only — no Beginning:/Obstacle:/Decision: prefixes.",
                "Do not emit YAML. Use the tool call only.",
                "",
                "CURRENT_JSON:",
                tighten_tools.current_json_for_prompt(kind, body),
                "",
                "OPERATOR GUIDANCE:",
                guide,
            ]
        )

    def _default_tools(p: str) -> dict[str, Any]:
        mymemory = Path(__file__).resolve().parent.parent
        if str(mymemory) not in sys.path:
            sys.path.insert(0, str(mymemory))
        plugins_root = mymemory.parent
        plugins_root_str = str(plugins_root)
        if plugins_root_str not in sys.path:
            sys.path.insert(0, plugins_root_str)
        from worker_llm import run_worker_llm_oneshot

        return run_worker_llm_oneshot(
            p,
            plugin="memory-weekly",
            purpose="ui_tighten",
            force_tool_name=force_tool,
            tool_schema=tighten_tools.tool_schema_for_kind(kind),
        )

    if call_tools is not None:
        captured = call_tools(
            prompt,
            plugin="memory-weekly",
            purpose="ui_tighten",
            force_tool_name=force_tool,
        )
    elif call_llm is not None:
        captured = {
            "tool_name": force_tool,
            "tool_args": {"text": str(call_llm(prompt, plugin="memory-weekly", purpose="ui_tighten") or "")},
        }
        if kind != "text":
            raise ValueError("typed tighten requires a JSON tool call")
    else:
        captured = _default_tools(prompt)

    tool_name = str((captured or {}).get("tool_name") or "").strip()
    tool_args = (captured or {}).get("tool_args")
    if not isinstance(tool_args, dict):
        tool_args = {}
    extra_text = str((captured or {}).get("final_response") or "").strip()
    if not extra_text:
        extras: list[str] = []
        for msg in (captured or {}).get("messages") or []:
            if not isinstance(msg, dict):
                continue
            if str(msg.get("role") or "").strip() != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                extras.append(content.strip())
        extra_text = "\n".join(extras).strip()
    if tool_name and tool_name != force_tool:
        raise ValueError(f"expected {force_tool} tool call, got {tool_name}")
    if (captured or {}).get("failed") and not extra_text:
        raise ValueError("worker LLM call failed")
    if (captured or {}).get("failed"):
        raise ValueError(extra_text.strip().strip('"'))
    if kind == "text" and not str(tool_args.get("text") or extra_text or "").strip():
        raise ValueError("tighten text is empty")
    if kind in tighten_tools.WORKER_TYPES:
        required = {
            "event": ("beginning", "course", "outcome"),
            "fact": ("kind", "content"),
            "procedure": ("obstacle", "solution"),
            "decision": ("kind", "subject", "ruling"),
        }[kind]
        bag = tighten_tools.normalize_tighten_args(
            kind, tool_args, current=None, extra_text=extra_text
        )
        if not any(str(bag.get(key) or "").strip() for key in required if key != "kind"):
            keys = ",".join(sorted(str(k) for k in tool_args.keys())) or "none"
            raise ValueError(f"tighten {kind} returned empty slots (keys={keys})")
    merged = tighten_tools.normalize_tighten_args(
        kind, tool_args, current=current_slots, extra_text=extra_text
    )
    tightened = tighten_tools.render_tighten_args(kind, merged)
    if original_body is not None and tightened.strip() == original_body.strip():
        raise ValueError("tighten produced no change")
    return {"tightened": tightened, "kind": kind}


def get_or_refresh_chronicle(
    week_key: str | None = None, *, force: bool = False
) -> dict[str, Any]:
    """Load or regenerate the news-anchor Chronicle sidecar for a week."""
    try:
        from . import chronicle
    except ImportError:
        _module_path = Path(__file__).with_name("chronicle.py")
        _spec = importlib.util.spec_from_file_location(
            "memory_weekly_chronicle", _module_path
        )
        if _spec is None or _spec.loader is None:
            raise ImportError(f"could not load chronicle from {_module_path}")
        chronicle = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(chronicle)
    return chronicle.get_or_refresh_chronicle(week_key, force=force)


def load_weekly_json(week_key: str | None = None) -> dict[str, Any]:
    """Return the schema parsed from YYYY-Www.md; missing file stays 404-equivalent."""
    try:
        from . import weekly_json
    except ImportError:
        import weekly_json as weekly_json  # type: ignore
    parsed = weekly._parse_week_key(str(week_key or ""))
    if parsed is None:
        return {"outcome": "bad_week", "week": week_key or ""}
    target = weekly._weekly_path(*parsed)
    try:
        payload = weekly_json.load_sidecar(target)
    except FileNotFoundError:
        return {"outcome": "missing", "week": weekly._week_key(*parsed)}
    if not isinstance(payload, dict):
        return {"outcome": "missing", "week": weekly._week_key(*parsed)}
    return {
        "outcome": "ok",
        "week": weekly._week_key(*parsed),
        "payload": payload,
    }


def _refresh_hot_health_after_generate(reason: str) -> None:
    """No-op: hot health is scheduled only from UI rescan/auto-rescan, not generate."""
    return


def _refresh_chronicle_after_generate(week_key: str, reason: str) -> None:
    """Force-refresh Chronicle after MD write (must include Worker 2 Brief)."""
    try:
        get_or_refresh_chronicle(week_key, force=True)
    except Exception as exc:  # noqa: BLE001 - generation remains successful
        weekly._log(
            f"chronicle refresh failed after generate_week ({reason}) week={week_key}: {exc}"
        )

def weekly_status() -> dict[str, Any]:
    """Pending presentation weeks, snooze state, generation backlog."""
    state = weekly._load_state()
    presentation = weekly._presentation_state(state)
    pending_presentation = weekly._weeks_needing_presentation(state=state)
    backlog = [weekly._week_key(y, w) for y, w in weekly._weeks_needing_report()]

    return {
        "pending_presentation": pending_presentation,
        "active_week": str(presentation.get("active_week") or ""),
        "snooze_until": presentation.get("snooze_until"),
        "hot_promotion_until": presentation.get("hot_promotion_until"),
        "generation_backlog": backlog,
        "last_generated_week": state.get("last_generated_week"),
        "last_generated_at": state.get("last_generated_at"),
        "completed_weeks": list(presentation.get("completed_weeks") or []),
        "last_completed_at": presentation.get("last_completed_at"),
        "tidy_pending_week": str(presentation.get("tidy_pending_week") or ""),
        "tidy_completed_weeks": list(presentation.get("tidy_completed_weeks") or []),
    }


def list_weekly_review_status() -> dict[str, Any]:
    """Weekly files for ``/weekly show`` with ``pending`` | ``reviewed`` (filesystem)."""
    rows = weekly._weeks_status_rows()
    if not rows:
        return {"outcome": "empty", "weeks": []}
    return {"outcome": "listed", "weeks": rows}


def list_weekly_pending_approval() -> dict[str, Any]:
    """Backward-compatible alias for :func:`list_weekly_review_status`."""
    return list_weekly_review_status()


# Mid-week draft refresh reasons: blank week_key → current ISO week (not backlog).
_CURRENT_WEEK_GENERATE_REASONS = frozenset({"update", "rescan"})


def generate_week(
    week_key: str | None = None,
    *,
    reason: str = "slash",
    background: bool = False,
) -> dict[str, Any]:
    """Generate a weekly review file, or kick the existing background thread.

    Slash and UI Re-scan wait in-process (background=False). Overdue catch-up
    still kicks a daemon via process_overdue_week_marks.
    """
    _purge_orphan_daily_blocks_before_generate()
    if isinstance(background, str):
        background = background.strip().lower() in {"1", "true", "yes"}
    else:
        background = bool(background)

    if not week_key and reason in _CURRENT_WEEK_GENERATE_REASONS:
        year, week = weekly._current_iso_week()
        week_key = weekly._week_key(year, week)

    if not week_key:
        previous_generated_at = weekly._load_state().get("last_generated_at")
        weekly._run_weekly(reason)
        state = weekly._load_state()
        if state.get("last_generated_at") != previous_generated_at:
            _refresh_hot_health_after_generate(reason)
            last_week = state.get("last_generated_week")
            if last_week:
                _refresh_chronicle_after_generate(str(last_week), reason)
        return {
            "outcome": "backlog",
            "last_generated_week": state.get("last_generated_week"),
            "backlog_pending": state.get("backlog_pending", []),
        }

    parsed = weekly._parse_week_key(week_key)
    if parsed is None:
        return {"outcome": "bad_week", "week": week_key}

    year, week = parsed
    hermes_home = weekly._hermes_home()
    try:
        from memory_staging import (
            WEEK_STATUS_PENDING,
            week_blocks_backlog_regenerate,
            write_week_status,
        )
    except ImportError:  # pragma: no cover
        from plugins.memory_staging import (  # type: ignore
            WEEK_STATUS_PENDING,
            week_blocks_backlog_regenerate,
            write_week_status,
        )
    # Do not recreate when closed (week_status reviewed or legacy … reviewed.md).
    if week_blocks_backlog_regenerate(hermes_home, year, week):
        return {"outcome": "already_closed", "week": week_key}

    weekly._log(f"weekly generation waiting for lock ({reason}) week={week_key}")
    with weekly._run_lock:
        weekly._log(f"weekly generation lock acquired ({reason}) week={week_key}")
        files = weekly._usable_daily_files(weekly._daily_files_for_week(year, week))
        if not files:
            # Orphan open draft outlives purged digests → Brief/cites stay.
            # Unlink pending draft only (reviewed already returned above).
            target = weekly._weekly_path(year, week)
            draft_cleared = False
            if target.exists():
                try:
                    target.unlink()
                    draft_cleared = True
                    weekly._log(
                        f"weekly empty digests cleared orphan draft {week_key} "
                        f"path={target} reason={reason}"
                    )
                except OSError as exc:
                    weekly._log(
                        f"weekly empty digests draft unlink failed {week_key}: {exc}"
                    )
            state = weekly._load_state()
            presentation = weekly._presentation_state(state)
            fp_map = weekly._digest_fingerprint_map(presentation)
            if week_key in fp_map:
                del fp_map[week_key]
                weekly._save_state(state)
            return {
                "outcome": "no_daily",
                "week": week_key,
                "empty_digests": True,
                "draft_cleared": draft_cleared,
            }

        if background:
            state = weekly._load_state()
            mark = weekly.ensure_week_open_mark(state, week_key)
            already = bool(mark.get("generate_in_flight"))
            mark["generate_in_flight"] = True
            weekly._save_state(state)
            if not already:
                _kick_background_generate_week(week_key)
            return {
                "outcome": "started",
                "week": week_key,
                "generate_in_flight": True,
            }

        target = weekly._weekly_path(year, week)
        weekly._log(
            f"weekly generation started {week_key} sources={len(files)} reason={reason}"
        )
        content = weekly._generate_weekly_content(week_key, files, reason=reason)
        if content is None:
            weekly._log(f"weekly generation failed {week_key} reason={reason}")
            return {"outcome": "failed", "week": week_key}

        target.parent.mkdir(parents=True, exist_ok=True)
        payload = weekly._last_weekly_payload
        if payload is None:
            payload = weekly.WeeklyReviewPayload(days=(), week_key=week_key)
        payload = weekly._commit_weekly_outputs(target, content, payload, week_key)

        fingerprint = weekly._digest_fingerprint_for_files(files)
        state = weekly._load_state()
        state["last_generated_week"] = week_key
        state["last_generated_at"] = weekly._now().isoformat()
        state["last_reason"] = reason
        presentation = weekly._presentation_state(state)
        weekly._store_digest_fingerprint(presentation, week_key, fingerprint)
        weekly.ensure_week_open_mark(state, week_key)
        weekly._save_state(state)

    weekly._log(f"weekly generated {week_key} path={target} sources={len(files)} reason={reason}")
    _refresh_hot_health_after_generate(reason)
    _refresh_chronicle_after_generate(week_key, reason)
    result: dict[str, Any] = {
        "outcome": "generated",
        "week": week_key,
        "path": str(target),
        "sources": len(files),
        "fingerprint": fingerprint,
        "brief": "\n".join(
            (
                f"- {row.text} ({', '.join(row.weekdays)})"
                if row.weekdays
                else f"- {row.text}"
            )
            for row in payload.summary
        ),
    }
    if weekly._last_brief_error:
        result["brief_error"] = weekly._last_brief_error
    return result


# Thin alias for slash naming (`/weekly update`) without a parallel generator.
update_week = generate_week


def digest_staleness(week_key: str | None = None) -> dict[str, Any]:
    """Cheap daily-digest staleness check for UI Rescan prompts."""
    return weekly.digest_staleness(week_key)


def review_week(week_key: str | None = None) -> dict[str, Any]:
    """Open the hot-promotion window for a week and return its presentation text.

    With ``week_key`` (``YYYY-Www``) reviews that specific week — including the
    current in-progress week when its file exists. Without it, prefers the
    current week, then the oldest past week awaiting approval.
    """
    now = weekly._now()
    state = weekly._load_state()
    presentation = weekly._presentation_state(state)

    if week_key and weekly._parse_week_key(week_key) is None:
        return {"outcome": "bad_week", "week": week_key}

    resolved = weekly._resolve_manual_review_week(week_key, state=state)
    if resolved is None:
        if week_key:
            return {"outcome": "no_file", "week": week_key}
        generation_pending = weekly._weeks_needing_report()
        if generation_pending:
            return {
                "outcome": "generation_pending",
                "week": weekly._week_key(*generation_pending[0]),
            }
        return {"outcome": "nothing"}

    key, path = resolved
    presentation["active_week"] = key
    presentation["last_presented_at"] = now.isoformat()
    weekly._clear_snooze_tracking(presentation)
    presentation["hot_promotion_allowed"] = True
    presentation["hot_promotion_until"] = (
        now + timedelta(seconds=weekly.HOT_PROMOTION_SECONDS)
    ).isoformat()
    weekly._save_state(state)
    weekly._log(f"weekly review opened {key} via slash (hot promotion window open)")
    return {
        "outcome": "review",
        "week": key,
        "path": str(path),
        "context": weekly._build_presentation_context(key, path, force=True),
    }


def snooze_week(*, seconds: int | None = None, session_id: str = "") -> dict[str, Any]:
    """Snooze the active / next pending weekly presentation."""
    state = weekly._load_state()
    presentation = weekly._presentation_state(state)
    target = _target_week(presentation, state)
    if target is None:
        return {"outcome": "nothing"}

    until = weekly._record_presentation_snooze(
        presentation, target, session_id=session_id, seconds=seconds
    )
    weekly._save_state(state)
    weekly._log(
        f"weekly presentation snoozed {target} until {until} via slash "
        f"session={session_id}"
    )
    return {"outcome": "snoozed", "week": target, "snooze_until": until}


def skip_week() -> dict[str, Any]:
    """Mark the active / next pending weekly presentation as completed."""
    state = weekly._load_state()
    presentation = weekly._presentation_state(state)
    target = _target_week(presentation, state)
    if target is None:
        return {"outcome": "nothing"}

    weekly._finalize_week_close(presentation, target, state=state)
    weekly._save_state(state)
    weekly._log(f"weekly presentation completed {target} via slash")
    return {"outcome": "completed", "week": target}


def close_week(
    week_key: str | None = None,
    *,
    enforce_sunday: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Close a week in place (``week_status: reviewed`` on ``YYYY-Www.md``).

    Blank ``week_key`` → current ISO week. Slash and UI Close both use
    ``enforce_sunday=False`` (anytime). Pass ``enforce_sunday=True`` only when
    a caller still wants the Sunday gate. When already reviewed, returns
    ``already_closed`` so callers can offer reopen instead of silently
    re-closing.
    """
    from memory_staging import (
        WEEK_STATUS_REVIEWED,
        week_file_path,
        week_is_reviewed,
        write_week_status,
    )

    base_today = today or weekly.hermes_local_today()
    target = week_key
    if not target:
        year, week = weekly._current_iso_week(base_today)
        target = weekly._week_key(year, week)

    parsed = weekly._parse_week_key(target)
    if parsed is None:
        return {"outcome": "bad_week", "week": target}
    year, week = parsed

    if enforce_sunday and base_today.weekday() != 6:
        return {"outcome": "sunday_only", "week": target}

    hermes_home = weekly._hermes_home()
    canonical = week_file_path(hermes_home, year, week)
    draft = weekly._weekly_path(year, week)

    if week_is_reviewed(hermes_home, year, week):
        return {"outcome": "already_closed", "week": target}
    if not draft.exists():
        # After empty-digest draft purge (or never generated): Close should still
        # work when there is nothing to review — write a minimal reviewed stub.
        # If usable digests exist, keep no_draft so the operator runs update first.
        files = weekly._usable_daily_files(weekly._daily_files_for_week(year, week))
        if files:
            return {"outcome": "no_draft", "week": target}
        stub = (
            f"# Weekly Memory Review — {target}\n\n"
            "## Brief\n\n"
            "No current news for this week.\n"
        )
        write_week_status(
            canonical,
            WEEK_STATUS_REVIEWED,
            week_key_str=target,
            content=stub,
        )
        state = weekly._load_state()
        presentation = weekly._presentation_state(state)
        weekly._finalize_week_close(
            presentation, target, state=state, ask_pending=False
        )
        weekly._save_state(state)
        weekly._log(f"weekly closed empty {target} via close_week path={canonical}")
        return {
            "outcome": "closed",
            "week": target,
            "path": str(canonical),
            "empty_week": True,
        }

    state = weekly._load_state()
    presentation = weekly._presentation_state(state)
    weekly._finalize_week_close(presentation, target, state=state, ask_pending=False)
    weekly._save_state(state)
    weekly._log(f"weekly closed {target} via close_week")
    return {"outcome": "closed", "week": target, "path": str(canonical)}


def reopen_week(week_key: str | None = None) -> dict[str, Any]:
    """Reopen a closed weekly review: reverse tidy ledger and restore draft.

    Default ``week_key`` is the current ISO week. Does not edit hot MEMORY/USER.
    """
    try:
        from . import weekly_tidy
    except ImportError:
        import weekly_tidy  # type: ignore[no-redef]

    target = week_key
    if not target:
        year, week = weekly._current_iso_week()
        target = weekly._week_key(year, week)
    result = weekly_tidy.reopen_week(target)
    if result.get("outcome") == "reopened":
        state = weekly._load_state()
        # ensure alone does not clear ask_pending / ask_resolved / closed_at
        mark = weekly.ensure_week_open_mark(state, target)
        mark["ask_pending"] = False
        mark["ask_resolved"] = None
        mark["closed_at"] = None
        weekly._save_state(state)
    return result


def list_tidy_candidates(week_key: str | None = None) -> dict[str, Any]:
    try:
        from . import weekly_tidy
    except ImportError:
        import weekly_tidy  # type: ignore[no-redef]

    target = week_key or ""
    if not target or weekly._parse_week_key(target) is None:
        return {"outcome": "bad_week", "week": target or ""}
    # Approval Hub feed:
    # - Distill/Brief-cited type:event rows
    # - Four-part Cite map "event" quotes (day-header evidence)
    # - All week daily type:event blocks (uncited included)
    cited = weekly_tidy.filter_approval_hub_candidates(
        weekly_tidy.parse_brief_cite_candidates(target)
    )
    quoted = weekly_tidy.parse_four_part_cite_map_event_candidates(target)
    week_events = weekly_tidy.list_week_daily_event_candidates(target)
    candidates = weekly_tidy.merge_approval_hub_event_candidates(
        cited + quoted, week_events
    )
    return {"outcome": "listed", "week": target, "candidates": candidates}


def _target_week(presentation: dict[str, Any], state: dict[str, Any]) -> str | None:
    active = str(presentation.get("active_week") or "")
    if weekly._parse_week_key(active) is not None:
        return active
    pending = weekly._weeks_needing_presentation(state=state)
    return pending[0] if pending else None


def _clear_generate_in_flight(week_key: str) -> None:
    state = weekly._load_state()
    marks = weekly._week_open_marks(state)
    mark = marks.get(week_key)
    if isinstance(mark, dict):
        mark["generate_in_flight"] = False
        weekly._save_state(state)


def _kick_background_generate_week(week_key: str) -> None:
    """Daemon thread: generate_week(rescan), then clear generate_in_flight."""

    def _target() -> None:
        try:
            generate_week(week_key, reason="rescan")
        finally:
            try:
                _clear_generate_in_flight(week_key)
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                weekly._log(
                    f"weekly overdue generate cleanup failed {week_key}: {exc}"
                )

    threading.Thread(
        target=_target,
        name=f"weekly-gen-{week_key}",
        daemon=True,
    ).start()


def process_overdue_week_marks(
    *,
    today: date | None = None,
    kick_generate: bool = True,
) -> dict[str, Any]:
    """Close overdue open weeks, park empties, or kick background generate once.

    Silent catch-up for Sunday cron (and similar). Does not inject chat asks.

    Returns:
      {
        "closed_weeks": list[str],
        "ask_weeks": list[str],  # always empty (A/B ask removed)
        "generate_started": list[str],
        "skipped_empty": list[str],
      }
    """
    base_today = today or weekly.hermes_local_today()
    current = weekly._current_iso_week(base_today)
    state = weekly._load_state()
    marks = weekly._week_open_marks(state)

    closed_weeks: list[str] = []
    generate_started: list[str] = []
    skipped_empty: list[str] = []

    overdue_open: list[str] = []
    for week_key, mark in marks.items():
        if not isinstance(mark, dict) or mark.get("status") != "open":
            continue
        parsed = weekly._parse_week_key(str(week_key))
        if parsed is None or parsed >= current:
            continue
        overdue_open.append(str(week_key))

    for week_key in sorted(overdue_open):
        parsed = weekly._parse_week_key(week_key)
        if parsed is None:
            continue
        year, week = parsed

        # Prefer close_week first: closes draft, or already_closed when reviewed
        # exists without draft. Do not gate only on draft.exists().
        result = close_week(week_key, enforce_sunday=False, today=base_today)
        if result.get("outcome") == "closed" and result.get("empty_week"):
            weekly._log(f"weekly overdue parked empty (no_daily) {week_key}")
            skipped_empty.append(week_key)
            closed_weeks.append(week_key)
            continue
        if result.get("outcome") in ("closed", "already_closed"):
            state = weekly._load_state()
            weekly.mark_week_closed_in_state(state, week_key, ask_pending=False)
            weekly._save_state(state)
            closed_weeks.append(week_key)
            continue

        # no draft and no reviewed — park empty or kick generate once
        files = weekly._usable_daily_files(weekly._daily_files_for_week(year, week))
        if not files:
            state = weekly._load_state()
            weekly.mark_week_closed_in_state(state, week_key, ask_pending=False)
            weekly._save_state(state)
            weekly._log(f"weekly overdue parked empty (no_daily) {week_key}")
            skipped_empty.append(week_key)
            closed_weeks.append(week_key)
            continue

        state = weekly._load_state()
        mark = weekly._week_open_marks(state).get(week_key)
        if not isinstance(mark, dict):
            mark = weekly.ensure_week_open_mark(state, week_key)
        if mark.get("generate_in_flight"):
            continue
        if not kick_generate:
            continue
        mark["generate_in_flight"] = True
        weekly._save_state(state)
        _kick_background_generate_week(week_key)
        generate_started.append(week_key)

    return {
        "closed_weeks": closed_weeks,
        "ask_weeks": [],
        "generate_started": generate_started,
        "skipped_empty": skipped_empty,
    }
