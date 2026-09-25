"""Month-rollover clock against a sandboxed .monthly-state.json."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import monthly_clock as clock
import monthly_state

SHANGHAI = ZoneInfo("Asia/Shanghai")
AUG_31 = datetime(2026, 8, 31, 23, 0, tzinfo=SHANGHAI)
SEP_1 = datetime(2026, 9, 1, 8, 0, tzinfo=SHANGHAI)
SEP_15 = datetime(2026, 9, 15, 8, 0, tzinfo=SHANGHAI)
AUG_15 = datetime(2026, 8, 15, 8, 0, tzinfo=SHANGHAI)


def _stub(monkeypatch, tmp_path, *, fail: bool = False):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    generated: list[str] = []

    def fake_generate(month_key=None, *, reason="bridge", **_):
        if fail:
            raise RuntimeError("boom")
        generated.append(str(month_key))
        return {"outcome": "ok", "month": month_key}

    monkeypatch.setattr("monthly_actions.generate_month", fake_generate)
    return generated


def test_aug_31_idle(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    result = clock.maybe_run(AUG_31)
    assert result["outcome"] == "idle"
    assert generated == []


def test_sep_1_generates_august_once(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    first = clock.maybe_run(SEP_1)
    assert first["outcome"] == "generated"
    assert generated == ["2026-08"]
    second = clock.maybe_run(SEP_1)
    assert second["outcome"] == "idle"
    assert generated == ["2026-08"]


def test_sep_15_with_key_idle(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    clock.maybe_run(SEP_1)
    later = clock.maybe_run(SEP_15)
    assert later["outcome"] == "idle"
    assert generated == ["2026-08"]


def test_mid_month_never_generates_in_progress(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    result = clock.maybe_run(AUG_15)
    assert result["outcome"] == "idle"
    assert generated == []


def test_failure_records_error_without_month_key(tmp_path, monkeypatch):
    _stub(monkeypatch, tmp_path, fail=True)
    result = clock.maybe_run(SEP_1)
    assert result["outcome"] == "error"
    state = monthly_state.load_state()
    assert "last_monthly_generate_month" not in state
    assert state.get("last_error")
