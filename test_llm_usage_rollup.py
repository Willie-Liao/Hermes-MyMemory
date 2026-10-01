"""Tests for daily LLM usage rollup (Task 4)."""

from __future__ import annotations

import json
from datetime import date


def test_rollup_day_sums(tmp_path):
    from llm_usage_rollup import rollup_day

    p = tmp_path / "llm-usage.jsonl"
    rows = [
        {
            "ts": "2026-07-16T01:00:00+00:00",
            "plugin": "memory-digest",
            "purpose": "digest",
            "total_tokens": 10,
            "cost_usd": 0.01,
        },
        {
            "ts": "2026-07-16T02:00:00+00:00",
            "plugin": "memory-weekly",
            "purpose": "worker2_brief",
            "total_tokens": 5,
            "cost_usd": 0.02,
        },
        {
            "ts": "2026-07-15T02:00:00+00:00",
            "plugin": "other-plugin",
            "purpose": "decision_classifier",
            "total_tokens": 99,
            "cost_usd": 1.0,
        },
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = rollup_day(date(2026, 7, 16), ledger_path=p)
    assert out["day"] == "2026-07-16"
    assert out["total_tokens"] == 15
    assert abs(out["cost_usd"] - 0.03) < 1e-9
    assert out["by_plugin"]["memory-digest"]["total_tokens"] == 10
    assert abs(out["by_plugin"]["memory-digest"]["cost_usd"] - 0.01) < 1e-9
    assert out["by_plugin"]["memory-weekly"]["total_tokens"] == 5
    assert out["by_purpose"]["digest"]["total_tokens"] == 10
    assert out["by_purpose"]["worker2_brief"]["total_tokens"] == 5
    assert "other-plugin" not in out["by_plugin"]
    assert "decision_classifier" not in out["by_purpose"]


def test_rollup_day_missing_ledger(tmp_path):
    from llm_usage_rollup import rollup_day

    missing = tmp_path / "does-not-exist.jsonl"
    out = rollup_day(date(2026, 7, 16), ledger_path=missing)
    assert out["day"] == "2026-07-16"
    assert out["total_tokens"] == 0
    assert out["cost_usd"] == 0.0
    assert out["by_plugin"] == {}
    assert out["by_purpose"] == {}


def test_rollup_range_skips_bench_and_zero_and_other_plugins(tmp_path):
    from llm_usage_rollup import rollup_range

    p = tmp_path / "llm-usage.jsonl"
    rows = [
        {
            "ts": "2026-08-12T01:00:00+00:00",
            "plugin": "memory-digest",
            "purpose": "digest-phase1",
            "total_tokens": 100,
            "cost_usd": 0.0,
        },
        {
            "ts": "2026-08-12T02:00:00+00:00",
            "plugin": "memory-digest",
            "purpose": "bench-old-event",
            "total_tokens": 50,
            "cost_usd": 0.0,
        },
        {
            "ts": "2026-08-12T03:00:00+00:00",
            "plugin": "memory-digest",
            "purpose": "digest-phase1",
            "total_tokens": 0,
            "cost_usd": 0.0,
        },
        {
            "ts": "2026-08-12T04:00:00+00:00",
            "plugin": "memory-weekly",
            "purpose": "worker2_brief",
            "total_tokens": 9,
            "cost_usd": 0.0,
        },
        {
            "ts": "2026-08-10T04:00:00+00:00",
            "plugin": "memory-digest",
            "purpose": "digest-phase1",
            "total_tokens": 999,
            "cost_usd": 0.0,
        },
    ]
    p.write_text("\n".join(__import__("json").dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = rollup_range(
        date(2026, 8, 11),
        date(2026, 8, 12),
        ledger_path=p,
        plugins=["memory-digest"],
        purposes=["digest-phase1"],
    )
    assert out["total_tokens"] == 100
    assert out["n"] == 1
    assert out["skipped"] >= 3
    assert "bench-old-event" not in out["by_purpose"]
