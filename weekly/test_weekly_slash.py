from __future__ import annotations

import importlib.util
import sys
import threading
from datetime import date
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))


def _load_slash():
    path = Path(__file__).with_name("slash.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_slash_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_four_commands(text: str) -> None:
    assert "/weekly ui" in text
    assert "/weekly update" in text
    assert "/weekly close" in text
    assert "/weekly reopen" in text
    for banned in ("/weekly status", "/weekly generate", "/weekly review", "/weekly tidy", "/weekly show", "/weekly snooze", "/weekly skip"):
        assert banned not in text


def test_help_lists_four_commands():
    s = _load_slash()
    out = s.handle_weekly("help")
    _assert_four_commands(out)


def test_empty_args_lists_four_commands():
    s = _load_slash()
    out = s.handle_weekly("")
    _assert_four_commands(out)


def test_unknown_tidy_returns_unknown_and_help():
    s = _load_slash()
    out = s.handle_weekly("tidy status")
    assert "Unknown /weekly subcommand" in out
    assert "tidy" in out
    _assert_four_commands(out)


def test_unknown_review_and_generate():
    s = _load_slash()
    for sub in ("review", "generate 2026-W24", "show", "snooze", "skip", "status"):
        out = s.handle_weekly(sub)
        assert "Unknown /weekly subcommand" in out
        _assert_four_commands(out)


_DISTILL_BRIEF_FIXTURE = """# Weekly Memory Review — 2026-W24

## Distill

---
id: distill-1
type: event
entity: Launch
related: ["[1] mem-2026-06-15-abc"]
---
Distill body that must not appear in chat.

## Brief

Ship landed Monday; review focused on launch readiness and follow-ups.
"""


def test_update_pastes_brief_not_distill(tmp_path, monkeypatch):
    """On generated: chat reply is Brief without dig-in ask; Distill YAML stays out."""
    s = _load_slash()
    week_path = tmp_path / "2026-W24.md"
    week_path.write_text(_DISTILL_BRIEF_FIXTURE, encoding="utf-8")

    def fake_update(week_key=None, *, reason="slash"):
        return {
            "outcome": "generated",
            "week": week_key,
            "sources": 3,
            "path": str(week_path),
        }

    monkeypatch.setattr(s.weekly_actions, "update_week", fake_update)
    out = s.handle_weekly("update 2026-W24")

    assert "Ship landed Monday" in out
    assert "type: event" not in out
    assert "related:" not in out
    assert "Distill body" not in out
    assert "refer to [" not in out.lower()
    assert "2026-W24" in out  # short path/week footer ok


def test_update_sets_staging_unlocked(tmp_path, monkeypatch):
    """Slash /weekly update opts in — sets presentation staging_unlocked."""
    s = _load_slash()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "memories" / "staging").mkdir(parents=True)

    def fake_update(week_key=None, *, reason="slash"):
        return {
            "outcome": "generated",
            "week": week_key or "2026-W24",
            "sources": 1,
            "path": "/tmp/x.md",
            "brief": "Events\n- Done [1].",
        }

    monkeypatch.setattr(s.weekly_actions, "update_week", fake_update)
    s.handle_weekly("update 2026-W24")
    state = s.weekly_mod._load_state()
    presentation = s.weekly_mod._presentation_state(state)
    assert presentation.get("staging_unlocked") is True
    assert presentation.get("staging_session_id") == s.weekly_mod.SLASH_STAGING_SESSION


def test_update_does_not_arm_cite_dig_in(tmp_path, monkeypatch):
    """Slash /weekly update must not arm weekly_cite dig-in state."""
    s = _load_slash()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "memories" / "staging").mkdir(parents=True)

    cite_path = Path(__file__).with_name("weekly_cite.py")
    cite_spec = importlib.util.spec_from_file_location(
        "memory_weekly_cite_slash_update_arm", cite_path
    )
    assert cite_spec is not None and cite_spec.loader is not None
    cite = importlib.util.module_from_spec(cite_spec)
    cite_spec.loader.exec_module(cite)
    cite.clear_dig_in()

    def fake_update(week_key=None, *, reason="slash"):
        return {
            "outcome": "generated",
            "week": week_key or "2026-W24",
            "sources": 1,
            "path": "/tmp/x.md",
            "brief": "Events\n- Done [1].",
        }

    monkeypatch.setattr(s.weekly_actions, "update_week", fake_update)
    out = s.handle_weekly("update 2026-W24")
    assert cite.get_dig_in() is None


