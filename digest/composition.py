"""Dry-run composition checks, retiring-id tracking, and standalone decay.

Used by ``prepare_operations`` after proposal validation and by digest commit
paths for soft merge pressure / decay append. Pure functions over block dicts
and Operation mappings — no LLM.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from operations import (
    apply_operation,
    normalize_operation,
    purge_dropped_blocks,
)

_EXEMPT_DECAY_TYPES = frozenset({"event", "hypothesis"})
_EXEMPT_DECAY_STATUS = frozenset({"approved"})


def _as_op_mapping(operation: Any) -> Mapping[str, Any] | Any:
    """Normalize Operation-like objects to mappings (pytest dual-module safe)."""
    to_dict = getattr(operation, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return operation


def _block_id(block: Mapping[str, Any]) -> str:
    return str(block.get("id") or "").strip()


def _related_ids(block: Mapping[str, Any]) -> list[str]:
    related = block.get("related")
    if not isinstance(related, list):
        return []
    out: list[str] = []
    for ref in related:
        rid = str(ref).strip()
        if rid:
            out.append(rid)
    return out


def scrub_related_on_blocks(
    blocks: Iterable[Mapping[str, Any]],
    *,
    keep_ids: set[str],
) -> list[dict[str, Any]]:
    """Drop related pointers not in keep_ids (file survivors ∪ week-alive)."""
    allowed = {str(item).strip() for item in keep_ids if str(item).strip()}
    out: list[dict[str, Any]] = []
    for block in blocks:
        item = dict(block)
        related = item.get("related")
        if isinstance(related, list):
            kept = [ref for ref in related if str(ref).strip() in allowed]
            if kept:
                item["related"] = kept
            else:
                item.pop("related", None)
        out.append(item)
    return out


def retiring_ids_from_operations(operations: Iterable[Any]) -> set[str]:
    """IDs that leave the file as a result of these operations."""
    retiring: set[str] = set()
    for raw in operations:
        try:
            op = normalize_operation(_as_op_mapping(raw))
        except (TypeError, ValueError):
            continue
        if op.operation == "drop" and op.id:
            retiring.add(str(op.id).strip())
        elif op.operation == "merge":
            for absorbed in op.absorbed_ids or []:
                rid = str(absorbed).strip()
                if rid:
                    retiring.add(rid)
        elif op.operation == "supersede" and op.helper_id:
            retiring.add(str(op.helper_id).strip())
    return {rid for rid in retiring if rid}


def dry_run_apply(
    existing_blocks: Iterable[Mapping[str, Any]],
    operations: Iterable[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply ops in memory; return (pre_purge_blocks, post_purge_blocks)."""
    blocks = [dict(b) for b in existing_blocks]
    for raw in operations:
        blocks = apply_operation(_as_op_mapping(raw), blocks)
    pre_purge = [dict(b) for b in blocks]
    post_purge, _purged = purge_dropped_blocks(blocks)
    return pre_purge, [dict(b) for b in post_purge]


def composition_errors_after_dry_run(
    blocks_pre_purge: Iterable[Mapping[str, Any]],
    operations: Iterable[Any],
    *,
    alive_ids: set[str] | None = None,
) -> list[str]:
    """Flag related→retiring and related→missing (vs week-alive set)."""
    retiring = retiring_ids_from_operations(operations)
    created: set[str] = set()
    for raw in operations:
        try:
            op = normalize_operation(_as_op_mapping(raw))
        except (TypeError, ValueError):
            continue
        if op.operation == "create" and isinstance(op.block, Mapping):
            cid = _block_id(op.block)
            if cid:
                created.add(cid)

    errors: list[str] = []
    for block in blocks_pre_purge:
        bid = _block_id(block)
        for ref in _related_ids(block):
            if ref in retiring:
                errors.append(f"related points at retiring id {bid} -> {ref}")
            elif alive_ids is not None:
                if ref not in alive_ids and ref not in created:
                    errors.append(
                        f"dangling related reference {bid} -> {ref} "
                        f"(not in week-alive set)"
                    )
    return list(dict.fromkeys(errors))


def standalone_ids_from_blocks(blocks: Iterable[Mapping[str, Any]]) -> set[str]:
    """Non-event block ids that no event points at via related:."""
    all_ids: set[str] = set()
    event_ids: set[str] = set()
    referenced: set[str] = set()
    for block in blocks:
        bid = _block_id(block)
        if not bid:
            continue
        all_ids.add(bid)
        btype = str(block.get("type") or "").strip().lower()
        if btype == "event":
            event_ids.add(bid)
            for ref in _related_ids(block):
                referenced.add(ref)
    return {
        bid
        for bid in all_ids
        if bid not in event_ids and bid not in referenced
    }


def append_standalone_decay_ops(
    operations: Sequence[Any],
    existing_blocks: Iterable[Mapping[str, Any]],
    *,
    eligible_ids: set[str] | None = None,
    referenced_ids: set[str] | None = None,
) -> list[Any]:
    """Append importance−1 / drop-at-0 ops for standalone pre-existing blocks.

    Drop only at 0 so a create-time score of 1 is a real low card (1→0, then
    drop), not an immediate purge left over from the old 3–5 write floor.
    """
    result: list[Any] = [dict(_as_op_mapping(op)) for op in operations]
    blocks = [dict(b) for b in existing_blocks]
    by_id = {_block_id(b): b for b in blocks if _block_id(b)}

    if referenced_ids is None:
        standalone = standalone_ids_from_blocks(blocks)
    else:
        standalone = {
            bid
            for bid, block in by_id.items()
            if str(block.get("type") or "").strip().lower() != "event"
            and bid not in referenced_ids
        }

    for bid in sorted(standalone):
        if eligible_ids is not None and bid not in eligible_ids:
            continue
        block = by_id.get(bid)
        if block is None:
            continue
        btype = str(block.get("type") or "").strip().lower()
        status = str(block.get("status") or "").strip().lower()
        if btype in _EXEMPT_DECAY_TYPES or status in _EXEMPT_DECAY_STATUS:
            continue
        try:
            importance = int(block.get("importance", 3))
        except (TypeError, ValueError):
            importance = 3
        if importance <= 0:
            result.append({"operation": "drop", "id": bid})
        else:
            result.append(
                {
                    "operation": "update",
                    "id": bid,
                    "changes": {"importance": importance - 1},
                }
            )
    return result
