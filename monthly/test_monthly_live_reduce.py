"""One live suggest call. The installer ignores this file.

The old live reduce of 2026-06 wrote the real month home and only checked the
retry log. That test is gone so a REAL_LLM_TEST run cannot rewrite June.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest


def _load_env_file(path: Path) -> None:
    """Put worker keys in the process when hermes_cli's loader is not on this path.

    The oneshot reads os.environ. A copied .env that never gets parsed looks like
    a transport failure and burns the three reduce tries with zero tokens.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        row = line.strip()
        if not row or row.startswith("#") or "=" not in row:
            continue
        key, _, val = row.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def test_live_suggest_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Print one October suggest reply so the flat lines and the bill are visible.

    A temp home keeps the real 2026-10.md untouched. Comparison rows are filled
    here so the three headings appear without a full month reduce.
    """
    flag = os.environ.get("REAL_LLM_TEST", "0").strip().lower()
    if flag in {"", "0", "false", "no"}:
        pytest.skip("skipped=true tokens=0")
    import time
    from dataclasses import replace

    from monthly_schema import MonthlyComparison, MonthlyComparisonChange
    from monthly_suggest import suggest_from_tokens
    from monthly_writer import loads, write_month
    from worker_llm import _ledger_path, usage_ledger_offset

    home = Path(__file__).resolve().parents[3]
    for name in ("config.yaml", ".env"):
        source = home / name
        if source.is_file():
            shutil.copy(source, tmp_path / name)
    _load_env_file(tmp_path / ".env")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    source_month = home / "memories" / "staging" / "monthly" / "2026-10.md"
    payload = loads(source_month.read_text(encoding="utf-8"))
    evidence = ("mem-2026-10-02-decision-A440FC874ADF",)
    payload = replace(
        payload,
        comparison_with_last_month=MonthlyComparison(
            unchanged=(
                MonthlyComparisonChange(
                    text="Goal alignment stays AI education and skill creation.",
                    evidence=evidence,
                ),
            ),
            changed=(
                MonthlyComparisonChange(
                    text="Decision preference now includes literal dating-file adds.",
                    evidence=evidence,
                ),
            ),
            suggestion="Keep the parent-message tone on the next month's messages.",
        ),
    )
    write_month(payload)
    offset = usage_ledger_offset()
    started = time.perf_counter()
    text = suggest_from_tokens(["2026-10"])
    latency_ms = int((time.perf_counter() - started) * 1000)
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
    ledger = _ledger_path()
    if ledger.is_file():
        with ledger.open(encoding="utf-8") as handle:
            handle.seek(offset)
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("purpose") != "monthly-suggest":
                    continue
                totals["input_tokens"] += int(rec.get("input_tokens") or 0)
                totals["output_tokens"] += int(rec.get("output_tokens") or 0)
                totals["total_tokens"] += int(rec.get("total_tokens") or 0)
                totals["cost_usd"] += float(rec.get("cost_usd") or 0.0)
    report = (
        f"{text}\n"
        f"input_tokens / output_tokens / total_tokens: "
        f"{totals['input_tokens']} / {totals['output_tokens']} / {totals['total_tokens']}\n"
        f"cost_usd: {totals['cost_usd']:.6f}\n"
        f"latency_ms: {latency_ms}"
    )
    print(report)
    assert "## Monthly suggest" in text
    assert "### comparison_unchanged" in text
    assert "### comparison_changed" in text
    assert "### comparison_suggestion" in text
    assert "suggested to add to:" not in text
    for line in text.splitlines():
        if line.startswith("- "):
            assert "#" not in line and "**" not in line and "→" in line
    assert totals["total_tokens"] > 0