def test_update_strips_theme_hashes_for_chat(monkeypatch):
    """Slash update pastes processed Brief: plain theme titles, keep cites."""
    s = _load_slash()
    raw = (
        "### Events\n- Ship landed Monday [1].\n\n"
        "### Hypothesis\n- None.\n\n"
        "### Conflict\n- None.\n\n"
        "### Procedure\n- Follow up Tuesday.\n"
    )

    def fake_update(week_key=None, *, reason="slash"):
        return {
            "outcome": "generated",
            "week": week_key,
            "sources": 2,
            "path": "/tmp/x.md",
            "brief": raw,
        }

    monkeypatch.setattr(s.weekly_actions, "update_week", fake_update)
    out = s.handle_weekly("update 2026-W24")
    assert "###" not in out
    assert "Events" in out
    assert "[1]" in out


def test_update_uses_brief_field_when_present(monkeypatch):
    s = _load_slash()

    def fake_update(week_key=None, *, reason="slash"):
        return {
            "outcome": "generated",
            "week": week_key,
            "sources": 2,
            "path": "/tmp/x.md",
            "brief": "Brief from generate_week return field.",
        }

    monkeypatch.setattr(s.weekly_actions, "update_week", fake_update)
    out = s.handle_weekly("update 2026-W24")
    assert "Brief from generate_week return field." in out
    assert "type: event" not in out


def test_update_specific_week(monkeypatch):
    s = _load_slash()
    captured = {}

    def fake_update(week_key=None, *, reason="slash"):
        captured["week"] = week_key
        captured["reason"] = reason
        return {
            "outcome": "generated",
            "week": week_key,
            "sources": 3,
            "path": "/tmp/x.md",
            "brief": "Week 24 brief summary for chat.",
        }

    monkeypatch.setattr(s.weekly_actions, "update_week", fake_update)
    out = s.handle_weekly("update 2026-W24")
    assert captured["week"] == "2026-W24"
    assert captured["reason"] == "update"
    assert "2026-W24" in out
    assert "Week 24 brief summary for chat." in out


def test_update_default_current_iso(monkeypatch):
    s = _load_slash()
    captured = {}

    def fake_update(week_key=None, *, reason="slash"):
        captured["week"] = week_key
        captured["reason"] = reason
        return {
            "outcome": "generated",
            "week": "2026-W29",
            "sources": 1,
            "path": "/tmp/w.md",
            "brief": "Current week brief.",
        }

    monkeypatch.setattr(s.weekly_actions, "update_week", fake_update)
    out = s.handle_weekly("update")
    assert captured["week"] is None
    assert captured["reason"] == "update"
    assert "2026-W29" in out
    assert "Current week brief." in out


def test_update_bad_week(monkeypatch):
    s = _load_slash()
    monkeypatch.setattr(
        s.weekly_actions,
        "update_week",
        lambda week_key=None, *, reason="slash": {
            "outcome": "bad_week",
            "week": week_key,
        },
    )
    out = s.handle_weekly("update not-a-week")
    assert "not a valid" in out


def test_update_failed_outcome(monkeypatch):
    s = _load_slash()
    monkeypatch.setattr(
        s.weekly_actions,
        "update_week",
        lambda week_key=None, *, reason="slash": {
            "outcome": "failed",
            "week": week_key or "2026-W27",
        },
    )
    out = s.handle_weekly("update 2026-W27")
    assert "failed" in out
    assert "2026-W27" in out


