"""Age-trim .digest-state.json session maps in retention sweep."""

from __future__ import annotations

import importlib.util
import json
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
    assert removed >= 2

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert young in data["sessions"]
    assert undated in data["sessions"]
    assert old not in data["sessions"]
    assert young in data["span_watches"]
    assert old not in data["span_watches"]
    assert data["not_a_map"] == "skip-me"


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
