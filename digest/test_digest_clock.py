from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_plugin_dir = Path(__file__).resolve().parent
_plugins_root = _plugin_dir.parent
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from conftest import load_plugin_module

from digest_clock import (
    PHASE2_BLOCK_GATE,
    digest_clock_tz,
    next_deadline,
    parse_aware,
    should_run_nightly_leftover,
    should_run_phase2_tick,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


def _load_digest():
    return load_plugin_module("digest.py", "memory_digest_clock_test")


def test_digest_clock_tz_falls_back_on_invalid_name():
    tz = digest_clock_tz(tz_name="Not/AZone")
    assert str(tz) in {"Asia/Shanghai", "Etc/UTC"}


def test_digest_clock_tz_accepts_new_york_name():
    assert digest_clock_tz(tz_name="America/New_York") == NEW_YORK


def test_next_deadline_shanghai_morning_to_noon():
    now = datetime(2026, 8, 16, 10, 0, tzinfo=SHANGHAI)
    kind, when = next_deadline(now, SHANGHAI)
    assert kind == "tick"
    assert when == datetime(2026, 8, 16, 12, 0, tzinfo=SHANGHAI)


def test_next_deadline_evening_to_nightly():
    now = datetime(2026, 8, 16, 21, 0, tzinfo=SHANGHAI)
    kind, when = next_deadline(now, SHANGHAI)
    assert kind == "nightly"
    assert when == datetime(2026, 8, 16, 23, 55, tzinfo=SHANGHAI)


def test_next_deadline_after_nightly_is_next_morning():
    now = datetime(2026, 8, 16, 23, 55, 1, tzinfo=SHANGHAI)
    kind, when = next_deadline(now, SHANGHAI)
    assert kind == "tick"
    assert when == datetime(2026, 8, 17, 8, 0, tzinfo=SHANGHAI)


def test_next_deadline_new_york_same_civil_grid():
    now = datetime(2026, 8, 16, 10, 0, tzinfo=NEW_YORK)
    _kind, when = next_deadline(now, NEW_YORK)
    assert when == datetime(2026, 8, 16, 12, 0, tzinfo=NEW_YORK)


def test_phase2_tick_requires_more_than_twenty_five_cards():
    now = datetime(2026, 8, 16, 12, 0, tzinfo=SHANGHAI)
    assert should_run_phase2_tick(25, None, now) is False
    assert should_run_phase2_tick(26, None, now) is True


def test_phase2_tick_cooldown_skips_recent_merge():
    now = datetime(2026, 8, 16, 16, 0, tzinfo=SHANGHAI)
    last = now - timedelta(minutes=5)
    assert should_run_phase2_tick(26, last, now) is False
    last = now - timedelta(minutes=21)
    assert should_run_phase2_tick(26, last, now) is True


def test_nightly_leftover_only_after_2355_once_per_date():
    before = datetime(2026, 8, 16, 23, 54, tzinfo=SHANGHAI)
    assert should_run_nightly_leftover(None, before, SHANGHAI) is False
    at = datetime(2026, 8, 16, 23, 55, tzinfo=SHANGHAI)
    assert should_run_nightly_leftover(None, at, SHANGHAI) is True
    assert should_run_nightly_leftover("2026-08-16", at, SHANGHAI) is False
    catch = datetime(2026, 8, 16, 23, 56, tzinfo=SHANGHAI)
    assert should_run_nightly_leftover("2026-08-15", catch, SHANGHAI) is True
    morning = datetime(2026, 8, 17, 8, 0, tzinfo=SHANGHAI)
    assert should_run_nightly_leftover("2026-08-16", morning, SHANGHAI) is False


def test_clock_phase2_grid_hours_over_gate(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    path = daily_dir / "2026-08-16.md"
    path.write_text(_card_yaml(26), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-16")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    for hour, minute in ((8, 0), (16, 0), (20, 0)):
        phase2: list[str] = []
        monkeypatch.setattr(
            digest,
            "run_manual_phase2",
            lambda daily_path, date_str="", _p=phase2: _p.append(str(daily_path))
            or {"outcome": "rewritten"},
        )
        with digest._digest_lock:
            state = digest._load_state()
            state.pop("last_phase2_at", None)
            state.pop("next_clock_at", None)
            state["phase2_in_flight"] = False
            digest._save_state(state)
        now = datetime(2026, 8, 16, hour, minute, tzinfo=SHANGHAI)
        digest.maybe_run_digest_clock(now=now, sync=True)
        assert phase2 == [str(path)], (hour, minute)


def test_parse_aware_iso_offset():
    parsed = parse_aware("2026-08-16T15:00:00+08:00", SHANGHAI)
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=8)


def _card_yaml(n: int) -> str:
    parts: list[str] = []
    for i in range(n):
        parts.append(
            f"---\nid: mem-clock-{i}\ntype: fact\nconfidence: high\n"
            f"status: candidate\nsources: []\n---\nFactual: card {i}\n"
        )
    return "\n".join(parts)


def test_clock_phase2_at_noon_only_over_gate(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    path = daily_dir / "2026-08-16.md"
    path.write_text(_card_yaml(25), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-16")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    phase2: list[str] = []
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda daily_path, date_str="": phase2.append(str(daily_path))
        or {"outcome": "rewritten", "path": str(daily_path), "date": date_str, "operations": 0},
    )
    now = datetime(2026, 8, 16, 12, 0, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert phase2 == []


def test_clock_phase2_at_noon_runs_over_gate(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    path = daily_dir / "2026-08-16.md"
    path.write_text(_card_yaml(26), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-16")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    phase2: list[str] = []
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda daily_path, date_str="": phase2.append(str(daily_path))
        or {"outcome": "rewritten", "path": str(daily_path), "date": date_str, "operations": 0},
    )
    now = datetime(2026, 8, 16, 12, 0, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert phase2 == [str(path)]


def test_clock_leftover_without_phase2_when_under_gate(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-08-16.md").write_text(_card_yaml(10), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-16")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    leftover: list[str] = []
    phase2: list[str] = []

    def fake_digest(session_key, reason, force=False, sync=False):
        leftover.append(session_key)
        digest._finalize_digest_success(session_key, 9, session_id=session_key)
        return {"outcome": "appended"}

    monkeypatch.setattr(digest, "_maybe_run_digest", fake_digest)
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda *a, **k: phase2.append("p2") or {"outcome": "rewritten"},
    )
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps(
            {
                "sessions": {
                    "s1": {"session_id": "s1", "platform": "wecom"},
                    "s2": {"session_id": "s2", "platform": "wecom"},
                }
            }
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 8, 16, 23, 55, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert leftover == ["s1", "s2"]
    assert phase2 == []
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["last_nightly_date"] == "2026-08-16"
    assert saved["sessions"]["s1"]["last_digest_message_id"] == 9
    assert saved["sessions"]["s2"]["last_digest_message_id"] == 9


def test_clock_leftover_then_one_phase2_when_over_gate(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-08-16.md").write_text(_card_yaml(26), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-16")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    leftover: list[str] = []
    phase2: list[str] = []
    monkeypatch.setattr(
        digest,
        "_maybe_run_digest",
        lambda session_key, reason, force=False, sync=False: leftover.append(session_key)
        or {"outcome": "appended"},
    )
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda *a, **k: phase2.append("p2") or {"outcome": "rewritten"},
    )
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps({"sessions": {"s1": {"session_id": "s1"}, "s2": {"session_id": "s2"}}}),
        encoding="utf-8",
    )
    now = datetime(2026, 8, 16, 23, 56, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert leftover == ["s1", "s2"]
    assert phase2 == ["p2"]
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert leftover == ["s1", "s2"]
    assert phase2 == ["p2"]
