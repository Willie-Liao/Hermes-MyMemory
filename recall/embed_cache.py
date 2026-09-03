"""Pre-computed embedding cache for Channel 4 recall.

Scans daily YAML cards plus monthly/weekly sidecar sentences, computes per-block
content hash, and caches GTE embeddings. Incremental update runs on the same
01:00 civil tick as daily cards (not 03:00).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("plugins.memory-embed-cache")

_CACHE_DIR_NAME = "embeddings"
_CACHE_FILE = "embed-cache.json"
_CACHE_META = "embed-cache-meta.json"
_BUILD_LOCK = "embed-cache.lock"
EMBED_TICK = (1, 0)
_DEFAULT_TZ_NAME = "Asia/Shanghai"

# Fields to include in content hash (semantic fields only)
_HASH_FIELDS = ("id", "type", "entity", "predicate", "confidence", "status", "related", "body")
_SCALE_HASH_FIELDS = (
    "scale",
    "kind",
    "weeks",
    "weekdays",
    "start",
    "mem_id",
    "week_key",
    "month_key",
)


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except Exception:
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val).resolve() if val else Path.home() / ".hermes"


def _staging_root() -> Path:
    return _hermes_home() / "memories" / "staging"


def _cache_root() -> Path:
    return _hermes_home() / "memories" / _CACHE_DIR_NAME


def _embed_cache_tz() -> ZoneInfo:
    """Civil zone for the 01:00 retrieval-cache check: hermes_time, then config.yaml timezone.

    A bad IANA name must not take down the gateway. Digest is not imported.
    """
    try:
        from hermes_time import get_timezone

        resolved = get_timezone()
        if resolved is not None:
            return resolved
    except Exception:
        pass
    try:
        import yaml

        path = _hermes_home() / "config.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        name = str(data.get("timezone") or "").strip()
        if name:
            return ZoneInfo(name)
    except (ZoneInfoNotFoundError, OSError, TypeError, Exception):
        pass
    try:
        return ZoneInfo(_DEFAULT_TZ_NAME)
    except (ZoneInfoNotFoundError, Exception):
        return ZoneInfo("Etc/UTC")


def _as_local(now: datetime, tz: ZoneInfo) -> datetime:
    """Attach or convert ``now`` into ``tz`` so 01:00 math never mixes naive UTC."""
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _next_embed_check_at(now: datetime, tz: ZoneInfo) -> datetime:
    """Next 01:00 in ``tz`` strictly after ``now`` so a 01:05 start waits until tomorrow."""
    local = _as_local(now, tz)
    today_tick = local.replace(
        hour=EMBED_TICK[0], minute=EMBED_TICK[1], second=0, microsecond=0
    )
    if local < today_tick:
        return today_tick
    return (local + timedelta(days=1)).replace(
        hour=EMBED_TICK[0], minute=EMBED_TICK[1], second=0, microsecond=0
    )


def _load_meta() -> dict[str, Any]:
    """Read embed-cache-meta.json so last_checked_on survives a no-op hash scan."""
    path = _cache_root() / _CACHE_META
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_blocks_from_file(path: Path) -> list[dict[str, Any]]:
    """Parse all blocks from a staging .md file."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []
    
    blocks = []
    # Split by --- separators
    sections = content.split("\n---\n")
    i = 0
    while i < len(sections):
        sec = sections[i].strip()
        if sec.startswith("id:"):
            # This is frontmatter; body is next section if it doesn't start with id:
            body = ""
            if i + 1 < len(sections):
                nxt = sections[i + 1].strip()
                if nxt and not nxt.startswith("id:") and not nxt.startswith("type:"):
                    body = nxt
                    i += 2
                else:
                    i += 1
            else:
                i += 1
            
            # Parse YAML frontmatter
            try:
                import yaml
                parsed = yaml.safe_load(sec)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            
            block_id = str(parsed.get("id", "")).strip()
            if not block_id:
                continue
            
            blocks.append({
                "id": block_id,
                "type": str(parsed.get("type", "")).strip(),
                "entity": str(parsed.get("entity", "")).strip(),
                "predicate": str(parsed.get("predicate", "")).strip(),
                "confidence": str(parsed.get("confidence", "")).strip(),
                "status": str(parsed.get("status", "")).strip(),
                "related": parsed.get("related", []),
                "body": body.strip()[:500],
                "source_file": str(path.relative_to(_staging_root())),
            })
        else:
            i += 1
    return blocks


