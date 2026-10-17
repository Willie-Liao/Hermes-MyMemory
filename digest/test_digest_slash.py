from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))


from conftest import load_plugin_module


def _load_slash():
    return load_plugin_module("slash.py", "memory_digest_slash_test")


def _stub_session(slash, monkeypatch):
    monkeypatch.setattr(slash, "_active_session", lambda: ("s1", "s1"))


def test_force_run(monkeypatch):
    slash = _load_slash()
    _stub_session(slash, monkeypatch)
    monkeypatch.setattr(
        slash.digest_run,
        "request_digest",
        lambda sk, **kw: {
            "outcome": "appended",
            "session_id": "s1",
            "user": 3,
            "assistant": 3,
            "usage": {"input_tokens": 11, "output_tokens": 4},
            "seconds": 2.5,
        },
    )
    out = slash.handle_digest("--batch")
    assert "staged new block" in out
    assert "3 user / 3 assistant" in out
    assert "input_tokens=11" in out
    assert "output_tokens=4" in out
    assert "seconds=" in out


def test_digest_slash_modes_do_not_wrap_up(monkeypatch):
    """--all and --batch stay on today; a past date is the only slash wrap-up."""
    slash = _load_slash()
    _stub_session(slash, monkeypatch)
    calls: list[dict] = []
    wrapups: list[str] = []

    def fake(sk, **kw):
        calls.append(kw)
        return {
            "outcome": "appended",
            "session_id": sk,
            "user": 1,
            "assistant": 0,
            "usage": {"input_tokens": 8, "output_tokens": 3},
            "seconds": 1.0,
            "path": "/tmp/2020-01-02.md",
        }

    monkeypatch.setattr(slash.digest_run, "request_digest", fake)
    monkeypatch.setattr(
        slash.digest, "run_day_wrapup", lambda path: wrapups.append(str(path))
    )
    bare = slash.handle_digest("")
    assert "Usage:" in bare
    assert calls == []
    assert wrapups == []
    slash.handle_digest("--all")
    assert calls[-1]["scope"] == "all"
    slash.handle_digest("--batch")
    assert calls[-1]["scope"] == "batch"
    assert wrapups == []
    mixed = slash.handle_digest("--all 2026-01-02")
    assert "Usage:" in mixed
    assert len(calls) == 2
    today = slash.digest.hermes_local_today().isoformat()
    rejected = slash.handle_digest(today)
    assert "past civil date only" in rejected
    assert len(calls) == 2
    past = slash.handle_digest("2020-01-02")
    assert calls[-1]["scope"] == "date"
    assert calls[-1]["date_str"] == "2020-01-02"
    assert "input_tokens=8" in past
    assert wrapups == ["/tmp/2020-01-02.md"]


def test_status(monkeypatch):
    slash = _load_slash()
    _stub_session(slash, monkeypatch)
    monkeypatch.setattr(
        slash.digest_run,
        "get_digest_status",
        lambda sk, sid: {
            "session_id": "s1",
            "bookmark": 42,
            "undigested_user": 1,
            "undigested_assistant": 2,
            "in_flight": False,
            "last_digest_at": None,
            "last_failure_at": None,
            "last_log": "some log",
            "has_state": True,
        },
    )
    out = slash.handle_digest("status")
    assert "message id 42" in out
    assert "1 user / 2 assistant" in out


def test_bookmark_set(monkeypatch):
    slash = _load_slash()
    _stub_session(slash, monkeypatch)
    monkeypatch.setattr(
        slash.digest_run,
        "set_bookmark",
        lambda sk, value: {"outcome": "updated", "previous": 10, "bookmark": value},
    )
    out = slash.handle_digest("bookmark set 5")
    assert "10 -> 5" in out


def test_bookmark_reset_requires_yes(monkeypatch):
    slash = _load_slash()
    _stub_session(slash, monkeypatch)
    out = slash.handle_digest("bookmark reset")
    assert "--yes" in out


def test_bookmark_reset_confirmed(monkeypatch):
    slash = _load_slash()
    _stub_session(slash, monkeypatch)
    monkeypatch.setattr(
        slash.digest_run,
        "reset_bookmark",
        lambda sk: {"outcome": "updated", "previous": 7, "bookmark": 0},
    )
    out = slash.handle_digest("bookmark reset --yes")
    assert "7 -> 0" in out


