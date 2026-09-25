"""YAML writer round-trip and atomic replace."""

from __future__ import annotations

from monthly_schema import (
    MonthlyPayload,
    MonthlyProgress,
    MonthlyRange,
    MonthlySummaryItem,
    payload_to_dict,
)
from monthly_writer import dump_yaml, load_month, loads, write_month


def _payload() -> MonthlyPayload:
    return MonthlyPayload(
        key="2026-08",
        weeks=("2026-W32",),
        range=MonthlyRange(start="2026-08-01", end="2026-08-31"),
        summary=(MonthlySummaryItem(text="one line", weeks=()),),
        core_progress=(
            MonthlyProgress(
                id="cp-1",
                title="t",
                body="b",
                evidence=("mem-2026-08-12-event-9625547B667B",),
            ),
        ),
    )


def test_yaml_round_trip_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = _payload()
    dumped = dump_yaml(payload)
    restored = loads(dumped)
    assert dump_yaml(restored) == dumped
    assert restored.range.start == "2026-08-01"


def test_write_month_uses_month_status_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = write_month(_payload())
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\nmonth: 2026-08\nmonth_status: pending\n---\n")
    loaded = load_month("2026-08")
    assert loaded.summary[0].text == "one line"
    assert isinstance(loaded.summary, tuple)
    assert loaded.range.start == "2026-08-01"
    from monthly_writer import dumps

    assert '"month_key"' in dumps(loaded)


def test_write_month_atomic_on_serializer_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = write_month(_payload())
    original = path.read_text(encoding="utf-8")

    def boom(_payload):
        raise RuntimeError("serialize failed")

    import monthly_writer

    monkeypatch.setattr(monthly_writer, "dump_yaml", boom)
    try:
        monthly_writer.write_month(_payload())
    except RuntimeError:
        pass
    assert path.read_text(encoding="utf-8") == original


def test_key_order_pinned():
    dumped = dump_yaml(_payload())
    keys = [line.split(":")[0] for line in dumped.splitlines() if line and not line.startswith(" ") and ":" in line]
    assert keys[:6] == [
        "schema_version",
        "cycle",
        "month_key",
        "range",
        "weeks",
        "generated_at",
    ]
    assert "options" not in dumped
    assert "collaboration_steps" not in dumped
