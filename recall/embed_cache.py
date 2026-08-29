"""Pre-computed embedding cache for Channel 4 recall.

Scans all daily/weekly staging files, computes per-block content hash,
and caches GTE embeddings. On incremental update, only re-embeds changed blocks.

Cache lives at ~/.hermes/memories/embeddings/embed-cache.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("plugins.memory-embed-cache")

_CACHE_DIR_NAME = "embeddings"
_CACHE_FILE = "embed-cache.json"
_CACHE_META = "embed-cache-meta.json"
_BUILD_LOCK = "embed-cache.lock"

# Fields to include in content hash (semantic fields only)
_HASH_FIELDS = ("id", "type", "entity", "predicate", "confidence", "status", "related", "body")


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
    for field in _HASH_FIELDS:
        val = block.get(field, "")
        if field == "related" and isinstance(val, list):
            val = ",".join(str(v) for v in sorted(val))
        parts.append(f"{field}={val}")
    text = "\n".join(parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _compose_embedding_text(block: dict[str, Any]) -> str:
    """Compose text for embedding: [entity] (type) predicate body"""
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
    body = block.get("body", "")
    if body:
        parts.append(body.strip())
    return " ".join(parts)


def scan_all_blocks() -> dict[str, dict[str, Any]]:
    """Scan all daily and weekly staging files, return {block_id: block_dict}."""
    root = _staging_root()
    all_blocks: dict[str, dict[str, Any]] = {}
    
    # Scan daily files
    daily_dir = root / "daily"
    if daily_dir.is_dir():
        for f in sorted(daily_dir.glob("*.md")):
            for block in _parse_blocks_from_file(f):
                all_blocks[block["id"]] = block
    
    # Scan weekly files
    weekly_dir = root / "weekly"
    if weekly_dir.is_dir():
        for f in sorted(weekly_dir.glob("*.md")):
            for block in _parse_blocks_from_file(f):
                all_blocks[block["id"]] = block
    
    return all_blocks


_cache_lock = threading.Lock()


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
) -> dict[str, Any]:
    """Scan files, compare hashes, re-embed changed blocks, save cache.
    
    Args:
        encode_fn: callable(texts: list[str]) -> list[list[float]]
        force: if True, re-embed all blocks regardless of hash
    
    Returns:
        {"total": N, "embedded": M, "skipped": S, "removed": R}
    """
    from .embed import _encode_texts
    
    if encode_fn is None:
        encode_fn = _encode_texts
    
    all_blocks = scan_all_blocks()
    cache = load_cache() if not force else {}
    
    # Find blocks to embed (changed or new)
    to_embed: list[dict[str, Any]] = []
    for block_id, block in all_blocks.items():
        current_hash = _compute_hash(block)
        cached = cache.get(block_id)
        if cached and cached.get("hash") == current_hash and not force:
            continue  # unchanged
        to_embed.append(block)
    
    # Find removed blocks
    removed_ids = set(cache.keys()) - set(all_blocks.keys())
    
    stats = {
        "total": len(all_blocks),
        "embedded": len(to_embed),
        "skipped": len(all_blocks) - len(to_embed),
        "removed": len(removed_ids),
    }
    
    if not to_embed and not removed_ids:
        logger.info("embed cache: no changes, skip update")
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
        save_cache(merged, meta)
    
    logger.info(
        "embed cache updated: total=%d embedded=%d skipped=%d removed=%d",
        stats["total"], stats["embedded"], stats["skipped"], stats["removed"],
    )
    return stats


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
