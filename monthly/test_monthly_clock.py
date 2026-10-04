"""Civil-tick monthly clock: 10th and 20th checkpoints, 1st close."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import monthly_clock as clock
import monthly_state

SHANGHAI = ZoneInfo("Asia/Shanghai")
AUG_15 = datetime(2026, 8, 15, 8, 0, tzinfo=SHANGHAI)
SEP_1 = datetime(2026, 9, 1, 8, 0, tzinfo=SHANGHAI)
OCT_1_EARLY = datetime(2026, 10, 1, 0, 4, tzinfo=SHANGHAI)
OCT_1 = datetime(2026, 10, 1, 0, 5, tzinfo=SHANGHAI)
OCT_2 = datetime(2026, 10, 2, 8, 0, tzinfo=SHANGHAI)
OCT_9 = datetime(2026, 10, 9, 20, 0, tzinfo=SHANGHAI)
OCT_10_EARLY = datetime(2026, 10, 10, 0, 4, tzinfo=SHANGHAI)
OCT_10 = datetime(2026, 10, 10, 0, 5, tzinfo=SHANGHAI)
OCT_15 = datetime(2026, 10, 15, 8, 0, tzinfo=SHANGHAI)
OCT_20 = datetime(2026, 10, 20, 0, 5, tzinfo=SHANGHAI)


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


def _write_decision(tmp_path, day: str, clause: str) -> None:
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    daily.joinpath(f"{day}.md").write_text(
        "---\n"
        f"id: mem-{day}-decision-ABCD\n"
        "type: decision\n"
        "entity: Tooling\n"
        "status: candidate\n"
        "---\n"
        f"{clause}\n",
        encoding="utf-8",
    )


def test_fresh_non_slot_day_is_idle(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    result = clock.maybe_run(OCT_15)
    assert result["outcome"] == "idle"
    assert generated == []


def test_card_edit_on_a_non_slot_day_does_not_generate(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    _write_decision(tmp_path, "2026-08-05", "Decision: keep the graph.")
    clock.maybe_run(AUG_15)
    _write_decision(tmp_path, "2026-08-17", "Decision: prefer short reviews.")
    changed = clock.maybe_run(AUG_15)
    assert changed["outcome"] == "idle"
    assert generated == []


def test_sep_1_generates_august_once(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    first = clock.maybe_run(SEP_1)
    assert first["outcome"] == "generated"
    assert generated == ["2026-08"]
    second = clock.maybe_run(SEP_1)
    assert second["outcome"] == "idle"
    assert generated == ["2026-08"]


def test_oct_10_before_0005_is_idle_then_writes_current_only(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    early = clock.maybe_run(OCT_10_EARLY)
    assert early["outcome"] == "idle"
    result = clock.maybe_run(OCT_10)
    assert generated == ["2026-10"]
    again = clock.maybe_run(OCT_10)
    assert again["outcome"] == "idle"
    assert generated == ["2026-10"]


def test_oct_20_writes_current_once(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    first = clock.maybe_run(OCT_20)
    assert first["months"] == ["2026-10"]
    second = clock.maybe_run(OCT_20)
    assert second["outcome"] == "idle"
    assert generated == ["2026-10"]


def test_oct_1_writes_previous_only(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    assert clock.maybe_run(OCT_1_EARLY)["outcome"] == "idle"
    result = clock.maybe_run(OCT_1)
    assert result["months"] == ["2026-09"]
    assert generated == ["2026-09"]


def test_missed_first_closes_on_the_next_day(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    clock.maybe_run(SEP_1)
    later = clock.maybe_run(OCT_2)
    assert later["months"] == ["2026-09"]
    assert generated == ["2026-08", "2026-09"]


def test_next_write_at_is_0005_on_slot_days():
    assert clock.next_write_at(OCT_9) == datetime(2026, 10, 10, 0, 5, tzinfo=SHANGHAI)
    assert clock.next_write_at(OCT_10) == datetime(2026, 10, 20, 0, 5, tzinfo=SHANGHAI)
    assert clock.next_write_at(OCT_20) == datetime(2026, 11, 1, 0, 5, tzinfo=SHANGHAI)


def test_failure_records_error_without_month_key(tmp_path, monkeypatch):
    _stub(monkeypatch, tmp_path, fail=True)
    result = clock.maybe_run(SEP_1)
    assert result["outcome"] == "error"
    state = monthly_state.load_state()
    assert "last_monthly_generate_month" not in state
    assert state.get("last_error")