def _compute_hash(block: dict[str, Any]) -> str:
    """Compute content hash from semantic fields."""
    parts = []
    fields = _HASH_FIELDS + (_SCALE_HASH_FIELDS if block.get("scale") else ())
    for field in fields:
        val = block.get(field, "")
        if field in {"related", "weeks", "weekdays", "evidence"} and isinstance(val, (list, tuple)):
            val = ",".join(str(v) for v in val)
        parts.append(f"{field}={val}")
    text = "\n".join(parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def scale_block_id(row: Mapping[str, Any] | dict[str, Any]) -> str:
    """Stable cache id so 01:00 re-embeds a sidecar sentence when its locator or text changes."""
    payload = {
        "scale": str(row.get("scale") or ("week" if row.get("week_key") else "month")),
        "kind": str(row.get("kind") or ""),
        "text": str(row.get("text") or row.get("body") or ""),
        "weeks": [str(w) for w in (row.get("weeks") or ())],
        "week_key": str(row.get("week_key") or ""),
        "month_key": str(row.get("month_key") or ""),
        "mem_id": str(row.get("mem_id") or ""),
        "start": str(row.get("start") or ""),
        "weekdays": [str(n) for n in (row.get("weekdays") or ())],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "emb-scale:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _compose_embedding_text(block: dict[str, Any]) -> str:
    """Daily cards keep entity/type/predicate; sidecar scale rows embed the sentence only."""
    body = str(block.get("body") or "").strip()
    if block.get("scale"):
        return body
    parts = []
    entity = block.get("entity", "")
    if entity:
        parts.append(f"[{entity}]")
    btype = block.get("type", "")
    if btype:
        parts.append(f"({btype})")
    predicate = block.get("predicate", "")
    if predicate:
        parts.append(predicate)
    if body:
        parts.append(body)
    return " ".join(parts)


def _sidecar_scale_blocks() -> dict[str, dict[str, Any]]:
    """Month/week sidecar sentences share the daily 01:00 incremental_update pass."""
    try:
        from .tools import collect_month_scale_rows, collect_week_scale_rows
    except Exception:
        return {}
    root = _staging_root()
    out: dict[str, dict[str, Any]] = {}
    for row in collect_month_scale_rows(root):
        item = dict(row)
        item.setdefault("scale", "month")
        bid = scale_block_id(item)
        out[bid] = {
            "id": bid,
            "type": "",
            "entity": "",
            "predicate": "",
            "body": str(item.get("text") or ""),
            "scale": "month",
            "kind": item.get("kind") or "",
            "weeks": list(item.get("weeks") or ()),
            "mem_id": str(item.get("mem_id") or ""),
            "month_key": str(item.get("month_key") or ""),
            "source_file": f"monthly/{item.get('month_key') or ''}.md",
        }
    for row in collect_week_scale_rows(root):
        item = dict(row)
        item.setdefault("scale", "week")
        bid = scale_block_id(item)
        out[bid] = {
            "id": bid,
            "type": "",
            "entity": "",
            "predicate": "",
            "body": str(item.get("text") or ""),
            "scale": "week",
            "kind": item.get("kind") or "",
            "week_key": str(item.get("week_key") or ""),
            "start": str(item.get("start") or ""),
            "weekdays": list(item.get("weekdays") or ()),
            "source_file": f"weekly/{item.get('week_key') or ''}.md",
        }
    return out


def scan_all_blocks() -> dict[str, dict[str, Any]]:
    """Scan daily YAML cards plus monthly/weekly sidecar sentences for the 01:00 cache."""
    root = _staging_root()
    all_blocks: dict[str, dict[str, Any]] = {}

    daily_dir = root / "daily"
    if daily_dir.is_dir():
        for f in sorted(daily_dir.glob("*.md")):
            for block in _parse_blocks_from_file(f):
                all_blocks[block["id"]] = block

    weekly_dir = root / "weekly"
    if weekly_dir.is_dir():
        for f in sorted(weekly_dir.glob("*.md")):
            for block in _parse_blocks_from_file(f):
                all_blocks[block["id"]] = block

    all_blocks.update(_sidecar_scale_blocks())
    return all_blocks


_cache_lock = threading.Lock()
_embed_clock_stop = threading.Event()
_embed_clock_thread: threading.Thread | None = None


def load_cache() -> dict[str, dict[str, Any]]:
    """Load the embedding cache from disk."""
    cache_path = _cache_root() / _CACHE_FILE
    if not cache_path.is_file():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return data.get("blocks", {})
    except Exception as exc:
        logger.warning("embed cache load failed: %s", exc)
        return {}


def save_cache(blocks: dict[str, dict[str, Any]], meta: dict[str, Any] | None = None) -> None:
    """Save the embedding cache to disk."""
    cache_dir = _cache_root()
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    cache_path = cache_dir / _CACHE_FILE
    data = {"blocks": blocks}
    cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    
    if meta:
        meta_path = cache_dir / _CACHE_META
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def incremental_update(
    encode_fn=None,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Scan files, compare hashes, re-embed changed blocks, save cache.

    Clock callers pass civil ``now`` so Channel 4 stays off the query path:
    skip before 01:00 in the configured zone, and at most once per civil day.
    ``now is None`` is a full hash pass for tests and a manual rebuild.
    """
    from .embed import _encode_texts

    if encode_fn is None:
        encode_fn = _encode_texts

    stats: dict[str, Any] = {
        "total": 0,
        "embedded": 0,
        "skipped": 0,
        "removed": 0,
    }
    stamp_day: str | None = None
    if now is not None:
        tz = _embed_cache_tz()
        local = _as_local(now, tz)
        stamp_day = local.date().isoformat()
        if (local.hour, local.minute) < EMBED_TICK:
            stats["outcome"] = "idle"
            return stats
        if _load_meta().get("last_checked_on") == stamp_day:
            stats["outcome"] = "idle"
            return stats

    all_blocks = scan_all_blocks()
    cache = load_cache() if not force else {}

    to_embed: list[dict[str, Any]] = []
    for block_id, block in all_blocks.items():
        current_hash = _compute_hash(block)
        cached = cache.get(block_id)
        if cached and cached.get("hash") == current_hash and not force:
            continue
        to_embed.append(block)

    removed_ids = set(cache.keys()) - set(all_blocks.keys())

    stats = {
        "total": len(all_blocks),
        "embedded": len(to_embed),
        "skipped": len(all_blocks) - len(to_embed),
        "removed": len(removed_ids),
    }

    if not to_embed and not removed_ids:
        logger.info("embed cache: no changes, skip update")
        if stamp_day is not None:
            _stamp_last_checked(load_cache(), stamp_day)
        return stats
    
    # Encode new/changed blocks
    new_embeddings: dict[str, dict[str, Any]] = {}
    if to_embed:
        texts = [_compose_embedding_text(b) for b in to_embed]
        try:
            vectors = encode_fn(texts)
        except Exception as exc:
            logger.error("embed cache encode failed: %s", exc)
            return stats
        
        if len(vectors) == len(to_embed):
            for block, vec in zip(to_embed, vectors):
                block_id = block["id"]
                new_embeddings[block_id] = {
                    "hash": _compute_hash(block),
                    "embedding": vec,
                    "source_file": block.get("source_file", ""),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
            stats["embedded"] = len(new_embeddings)
    
    # Merge: keep unchanged from cache, add new, remove deleted
    with _cache_lock:
        merged: dict[str, dict[str, Any]] = {}
        for block_id, block in all_blocks.items():
            if block_id in new_embeddings:
                merged[block_id] = new_embeddings[block_id]
            elif block_id in cache:
                merged[block_id] = cache[block_id]
        
        meta = {
            "version": 1,
            "model": "gte-multilingual-base",
            "dim": 768,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "total_blocks": len(merged),
        }
        if stamp_day is not None:
            meta["last_checked_on"] = stamp_day
        save_cache(merged, meta)
    
    logger.info(
        "embed cache updated: total=%d embedded=%d skipped=%d removed=%d",
        stats["total"], stats["embedded"], stats["skipped"], stats["removed"],
    )
    return stats


def _stamp_last_checked(blocks: dict[str, dict[str, Any]], stamp_day: str) -> None:
    """Persist last_checked_on after a no-op scan so the 01:00 clock does not re-encode."""
    meta = dict(_load_meta())
    meta["last_checked_on"] = stamp_day
    meta.setdefault("version", 1)
    meta.setdefault("model", "gte-multilingual-base")
    meta.setdefault("dim", 768)
    meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta["total_blocks"] = len(blocks)
    save_cache(blocks, meta)


def start_embed_cache_clock_thread() -> None:
    """Arm the retrieval-cache 01:00 sleeper without sharing the digest clock.

    Pytest must not catch-up-encode live staging from unrelated initialize tests.
    """
    global _embed_clock_thread
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if _embed_clock_thread is not None and _embed_clock_thread.is_alive():
        return
    _embed_clock_stop.clear()
    _embed_clock_thread = threading.Thread(
        target=_embed_cache_clock_loop,
        name="memory-embed-cache-clock",
        daemon=True,
    )
    _embed_clock_thread.start()


def stop_embed_cache_clock_thread() -> None:
    """Pytest-only stop so production daemons keep ticking."""
    global _embed_clock_thread
    _embed_clock_stop.set()
    thread = _embed_clock_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    _embed_clock_thread = None


def _embed_cache_clock_loop() -> None:
    """Sleep until the next configured-zone 01:00; catch up if Hermes started later."""
    while not _embed_clock_stop.is_set():
        try:
            tz = _embed_cache_tz()
            local = datetime.now(tz)
            today = local.date().isoformat()
            if (local.hour, local.minute) >= EMBED_TICK and _load_meta().get(
                "last_checked_on"
            ) != today:
                incremental_update(now=local)
            local = datetime.now(tz)
            nxt = _next_embed_check_at(local, tz)
            delay = max(0.0, (nxt - local).total_seconds())
            if _embed_clock_stop.wait(delay):
                break
        except Exception as exc:
            logger.warning("embed cache clock: %s", exc)
            if _embed_clock_stop.wait(60.0):
                break


def search_cache(
    query_vector: list[float],
    k: int = 10,
    cosine_floor: float = 0.30,
) -> list[tuple[str, float, dict[str, Any]]]:
    """Search cached embeddings by cosine similarity.
    
    Returns: list of (block_id, cosine_score, cache_entry) sorted by score desc.
    """
    cache = load_cache()
    if not cache:
        return []
    
    qv = query_vector
    n = len(qv)
    if n == 0:
        return []
    
    # Precompute query norm
    qnorm = sum(float(qv[i]) ** 2 for i in range(n)) ** 0.5
    if qnorm == 0.0:
        return []
    
    results: list[tuple[str, float, dict[str, Any]]] = []
    for block_id, entry in cache.items():
        vec = entry.get("embedding", [])
        if len(vec) < n:
            continue
        # Cosine similarity
        dot = sum(float(qv[i]) * float(vec[i]) for i in range(n))
        vnorm = sum(float(vec[i]) ** 2 for i in range(n)) ** 0.5
        if vnorm == 0.0:
            continue
        score = dot / (qnorm * vnorm)
        if score >= cosine_floor:
            results.append((block_id, score, entry))
    
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:k]