def test_update_empty_digests(monkeypatch):
    s = _load_slash()
    monkeypatch.setattr(
        s.weekly_actions,
        "update_week",
        lambda week_key=None, *, reason="slash": {
            "outcome": "no_daily",
            "week": "2026-W29",
            "empty_digests": True,
        },
    )
    out = s.handle_weekly("update 2026-W29")
    assert "newsroom is quiet" in out.lower() or "no daily" in out.lower()


def test_close_anytime(monkeypatch):
    s = _load_slash()
    captured = {}

    def fake_close(week_key=None, *, enforce_sunday=False, today=None):
        captured["week"] = week_key
        captured["enforce_sunday"] = enforce_sunday
        return {
            "outcome": "closed",
            "week": week_key or "2026-W29",
            "path": "/tmp/2026-W29 reviewed.md",
        }

    monkeypatch.setattr(s.weekly_actions, "close_week", fake_close)
    out = s.handle_weekly("close 2026-W28")
    assert captured["week"] == "2026-W28"
    assert captured["enforce_sunday"] is False
    assert "closed" in out.lower()
    assert "2026-W28" in out


def test_close_default_current_iso(monkeypatch):
    s = _load_slash()
    captured = {}

    def fake_close(week_key=None, *, enforce_sunday=False, today=None):
        captured["week"] = week_key
        return {"outcome": "closed", "week": "2026-W29", "path": "/x"}

    monkeypatch.setattr(s.weekly_actions, "close_week", fake_close)
    out = s.handle_weekly("close")
    assert captured["week"] is None
    assert "2026-W29" in out


def test_close_already_closed_prompts_reopen(monkeypatch):
    s = _load_slash()
    monkeypatch.setattr(
        s.weekly_actions,
        "close_week",
        lambda week_key=None, *, enforce_sunday=False, today=None: {
            "outcome": "already_closed",
            "week": week_key or "2026-W29",
        },
    )
    out = s.handle_weekly("close 2026-W29")
    assert "already closed" in out.lower()
    assert "reopen" in out.lower()
    assert "2026-W29" in out


def test_close_no_draft(monkeypatch):
    s = _load_slash()
    monkeypatch.setattr(
        s.weekly_actions,
        "close_week",
        lambda week_key=None, *, enforce_sunday=False, today=None: {
            "outcome": "no_draft",
            "week": "2026-W29",
        },
    )
    out = s.handle_weekly("close 2026-W29")
    assert "no draft" in out.lower() or "update" in out.lower()


def test_reopen_week(monkeypatch):
    s = _load_slash()
    captured = {}

    def fake_reopen(week_key=None):
        captured["week"] = week_key
        return {
            "outcome": "reopened",
            "week": week_key,
            "restored_blocks": ["b1"],
            "path": "/tmp/2026-W28.md",
        }

    monkeypatch.setattr(s.weekly_actions, "reopen_week", fake_reopen)
    out = s.handle_weekly("reopen 2026-W28")
    assert captured["week"] == "2026-W28"
    assert "reopened" in out.lower()
    assert "2026-W28" in out


def test_reopen_default_current_iso(monkeypatch):
    s = _load_slash()
    captured = {}

    def fake_reopen(week_key=None):
        captured["week"] = week_key
        return {
            "outcome": "reopened",
            "week": "2026-W29",
            "restored_blocks": [],
            "path": "/tmp/x.md",
        }

    monkeypatch.setattr(s.weekly_actions, "reopen_week", fake_reopen)
    out = s.handle_weekly("reopen")
    assert captured["week"] is None
    assert "2026-W29" in out


def test_ui_returns_link_when_already_up(monkeypatch):
    s = _load_slash()
    monkeypatch.setattr(s, "_ui_probe", lambda url: True)
    monkeypatch.setattr(s, "_ensure_cloudflare_tunnel", lambda: (None, None))
    monkeypatch.delenv("WEEKLY_UI_URL", raising=False)
    out = s.handle_weekly("ui")
    assert "http://127.0.0.1:3000" in out
    assert "failed" not in out.lower()


