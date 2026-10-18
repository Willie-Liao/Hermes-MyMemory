"""Age-trim .digest-state.json session maps in retention sweep."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path


def _load_retention():
    plugins = Path(__file__).resolve().parents[1]
    if str(plugins) not in sys.path:
        sys.path.insert(0, str(plugins))
    path = Path(__file__).with_name("retention.py")
    spec = importlib.util.spec_from_file_location("memory_retention_digest_trim", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sid(d: date, suffix: str = "abcdef") -> str:
    return f"{d.strftime('%Y%m%d')}_120000_{suffix}"


def test_sweep_digest_state_drops_old_keeps_young_and_undated(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    today = date(2026, 7, 18)
    monkeypatch.setattr(ret, "hermes_local_today", lambda: today)

    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.parent.mkdir(parents=True)
    young = _sid(today)
    old = _sid(today - timedelta(days=8))
    undated = "nosession-key"
    state_path.write_text(
        json.dumps(
            {
                "sessions": {
                    young: {"session_id": young, "platform": "cli"},
                    old: {"session_id": old, "platform": "cli"},
                    undated: {"mode": "x"},
                },
                "span_watches": {
                    young: {},
                    old: {"mem-x": {"phase": "watching"}},
                },
                "not_a_map": "skip-me",
            }
        ),
        encoding="utf-8",
    )

    removed = ret._sweep_digest_state()
    assert removed >= 1

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert young in data["sessions"]
    assert undated in data["sessions"]
    assert old not in data["sessions"]
    assert "span_watches" not in data
    assert data["not_a_map"] == "skip-me"
    archive = tmp_path / "memories" / ".archive" / f"span-watch-{today.isoformat()}.json"
    snap = json.loads(archive.read_text(encoding="utf-8"))
    assert young in snap["maps"]["span_watches"]
    assert old in snap["maps"]["span_watches"]


def test_sweep_archives_then_strips_span_watch_keys(tmp_path, monkeypatch):
    """Span-watch maps leave digest-state only after a dated .archive snapshot."""
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    today = date(2026, 8, 26)
    monkeypatch.setattr(ret, "hermes_local_today", lambda: today)

    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "sessions": {"keep": {"session_id": "keep"}},
                "span_watches": {"s1": {"mem-a": {"confidence": "high"}}},
                "span_pending_writes": {},
                "span_ask_sessions": {},
                "span_validator_budget": {"s1": {"date": "2026-07-24", "count": 1}},
            }
        ),
        encoding="utf-8",
    )

    assert ret._sweep_digest_state() >= 0
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["sessions"]["keep"]["session_id"] == "keep"
    for key in ret.SPAN_WATCH_STATE_KEYS:
        assert key not in data
    archive = tmp_path / "memories" / ".archive" / "span-watch-2026-08-26.json"
    snap = json.loads(archive.read_text(encoding="utf-8"))
    assert snap["source"] == "memories/staging/.digest-state.json"
    assert "validate_weekly_spans" in snap["symbols"]
    assert snap["maps"]["span_watches"]["s1"]["mem-a"]["confidence"] == "high"
    assert ret._sweep_digest_state() == 0
    assert archive.is_file()


def test_sweep_archives_empty_span_maps_when_no_prior_snapshot(tmp_path, monkeypatch):
    """Retirement must still leave an auditable snapshot when span keys are already gone."""
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    today = date(2026, 8, 26)
    monkeypatch.setattr(ret, "hermes_local_today", lambda: today)

    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"sessions": {"keep": {}}}), encoding="utf-8")

    assert ret._sweep_digest_state() == 0
    archive = tmp_path / "memories" / ".archive" / "span-watch-2026-08-26.json"
    snap = json.loads(archive.read_text(encoding="utf-8"))
    assert snap["maps"]["span_watches"] == {}
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert "span_watches" not in data


def test_sweep_digest_state_keeps_old_key_still_in_state_db(tmp_path, monkeypatch):
    """Live WeChat sessions keep YYYYMMDD_ ids from the open day; age-trim must not drop their bookmark."""
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    today = date(2026, 8, 21)
    monkeypatch.setattr(ret, "hermes_local_today", lambda: today)

    live = _sid(date(2026, 8, 11), "fdef935a")
    dead = _sid(date(2026, 8, 11), "deadold")
    con = sqlite3.connect(tmp_path / "state.db")
    con.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, "
        "started_at REAL NOT NULL)"
    )
    con.execute(
        "INSERT INTO sessions (id, source, started_at) VALUES (?, 'weixin', 0.0)",
        (live,),
    )
    con.commit()
    con.close()

    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "sessions": {
                    live: {
                        "session_id": live,
                        "last_digest_message_id": 66185,
                    },
                    dead: {
                        "session_id": dead,
                        "last_digest_message_id": 12,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    removed = ret._sweep_digest_state()
    assert removed == 1
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert live in data["sessions"]
    assert data["sessions"][live]["last_digest_message_id"] == 66185
    assert dead not in data["sessions"]


def test_sweep_digest_state_missing_file_noop(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    assert ret._sweep_digest_state() == 0


def test_sweep_digest_state_corrupt_json_noop(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    path = tmp_path / "memories" / "staging" / ".digest-state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    assert ret._sweep_digest_state() == 0
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_run_retention_sweep_calls_digest_trim(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    called = {"n": 0}

    def _fake():
        called["n"] += 1
        return 0

    monkeypatch.setattr(ret, "_sweep_snapshots", lambda: None)
    monkeypatch.setattr(ret, "_sweep_staging_and_archive", lambda: None)
    monkeypatch.setattr(ret, "_sweep_digest_state", _fake)
    ret.run_retention_sweep("test")
    assert called["n"] == 1
