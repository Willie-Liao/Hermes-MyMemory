"""Opt-in before/after wall-clock for paint bands and Channel 4 scales.

Production recall_memory stays signature-stable; this module wraps public calls
and copies Band A–D loop boundaries from render_bands so baseline can run
before the embed ladder exists.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from recall.embed import _encode_texts, embed_enabled, rerank_embed
from recall.edges import load_edges
from recall.ids import BlockIndex, iso_week, staging_root
from recall.l1 import search_l1
from recall.normalize import load_entity_index
from recall.tools import (
    INDEX_TYPES,
    _wrapup_for_path,
    recall_memory,
    render_bands,
)

_HERE = Path(__file__).resolve().parent
_PLUGIN = _HERE.parent
_DEFAULT_STAGING = _PLUGIN.parent.parent / "memories" / "staging"
FTS_MISS_Q = "qzxnmprefersdeckstructure"
STORY_Q = "张主任 painting"
PAINT_TODAY = date(2026, 8, 18)
BEFORE_PATH = _HERE / "embed_scale_latency_before.json"
AFTER_PATH = _HERE / "embed_scale_latency_after.json"


def _llm_flag_on() -> bool:
    flag = os.environ.get("REAL_LLM_TEST", os.environ.get("PLAN_LOOP_LIVE_LLM", "1")).strip().lower()
    return flag not in {"0", "false", "no"}


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def time_band_paint(root: Path, today: date) -> dict[str, float]:
    """Copy render_bands A–D construction so paint cost is split without editing paint."""
    store = BlockIndex(root)
    day0 = today
    band_a_days = [(day0 - timedelta(days=i)) for i in range(0, 7)]
    daily = root / "daily"

    t0 = time.perf_counter()
    a_lines = ["## Memory / recent days (wrap-up)"]
    for d in band_a_days:
        path = daily / f"{d.isoformat()}.md"
        if not path.is_file():
            continue
        nblk = sum(1 for r in store.records if r.path == path)
        wrap = _wrapup_for_path(path)
        wrap_one = " ".join(wrap.split())
        if len(wrap_one) > 120:
            wrap_one = wrap_one[:119].rstrip() + "…"
        a_lines.append(f"- {d.isoformat()} {iso_week(d)} {nblk}blk :: {wrap_one}")
    band_a_ms = _ms(t0)

    t0 = time.perf_counter()
    idx = load_entity_index(root)
    window = {d.isoformat() for d in band_a_days}
    b_lines = ["## Memory / entity index (normalized, last 7 days)"]
    rows = []
    for key, node in idx.items():
        days = [x for x in (node.get("days") or []) if x in window]
        if not days:
            continue
        nblk = sum(
            1
            for mid in node.get("mem_ids") or []
            if store.get(mid) and store.get(mid).day in window
        )
        surfaces = [node.get("canonical") or key] + list(node.get("aliases") or [])
        surf = "|".join(dict.fromkeys(str(s) for s in surfaces if s))
        compact_days = ",".join(d[5:] if len(d) >= 10 else d for d in sorted(days))
        line = f"- {key} ({surf}) {nblk}blk d={compact_days}"
        mems = [
            mid
            for mid in (node.get("mem_ids") or [])
            if store.get(mid)
            and store.get(mid).day in window
            and store.get(mid).item_type in INDEX_TYPES
        ][:1]
        if nblk <= 3 and mems:
            line += "  " + " ".join(mems)
        rows.append((-nblk, key, line))
    rows.sort()
    b_lines.extend(r[2] for r in rows[:12])
    for edge in load_edges(root):
        if edge.get("type") != "related":
            continue
        frm = store.get(str(edge.get("from")))
        to = store.get(str(edge.get("to")))
        if not frm or not to or frm.day == to.day:
            continue
        if frm.day not in window and to.day not in window:
            continue
        b_lines.append(f"- {frm.block_id} -related-> {to.block_id}")
        if sum(1 for ln in b_lines if "-related->" in ln) >= 8:
            break
    band_b_ms = _ms(t0)

    t0 = time.perf_counter()
    weekly = root / "weekly"
    if weekly.is_dir():
        wdir = _PLUGIN / "weekly"
        if str(wdir) not in sys.path:
            sys.path.insert(0, str(wdir))
        try:
            from weekly_json import load_sidecar
        except Exception:
            load_sidecar = None
        week_paths = sorted(weekly.glob("*.md"))[-4:]
        for path in week_paths:
            if load_sidecar:
                try:
                    load_sidecar(path)
                except Exception:
                    pass
    band_c_ms = _ms(t0)

    t0 = time.perf_counter()
    mdir = _PLUGIN / "monthly"
    if str(mdir) not in sys.path:
        sys.path.insert(0, str(mdir))
    from monthly_actions import month_band

    month_band(limit=4, staging=root)
    band_d_ms = _ms(t0)
    del a_lines, b_lines
    return {
        "band_a_ms": band_a_ms,
        "band_b_ms": band_b_ms,
        "band_c_ms": band_c_ms,
        "band_d_ms": band_d_ms,
    }


def _time_scale_helpers(root: Path, query: str) -> dict[str, float | None]:
    """After the ladder lands, time month/week bags; before, they are null."""
    try:
        from recall.tools import collect_month_scale_rows, collect_week_scale_rows, rank_plain_passages
    except ImportError:
        return {"embed_month_ms": None, "embed_week_ms": None}
    t0 = time.perf_counter()
    rank_plain_passages(query, collect_month_scale_rows(root))
    month_ms = _ms(t0)
    t0 = time.perf_counter()
    rank_plain_passages(query, collect_week_scale_rows(root))
    week_ms = _ms(t0)
    return {"embed_month_ms": month_ms, "embed_week_ms": week_ms}


def _live_llm_oneshot(query: str, staging: Path) -> dict[str, Any]:
    if not _llm_flag_on():
        return {
            "llm_latency_ms": 0.0,
            "llm_input_tokens": 0,
            "llm_output_tokens": 0,
            "llm_total_tokens": 0,
            "skipped": True,
        }
    if str(_PLUGIN) not in sys.path:
        sys.path.insert(0, str(_PLUGIN))
    from recall.tools import TOOL_SCHEMAS
    from worker_llm import run_worker_llm_oneshot

    bands = render_bands(staging, today=PAINT_TODAY)
    schema = next(row for row in TOOL_SCHEMAS if row["name"] == "recall_memory")
    prompt = (
        f"{bands}\n\nCall recall_memory for this question (FTS likely misses): {query}"
    )
    started = time.perf_counter()
    try:
        capture = run_worker_llm_oneshot(
            prompt,
            plugin="MyMemory",
            purpose="embed-scale-latency",
            force_tool_name="recall_memory",
            tool_schema=schema,
            max_tokens=256,
        )
    except Exception as exc:
        return {
            "llm_latency_ms": _ms(started),
            "llm_input_tokens": 0,
            "llm_output_tokens": 0,
            "llm_total_tokens": 0,
            "skipped": False,
            "ok": False,
            "notes": str(exc),
        }
    return {
        "llm_latency_ms": _ms(started),
        "llm_input_tokens": int(capture.get("input_tokens") or 0),
        "llm_output_tokens": int(capture.get("output_tokens") or 0),
        "llm_total_tokens": int(capture.get("total_tokens") or 0),
        "skipped": False,
        "ok": not capture.get("failed"),
        "notes": str(capture.get("final_response") or "")[:200],
    }


def measure_embed_scale_latency(
    staging: Path | None = None,
    *,
    phase: str = "before",
) -> dict[str, Any]:
    """Warm GTE, then record the plan JSON keys for one fixed staging/query set."""
    root = staging_root(staging or _DEFAULT_STAGING)
    os.environ["MYMEMORY_EMBED_FORCE"] = "1"
    _encode_texts(["warmup embed scale latency"])
    paint = time_band_paint(root, PAINT_TODAY)
    store = BlockIndex(root)
    live = [rec for rec in store.records if str(rec.parsed.get("status") or "") != "rejected"]
    t0 = time.perf_counter()
    if embed_enabled(root):
        rerank_embed(FTS_MISS_Q, live, k=12)
    daily_ms = _ms(t0)
    t0 = time.perf_counter()
    search_l1(FTS_MISS_Q, k=5, home=root.parents[1] if root.name == "staging" else None)
    l1_ms = _ms(t0)
    t0 = time.perf_counter()
    recall_memory(FTS_MISS_Q, staging=root)
    e2e_ms = _ms(t0)
    scales = _time_scale_helpers(root, STORY_Q)
    if phase == "before":
        scales = {"embed_month_ms": None, "embed_week_ms": None}
    llm = _live_llm_oneshot(STORY_Q, root)
    out = {
        "phase": phase,
        "staging": str(root),
        "today": PAINT_TODAY.isoformat(),
        "fts_miss_query": FTS_MISS_Q,
        "story_query": STORY_Q,
        **paint,
        **scales,
        "embed_daily_ms": daily_ms,
        "l1_ms": l1_ms,
        "recall_e2e_ms": e2e_ms,
        **{k: llm[k] for k in (
            "llm_latency_ms",
            "llm_input_tokens",
            "llm_output_tokens",
            "llm_total_tokens",
            "skipped",
        )},
    }
    return out


def write_latency_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def test_embed_scale_latency_probe_writes_requested_phase(tmp_path, monkeypatch):
    """Cheap validator: JSON keys exist. Live GTE/LLM only when REAL_LLM_TEST is on."""
    from recall.conftest import write_fake_staging

    root = write_fake_staging(tmp_path)
    monkeypatch.setenv("MYMEMORY_EMBED_FORCE", "1")
    if not _llm_flag_on():
        payload = measure_embed_scale_latency(root, phase="before")
        for key in (
            "band_a_ms",
            "band_b_ms",
            "band_c_ms",
            "band_d_ms",
            "embed_daily_ms",
            "l1_ms",
            "recall_e2e_ms",
            "llm_latency_ms",
        ):
            assert key in payload
        assert payload["embed_month_ms"] is None
        return
    payload = measure_embed_scale_latency(root, phase="before")
    assert payload["band_a_ms"] >= 0


if __name__ == "__main__":
    phase = "after" if "--after" in sys.argv else "before"
    dest = AFTER_PATH if phase == "after" else BEFORE_PATH
    payload = measure_embed_scale_latency(phase=phase)
    write_latency_json(dest, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