def test_ui_uses_weekly_ui_url_env(monkeypatch):
    s = _load_slash()
    monkeypatch.setenv("WEEKLY_UI_URL", "http://example.test:4000")
    monkeypatch.setattr(s, "_ui_probe", lambda url: True)
    monkeypatch.setattr(s, "_ensure_cloudflare_tunnel", lambda: (None, None))
    out = s.handle_weekly("ui")
    assert "http://example.test:4000" in out
    assert "http://127.0.0.1:3000" in out
    assert "phone" in out.lower()


def test_ui_prefers_live_tunnel_url(monkeypatch):
    s = _load_slash()
    monkeypatch.setattr(s, "_ui_probe", lambda url: True)
    monkeypatch.setattr(
        s,
        "_ensure_cloudflare_tunnel",
        lambda: ("https://fresh-tunnel.trycloudflare.com", None),
    )
    out = s.handle_weekly("ui")
    assert "https://fresh-tunnel.trycloudflare.com" in out
    assert "http://127.0.0.1:3000" in out
    assert "phone" in out.lower()


def test_ui_probes_local_even_when_share_url_is_public(monkeypatch):
    s = _load_slash()
    probed = []

    def fake_probe(url):
        probed.append(url)
        return True

    monkeypatch.setenv("WEEKLY_UI_URL", "http://example.test:4000")
    monkeypatch.setattr(s, "_ui_probe", fake_probe)
    monkeypatch.setattr(s, "_ensure_cloudflare_tunnel", lambda: (None, None))
    out = s.handle_weekly("ui")
    assert probed == ["http://127.0.0.1:3000"]
    assert "http://example.test:4000" in out


def test_ui_starts_dev_server_when_down(monkeypatch):
    s = _load_slash()
    calls = {"probe": 0, "popen": 0}

    def fake_probe(url):
        calls["probe"] += 1
        return calls["probe"] > 1

    def fake_popen(*args, **kwargs):
        calls["popen"] += 1
        assert args[0][:3] == ["npm", "run", "dev"]
        assert "HERMES_HOME" in kwargs.get("env", {})
        return object()

    monkeypatch.delenv("WEEKLY_UI_URL", raising=False)
    monkeypatch.setattr(s, "_ui_probe", fake_probe)
    monkeypatch.setattr(s.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)
    monkeypatch.setattr(s, "_ensure_cloudflare_tunnel", lambda: (None, None))
    out = s.handle_weekly("ui")
    assert calls["popen"] == 1
    assert "http://127.0.0.1:3000" in out
    assert "failed" not in out.lower()


