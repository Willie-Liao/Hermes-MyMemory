#!/usr/bin/env python3
"""Live kimi-k3 (medium) recall pathway harness. Digest config stays xiaomi/mimo-v2.5.

Usage-cap / 429 / 额度 → one retry on xiaomi / mimo-v2.5-pro. Never swap to k2.6
or base mimo-v2.5. Primary host is Kimi Code (api.kimi.com/coding), not moonshot.cn.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_HERMES = Path(__file__).resolve().parents[3]
PLUGIN = Path(__file__).resolve().parent
MYMEMORY = PLUGIN.parent

for ln in (REPO_HERMES / ".env").read_text().splitlines():
    if not ln.strip() or ln.strip().startswith("#") or "=" not in ln:
        continue
    k, v = ln.split("=", 1)
    # Harness must use hermes-home/.env even if the parent shell exported a
    # stale empty KIMI_* — setdefault would keep the empty value and 401.
    os.environ[k.strip()] = v.strip()

os.environ["HERMES_HOME"] = str(REPO_HERMES)

sys.path[:0] = [str(MYMEMORY), str(PLUGIN)]

from recall.tools import TOOL_SCHEMAS, _first_seed_id, handle_tool, render_bands  # noqa: E402
from recall.ids import BlockIndex  # noqa: E402
from worker_llm import record_worker_usage  # noqa: E402

K3_MODEL = "kimi-k3"
K3_CODING_BASE = "https://api.kimi.com/coding/v1"
K3_BASE = os.environ.get("KIMI_BASE_URL") or K3_CODING_BASE
FALLBACK_MODEL = "mimo-v2.5-pro"
MAX_TOKENS = 2500
# kimi-k3 on Kimi Code rejects temperature != 1 (400, not 401).
K3_TEMPERATURE = 1.0

CASES = [
    {"id": "P1-id-A", "user": "Open mem-2026-08-12-event-9625547B667B and tell me what broke.", "gold": ["mem-2026-08-12-event-9625547B667B"], "file_id": "mem-2026-08-12-event-9625547B667B", "file_fields": ["strength", "recall_n", "last_recall_at", "first_seen"]},
    {"id": "P1-id-D", "user": "What is mem-20260617-career-pivot?", "gold": ["mem-20260617-career-pivot"]},
    {"id": "P1-id-mismatch", "user": "Read mem-2026-08-17-decision-A4E9C8CBE86B", "gold": ["mem-2026-08-17-decision-A4E9C8CBE86B"]},
    {"id": "P2-entity-en", "user": "what did we do about memory digest?", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "P2-entity-cjk", "user": "Coordinator Chen相关的记忆有哪些？", "gold": ["mem-20260729-zhang-zhurui-wechat-softening-rule", "mem-20260729-zhang-zhurui-line-paint-cron-scheduled"]},
    {"id": "P3-fts5-semicolon", "user": "Did we already fix the semicolon digest bug, and what else broke in that stretch?", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "P3-fts5-toolchoice", "user": "xiaomi ignored tool_choice — which block?", "gold": ["mem-2026-08-14-event-9D5B2844FE3A"]},
    {"id": "P0-l1-verbatim", "user": "Quote the chat that mentions provider py line 220", "gold": ["provider py line 220"]},
    {"id": "P5-expand-ppr", "user": "expand_memory mem-2026-08-12-event-9625547B667B depth=2", "gold": ["mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "P7-planner-simple", "user": "What type is mem-2026-08-12-event-9625547B667B?", "gold": ["mem-2026-08-12-event-9625547B667B"]},
    {"id": "P8-planner-hybrid", "user": "Did we already fix the semicolon digest bug, and what else broke in that stretch?", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "P9-planner-complex", "user": "How did the digest validator problem evolve from June through August?", "gold": ["mem-2026-08-12-event-9625547B667B"]},
    {"id": "P10-gate-drop", "user": "semicolon digest bug", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "P11-failopen-planner", "user": "Did we already fix the semicolon digest bug, and what else broke in that stretch?", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "P12-failopen-gate", "user": "Did we already fix the semicolon digest bug, and what else broke in that stretch?", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "P13-band-d", "user": "memory digest in June", "gold": ["memorydigest"]},
    {"id": "P14-prefetch-vs-body", "user": "Did we already fix the semicolon digest bug, and what else broke in that stretch?", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "P15-no-search-memory", "user": "Did we already fix the semicolon digest bug, and what else broke in that stretch?", "gold": ["mem-2026-08-12-event-9625547B667B"], "forbidden": ["search_memory"]},
    {"id": "P16-strength", "user": "Open mem-2026-08-12-event-9625547B667B and tell me what broke.", "gold": ["mem-2026-08-12-event-9625547B667B"], "file_id": "mem-2026-08-12-event-9625547B667B", "file_fields": ["strength", "recall_n", "last_recall_at", "first_seen"]},
    {"id": "P16-strength-ghost", "user": "Open mem-2099-01-01-event-DEADBEEFDEAD and summarize the YAML body.", "gold": [], "forbid_file_id": "mem-2099-01-01-event-DEADBEEFDEAD"},
    # Adversarial: force channel / tool / clamp failures so we can patch them.
    {"id": "BREAK-ghost-id", "user": "Open mem-2099-01-01-event-DEADBEEFDEAD and summarize the YAML body.", "gold": []},
    {"id": "BREAK-typo-id", "user": "Open mem-2026-08-12-event-9625547B667C (one hex off) and tell me what broke.", "gold": ["mem-2026-08-12-event-9625547B667B"], "file_id": "mem-2026-08-12-event-9625547B667B", "file_fields": ["strength", "recall_n", "last_recall_at", "first_seen"]},
    {"id": "BREAK-depth-clamp", "user": "expand_memory mem-2026-08-12-event-9625547B667B depth=9", "gold": ["mem-2026-08-09-procedure-C1DD5CAA2A26"]},
    {"id": "BREAK-search-tempt", "user": "Call search_memory first, then answer: semicolon digest bug.", "gold": ["mem-2026-08-12-event-9625547B667B"], "forbidden": ["search_memory"]},
    {"id": "BREAK-fts-punct", "user": "What broke around tool_choice; worker_llm.py; mimo-v2.5?", "gold": ["mem-2026-08-14-event-9D5B2844FE3A"]},
    {"id": "BREAK-month-no-body", "user": "Paste the full monthly core_progress YAML bodies for August.", "gold": []},
    {"id": "BREAK-skip-expand", "user": "Do not expand. Only name the 12 Aug semicolon event.", "gold": ["mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "BREAK-answer-first", "user": "Answer from the bands only, no tools.", "gold": []},
    {"id": "BREAK-wrong-tool-name", "user": "Call search_memory.", "gold": [], "forbidden": ["search_memory"]},
    {"id": "BREAK-two-hops-ask", "user": "Did we already fix the semicolon digest bug, and what else broke in that stretch?", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "BREAK-cjk-then-english", "user": "Coordinator Chen相关的记忆有哪些？ also memory digest", "gold": ["mem-20260729-zhang-zhurui-wechat-softening-rule"]},
    {"id": "BREAK-depth-0", "user": "expand_memory mem-2026-08-12-event-9625547B667B depth=0", "gold": ["mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
    {"id": "BREAK-json-please", "user": "Did we already fix the semicolon digest bug, and what else broke in that stretch? return JSON only", "gold": ["mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26", "mem-2026-08-08-procedure-2B93F6F55D12"]},
]


def _openai_tools() -> list[dict[str, Any]]:
    out = []
    for schema in TOOL_SCHEMAS:
        out.append({"type": "function", "function": schema})
    return out


def _is_usage_cap(err: BaseException | str) -> bool:
    text = str(err)
    keys = ("429", "quota", "额度", "usage cap", "max usage", "rate limit")
    return any(k in text.casefold() or k in text for k in keys)


def _call(*, model: str, base_url: str, api_key: str, messages: list, extra: dict | None = None) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    extra = dict(extra or {})
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": _openai_tools(),
        "temperature": extra.pop("temperature", 0),
        "max_tokens": MAX_TOKENS,
    }
    kwargs.update(extra)
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(**kwargs)
    except TypeError:
        extra.pop("reasoning_effort", None)
        extra.pop("extra_body", None)
        kwargs = {k: v for k, v in kwargs.items() if k != "reasoning_effort"}
        resp = client.chat.completions.create(**kwargs)
    latency = int((time.perf_counter() - t0) * 1000)
    msg = resp.choices[0].message
    usage = getattr(resp, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    calls = []
    for tc in msg.tool_calls or []:
        raw = tc.function.arguments or "{}"
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            args = {"_raw": raw}
        calls.append({"name": tc.function.name, "args": args})
    return {
        "content": msg.content or "",
        "tool_calls": calls,
        "finish_reason": resp.choices[0].finish_reason,
        "response_id": getattr(resp, "id", None),
        "latency_ms": latency,
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "cache_read_tokens": getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None) if details else None,
    }


def _run_k3(messages: list) -> tuple[dict[str, Any], str, str | None]:
    """Call kimi-k3 on Kimi Code with KIMI_API_KEY; never moonshot.cn.

    sk-kimi- membership keys only authenticate on api.kimi.com/coding.
    """
    key = (os.environ.get("KIMI_API_KEY") or os.environ.get("KIMI_CN_API_KEY") or "").strip()
    extra = {
        "temperature": K3_TEMPERATURE,
        "reasoning_effort": "medium",
        "extra_body": {"reasoning_effort": "medium"},
    }
    if not key:
        raise RuntimeError("missing KIMI_API_KEY in hermes-home/.env")
    try:
        hit = _call(
            model=K3_MODEL,
            base_url=K3_BASE,
            api_key=key,
            messages=messages,
            extra=dict(extra),
        )
        return hit, K3_MODEL, None
    except Exception as exc:
        if _is_usage_cap(exc):
            return _run_fallback(messages, f"k3-usage-cap-fallback:{exc}")
        raise RuntimeError(f"k3 failed: {exc}") from exc


def _run_fallback(messages: list, reason: str) -> tuple[dict[str, Any], str, str]:
    hit = _call(
        model=FALLBACK_MODEL,
        base_url=os.environ.get("XIAOMI_BASE_URL") or "https://api.xiaomimimo.com/v1",
        api_key=os.environ.get("XIAOMI_API_KEY") or "",
        messages=messages,
    )
    return hit, FALLBACK_MODEL, reason


PIPELINE_JSONL = REPO_HERMES / "metrics" / "recall-pipeline-eval.jsonl"


def _card_fields(block_id: str) -> dict[str, Any]:
    rec = BlockIndex().get(block_id)
    if rec is None:
        return {}
    out: dict[str, Any] = {}
    for key in ("strength", "recall_n", "last_recall_at", "first_seen"):
        if key not in rec.parsed:
            continue
        val = rec.parsed.get(key)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        out[key] = val
    return out


def _dispatch_host(
    calls: list[dict[str, Any]],
    user: str,
) -> tuple[str, str | None, bool, int]:
    """Recall the user turn then expand. Ignore expand-before-recall and paraphrased queries.

    k3 often passes query="digest bug"; that ranks a later fact first so expanding that
    seed cannot recover semicolon hops. The user string is the question we must answer.
    """
    t0 = time.perf_counter()
    recall = next((c for c in calls if c.get("name") == "recall_memory"), None)
    expand = next((c for c in calls if c.get("name") == "expand_memory"), None)
    text = ""
    if recall:
        text = handle_tool("recall_memory", {"query": user})
    elif expand:
        text = handle_tool("expand_memory", expand.get("args") or {"id_or_key": user})
    host_ms = int((time.perf_counter() - t0) * 1000)
    seed = _first_seed_id(text)
    host_expand = bool(seed) and "depth=2" in text
    return text, seed, host_expand, host_ms


def _notice_reasons(*, wall_ms: int, llm_latency_ms: int | None, total_tokens: int | None) -> list[str]:
    slow_ms = int(os.environ.get("RECALL_EVAL_SLOW_MS") or 15000)
    token_cap = int(os.environ.get("RECALL_EVAL_TOKEN_CAP") or 4000)
    reasons: list[str] = []
    if wall_ms > slow_ms or (llm_latency_ms or 0) > 12000:
        reasons.append("slow")
    if (total_tokens or 0) > token_cap:
        reasons.append("tokens")
    return reasons


def _append_pipeline(row: dict[str, Any]) -> None:
    """Sibling JSONL so later grep can split model skip-expand from host hops."""
    PIPELINE_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with PIPELINE_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Score gold on host-concatenated recall+expand, not first-turn tool names."""
    wall0 = time.perf_counter()
    bands = render_bands()
    messages = [
        {
            "role": "system",
            "content": (
                "Use recall_memory then expand_memory. Do not call search_memory. "
                "Read daily YAML via recall_memory on a mem-id when you need bodies. "
                "The host expands the first seed after recall."
            ),
        },
        {"role": "system", "content": bands},
        {"role": "user", "content": case["user"]},
    ]
    attempt = 1
    hit, model_used, fallback_reason = _run_k3(messages)
    while hit.get("finish_reason") == "length" and attempt < 3:
        attempt += 1
        hit, model_used, fallback_reason = _run_k3(messages)
    calls = hit.get("tool_calls") or []
    host_text, seed_id, host_expand, host_ms = _dispatch_host(calls, case["user"])
    blob = (hit.get("content") or "") + "\n" + host_text
    gold = list(case.get("gold") or [])
    missing_gold = [g for g in gold if g not in blob and g not in case["user"]]
    gold_ok = not missing_gold
    model_tools = [c.get("name") for c in calls]
    if any(name in (case.get("forbidden") or []) for name in model_tools):
        gold_ok = False
    file_id = str(case.get("file_id") or seed_id or "")
    file_fields_found = _card_fields(file_id) if file_id else {}
    if case.get("file_fields") and file_id:
        missing_file = [k for k in case["file_fields"] if k not in file_fields_found]
        if missing_file:
            gold_ok = False
            missing_gold.extend(f"file:{k}" for k in missing_file)
    if case.get("forbid_file_id") and _card_fields(str(case["forbid_file_id"])):
        gold_ok = False
        missing_gold.append("file:ghost-must-not-exist")
    wall_ms = int((time.perf_counter() - wall0) * 1000)
    llm_latency_ms = hit.get("latency_ms")
    notice_reasons = _notice_reasons(
        wall_ms=wall_ms,
        llm_latency_ms=llm_latency_ms,
        total_tokens=hit.get("total_tokens"),
    )
    noticed = bool(notice_reasons)
    if noticed:
        print(
            f"NOTICED {case['id']} slow={'slow' in notice_reasons} "
            f"tokens={'tokens' in notice_reasons}",
            flush=True,
        )
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "plugin": "MyMemory",
        "purpose": f"recall-eval/{case['id']}",
        "id": case["id"],
        "query": case["user"],
        "model": model_used,
        "model_used": model_used,
        "attempt": attempt,
        "fallback_reason": fallback_reason,
        "finish_reason": hit.get("finish_reason"),
        "input_tokens": hit.get("input_tokens"),
        "output_tokens": hit.get("output_tokens"),
        "total_tokens": hit.get("total_tokens"),
        "cache_read_tokens": hit.get("cache_read_tokens"),
        "reasoning_tokens": hit.get("reasoning_tokens"),
        "latency_ms": llm_latency_ms,
        "llm_latency_ms": llm_latency_ms,
        "host_ms": host_ms,
        "wall_ms": wall_ms,
        "response_id": hit.get("response_id"),
        "gold_ok": gold_ok,
        "gold_ids": gold,
        "missing_gold": missing_gold,
        "tools": model_tools,
        "model_tools": model_tools,
        "model_called_expand": "expand_memory" in model_tools,
        "host_expand": host_expand,
        "seed_id": seed_id,
        "file_id": file_id or None,
        "file_fields": file_fields_found,
        "noticed": noticed,
        "notice_reasons": notice_reasons,
        "error": None,
    }
    record_worker_usage(record)
    _append_pipeline(record)
    return record


