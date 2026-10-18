"""UI hot MEMORY/USER recall store (.memory-3-step-recall/{memory,user}.json).

Mirrors hermes-home/plugins/memory-weekly/ui/src/hotRecall.ts + hotRecallStore.ts.
On-disk shape: ``{ file, ui: { batches } }`` only — max 3 batches / 24h TTL.
Legacy ``chat`` keys are ignored on load and stripped on the next save.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

RECALL_MAX_BATCHES = 3
RECALL_TTL_SECONDS = 24 * 3600
RECALL_LIMIT_MESSAGE = "you can only recall last 3 actions!"
RECALL_DIR_NAME = ".memory-3-step-recall"
CHAT_SOURCE_REMOVED_MESSAGE = "chat recall stack removed; use source='ui' only"

RecallSource = Literal["ui", "chat"]
_ALLOWED_SOURCES = frozenset({"ui"})
_ALLOWED_FILES = frozenset({"MEMORY.md", "USER.md"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _recall_stem(file: str) -> str:
    if file == "MEMORY.md":
        return "memory"
    if file == "USER.md":
        return "user"
    raise ValueError(f"unsupported hot recall file: {file!r}")


def resolve_recall_path(hermes_home: Path, file: str) -> Path:
    return (
        Path(hermes_home)
        / "memories"
        / "staging"
        / RECALL_DIR_NAME
        / f"{_recall_stem(file)}.json"
    )


def _parse_saved_at(saved_at: str) -> datetime | None:
    text = (saved_at or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def prune_expired_batches(
    batches: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    cutoff = (now or _now()).timestamp() - RECALL_TTL_SECONDS
    kept: list[dict[str, Any]] = []
    for batch in batches:
        saved = _parse_saved_at(str(batch.get("savedAt") or ""))
        if saved is None:
            continue
        if saved.timestamp() >= cutoff:
            kept.append(batch)
    return kept


def _is_edit(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    index = value.get("index")
    return isinstance(index, int) and not isinstance(index, bool) and isinstance(value.get("before"), str)


def _is_delete(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    index = value.get("index")
    return isinstance(index, int) and not isinstance(index, bool) and isinstance(value.get("text"), str)


def _is_batch(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("savedAt"), str)
        and isinstance(value.get("edits"), list)
        and all(_is_edit(e) for e in value["edits"])
        and isinstance(value.get("deletes"), list)
        and all(_is_delete(d) for d in value["deletes"])
    )


def _normalize_source_batches(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    batches = raw.get("batches")
    if not isinstance(batches, list):
        return []
    return [b for b in batches if _is_batch(b)]


def _empty_store(file: str) -> dict[str, Any]:
    return {"file": file, "ui": {"batches": []}}


def _normalize_store(file: str, parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict) or isinstance(parsed, list):
        return _empty_store(file)
    store = _empty_store(file)
    if "ui" in parsed or "chat" in parsed:
        store["ui"] = {"batches": _normalize_source_batches(parsed.get("ui"))}
        return store
    # Reject legacy flat {"file","batches"} — fresh start, no migrate
    return store


def _load_store(hermes_home: Path, file: str) -> dict[str, Any]:
    path = resolve_recall_path(hermes_home, file)
    store = _empty_store(file)
    parsed: Any = None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        store = _normalize_store(file, parsed)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return store

    rewritten = False
    if isinstance(parsed, dict) and "chat" in parsed:
        rewritten = True
    batches = store["ui"]["batches"]
    pruned = prune_expired_batches(batches)
    if len(pruned) != len(batches):
        store["ui"]["batches"] = pruned
        rewritten = True
    if rewritten:
        _save_store(hermes_home, store)
    return store


def _save_store(hermes_home: Path, store: dict[str, Any]) -> None:
    file = store.get("file")
    if file not in _ALLOWED_FILES:
        return
    path = resolve_recall_path(hermes_home, file)
    ui_batches = prune_expired_batches(list((store.get("ui") or {}).get("batches") or []))[
        -RECALL_MAX_BATCHES:
    ]

    if not ui_batches:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "file": file,
        "ui": {"batches": ui_batches},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _require_source(source: str) -> str | None:
    if source == "chat":
        return CHAT_SOURCE_REMOVED_MESSAGE
    if source not in _ALLOWED_SOURCES:
        return f"unsupported recall source: {source!r}"
    return None


def list_hot_recall(hermes_home: Path, file: str, *, source: RecallSource) -> list[dict]:
    err = _require_source(source)
    if err:
        return []
    return list(_load_store(hermes_home, file)["ui"]["batches"])


def push_hot_recall(
    hermes_home: Path,
    file: str,
    *,
    source: RecallSource,
    edits: list[dict] | None = None,
    deletes: list[dict] | None = None,
) -> dict:
    if file not in _ALLOWED_FILES:
        return {"ok": False, "error": f"unsupported hot recall file: {file!r}"}
    src_err = _require_source(source)
    if src_err:
        return {"ok": False, "error": src_err}

    edit_list = list(edits or [])
    delete_list = list(deletes or [])
    if not edit_list and not delete_list:
        return {"ok": False, "error": "empty recall batch"}

    for edit in edit_list:
        if not _is_edit(edit):
            return {"ok": False, "error": "invalid edit entry"}
    for delete in delete_list:
        if not _is_delete(delete):
            return {"ok": False, "error": "invalid delete entry"}

    store = _load_store(hermes_home, file)
    batch = {
        "savedAt": _now().isoformat().replace("+00:00", "Z"),
        "edits": [{"index": e["index"], "before": e["before"]} for e in edit_list],
        "deletes": [{"index": d["index"], "text": d["text"]} for d in delete_list],
    }
    batches = prune_expired_batches(store["ui"]["batches"]) + [batch]
    batches = batches[-RECALL_MAX_BATCHES:]
    store["ui"]["batches"] = batches
    _save_store(hermes_home, store)
    return {"ok": True}


def pop_hot_recall(hermes_home: Path, file: str, *, source: RecallSource) -> dict:
    if file not in _ALLOWED_FILES:
        return {"ok": False, "error": f"unsupported hot recall file: {file!r}"}
    src_err = _require_source(source)
    if src_err:
        return {"ok": False, "error": src_err}

    store = _load_store(hermes_home, file)
    batches = list(store["ui"]["batches"])
    if not batches:
        return {"ok": False, "error": RECALL_LIMIT_MESSAGE}

    batch = batches[-1]
    store["ui"]["batches"] = batches[:-1]
    _save_store(hermes_home, store)
    return {"ok": True, "batch": batch}


def apply_recall_batch(entries: list[str], batch: dict) -> list[str]:
    """Restore entries: apply edits first, then deletes highest-index-first (TS parity)."""
    next_entries = list(entries)

    for edit in batch.get("edits") or []:
        if not isinstance(edit, dict):
            continue
        index = edit.get("index")
        before = edit.get("before")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            continue
        if not isinstance(before, str):
            continue
        idx = min(index, len(next_entries))
        if idx >= len(next_entries):
            next_entries.append(before)
        else:
            next_entries[idx] = before

    deletes = [d for d in (batch.get("deletes") or []) if isinstance(d, dict)]
    deletes.sort(key=lambda d: d.get("index", -1), reverse=True)
    for delete in deletes:
        index = delete.get("index")
        text = delete.get("text")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            continue
        if not isinstance(text, str):
            continue
        idx = min(index, len(next_entries))
        next_entries.insert(idx, text)

    return next_entries
