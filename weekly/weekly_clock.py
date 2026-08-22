"""Sunday 16:00 generate+WeChat link and 23:55 close without Hermes cron."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_section_dir = str(Path(__file__).resolve().parent)
if _section_dir not in sys.path:
    sys.path.insert(0, _section_dir)

try:
    from . import slash, weekly, weekly_actions
except ImportError:  # pragma: no cover - pytest file load
    def _load(name: str, alias: str):
        path = Path(__file__).with_name(f"{name}.py")
        spec = importlib.util.spec_from_file_location(alias, path)
        if spec is None or spec.loader is None:
            raise ImportError(name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    weekly = _load("weekly", "memory_weekly_for_clock")
    weekly_actions = _load("weekly_actions", "memory_weekly_actions_for_clock")
    slash = _load("slash", "memory_weekly_slash_for_clock")

generate_week = weekly_actions.generate_week
close_week = weekly_actions.close_week
_ui = slash._ui

logger = logging.getLogger(__name__)


def _week_key_for(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _previous_week_key(day: date) -> str:
    """ISO week that ended before ``day`` (Monday catch-up must not target the new week)."""
    return _week_key_for(day - timedelta(days=7))


def _gateway_runner_ref():
    """Live gateway process only — cron stdout is not a send path."""
    try:
        from gateway.run import _gateway_runner_ref as _ref

        return _ref()
    except Exception:
        return None


def _weixin_chat_id() -> str | None:
    """Env wins so tests can skip send without a config.yaml WeChat origin."""
    env = (os.environ.get("WEEKLY_BRIEF_WEIXIN_TO") or "").strip()
    if env:
        return env
    try:
        import yaml

        path = weekly._hermes_home() / "config.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = (data.get("plugins") or {}).get("entries") or {}
        mine = entries.get("MyMemory") or {}
        raw = mine.get("weekly_brief_weixin")
        if not raw:
            raw = (mine.get("weekly") or {}).get("weekly_brief_weixin")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception:
        pass
    return None


def _lookup_weixin_adapter(runner: Any) -> Any:
    adapters = getattr(runner, "adapters", None) or {}
    try:
        from gateway.config import Platform

        adapter = adapters.get(Platform.WEIXIN)
        if adapter is not None:
            return adapter
    except Exception:
        pass
    return adapters.get("weixin")


def send_weekly_brief_weixin(text: str) -> dict[str, Any]:
    """Push the /weekly ui tunnel text through the live Weixin adapter.

    Prefetch cannot deliver at 16:00; this is the same adapter.send cron used
    after job stdout. Missing chat id or adapter skips send so generate still
    persists.
    """
    chat_id = _weixin_chat_id()
    if not chat_id:
        return {"outcome": "skipped", "reason": "no_chat_id"}
    runner = _gateway_runner_ref()
    if runner is None:
        return {"outcome": "skipped", "reason": "no_runner"}
    adapter = _lookup_weixin_adapter(runner)
    if adapter is None:
        return {"outcome": "skipped", "reason": "no_adapter"}

    async def _send():
        return await adapter.send(chat_id=chat_id, content=text)

    try:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running.is_running():
            result = asyncio.run_coroutine_threadsafe(_send(), running).result(
                timeout=60
            )
        else:
            result = asyncio.run(_send())
    except Exception as exc:
        logger.warning("weekly clock WeChat send failed: %s", exc)
        weekly._log(f"weekly clock WeChat send failed: {exc}")
        return {"outcome": "error", "error": str(exc)}

    success = getattr(result, "success", None)
    if success is False:
        err = getattr(result, "error", "adapter send failed")
        return {"outcome": "error", "error": str(err)}
    if isinstance(result, dict) and result.get("error"):
        return {"outcome": "error", "error": str(result.get("error"))}
    return {"outcome": "sent"}


def _compose_ready_message(week_key: str) -> str:
    return f"Weekly review {week_key} is ready.\n{_ui()}"


def maybe_run(local: datetime, leftover_ran: bool = False) -> dict[str, Any]:
    """Sunday 16:00 generate+link, 23:55 close after leftover; Monday previous week only.

    Catch-up keys live in ``.weekly-state.json`` so a stale cron ``next_run_at``
    is not required and cannot close the new Monday week.
    """
    payload: dict[str, Any] = {
        "outcome": "idle",
        "generate": None,
        "close": None,
        "send": None,
    }
    day = local.date()
    current = _week_key_for(day)
    state = weekly._load_state()
    last_gen = state.get("last_sunday_generate_week")
    last_close = state.get("last_sunday_close_week")

    if local.weekday() == 6:
        if (local.hour, local.minute) < (16, 0):
            return payload
        target = current
        if last_gen != target:
            generate_week(target, reason="update")
            state = weekly._load_state()
            state["last_sunday_generate_week"] = target
            weekly._save_state(state)
            payload["generate"] = target
            message = _compose_ready_message(target)
            payload["send"] = send_weekly_brief_weixin(message)
        if leftover_ran and last_close != target:
            close_week(target, enforce_sunday=False, today=day)
            state = weekly._load_state()
            state["last_sunday_close_week"] = target
            weekly._save_state(state)
            payload["close"] = target
    else:
        target = _previous_week_key(day)
        if last_gen != target:
            generate_week(target, reason="update")
            state = weekly._load_state()
            state["last_sunday_generate_week"] = target
            weekly._save_state(state)
            payload["generate"] = target
            message = _compose_ready_message(target)
            payload["send"] = send_weekly_brief_weixin(message)
        if last_close != target:
            close_week(target, enforce_sunday=False, today=day)
            state = weekly._load_state()
            state["last_sunday_close_week"] = target
            weekly._save_state(state)
            payload["close"] = target

    if payload["generate"] and payload["close"]:
        payload["outcome"] = "generated_and_closed"
    elif payload["generate"]:
        payload["outcome"] = "generated"
    elif payload["close"]:
        payload["outcome"] = "closed"
    return payload