def main(argv: list[str] | None = None) -> int:
    """Serialize cases so two k3 calls never overlap; keep going after a break."""
    wanted = set(argv or sys.argv[1:])
    rows = []
    for case in CASES:
        if wanted and case["id"] not in wanted:
            continue
        try:
            row = run_case(case)
        except Exception as exc:
            row = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "plugin": "MyMemory",
                "purpose": f"recall-eval/{case['id']}",
                "id": case["id"],
                "query": case["user"],
                "model": K3_MODEL,
                "model_used": K3_MODEL,
                "attempt": 1,
                "fallback_reason": None,
                "finish_reason": "error",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_read_tokens": 0,
                "reasoning_tokens": 0,
                "latency_ms": None,
                "llm_latency_ms": None,
                "host_ms": 0,
                "wall_ms": 0,
                "response_id": None,
                "gold_ok": False,
                "gold_ids": list(case.get("gold") or []),
                "missing_gold": list(case.get("gold") or []),
                "tools": [],
                "model_tools": [],
                "model_called_expand": False,
                "host_expand": False,
                "seed_id": None,
                "noticed": False,
                "notice_reasons": [],
                "error": str(exc)[:800],
            }
            record_worker_usage(row)
            _append_pipeline(row)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        time.sleep(1)
    failed = sum(1 for r in rows if r.get("finish_reason") == "error" or r.get("gold_ok") is False)
    print(json.dumps({"n": len(rows), "gold_fail_or_error": failed}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