def test_ui_npm_install_when_node_modules_missing(tmp_path, monkeypatch):
    s = _load_slash()
    ui = tmp_path / "ui"
    ui.mkdir()
    cmds = []

    def fake_run(cmd, **kwargs):
        cmds.append(("run", list(cmd)))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_popen(cmd, **kwargs):
        cmds.append(("popen", list(cmd)))
        return object()

    monkeypatch.delenv("WEEKLY_UI_URL", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(s, "_ui_dir", lambda: ui)
    monkeypatch.setattr(s, "_ui_probe", lambda url: False)
    monkeypatch.setattr(s.subprocess, "run", fake_run)
    monkeypatch.setattr(s.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)
    monkeypatch.setattr(s, "_UI_START_WAIT_SECONDS", 0.01)
    out = s.handle_weekly("ui")
    assert ("run", ["npm", "install"]) in cmds
    assert ("popen", ["npm", "run", "dev"]) in cmds
    assert "fail" in out.lower()


def test_ui_skips_dev_when_npm_install_fails(tmp_path, monkeypatch):
    s = _load_slash()
    ui = tmp_path / "ui"
    ui.mkdir()
    popen = []

    def fake_run(cmd, **kwargs):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "no tsx"})()

    monkeypatch.delenv("WEEKLY_UI_URL", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(s, "_ui_dir", lambda: ui)
    monkeypatch.setattr(s, "_ui_probe", lambda url: False)
    monkeypatch.setattr(s.subprocess, "run", fake_run)
    monkeypatch.setattr(s.subprocess, "Popen", lambda *a, **k: popen.append(a) or object())
    out = s.handle_weekly("ui")
    assert popen == []
    assert "npm install failed" in out
    assert "no tsx" in out


def test_ui_fails_clearly_when_start_does_not_come_up(monkeypatch):
    s = _load_slash()
    monkeypatch.delenv("WEEKLY_UI_URL", raising=False)
    monkeypatch.setattr(s, "_ui_probe", lambda url: False)
    monkeypatch.setattr(s.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)
    monkeypatch.setattr(s, "_UI_START_WAIT_SECONDS", 0.01)
    out = s.handle_weekly("ui")
    assert "fail" in out.lower()
    assert "http://127.0.0.1:3000" in out


def test_tunnel_records_pid_on_spawn(tmp_path, monkeypatch):
    s = _load_slash()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("WEEKLY_UI_TUNNEL", "1")
    log_path = tmp_path / "cache" / "weekly-ui-tunnel.log"
    pid_path = tmp_path / "cache" / "weekly-ui-tunnel.pid"
    (tmp_path / "cache").mkdir(parents=True)

    class FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        assert "tunnel" in cmd
        log_path.write_text(
            "INF | https://owned-tunnel.trycloudflare.com\n",
            encoding="utf-8",
        )
        return FakeProc()

    monkeypatch.setattr(s.shutil, "which", lambda name: "/usr/bin/cloudflared")
    monkeypatch.setattr(s.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)
    monkeypatch.setattr(s, "_TUNNEL_START_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(s, "_pid_alive", lambda pid: pid == 4242)
    # Stale global cloudflared must not count as "our" tunnel.
    monkeypatch.setattr(
        s.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "9999\n"})(),
    )

    url, err = s._ensure_cloudflare_tunnel()
    assert err is None
    assert url == "https://owned-tunnel.trycloudflare.com"
    assert pid_path.read_text(encoding="utf-8").strip() == "4242"
    assert s._read_tunnel_pid() == 4242
    assert s._tunnel_process_running() is True


