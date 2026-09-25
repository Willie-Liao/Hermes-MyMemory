"""Live mimo-v2.5 eval of the August map-reduce pipeline."""

from __future__ import annotations

import os
import time

import pytest


def test_live_august_pipeline_tokens():
    flag = os.environ.get("REAL_LLM_TEST", os.environ.get("PLAN_LOOP_LIVE_LLM", "1")).strip().lower()
    if flag in {"0", "false", "no"}:
        pytest.skip("REAL_LLM_TEST=0")
    from monthly_actions import generate_month
    from monthly_slice import pack_batches, week_slices

    started = time.monotonic()
    try:
        result = generate_month("2026-08", reason="eval", force_refresh=True)
    except ValueError as exc:
        if "API_KEY" in str(exc):
            pytest.skip(str(exc))
        raise
    elapsed_ms = (time.monotonic() - started) * 1000
    assert result["outcome"] == "ok"
    payload = result["payload"]
    assert payload["summary"]
    for row in payload["key_decisions"]:
        assert not str(row.get("id") or "").startswith("mem-invented")
    # Predicted ~14700 input across 2 map + 1 reduce; allow 20% plus map cache effects.
    usage = result.get("usage") or {}
    prompt_est = int(usage.get("prompt_tokens_est") or 0)
    batches = pack_batches(week_slices("2026-08"))
    assert len(batches) == 2
    assert elapsed_ms > 0
    assert prompt_est < 20000 or usage.get("input_tokens", 0) < 20000
