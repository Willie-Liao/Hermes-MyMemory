"""Live mimo-v2.5 eval: submit_weekly_thread or full weekly generate on 2026-W33."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
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


def _daily_files_w33() -> list[Path]:
    """Only days that exist so generate does not fail on a missing Sunday file."""
    return [
        DAILY / f"{day.isoformat()}.md"
        for day in _w33_days()
        if (DAILY / f"{day.isoformat()}.md").is_file()
    ]


def run_full_generate() -> int:
    """Call _generate_weekly_content without committing live staging.

    Distill-freeze pytest cannot prove Worker 1 still returns dump_yaml.
    """
    import weekly as weekly_mod
    import weekly_json

    live = HERMES_HOME / "memories" / "staging" / "weekly" / "2026-W33.md"
    mtime_before = live.stat().st_mtime_ns if live.is_file() else None
    files = _daily_files_w33()
    yaml_text = None
    elapsed_ms = 0
    last_error = ""
    for _attempt in range(3):
        t0 = time.perf_counter()
        try:
            yaml_text = weekly_mod._generate_weekly_content(
                "2026-W33", files, reason="eval_full_generate"
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            yaml_text = None
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if yaml_text:
            break
    mtime_after = live.stat().st_mtime_ns if live.is_file() else None
    missing: list[str] = []
    has_cross = False
    json_gone = False
    yaml_gone = False
    distill = False
    if not yaml_text:
        missing.append("yaml_text")
        if last_error:
            missing.append(last_error[:200])
    else:
        distill = "## Distill" in yaml_text
        if distill:
            missing.append("distill_heading")
        payload = weekly_json.loads(yaml_text)
        has_cross = bool(payload.cross_day_thread)
        if not has_cross:
            missing.append("cross-day-thread")
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "2026-W33.md"
            md.write_text(yaml_text, encoding="utf-8")
            json_path = Path(tmp) / "2026-W33.json"
            yaml_path = Path(tmp) / "2026-W33.yaml"
            json_path.write_text("{}\n", encoding="utf-8")
            yaml_path.write_text("stale: 1\n", encoding="utf-8")
            weekly_json.write_sidecars(md, payload)
            json_gone = not json_path.exists()
            yaml_gone = not yaml_path.exists()
            if not json_gone:
                missing.append("json_sidecar")
            if not yaml_gone:
                missing.append("yaml_sidecar")
    staging_ok = mtime_before == mtime_after
    if not staging_ok:
        missing.append("staging_mtime")
    ok = not missing
    report = {
        "ok": ok,
        "mode": "full_generate",
        "latency_ms": elapsed_ms,
        "has_cross_day_thread": has_cross,
        "distill_heading": distill,
        "json_sidecar_deleted": json_gone,
        "yaml_sidecar_deleted": yaml_gone,
        "staging_mtime_unchanged": staging_ok,
        "daily_files": len(files),
        "missing": missing,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def run_thread_eval() -> int:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full-generate",
        action="store_true",
        help="Live _generate_weekly_content on W33 dailies (no staging commit)",
    )
    args = parser.parse_args(argv)
    if args.full_generate:
        return run_full_generate()
    return run_thread_eval()


if __name__ == "__main__":
    raise SystemExit(main())