def test_tunnel_reuses_tracked_live_pid(tmp_path, monkeypatch):
    s = _load_slash()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("WEEKLY_UI_TUNNEL", "1")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    (cache / "weekly-ui-tunnel.pid").write_text("5151\n", encoding="utf-8")
    (cache / "weekly-ui-tunnel.log").write_text(
        "https://reuse-me.trycloudflare.com\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(s.shutil, "which", lambda name: "/usr/bin/cloudflared")
    monkeypatch.setattr(s, "_pid_alive", lambda pid: pid == 5151)
    calls = {"popen": 0}

    def boom(*a, **k):
        calls["popen"] += 1
        raise AssertionError("should reuse tracked tunnel")

    monkeypatch.setattr(s.subprocess, "Popen", boom)
    url, err = s._ensure_cloudflare_tunnel()
    assert err is None
    assert url == "https://reuse-me.trycloudflare.com"
    assert calls["popen"] == 0


def test_tunnel_replaces_stale_pid_record(tmp_path, monkeypatch):
    s = _load_slash()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("WEEKLY_UI_TUNNEL", "1")
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    pid_path = cache / "weekly-ui-tunnel.pid"
    pid_path.write_text("7777\n", encoding="utf-8")
    (cache / "weekly-ui-tunnel.log").write_text(
        "https://old.trycloudflare.com\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(s.shutil, "which", lambda name: "/usr/bin/cloudflared")
    monkeypatch.setattr(s, "_pid_alive", lambda pid: False)

    class FakeProc:
        pid = 8888

    def fake_popen(cmd, **kwargs):
        (cache / "weekly-ui-tunnel.log").write_text(
            "https://fresh.trycloudflare.com\n",
            encoding="utf-8",
        )
        return FakeProc()

    monkeypatch.setattr(s.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)
    monkeypatch.setattr(s, "_TUNNEL_START_WAIT_SECONDS", 0.01)
    url, err = s._ensure_cloudflare_tunnel()
    assert err is None
    assert url == "https://fresh.trycloudflare.com"
    assert pid_path.read_text(encoding="utf-8").strip() == "8888"


def test_stop_tracked_tunnel_kills_only_recorded_pid(tmp_path, monkeypatch):
    s = _load_slash()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cache = tmp_path / "cache"
    cache.mkdir(parents=True)
    (cache / "weekly-ui-tunnel.pid").write_text("6161\n", encoding="utf-8")
    killed = []

    def fake_kill(pid, sig=None):
        killed.append((pid, sig))

    monkeypatch.setattr(s.os, "kill", fake_kill)
    monkeypatch.setattr(s, "_pid_alive", lambda pid: pid == 6161)
    assert s._stop_tracked_tunnel() is True
    assert killed == [(6161, s.signal.SIGTERM)]
    assert not (cache / "weekly-ui-tunnel.pid").exists()
    # Unrelated PID must never be targeted.
    assert all(pid == 6161 for pid, _ in killed)


def _load_weekly_module(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    module_path = Path(__file__).with_name("weekly.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_slash_gen_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event_tool_args(
    *,
    evt_id: str = "evt-a",
    day: str = "2026-06-30",
    mem_id: str = "mem-2026-06-30-a",
) -> dict:
    return {
        "id": evt_id,
        "entity": "Example",
        "predicate": "example_delivered",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
        "valid_from": day,
        "valid_to": day,
        "confidence": "high",
        "status": "candidate",
        "sources": ["session s1"],
        "related": [mem_id],
        "beginning": f"Fact recorded on {day}",
        "course": f"Fact recorded progress on {day}",
        "outcome": f"Fact recorded on {day}",
    }


def _incomplete_event_tool_args(day: str = "2026-06-30") -> dict:
    """Missing required event slots — forces validation retry."""
    return {
        "events": [
            {
                "entity": "Example",
                "predicate": "example_delivered",
                "participants": [],
                "valid_from": day,
                "valid_to": day,
                "confidence": "high",
                "sources": ["session s1"],
                "related": [],
                "beginning": "x",
                "course": "y",
                "outcome": "z",
            }
        ]
    }


def _hypothesis_tool_args(event_id: str = "evt-a") -> dict:
    return {
        "hypotheses": [
            {
                "id": "hyp-a",
                "entity": "Example",
                "valid_from": "2026-06-30",
                "sources": ["session s1"],
                "related": [event_id],
                "confidence": "medium",
                "status": "candidate",
                "statement": "Still open.",
            }
        ]
    }


def _purpose_keyed_llm(weekly, monkeypatch, handler) -> list[dict[str, object]]:
    """Patch weekly LLM helpers with purpose-aware handlers; return call log."""
    calls: list[dict[str, object]] = []
    lock = threading.Lock()

    def fake(prompt: str, *, purpose: str = "weekly_llm") -> str:
        with lock:
            calls.append({"purpose": purpose, "prompt": prompt, "kind": "text"})
        out = handler(prompt, purpose)
        return out if isinstance(out, str) else ""

    def fake_tools(
        prompt: str,
        *,
        purpose: str = "weekly_llm",
        force_tool_name: str,
    ) -> dict:
        with lock:
            calls.append(
                {
                    "purpose": purpose,
                    "prompt": prompt,
                    "kind": "tools",
                    "force_tool_name": force_tool_name,
                }
            )
        try:
            out = handler(prompt, purpose, force_tool_name=force_tool_name)
        except TypeError:
            out = handler(prompt, purpose)
        if isinstance(out, dict) and "tool_name" in out:
            return {
                "final_response": "",
                "tool_name": out["tool_name"],
                "tool_args": out.get("tool_args") or {},
                "tool_calls": [(out["tool_name"], out.get("tool_args") or {})],
                "messages": [],
                "failed": False,
            }
        if isinstance(out, dict) and "tool_args" in out:
            return {
                "final_response": "",
                "tool_name": force_tool_name,
                "tool_args": out["tool_args"],
                "tool_calls": [(force_tool_name, out["tool_args"])],
                "messages": [],
                "failed": False,
            }
        return {
            "final_response": str(out or ""),
            "tool_name": None,
            "tool_args": None,
            "tool_calls": [],
            "messages": [],
            "failed": True,
        }

    monkeypatch.setattr(weekly, "_call_weekly_llm", fake)
    monkeypatch.setattr(weekly, "_call_weekly_llm_tools", fake_tools)
    return calls


def test_build_prompt_retry_includes_validator_errors(tmp_path, monkeypatch):
    weekly = _load_weekly_module(tmp_path, monkeypatch)
    prompt = weekly._build_prompt(
        "2026-W27",
        "daily bundle",
        attempt=2,
        errors=("missing ## Distill section",),
        previous_output="bad output",
    )
    assert "VALIDATION FAILED (attempt 2 of 3)" in prompt
    assert "missing ## Distill section" in prompt
    assert "bad output" in prompt
    assert "## Distill" in prompt


def test_generate_weekly_content_bounded_fallback_on_llm_error(
    tmp_path, monkeypatch
):
    """Total LLM failure still yields Distill via bounded day fallback (not None)."""
    weekly = _load_weekly_module(tmp_path, monkeypatch)
    logs: list[str] = []
    monkeypatch.setattr(weekly, "_log", logs.append)

    def handler(_prompt: str, purpose: str) -> str:
        if purpose.startswith("worker1_") or purpose == "worker2_brief":
            return "API call failed after 3 retries: Connection error."
        return ""

    _purpose_keyed_llm(weekly, monkeypatch, handler)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-06-30.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "---\nid: mem-1\ntype: fact\nentity: X\nconfidence: high\n"
        "status: candidate\nsources: [session s1]\n---\nFact.\n",
        encoding="utf-8",
    )

    result = weekly._generate_weekly_content("2026-W27", [daily], reason="test")
    assert result is not None
    assert "cross-day-thread" in result
    assert "## Distill" not in result
    assert "2026-06-30" in result
    assert any("fallback" in line.casefold() for line in logs)


def test_generate_weekly_content_retries_then_succeeds(tmp_path, monkeypatch):
    """Per-event-worker retries: incomplete submit twice, then a valid patch."""
    weekly = _load_weekly_module(tmp_path, monkeypatch)
    event_attempts: dict[str, int] = {}
    lock = threading.Lock()

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose.startswith("worker1_event"):
            with lock:
                n = event_attempts.get(purpose, 0) + 1
                event_attempts[purpose] = n
            if "2026-06-30" not in prompt and n == 1:
                return {
                    "tool_name": "submit_weekly_event",
                    "tool_args": {"events": []},
                }
            if n < 3:
                return {
                    "tool_name": force_tool_name or "submit_weekly_event",
                    "tool_args": _incomplete_event_tool_args(),
                }
            return {
                "tool_name": force_tool_name or "patch_weekly_event",
                "tool_args": {
                    "events": [
                        _event_tool_args(
                            evt_id="evt-a",
                            day="2026-06-30",
                            mem_id="mem-2026-06-30-a",
                        )
                    ]
                },
            }
        if purpose == "worker1_thread":
            return {
                "tool_name": force_tool_name or "submit_weekly_thread",
                "tool_args": {"cross-day-thread": []},
            }
        return ""

    calls = _purpose_keyed_llm(weekly, monkeypatch, handler)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-06-30.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "---\nid: mem-2026-06-30-a\ntype: fact\nentity: X\nconfidence: high\n"
        "status: candidate\nsources: [session s1]\n---\n"
        "Fact recorded on 2026-06-30.\n",
        encoding="utf-8",
    )

    result = weekly._generate_weekly_content("2026-W27", [daily], reason="test")
    assert result is not None
    assert "cross-day-thread" in result
    assert "## Distill" not in result
    assert "2026-06-30" in result
    active = [
        c
        for c in calls
        if str(c["purpose"]).startswith("worker1_event")
        and c.get("kind") == "tools"
    ]
    assert len(active) == 3
    assert active[1].get("force_tool_name") == "patch_weekly_event"
    assert "VALIDATION FAILED" in str(active[1]["prompt"])


def test_generate_weekly_content_retries_shape_errors(tmp_path, monkeypatch):
    """Event worker recovers after a missing-field shape error; hypothesis attaches."""
    weekly = _load_weekly_module(tmp_path, monkeypatch)
    event_attempts: dict[str, int] = {}
    lock = threading.Lock()

    def handler(prompt: str, purpose: str, force_tool_name: str = "") -> object:
        if purpose.startswith("worker1_event"):
            with lock:
                n = event_attempts.get(purpose, 0) + 1
                event_attempts[purpose] = n
            if n == 1:
                return {
                    "tool_name": force_tool_name or "submit_weekly_event",
                    "tool_args": _incomplete_event_tool_args(),
                }
            return {
                "tool_name": force_tool_name or "patch_weekly_event",
                "tool_args": {
                    "events": [
                        _event_tool_args(
                            evt_id="evt-a",
                            day="2026-06-30",
                            mem_id="mem-2026-06-30-a",
                        )
                    ]
                },
            }
        if purpose == "worker1_thread":
            return {
                "tool_name": force_tool_name or "submit_weekly_thread",
                "tool_args": {"cross-day-thread": []},
            }
        return ""

    calls = _purpose_keyed_llm(weekly, monkeypatch, handler)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-06-30.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "---\nid: mem-2026-06-30-a\ntype: fact\nentity: X\nconfidence: high\n"
        "status: candidate\nsources: [session s1]\n---\n"
        "Fact recorded on 2026-06-30.\n",
        encoding="utf-8",
    )

    result = weekly._generate_weekly_content("2026-W27", [daily], reason="test")

    assert result is not None
    assert "cross-day-thread" in result
    assert "## Distill" not in result
    assert "type: hypothesis" not in result
    assert any(c["purpose"] == "worker1_thread" for c in calls)
    active = [
        c
        for c in calls
        if str(c["purpose"]).startswith("worker1_event")
        and c.get("kind") == "tools"
    ]
    assert len(active) == 2
    assert "VALIDATION FAILED" in str(active[1]["prompt"])
    assert "related" in str(active[1]["prompt"]).casefold()


