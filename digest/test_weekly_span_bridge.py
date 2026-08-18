"""Weekly/digest-bridge span ops: overdue reporting + resolution (Task 4).

Covers list/validate scoping + confidence filtering and the three resolve
actions (confirm / put_off / set_due_date), independent of chat recall.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from conftest import load_plugin_module


def _load_span_weekly():
    return load_plugin_module("span_weekly.py", "memory_digest_span_weekly_test")


def _write_daily(home: Path, date_str: str, blocks: list[str]) -> None:
    daily = home / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    (daily / f"{date_str}.md").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _block(block_id: str, *, entity: str, valid_to: str, body: str = "note") -> str:
    return "\n".join(
        [
            "---",
            f"id: {block_id}",
            "type: event",
            f"entity: {entity}",
            "predicate: did_thing",
            "valid_from: 2026-07-13",
            f"valid_to: {valid_to}",
            "confidence: high",
            "status: candidate",
            "sources: [session s1]",
            "---",
            body,
        ]
    )


WEEK = "2026-W29"  # Mon 2026-07-13 .. Sun 2026-07-19


def test_list_weekly_span_candidates_scopes_to_iso_week(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)

    _write_daily(tmp_path, "2026-07-13", [_block("mem-in-week", entity="A", valid_to="open")])
    # Outside the ISO week (Monday before).
    _write_daily(tmp_path, "2026-07-06", [_block("mem-out-week", entity="B", valid_to="open")])

    result = sw.list_weekly_span_candidates(WEEK)

    assert result["outcome"] == "listed"
    ids = {c["id"] for c in result["candidates"]}
    assert ids == {"mem-in-week"}


def test_list_weekly_span_candidates_invalid_week_key(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)

    result = sw.list_weekly_span_candidates("not-a-week")

    assert result["outcome"] == "invalid_week"
    assert result["candidates"] == []


def test_list_weekly_span_candidates_includes_overdue_and_open(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(sw.digest, "hermes_local_today_str", lambda: "2026-07-20")

    _write_daily(
        tmp_path,
        "2026-07-13",
        [
            _block("mem-open", entity="A", valid_to="open"),
            _block("mem-overdue", entity="B", valid_to="2026-07-14"),
            _block("mem-future", entity="C", valid_to="2026-12-31"),
        ],
    )

    result = sw.list_weekly_span_candidates(WEEK)
    ids = {c["id"] for c in result["candidates"]}
    assert ids == {"mem-open", "mem-overdue"}


def test_validate_weekly_spans_filters_to_explicit_and_high(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(
        tmp_path,
        "2026-07-13",
        [
            _block("mem-explicit", entity="A", valid_to="open"),
            _block("mem-high", entity="B", valid_to="open"),
            _block("mem-medium", entity="C", valid_to="open"),
            _block("mem-low", entity="D", valid_to="open"),
        ],
    )

    def fake_validator(user_message, candidates, conversation_excerpt=""):
        return [
            {"block_key": "mem-explicit", "confidence": "explicit", "proposed_valid_to": "2026-08-01"},
            {"block_key": "mem-high", "confidence": "high"},
            {"block_key": "mem-medium", "confidence": "medium"},
            {"block_key": "mem-low", "confidence": "low"},
        ]

    monkeypatch.setattr(sw.digest, "_run_span_validator_llm", fake_validator)

    result = sw.validate_weekly_spans(WEEK)

    assert result["outcome"] == "validated"
    ids = {r["block_id"] for r in result["results"]}
    assert ids == {"mem-explicit", "mem-high"}
    explicit_row = next(r for r in result["results"] if r["block_id"] == "mem-explicit")
    assert explicit_row["proposed_valid_to"] == "2026-08-01"
    high_row = next(r for r in result["results"] if r["block_id"] == "mem-high")
    assert "proposed_valid_to" not in high_row


def test_validate_weekly_spans_empty_candidates(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)

    result = sw.validate_weekly_spans(WEEK, candidates=[])

    assert result["outcome"] == "empty"
    assert result["results"] == []


def test_validate_weekly_spans_invalid_week_key(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)

    result = sw.validate_weekly_spans("bogus")

    assert result["outcome"] == "invalid_week"
    assert result["results"] == []


def test_resolve_confirm_applies_proposed_valid_to(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "confirm", proposed_valid_to="2026-08-01")

    assert result["outcome"] == "applied"
    assert result["valid_to"] == "2026-08-01"
    daily = (tmp_path / "memories" / "staging" / "daily" / "2026-07-13.md").read_text()
    assert "valid_to: 2026-08-01" in daily


def test_resolve_confirm_rejects_invalid_date(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "confirm", proposed_valid_to="2026-13-40")

    assert result["outcome"] == "error"
    daily = (tmp_path / "memories" / "staging" / "daily" / "2026-07-13.md").read_text()
    assert "valid_to: open" in daily


def test_resolve_set_due_date_rejects_invalid_date(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "set_due_date", due_date="not-a-date")

    assert result["outcome"] == "error"


def test_resolve_set_due_date_applies_user_selected_date(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "set_due_date", due_date="2026-09-15")

    assert result["outcome"] == "applied"
    assert result["valid_to"] == "2026-09-15"


def test_resolve_put_off_1d_from_open_uses_today(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(sw.digest, "hermes_local_today", lambda: date(2026, 7, 20))
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "put_off", interval="1d")

    assert result["outcome"] == "applied"
    assert result["valid_to"] == "2026-07-21"


def test_resolve_put_off_7d_from_existing_due_date(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="2026-07-14")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "put_off", interval="7d")

    assert result["outcome"] == "applied"
    assert result["valid_to"] == "2026-07-21"


def test_resolve_put_off_2w(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="2026-07-14")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "put_off", interval="2w")

    assert result["outcome"] == "applied"
    assert result["valid_to"] == "2026-07-28"


def test_resolve_put_off_1mo(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="2026-07-31")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "put_off", interval="1mo")

    assert result["outcome"] == "applied"
    # Aug has 31 days too, so day-of-month is preserved here.
    assert result["valid_to"] == "2026-08-31"


def test_resolve_put_off_1mo_clamps_short_month(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-01-31", [_block("mem-1", entity="A", valid_to="2026-01-31")])

    result = sw.resolve_weekly_span(
        "2026-W05", "mem-1", "put_off", interval="1mo"
    )

    assert result["outcome"] == "applied"
    assert result["valid_to"] == "2026-02-28"


def test_resolve_put_off_unknown_interval(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "put_off", interval="3d")

    assert result["outcome"] == "error"


def test_resolve_missing_block_returns_error(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    result = sw.resolve_weekly_span(WEEK, "mem-does-not-exist", "confirm", proposed_valid_to="2026-08-01")

    assert result["outcome"] == "error"
    assert "not found" in result["error"]


def test_resolve_invalid_week_key_returns_error(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)

    result = sw.resolve_weekly_span("bogus", "mem-1", "confirm", proposed_valid_to="2026-08-01")

    assert result["outcome"] == "error"


def test_resolve_unknown_action_returns_error(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    result = sw.resolve_weekly_span(WEEK, "mem-1", "delete")

    assert result["outcome"] == "error"


def test_resolve_duplicate_put_off_does_not_double_apply(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(sw.digest, "hermes_local_today", lambda: date(2026, 7, 20))
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    first = sw.resolve_weekly_span(WEEK, "mem-1", "put_off", interval="1d")
    second = sw.resolve_weekly_span(WEEK, "mem-1", "put_off", interval="1d")

    assert first["outcome"] == "applied"
    assert first["valid_to"] == "2026-07-21"
    assert second["outcome"] == "duplicate"
    assert second["idempotent"] is True
    assert second["valid_to"] == "2026-07-21"

    daily = (tmp_path / "memories" / "staging" / "daily" / "2026-07-13.md").read_text()
    assert "valid_to: 2026-07-21" in daily
    assert "valid_to: 2026-07-22" not in daily


def test_resolve_duplicate_via_explicit_idempotency_key(tmp_path, monkeypatch):
    sw = _load_span_weekly()
    monkeypatch.setattr(sw.digest, "get_hermes_home", lambda: tmp_path)
    _write_daily(tmp_path, "2026-07-13", [_block("mem-1", entity="A", valid_to="open")])

    first = sw.resolve_weekly_span(
        WEEK, "mem-1", "confirm", proposed_valid_to="2026-08-01", idempotency_key="click-1"
    )
    second = sw.resolve_weekly_span(
        WEEK, "mem-1", "confirm", proposed_valid_to="2026-09-01", idempotency_key="click-1"
    )

    assert first["outcome"] == "applied"
    assert second["outcome"] == "duplicate"
    assert second["valid_to"] == "2026-08-01"