def test_force_run_ignores_session_flag(monkeypatch):
    slash = _load_slash()
    seen: list[str] = []

    def fake_resolve(raw: str = "") -> tuple[str, str]:
        seen.append(raw)
        return ("s1", "s1")

    monkeypatch.setattr(slash, "resolve_session", fake_resolve)
    monkeypatch.setattr(
        slash.digest_run,
        "request_digest",
        lambda sk, **kw: {"outcome": "empty", "session_id": sk},
    )
    slash.handle_digest("--batch")
    assert seen == [""]


def test_unknown_subcommand(monkeypatch):
    slash = _load_slash()
    _stub_session(slash, monkeypatch)
    out = slash.handle_digest("bogus")
    assert "Unknown /digest subcommand" in out


def test_history_matrix_and_confirm_gate(monkeypatch):
    slash = _load_slash()
    _stub_session(slash, monkeypatch)
    seen = {"run": 0}

    monkeypatch.setattr(
        slash.digest_run,
        "estimate_history",
        lambda: {
            "outcome": "ok",
            "plans": [
                {
                    "outcome": "ok",
                    "preset": "1d",
                    "message_count": 2,
                    "session_count": 1,
                    "batch_count": 1,
                    "day_count": 1,
                    "cutoff_iso": "2026-08-20T12:00:00+00:00",
                    "digest_tokens": {"low": 1, "typical": 2, "high": 3},
                    "consolidate_tokens": {"low": 4, "typical": 5, "high": 6},
                    "total_elapsed_ms": {"low": 1000, "typical": 2000, "high": 3000},
                    "calibration": {"disclaimer": "bands", "time_confidence": "low"},
                }
            ],
        },
    )
    monkeypatch.setattr(
        slash.digest_run,
        "plan_history",
        lambda preset, **k: {
            "outcome": "ok",
            "preset": preset,
            "cutoff_iso": "x",
            "message_count": 2,
            "session_count": 1,
            "batch_count": 1,
            "day_count": 1,
            "digest_tokens": {"low": 1, "typical": 2, "high": 3},
            "consolidate_tokens": {"low": 4, "typical": 5, "high": 6},
            "total_elapsed_ms": {"low": 1000, "typical": 2000, "high": 3000},
            "calibration": {"disclaimer": "bands", "time_confidence": "low"},
        },
    )

    def fake_run(*_a, **kw):
        seen["run"] += 1
        return {"outcome": "started"}

    monkeypatch.setattr(slash.digest_run, "request_history_run", fake_run)
    matrix = slash.handle_digest("history")
    assert "1d" in matrix
    assert seen["run"] == 0
    preview = slash.handle_digest("history 7d")
    assert "--yes" in preview or "Confirm" in preview
    assert seen["run"] == 0
    bg = slash.handle_digest("history 7d --yes")
    assert seen["run"] == 1
    assert "background" in bg
    cli = slash.handle_digest("history 7d --yes", history_sync=True)
    assert seen["run"] == 2


def test_history_help_lists_presets(monkeypatch):
    slash = _load_slash()
    out = slash.handle_digest("help")
    assert "history" in out
    assert "1d" in out


def test_async_slash_handler_sees_bound_session_then_clears():
    """A desktop /digest call binds the chat id; a later call must not keep it.

    The installer collects this file. The handler reads the context variable
    the same way resolve_session does, including when a loop is already running
    and the await moves to a helper thread.
    """
    import asyncio

    agent = Path(__file__).resolve().parents[3] / "hermes-agent"
    if str(agent) not in sys.path:
        sys.path.insert(0, str(agent))
    from gateway.session_context import (
        clear_session_vars,
        get_session_env,
        set_session_vars,
    )
    from hermes_cli.plugins import resolve_plugin_command_result

    async def read_id():
        return get_session_env("HERMES_SESSION_ID")

    tokens = set_session_vars(session_key="sess-key", session_id="sess-key")
    try:
        assert resolve_plugin_command_result(read_id()) == "sess-key"
    finally:
        clear_session_vars(tokens)
    assert get_session_env("HERMES_SESSION_ID") == ""

    tokens = set_session_vars(session_key="sess-key", session_id="sess-key")
    try:

        async def inside_loop():
            return resolve_plugin_command_result(read_id())

        assert asyncio.run(inside_loop()) == "sess-key"
    finally:
        clear_session_vars(tokens)
    assert get_session_env("HERMES_SESSION_ID") == ""
