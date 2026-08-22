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
    digest = load_plugin_module("digest.py", "memory_digest_clock_test")
    digest._maybe_run_weekly_clock = lambda *a, **k: {"outcome": "stubbed"}
    return digest


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
    assert should_run_phase2_tick(1, None, now, ignore_block_gate=True) is True
    assert should_run_phase2_tick(0, None, now, ignore_block_gate=True) is False


def test_phase2_tick_cooldown_skips_recent_merge():
    now = datetime(2026, 8, 16, 16, 0, tzinfo=SHANGHAI)
    last = now - timedelta(minutes=5)
    assert should_run_phase2_tick(26, last, now) is False
    last = now - timedelta(minutes=21)
    assert should_run_phase2_tick(26, last, now) is True


def test_nightly_leftover_2355_today_and_morning_writes_yesterday():
    """23:55 flushes today; 08:00 catch-up is only if yesterday's 23:55 never stamped."""
    morning = datetime(2026, 8, 17, 8, 0, tzinfo=SHANGHAI)
    noon = datetime(2026, 8, 16, 12, 0, tzinfo=SHANGHAI)
    at = datetime(2026, 8, 16, 23, 55, tzinfo=SHANGHAI)
    # Yesterday already flushed → morning must wait for tonight 23:55.
    assert should_run_nightly_leftover("2026-08-16", morning, SHANGHAI) is None
    assert should_run_nightly_leftover("2026-08-16", noon, SHANGHAI) is None
    # Missed 16th 23:55 (stamp still 15th) → 08:00 on 17th writes the 16th file.
    assert should_run_nightly_leftover("2026-08-15", morning, SHANGHAI) == "2026-08-16"
    assert should_run_nightly_leftover(None, morning, SHANGHAI) == "2026-08-16"
    # 23:55 same day.
    assert should_run_nightly_leftover("2026-08-16", at, SHANGHAI) is None
    assert should_run_nightly_leftover("2026-08-15", at, SHANGHAI) == "2026-08-16"
    assert should_run_nightly_leftover(None, at, SHANGHAI) == "2026-08-16"


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
            state["last_nightly_date"] = "2026-08-16"
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
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps({"last_nightly_date": "2026-08-16", "sessions": {}}),
        encoding="utf-8",
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
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps({"last_nightly_date": "2026-08-16", "sessions": {}}),
        encoding="utf-8",
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


def test_clock_leftover_with_phase2_when_under_gate(tmp_path, monkeypatch):
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

    def fake_digest(session_key, reason, force=False, sync=False, date_str=None):
        leftover.append(session_key)
        digest._finalize_digest_success(session_key, 9, session_id=session_key)
        return {"outcome": "appended"}

    monkeypatch.setattr(digest, "_maybe_run_digest", fake_digest)
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda *a, **k: phase2.append("p2") or {"outcome": "rewritten"},
    )
    monkeypatch.setattr(digest, "run_day_wrapup", lambda *_a, **_k: None)
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
    assert phase2 == ["p2"]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["last_nightly_date"] == "2026-08-16"
    assert saved["sessions"]["s1"]["last_digest_message_id"] == 9
    assert saved["sessions"]["s2"]["last_digest_message_id"] == 9