def test_weeks_needing_report_never_requeues_reviewed(
    tmp_path, monkeypatch
):
    """Any ``… reviewed.md`` (even invalid) must never enter backlog regenerate."""
    weekly = _load_weekly_module(tmp_path, monkeypatch)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-06-30.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("---\nid: mem-1\ntype: fact\nentity: X\nconfidence: high\nstatus: candidate\nsources: [session s1]\n---\nFact.\n", encoding="utf-8")
    reviewed = tmp_path / "memories" / "staging" / "weekly" / "2026-W27 reviewed.md"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text("API call failed after 3 retries: Connection error.\n", encoding="utf-8")

    pending = weekly._weeks_needing_report(date(2026, 7, 7))
    assert (2026, 27) not in pending


def test_weeks_needing_report_skips_when_draft_beside_reviewed(tmp_path, monkeypatch):
    weekly = _load_weekly_module(tmp_path, monkeypatch)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-06-16.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "---\nid: mem-1\ntype: fact\nentity: X\nconfidence: high\nstatus: candidate\n"
        "sources: [session s1]\n---\nFact.\n",
        encoding="utf-8",
    )
    weekly_dir = tmp_path / "memories" / "staging" / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    (weekly_dir / "2026-W25 reviewed.md").write_text("# closed\n", encoding="utf-8")
    (weekly_dir / "2026-W25.md").write_text("broken draft\n", encoding="utf-8")

    pending = weekly._weeks_needing_report(date(2026, 6, 22))
    assert (2026, 25) not in pending
