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
    assert clock.next_write_at(OCT_10) == datetime(2026, 10, 11, 0, 5, tzinfo=SHANGHAI)
    assert clock.next_write_at(OCT_20) == datetime(2026, 10, 21, 0, 5, tzinfo=SHANGHAI)


def _write_resume(month_key: str) -> None:
    from monthly_actions import reduce_resume_path

    path = reduce_resume_path(month_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"merged": {"summary": "kept"}, "gaps": ["comparison_with_last_month is missing"], "notes": []}\n',
        encoding="utf-8",
    )


def test_partial_checkpoint_retries_gaps_only_once_per_day(tmp_path, monkeypatch):
    calls: list[bool] = []

    def fake_generate(month_key=None, *, reason="bridge", gaps_only=False, **_):
        calls.append(bool(gaps_only))
        if gaps_only:
            return {"outcome": "ok", "month": month_key}
        _write_resume(str(month_key))
        return {"outcome": "partial", "month": month_key}

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("monthly_actions.generate_month", fake_generate)
    assert clock.maybe_run(OCT_10)["months"] == ["2026-10"]
    eleventh = datetime(2026, 10, 11, 0, 5, tzinfo=SHANGHAI)
    assert clock.maybe_run(eleventh)["months"] == ["2026-10"]
    assert clock.maybe_run(eleventh)["outcome"] == "idle"
    assert calls == [False, True]


def test_repeated_partial_stops_after_the_window(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_generate(month_key=None, *, reason="bridge", gaps_only=False, **_):
        calls.append(str(month_key))
        _write_resume(str(month_key))
        return {"outcome": "partial", "month": month_key}

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("monthly_actions.generate_month", fake_generate)
    for day in (10, 11, 12, 13):
        clock.maybe_run(datetime(2026, 10, day, 0, 5, tzinfo=SHANGHAI))
    assert len(calls) == 4
    fourteenth = datetime(2026, 10, 14, 8, 0, tzinfo=SHANGHAI)
    assert clock.maybe_run(fourteenth)["outcome"] == "idle"
    assert len(calls) == 4
    state = monthly_state.load_state()
    assert "incomplete" in str(state.get("last_error"))
    assert state.get("last_checkpoint") == "2026-10-10"


def test_failed_close_retries_on_the_second_and_third(tmp_path, monkeypatch):
    calls: list[tuple[str, bool]] = []

    def fake_generate(month_key=None, *, reason="bridge", gaps_only=False, **_):
        calls.append((str(month_key), bool(gaps_only)))
        _write_resume(str(month_key))
        return {"outcome": "partial", "month": month_key}

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("monthly_actions.generate_month", fake_generate)
    clock.maybe_run(OCT_1)
    clock.maybe_run(datetime(2026, 10, 2, 0, 5, tzinfo=SHANGHAI))
    clock.maybe_run(datetime(2026, 10, 3, 0, 5, tzinfo=SHANGHAI))
    assert calls == [("2026-09", False), ("2026-09", True), ("2026-09", True)]
    fourth = datetime(2026, 10, 4, 8, 0, tzinfo=SHANGHAI)
    clock.maybe_run(fourth)
    assert len(calls) == 3
    assert "close" in str(monthly_state.load_state().get("last_error"))


def test_config_moves_the_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    MyMemory:\n      monthly:\n"
        "        write_at: '01:00'\n        checkpoint_days: [15]\n        retry_days: 3\n",
        encoding="utf-8",
    )
    assert clock.next_write_at(OCT_9) == datetime(2026, 10, 15, 1, 0, tzinfo=SHANGHAI)


def test_bad_config_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  entries:\n    MyMemory:\n      monthly:\n"
        "        write_at: nope\n        checkpoint_days: ['no']\n        retry_days: 0\n",
        encoding="utf-8",
    )
    assert clock.next_write_at(OCT_9) == datetime(2026, 10, 10, 0, 5, tzinfo=SHANGHAI)


def test_failure_records_error_without_month_key(tmp_path, monkeypatch):
    _stub(monkeypatch, tmp_path, fail=True)
    result = clock.maybe_run(SEP_1)
    assert result["outcome"] == "error"
    state = monthly_state.load_state()
    assert "last_monthly_generate_month" not in state
    assert state.get("last_error")
