"""Civil-tick monthly clock: source fingerprint, backfill, rollover."""

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


def _write_decision(tmp_path, day: str, clause: str, extra: str = "") -> None:
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    daily.joinpath(f"{day}.md").write_text(
        "---\n"
        f"id: mem-{day}-decision-ABCD\n"
        "type: decision\n"
        "entity: Tooling\n"
        "status: candidate\n"
        f"{extra}"
        "---\n"
        f"{clause}\n",
        encoding="utf-8",
    )


def test_aug_31_first_tick_refreshes_current_when_sources_exist(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    _write_decision(tmp_path, "2026-08-05", "Decision: keep the graph.")
    result = clock.maybe_run(AUG_31)
    assert result["outcome"] == "generated"
    assert "2026-08" in generated
    assert "2026-07" in generated


def test_sep_1_generates_august_once(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    _write_decision(tmp_path, "2026-08-05", "Decision: keep the graph.")
    first = clock.maybe_run(SEP_1)
    assert first["outcome"] == "generated"
    assert generated[0] == "2026-08"
    second = clock.maybe_run(SEP_1)
    assert second["outcome"] == "idle"
    assert generated.count("2026-08") == 1


def test_sep_15_with_key_idle(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    _write_decision(tmp_path, "2026-08-05", "Decision: keep the graph.")
    clock.maybe_run(SEP_1)
    later = clock.maybe_run(SEP_15)
    assert later["outcome"] == "idle"
    assert generated.count("2026-08") == 1


def test_mid_month_first_tick_backfills_then_idle_if_unchanged(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    _write_decision(tmp_path, "2026-08-05", "Decision: keep the graph.")
    first = clock.maybe_run(AUG_15)
    assert first["outcome"] == "generated"
    assert generated == ["2026-07", "2026-08"]
    second = clock.maybe_run(AUG_15)
    assert second["outcome"] == "idle"
    assert generated == ["2026-07", "2026-08"]


def test_source_change_refreshes_current_recall_stamp_does_not(tmp_path, monkeypatch):
    generated = _stub(monkeypatch, tmp_path)
    _write_decision(tmp_path, "2026-08-05", "Decision: keep the graph.")
    clock.maybe_run(AUG_15)
    _write_decision(tmp_path, "2026-08-17", "Decision: prefer short reviews.")
    changed = clock.maybe_run(AUG_15)
    assert changed["outcome"] == "generated"
    assert generated[-1] == "2026-08"
    n = len(generated)
    _write_decision(
        tmp_path,
        "2026-08-17",
        "Decision: prefer short reviews.",
        extra="recall_n: 9\nstrength: 4.2\nlast_recall_at: '2026-08-17T12:00:00+08:00'\n",
    )
    stamped = clock.maybe_run(AUG_15)
    assert stamped["outcome"] == "idle"
    assert len(generated) == n


def test_failure_records_error_without_month_key(tmp_path, monkeypatch):
    _stub(monkeypatch, tmp_path, fail=True)
    _write_decision(tmp_path, "2026-08-05", "Decision: keep the graph.")
    result = clock.maybe_run(SEP_1)
    assert result["outcome"] == "error"
    state = monthly_state.load_state()
    assert "last_monthly_generate_month" not in state
    assert state.get("last_error")
