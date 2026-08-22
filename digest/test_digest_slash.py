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
        lambda sk, **kw: {"outcome": "appended", "session_id": "s1", "user": 3, "assistant": 3},
    )
    out = slash.handle_digest("")
    assert "staged new block" in out
    assert "3 user / 3 assistant" in out


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
    slash.handle_digest("--session other-session")
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