def test_clock_noon_phase2_does_not_call_wrapup(tmp_path, monkeypatch):
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
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps({"last_nightly_date": "2026-08-16", "sessions": {}}),
        encoding="utf-8",
    )
    wrap: list[str] = []
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda daily_path, date_str="": {"outcome": "rewritten"},
    )
    monkeypatch.setattr(
        digest,
        "run_day_wrapup",
        lambda daily_path: wrap.append(str(daily_path)),
    )
    now = datetime(2026, 8, 16, 12, 0, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert wrap == []


def test_clock_2355_calls_wrapup_even_under_gate(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    path = daily_dir / "2026-08-16.md"
    path.write_text(_card_yaml(10), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-16")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    wrap: list[str] = []
    monkeypatch.setattr(digest, "_maybe_run_digest", lambda *a, **k: None)
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda *a, **k: {"outcome": "rewritten"},
    )
    monkeypatch.setattr(
        digest,
        "run_day_wrapup",
        lambda daily_path: wrap.append(str(daily_path)),
    )
    now = datetime(2026, 8, 16, 23, 55, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert wrap == [str(path)]


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
        lambda session_key, reason, force=False, sync=False, date_str=None: leftover.append(session_key)
        or {"outcome": "appended"},
    )
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda *a, **k: phase2.append("p2") or {"outcome": "rewritten"},
    )
    monkeypatch.setattr(digest, "run_day_wrapup", lambda *_a, **_k: None)
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


def test_clock_weekly_hook_after_leftover(tmp_path, monkeypatch):
    digest = load_plugin_module("digest.py", "memory_digest_clock_weekly_hook")
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-08-16.md").write_text(_card_yaml(10), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-16")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    order: list = []
    monkeypatch.setattr(
        digest,
        "_maybe_run_digest",
        lambda session_key, reason, force=False, sync=False, date_str=None: order.append("leftover"),
    )
    monkeypatch.setattr(digest, "run_day_wrapup", lambda *_a, **_k: None)
    monkeypatch.setattr(
        digest,
        "run_manual_phase2",
        lambda *a, **k: {"outcome": "rewritten"},
    )
    monkeypatch.setattr(
        digest,
        "_maybe_run_weekly_clock",
        lambda local, leftover_ran=False: order.append(("weekly", leftover_ran))
        or {"outcome": "ok"},
    )
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps({"sessions": {"s1": {"session_id": "s1"}}}),
        encoding="utf-8",
    )
    now = datetime(2026, 8, 16, 23, 55, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert order[0] == "leftover"
    assert ("weekly", True) in order
    assert order.index("leftover") < order.index(("weekly", True))


def test_clock_morning_skips_leftover_if_last_night_already_flushed(tmp_path, monkeypatch):
    """08:00 must not consume tonight's slot when yesterday's 23:55 already stamped."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-08-17.md").write_text(_card_yaml(3), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-17")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    leftover: list[tuple] = []
    monkeypatch.setattr(
        digest,
        "_maybe_run_digest",
        lambda session_key, reason, force=False, sync=False, date_str=None: leftover.append(
            (session_key, force, reason, date_str)
        ),
    )
    monkeypatch.setattr(digest, "run_manual_phase2", lambda *a, **k: {"outcome": "rewritten"})
    monkeypatch.setattr(digest, "run_day_wrapup", lambda *_a, **_k: None)
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps(
            {
                "last_nightly_date": "2026-08-16",
                "sessions": {"s1": {"session_id": "s1"}},
            }
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 8, 17, 8, 0, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert leftover == []
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["last_nightly_date"] == "2026-08-16"


def test_clock_morning_catchup_writes_yesterday_if_night_missed(tmp_path, monkeypatch):
    """Missed 16th 23:55: 08:00 on the 17th leftover-extracts into the 16th file."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-08-16.md").write_text(_card_yaml(3), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-17")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    leftover: list[tuple] = []
    monkeypatch.setattr(
        digest,
        "_maybe_run_digest",
        lambda session_key, reason, force=False, sync=False, date_str=None: leftover.append(
            (session_key, force, reason, date_str)
        ),
    )
    monkeypatch.setattr(digest, "run_manual_phase2", lambda *a, **k: {"outcome": "rewritten"})
    monkeypatch.setattr(digest, "run_day_wrapup", lambda *_a, **_k: None)
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps(
            {
                "last_nightly_date": "2026-08-15",
                "sessions": {"s1": {"session_id": "s1"}},
            }
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 8, 17, 8, 0, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert leftover == [("s1", True, "nightly_leftover", "2026-08-16")]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["last_nightly_date"] == "2026-08-16"


def test_clock_morning_wrapup_yesterday_if_no_trailer(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    yesterday = daily_dir / "2026-08-16.md"
    yesterday.write_text(_card_yaml(4), encoding="utf-8")
    (daily_dir / "2026-08-17.md").write_text(_card_yaml(2), encoding="utf-8")
    monkeypatch.setattr(digest, "hermes_local_today_str", lambda: "2026-08-17")
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    wrap: list[str] = []
    monkeypatch.setattr(digest, "_maybe_run_digest", lambda *a, **k: None)
    monkeypatch.setattr(digest, "run_manual_phase2", lambda *a, **k: {"outcome": "rewritten"})
    monkeypatch.setattr(
        digest,
        "run_day_wrapup",
        lambda daily_path: wrap.append(Path(daily_path).name),
    )
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps(
            {
                "last_nightly_date": "2026-08-15",
                "sessions": {"s1": {"session_id": "s1"}},
            }
        ),
        encoding="utf-8",
    )
    now = datetime(2026, 8, 17, 8, 0, tzinfo=SHANGHAI)
    digest.maybe_run_digest_clock(now=now, sync=True)
    assert "2026-08-16.md" in wrap
    assert "2026-08-17.md" not in wrap


def test_clock_loop_sleeps_until_deadline_not_sixty(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "memories" / "staging").mkdir(parents=True)
    waits: list[float] = []

    def fake_wait(timeout=None):
        waits.append(float(timeout))
        return True

    monkeypatch.setattr(digest._clock_stop, "wait", fake_wait)
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    future = datetime(2026, 8, 16, 23, 55, tzinfo=SHANGHAI)
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps({"next_clock_at": future.isoformat(), "sessions": {}}),
        encoding="utf-8",
    )
    frozen = datetime(2026, 8, 16, 12, 0, tzinfo=SHANGHAI)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(digest, "datetime", FrozenDateTime)
    digest._digest_clock_loop()
    assert waits, "clock loop must wait once"
    assert waits[0] >= 3600, waits
    assert waits[0] <= 12 * 3600 + 1, waits
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved.get("clock_heartbeat_at")
    assert saved["clock_alive"] is False
    assert saved.get("clock_stopped_at")


def test_clock_loop_death_writes_error(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "memories" / "staging").mkdir(parents=True)
    monkeypatch.setattr(digest._clock_stop, "wait", lambda timeout=None: False)
    monkeypatch.setattr(
        digest,
        "maybe_run_digest_clock",
        lambda **k: (_ for _ in ()).throw(RuntimeError("clock boom")),
    )
    monkeypatch.setattr(digest, "start_digest_clock_thread", lambda: None)
    monkeypatch.setattr(
        digest.digest_clock, "digest_clock_tz", lambda **_k: SHANGHAI
    )
    state_path = tmp_path / "memories" / "staging" / ".digest-state.json"
    state_path.write_text(
        json.dumps(
            {
                "next_clock_at": datetime(2026, 8, 16, 8, 0, tzinfo=SHANGHAI).isoformat(),
                "sessions": {},
            }
        ),
        encoding="utf-8",
    )
    digest._digest_clock_loop()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["clock_alive"] is False
    assert "clock boom" in str(saved.get("clock_error"))
    assert saved.get("clock_stopped_at")


def test_on_agent_end_records_observed_dead_clock(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "memories" / "staging").mkdir(parents=True)
    digest._clock_thread = None
    monkeypatch.setattr(digest, "_fetch_messages", lambda *a, **k: [])
    monkeypatch.setattr(digest, "maybe_run_digest_clock", lambda **k: {"outcome": "idle"})
    logs: list[str] = []
    monkeypatch.setattr(digest, "_log", logs.append)
    digest.on_agent_end({"session_id": "s1", "platform": "wecom"})
    saved = json.loads(
        (tmp_path / "memories" / "staging" / ".digest-state.json").read_text(encoding="utf-8")
    )
    assert saved["clock_alive"] is False
    assert any("observed dead" in line for line in logs)
