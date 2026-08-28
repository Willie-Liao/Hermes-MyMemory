"""Map stage: one oneshot per batch, cached by source sha256."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

_monthly = Path(__file__).resolve().parent
_mymemory = _monthly.parent
for path in (_monthly, _mymemory, _mymemory / "weekly"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from monthly_schema import CAP_NOTE_ITEMS, NOTE_WORD_CAP, MonthlyNoteItem  # noqa: E402
from monthly_slice import Batch  # noqa: E402
from monthly_state import atomic_json_write, notes_dir  # noqa: E402
from monthly_tools import (  # noqa: E402
    merge_field_patch,
    patch_month_note_schema,
    submit_month_note_schema,
)

CallOneshot = Callable[..., dict[str, Any]]
MAX_ATTEMPTS = 3
MAP_SYSTEM = (
    "You select repeated reusable decision/preference and procedure patterns that matter for monthly guidance. "
    "Cite only ids from the user message. At most 6 items. Each `what` is under 40 words. "
    "Do not copy Beginning/Course/Outcome event prose."
)


def _default_oneshot(prompt: str, **kwargs: Any) -> dict[str, Any]:
    from worker_llm import run_worker_llm_oneshot

    return run_worker_llm_oneshot(
        prompt,
        plugin="memory-monthly",
        purpose=kwargs.get("purpose") or "monthly-map",
        force_tool_name=kwargs.get("force_tool_name"),
        tool_schema=kwargs.get("tool_schema"),
        max_tokens=int(kwargs.get("max_tokens") or 2048),
    )


def note_cache_path(month_key: str, batch_index: int) -> Path:
    return notes_dir() / f"{month_key}_b{batch_index}.json"


def load_cached_note(month_key: str, batch: Batch) -> dict[str, Any] | None:
    """Reuse a note whose source hash still matches so unchanged dailies cost zero map calls."""
    path = note_cache_path(month_key, batch.index)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("source_sha256") or "") != batch.source_sha256:
        return None
    return data


def _drop_unsourced(items: list[dict[str, Any]], allowed_ids: set[str]) -> list[dict[str, Any]]:
    """Delete synthesized items citing ids the model was never shown, because an
    unverifiable claim in a month file is worse than a missing one - recall will
    quote it for months without any way to check it.
    """
    kept: list[dict[str, Any]] = []
    for item in items:
        evidence = [
            str(eid)
            for eid in (item.get("evidence") or [])
            if str(eid) in allowed_ids
        ]
        if not evidence:
            continue
        what = str(item.get("what") or "").strip()
        if not what:
            continue
        if len(what.split()) > NOTE_WORD_CAP:
            continue
        kept.append(
            {
                "kind": str(item.get("kind") or "note"),
                "what": what,
                "why_it_mattered": str(item.get("why_it_mattered") or ""),
                "evidence": evidence,
            }
        )
        if len(kept) >= CAP_NOTE_ITEMS:
            break
    return kept


def notes_from_cache(data: dict[str, Any]) -> tuple[MonthlyNoteItem, ...]:
    items = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        items.append(
            MonthlyNoteItem(
                kind=str(raw.get("kind") or "note"),
                what=str(raw.get("what") or ""),
                why_it_mattered=str(raw.get("why_it_mattered") or ""),
                evidence=tuple(str(x) for x in (raw.get("evidence") or [])),
            )
        )
    return tuple(items)


def map_batch(
    month_key: str,
    batch: Batch,
    *,
    call_oneshot: CallOneshot | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Turn one packed batch into at most six notes; skip the LLM when the hash hits."""
    if not force_refresh:
        cached = load_cached_note(month_key, batch)
        if cached is not None:
            cached["cache_hit"] = True
            return cached
    prompt = f"{MAP_SYSTEM}\n\n---\nmonth: {month_key}\n{batch.rendered}"
    caller = call_oneshot or _default_oneshot
    previous: dict[str, Any] | None = None
    usage: dict[str, Any] = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        submit = attempt == 1
        schema = submit_month_note_schema() if submit else patch_month_note_schema()
        result = caller(
            prompt,
            purpose="monthly-map",
            force_tool_name=schema["name"],
            tool_schema=schema,
            max_tokens=2048,
        )
        usage = result
        if result.get("failed"):
            continue
        name = str(result.get("tool_name") or "")
        args = result.get("tool_args") if isinstance(result.get("tool_args"), dict) else {}
        if submit:
            previous = args
        else:
            if name == submit_month_note_schema()["name"]:
                continue
            previous = merge_field_patch(previous or {}, args)
        items = previous.get("items") if isinstance(previous, dict) else None
        if isinstance(items, list):
            break
    raw_items = previous.get("items") if isinstance(previous, dict) else None
    if not isinstance(raw_items, list):
        existing = note_cache_path(month_key, batch.index)
        if existing.is_file():
            try:
                return json.loads(existing.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "month_key": month_key,
            "batch_index": batch.index,
            "source_sha256": batch.source_sha256,
            "items": [],
            "cache_hit": False,
            "failed": True,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
            "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
            "model": str(usage.get("model") or ""),
        }
    cleaned = _drop_unsourced(raw_items, set(batch.ids))
    record = {
        "month_key": month_key,
        "batch_index": batch.index,
        "source_sha256": batch.source_sha256,
        "items": cleaned,
        "cache_hit": False,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
        "model": str(usage.get("model") or ""),
    }
    path = note_cache_path(month_key, batch.index)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, record)
    return record
