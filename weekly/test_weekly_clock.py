from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

SHANGHAI = ZoneInfo("Asia/Shanghai")
SUNDAY_1559 = datetime(2026, 8, 16, 15, 59, tzinfo=SHANGHAI)
SUNDAY_1600 = datetime(2026, 8, 16, 16, 0, tzinfo=SHANGHAI)
SUNDAY_1601 = datetime(2026, 8, 16, 16, 1, tzinfo=SHANGHAI)
SUNDAY_2355 = datetime(2026, 8, 16, 23, 55, tzinfo=SHANGHAI)
MONDAY_0800 = datetime(2026, 8, 17, 8, 0, tzinfo=SHANGHAI)
W33 = "2026-W33"
W34 = "2026-W34"
UI_TEXT = (
    "/weekly ui: ready\n"
    "• phone / WeChat: https://example.trycloudflare.com\n"
    "• this machine: http://127.0.0.1:3000"
)


def _load_clock():
    path = Path(__file__).with_name("weekly_clock.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_clock_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stub_clock(clock, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    generated: list[str] = []
    closed: list[str] = []
    sent: list[str] = []

    def fake_generate(week_key=None, *, reason="slash"):
        generated.append(str(week_key))
        return {"outcome": "ok", "week": week_key}

    def fake_close(week_key=None, *, enforce_sunday=False, today=None):
        closed.append(str(week_key))
        return {"outcome": "ok", "week": week_key}

    monkeypatch.setattr(clock, "generate_week", fake_generate)
    monkeypatch.setattr(clock, "close_week", fake_close)
    monkeypatch.setattr(clock, "_ui", lambda: UI_TEXT)
    monkeypatch.setattr(
        clock,
        "send_weekly_brief_weixin",
        lambda text: sent.append(text) or {"outcome": "sent"},
    )
    return generated, closed, sent


def test_sunday_1559_idle(tmp_path, monkeypatch):
    clock = _load_clock()
    generated, closed, sent = _stub_clock(clock, monkeypatch, tmp_path)
    result = clock.maybe_run(SUNDAY_1559, leftover_ran=False)
    assert result["outcome"] == "idle"
    assert generated == []
    assert closed == []
    assert sent == []


def test_sunday_1600_generate_once_and_send_link(tmp_path, monkeypatch):
    clock = _load_clock()
    generated, closed, sent = _stub_clock(clock, monkeypatch, tmp_path)
    first = clock.maybe_run(SUNDAY_1600, leftover_ran=False)
    assert first["outcome"] == "generated"
    assert generated == [W33]
    assert closed == []
    assert len(sent) == 1
    assert f"Weekly review {W33} is ready." in sent[0]
    assert "trycloudflare.com" in sent[0]
    assert "/weekly ui" in sent[0]
    second = clock.maybe_run(SUNDAY_1601, leftover_ran=False)
    assert second["outcome"] == "idle"
    assert generated == [W33]
    assert sent == [sent[0]]


def test_sunday_2355_closes_only_after_leftover(tmp_path, monkeypatch):
    clock = _load_clock()
    generated, closed, sent = _stub_clock(clock, monkeypatch, tmp_path)
    skipped = clock.maybe_run(SUNDAY_2355, leftover_ran=False)
    assert skipped["close"] is None
    assert closed == []
    clock.maybe_run(SUNDAY_1600, leftover_ran=False)
    done = clock.maybe_run(SUNDAY_2355, leftover_ran=True)
    assert closed == [W33]
    assert done["close"] == W33


def test_monday_does_not_close_new_week(tmp_path, monkeypatch):
    clock = _load_clock()
    generated, closed, _sent = _stub_clock(clock, monkeypatch, tmp_path)
    result = clock.maybe_run(MONDAY_0800, leftover_ran=False)
    assert W34 not in generated
    assert W34 not in closed
    assert result.get("close") != W34


def test_monday_catchup_previous_week_only(tmp_path, monkeypatch):
    clock = _load_clock()
    generated, closed, sent = _stub_clock(clock, monkeypatch, tmp_path)
    result = clock.maybe_run(MONDAY_0800, leftover_ran=False)
    assert generated == [W33]
    assert closed == [W33]
    assert W34 not in generated
    assert result["generate"] == W33
    assert result["close"] == W33
    again = clock.maybe_run(MONDAY_0800, leftover_ran=False)
    assert generated == [W33]
    assert closed == [W33]
    assert again["outcome"] == "idle"
    assert sent  # generate still notifies if Sunday send was missed


def test_send_skips_without_chat_id_still_generates(tmp_path, monkeypatch):
    clock = _load_clock()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WEEKLY_BRIEF_WEIXIN_TO", raising=False)
    generated: list[str] = []
    monkeypatch.setattr(
        clock,
        "generate_week",
        lambda week_key=None, *, reason="slash": generated.append(str(week_key))
        or {"outcome": "ok"},
    )
    monkeypatch.setattr(clock, "close_week", lambda *a, **k: {"outcome": "ok"})
    monkeypatch.setattr(clock, "_ui", lambda: UI_TEXT)
    monkeypatch.setattr(clock, "_weixin_chat_id", lambda: None)
    sent_calls: list[str] = []

    real_send = clock.send_weekly_brief_weixin

    def wrap(text: str):
        sent_calls.append(text)
        return real_send(text)

    monkeypatch.setattr(clock, "send_weekly_brief_weixin", wrap)
    result = clock.maybe_run(SUNDAY_1600, leftover_ran=False)
    assert generated == [W33]
    assert result["send"]["outcome"] == "skipped"
    assert sent_calls  # generate path still invokes send helper


def test_send_uses_live_adapter_when_runner_present(monkeypatch):
    clock = _load_clock()
    monkeypatch.setenv("WEEKLY_BRIEF_WEIXIN_TO", "wx-chat")

    class _Adapter:
        def __init__(self):
            self.sent = []

        async def send(self, chat_id, content, metadata=None, reply_to=None):
            self.sent.append((chat_id, content))

            class _R:
                success = True
                message_id = "m1"
                error = None

            return _R()

    adapter = _Adapter()

    class _Runner:
        adapters = {"weixin": adapter}

    monkeypatch.setattr(clock, "_gateway_runner_ref", lambda: _Runner())
    out = clock.send_weekly_brief_weixin("hello weekly")
    assert out["outcome"] == "sent"
    assert adapter.sent == [("wx-chat", "hello weekly")]


def test_state_persists_sunday_keys(tmp_path, monkeypatch):
    clock = _load_clock()
    _stub_clock(clock, monkeypatch, tmp_path)
    clock.maybe_run(SUNDAY_1600, leftover_ran=False)
    clock.maybe_run(SUNDAY_2355, leftover_ran=True)
    state = json.loads(
        (tmp_path / "memories" / "staging" / ".weekly-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["last_sunday_generate_week"] == W33
    assert state["last_sunday_close_week"] == W33
