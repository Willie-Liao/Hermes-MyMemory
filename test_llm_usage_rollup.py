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
