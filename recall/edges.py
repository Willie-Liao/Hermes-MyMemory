"""Build edges.jsonl from daily related, weekly legend, and entity overlap.

Dangling weekly cites become phantom PPR nodes if materialized; drop and log
them. Event→event related is illegal at L2 (digest.py:1571).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from .ids import BlockIndex, BlockRecord, classify_weekly_id, resolve_id, staging_root
from .normalize import entity_key, load_entity_index

log = logging.getLogger("mymemory.recall.edges")

WEIGHT_RELATED = 0.9
WEIGHT_CITE = 0.9
WEIGHT_OVERLAP = 0.5
WEIGHT_SUPERSEDES = 1.0


def _weekly_legend_ids(staging: Path) -> list[tuple[str, str, str]]:
    """(from_weekly_file, target_id, src) for every legend mem-id."""
    weekly = staging / "weekly"
    out: list[tuple[str, str, str]] = []
    if not weekly.is_dir():
        return out
    try:
        import sys

        weekly_mod = Path(__file__).resolve().parent.parent / "weekly"
        if str(weekly_mod) not in sys.path:
            sys.path.insert(0, str(weekly_mod))
        from weekly_json import load_sidecar
    except Exception:
        load_sidecar = None
    for path in sorted(weekly.glob("*.md")):
        legend: dict[Any, Any] = {}
        payload: dict[Any, Any] = {}
        if load_sidecar is not None:
            try:
                loaded = load_sidecar(path)
                if isinstance(loaded, dict):
                    payload = loaded
                    legend = payload.get("legend") or {}
            except Exception:
                legend = {}
                payload = {}
        if not legend:
            threads = payload.get("cross-day-thread") or []
            if isinstance(threads, list):
                for thread in threads:
                    if not isinstance(thread, dict):
                        continue
                    for step in thread.get("steps") or []:
                        if not isinstance(step, dict):
                            continue
                        mid = str(step.get("event_id") or "").strip()
                        if mid:
                            legend[mid] = mid
        if not legend:
            continue
        src = f"weekly/{path.name}#citemap"
        if isinstance(legend, dict):
            for _n, mem in legend.items():
                mid = str(mem or "").strip()
                if mid:
                    out.append((path.name, mid, src))
    return out


def extract_edges(
    staging: Path | None = None,
    *,
    index: BlockIndex | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (edges, dangling_weekly_ids). Daily related never dangles in this corpus."""
    root = staging_root(staging)
    store = index or BlockIndex(root)
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    dangling: list[str] = []

    def _add(frm: str, to: str, etype: str, weight: float, src: str) -> None:
        key = (frm, to, etype)
        if key in seen or not frm or not to or frm == to:
            return
        seen.add(key)
        edges.append(
            {"from": frm, "to": to, "type": etype, "weight": weight, "src": src}
        )

    for rec in store.records:
        src = f"daily/{rec.path.name}"
        for target in rec.related:
            dest = store.get(target)
            if dest is None:
                continue
            if rec.item_type == "event" and dest.item_type == "event":
                continue
            _add(rec.block_id, target, "related", WEIGHT_RELATED, src)
        supers = rec.parsed.get("supersedes")
        targets: list[str] = []
        if isinstance(supers, list):
            targets = [str(x).strip() for x in supers if str(x).strip()]
        elif isinstance(supers, str) and supers.strip():
            targets = [supers.strip()]
        for target in targets:
            if store.get(target):
                _add(rec.block_id, target, "supersedes", WEIGHT_SUPERSEDES, src)

    for _week_name, target, src in _weekly_legend_ids(root):
        resolved = resolve_id(target, staging=root, index=store)
        if resolved is None:
            dangling.append(target)
            log.warning("drop dangling weekly cite %s src=%s", target, src)
            continue
        # cite map is week → daily; skip if already a daily related edge
        frm = resolved.block_id
        # legend is N → event id, not an edge from week node; skip phantom week nodes
        _ = frm
        _ = classify_weekly_id(target)

    entity_index = load_entity_index(root)
    for key, node in entity_index.items():
        mem_ids = [str(x) for x in (node.get("mem_ids") or [])]
        recs = [store.get(mid) for mid in mem_ids]
        recs = [r for r in recs if r is not None]
        recs.sort(key=lambda r: (r.day, r.block_id))
        prev: BlockRecord | None = None
        for rec in recs:
            if prev is not None and prev.day != rec.day:
                src = f"key:{key}"
                _add(rec.block_id, prev.block_id, "entity_overlap", WEIGHT_OVERLAP, src)
                _add(prev.block_id, rec.block_id, "entity_overlap", WEIGHT_OVERLAP, src)
            prev = rec

    return edges, dangling


def write_edges(staging: Path | None = None) -> tuple[Path, list[str]]:
    """Write edges.jsonl; dangling weekly cites are logged, never stored as `to`."""
    root = staging_root(staging)
    edges, dangling = extract_edges(root)
    path = root / "edges.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for edge in edges:
            fh.write(json.dumps(edge, ensure_ascii=False) + "\n")
    if dangling:
        log.warning("dropped %s dangling weekly cites", len(dangling))
    return path, dangling


def load_edges(staging: Path | None = None) -> list[dict[str, Any]]:
    root = staging_root(staging)
    path = root / "edges.jsonl"
    if not path.is_file():
        edges, _ = extract_edges(root)
        return edges
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("from") and obj.get("to"):
            out.append(obj)
    return out


def adjacency(
    edges: Iterable[dict[str, Any]],
    *,
    min_weight: float = 0.0,
) -> dict[str, dict[str, tuple[float, str, str]]]:
    """node → neighbor → (weight, type, src) for expand_memory / PPR."""
    adj: dict[str, dict[str, tuple[float, str, str]]] = {}
    for edge in edges:
        w = float(edge.get("weight") or 0)
        if w < min_weight:
            continue
        frm = str(edge.get("from") or "")
        to = str(edge.get("to") or "")
        etype = str(edge.get("type") or "")
        src = str(edge.get("src") or "")
        adj.setdefault(frm, {})
        prev = adj[frm].get(to)
        if prev is None or w > prev[0]:
            adj[frm][to] = (w, etype, src)
    return adj
