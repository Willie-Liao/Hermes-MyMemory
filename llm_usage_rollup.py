"""Daily rollup over Hermes worker LLM usage JSONL ledger.

Reads ``{HERMES_HOME}/metrics/llm-usage.jsonl`` (or ``ledger_path``) and
sums tokens / cost for one UTC calendar day, broken down by plugin and purpose.

CLI::

    python3 -m llm_usage_rollup          # from plugins/MyMemory, today UTC
    python3 -m llm_usage_rollup 2026-07-16
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from worker_llm import _ledger_path


def _bucket() -> dict[str, float | int]:
    return {"total_tokens": 0, "cost_usd": 0.0}


def _record_day(ts: str) -> str | None:
    """Extract YYYY-MM-DD (UTC) from an ISO timestamp; None if unparseable."""
    if not ts:
        return None
    try:
        # fromisoformat handles "+00:00"; Z needs a nudge
        raw = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def rollup_day(
    day: date | None = None,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Sum ledger rows for one UTC day.

    Returns keys: ``day``, ``total_tokens``, ``cost_usd``, ``by_plugin``,
    ``by_purpose``. Missing ledger → empty zeros (fail-open read).
    """
    target = (day or datetime.now(timezone.utc).date()).isoformat()
    path = Path(ledger_path) if ledger_path is not None else _ledger_path()

    total_tokens = 0
    cost_usd = 0.0
    by_plugin: dict[str, dict[str, float | int]] = {}
    by_purpose: dict[str, dict[str, float | int]] = {}

    if not path.is_file():
        return {
            "day": target,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "by_plugin": {},
            "by_purpose": {},
        }

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {
            "day": target,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "by_plugin": {},
            "by_purpose": {},
        }

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if _record_day(str(row.get("ts") or "")) != target:
            continue

        tokens = int(row.get("total_tokens") or 0)
        cost = float(row.get("cost_usd") or 0.0)
        plugin = str(row.get("plugin") or "") or "(unknown)"
        purpose = str(row.get("purpose") or "") or "(unknown)"

        total_tokens += tokens
        cost_usd += cost

        pb = by_plugin.setdefault(plugin, _bucket())
        pb["total_tokens"] = int(pb["total_tokens"]) + tokens
        pb["cost_usd"] = float(pb["cost_usd"]) + cost

        ub = by_purpose.setdefault(purpose, _bucket())
        ub["total_tokens"] = int(ub["total_tokens"]) + tokens
        ub["cost_usd"] = float(ub["cost_usd"]) + cost

    return {
        "day": target,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "by_plugin": by_plugin,
        "by_purpose": by_purpose,
    }


def rollup_range(
    start: date,
    end: date,
    ledger_path: Path | None = None,
    *,
    purposes: list[str] | None = None,
    plugins: list[str] | None = None,
    skip_zero_tokens: bool = True,
    skip_bench_purposes: bool = True,
) -> dict[str, Any]:
    """Sum ledger rows from ``start`` through ``end`` inclusive (UTC dates).

    Filters exist so first-run estimates are not polluted by weekly/monthly
    jobs, zero-token failures, or ``bench-*`` purposes mixed into production.
    """
    path = Path(ledger_path) if ledger_path is not None else _ledger_path()
    want_purposes = set(purposes) if purposes else None
    want_plugins = set(plugins) if plugins else None
    total_tokens = 0
    cost_usd = 0.0
    by_purpose: dict[str, dict[str, float | int]] = {}
    n = 0
    skipped = 0
    if not path.is_file():
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "total_tokens": 0,
            "cost_usd": 0.0,
            "by_purpose": {},
            "n": 0,
            "skipped": 0,
        }
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "total_tokens": 0,
            "cost_usd": 0.0,
            "by_purpose": {},
            "n": 0,
            "skipped": 0,
        }
    start_s, end_s = start.isoformat(), end.isoformat()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        day = _record_day(str(row.get("ts") or ""))
        if day is None or day < start_s or day > end_s:
            continue
        purpose = str(row.get("purpose") or "") or "(unknown)"
        plugin = str(row.get("plugin") or "") or "(unknown)"
        tokens = int(row.get("total_tokens") or 0)
        if skip_zero_tokens and tokens <= 0:
            skipped += 1
            continue
        if skip_bench_purposes and purpose.startswith("bench-"):
            skipped += 1
            continue
        if want_purposes is not None and purpose not in want_purposes:
            skipped += 1
            continue
        if want_plugins is not None and plugin not in want_plugins:
            skipped += 1
            continue
        cost = float(row.get("cost_usd") or 0.0)
        total_tokens += tokens
        cost_usd += cost
        n += 1
        ub = by_purpose.setdefault(purpose, _bucket())
        ub["total_tokens"] = int(ub["total_tokens"]) + tokens
        ub["cost_usd"] = float(ub["cost_usd"]) + cost
    return {
        "start": start_s,
        "end": end_s,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "by_purpose": by_purpose,
        "n": n,
        "skipped": skipped,
    }


def _print_summary(out: dict[str, Any]) -> None:
    print(f"LLM usage {out['day']}: {out['total_tokens']} tokens, ${out['cost_usd']:.6f}")
    plugins = out.get("by_plugin") or {}
    if not plugins:
        print("  (no rows)")
        return
    print("  by plugin:")
    for name in sorted(plugins):
        b = plugins[name]
        print(f"    {name}: {b['total_tokens']} tokens, ${float(b['cost_usd']):.6f}")
    purposes = out.get("by_purpose") or {}
    if purposes:
        print("  by purpose:")
        for name in sorted(purposes):
            b = purposes[name]
            print(f"    {name}: {b['total_tokens']} tokens, ${float(b['cost_usd']):.6f}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    day: date | None = None
    if args:
        try:
            day = date.fromisoformat(args[0])
        except ValueError:
            print(f"usage: python -m llm_usage_rollup [YYYY-MM-DD]", file=sys.stderr)
            return 2
    _print_summary(rollup_day(day))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
