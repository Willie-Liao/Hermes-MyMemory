"""Live mimo-v2.5 eval: submit_weekly_thread over real 2026-W33 daily events."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

MYMEMORY = Path(__file__).resolve().parents[2]
HERMES_HOME = Path(__file__).resolve().parents[4]
os.environ.setdefault("HERMES_HOME", str(HERMES_HOME))
sys.path.insert(0, str(HERMES_HOME / "hermes-agent"))
sys.path.insert(0, str(MYMEMORY))
sys.path.insert(0, str(MYMEMORY / "weekly"))

try:
    from hermes_cli.env_loader import load_hermes_dotenv

    load_hermes_dotenv(hermes_home=str(HERMES_HOME))
except Exception:
    pass

from worker_llm import run_worker_llm_oneshot  # noqa: E402
from weekly_event_workers import _parse_blocks, _render_block  # noqa: E402
from weekly_tools import submit_weekly_thread_schema  # noqa: E402

DAILY = HERMES_HOME / "memories" / "staging" / "daily"


def _w33_days() -> list[date]:
    start = date.fromisocalendar(2026, 33, 1)
    return [start + timedelta(days=i) for i in range(7)]


def _event_cards() -> tuple[str, set[str]]:
    """Send only type:event fences so the oneshot is not truncated mid-tool-call.

    Full daily files blew the 2500-token completion cap (finish_reason=length).
    """
    allowed: set[str] = set()
    chunks: list[str] = []
    for day in _w33_days():
        path = DAILY / f"{day.isoformat()}.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        day_bits: list[str] = []
        for block in _parse_blocks(text):
            fm = block.get("frontmatter") or {}
            if str(fm.get("type") or "").strip().casefold() != "event":
                continue
            bid = str(fm.get("id") or "").strip()
            if "-event-" in bid:
                allowed.add(bid)
            body = str(block.get("body") or "")
            if len(body) > 400:
                block = dict(block)
                block["body"] = body[:400] + "\n…(truncated)…"
            day_bits.append(_render_block(block))
        if day_bits:
            chunks.append(f"# {path.name}\n\n" + "\n\n".join(day_bits))
    return "\n\n---\n\n".join(chunks), allowed


def main() -> int:
    bundle, allowed = _event_cards()
    prompt = (
        "Call submit_weekly_thread with cross-day-thread over these daily events.\n"
        "Each chain needs ≥2 distinct step dates and existing event_id values "
        "containing -event-. Seq 1 has no via. Later via evolves or invalidates.\n"
        "Do not invent ids. Do not emit wrap-ups, entities, or legend. "
        "Prefer one thread that includes 2026-08-12, 2026-08-13, and 2026-08-14 "
        "if those event ids exist. At most two threads.\n\n"
        f"DAILY EVENTS:\n{bundle}\n"
    )
    schema = submit_weekly_thread_schema()
    t0 = time.perf_counter()
    result = run_worker_llm_oneshot(
        prompt,
        plugin="memory-weekly",
        purpose="eval_weekly_thread_w33",
        force_tool_name="submit_weekly_thread",
        tool_schema=schema,
        max_tokens=6000,
        temperature=0.2,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    args = result.get("tool_args") or {}
    threads = args.get("cross-day-thread") if isinstance(args, dict) else None
    if not isinstance(threads, list):
        threads = []
    ids = []
    for item in threads:
        if not isinstance(item, dict):
            continue
        for step in item.get("steps") or []:
            if isinstance(step, dict):
                ids.append(str(step.get("event_id") or ""))
    hallucinated = [i for i in ids if i and i not in allowed]
    spanning = False
    for item in threads:
        dates = {
            str(s.get("date") or "")[:10]
            for s in (item.get("steps") or [])
            if isinstance(s, dict)
        }
        if {"2026-08-12", "2026-08-13", "2026-08-14"} <= dates or (
            "2026-08-12" in dates and "2026-08-14" in dates
        ):
            spanning = True
        elif len(dates) >= 2 and any(d.startswith("2026-08-1") for d in dates):
            spanning = spanning or (
                "2026-08-12" in dates and "2026-08-13" in dates
            )
    report = {
        "ok": bool(threads) and not hallucinated and spanning,
                    "model": result.get("model") or "mimo-v2.5",
                    "run_id": result.get("finish_reason"),
        "tool_name": result.get("tool_name"),
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "total_tokens": result.get("total_tokens"),
        "cache_read_tokens": result.get("cache_read_tokens"),
        "latency_ms": elapsed_ms,
        "thread_count": len(threads),
        "event_ids": ids,
        "hallucinated": hallucinated,
        "spanning_0812_0814": spanning,
        "allowed_sample": sorted(allowed)[:12],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
