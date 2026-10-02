"""Validated, file-free operations for the event-first digest update operator."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

OPERATION_TYPES = frozenset({"create", "update", "merge", "supersede", "drop"})
# Retirement marker written only by operator paths. ``purge_dropped_blocks``
# removes these in the same commit, so no dropped block ever reaches disk.
# Human review writes ``rejected``, which is never auto-purged.
DROPPED_STATUS = "dropped"
MAX_RELATED = 10
MAX_SUPERSEDES = 8
# Defined here rather than in digest.py because the merge path has to respect the
# cap and operations.py cannot import digest.py (digest imports this module).
# digest.py re-exports it, so ``digest.MAX_BODY_CHARS`` stays the public name.
MAX_BODY_CHARS = 500
REFERENCE_FIELDS = ("related", "supersedes")
MERGE_NEST_KEYS = ("event", "procedure", "decision", "fact")
_CANONICAL_ID_RE = r"^mem-\d{4}-\d{2}-\d{2}-[A-Za-z0-9_-]+$"
# Segment used in generated create IDs (mem-YYYY-MM-DD-{type}-SUFFIX).
_ID_TYPE_ALIASES = {"decision_constraint": "decision"}
_ID_TYPES = frozenset({"event", "fact", "procedure", "decision"})
# Single source for type precedence. Used to pick a winner, never to decide
# whether a pair is duplicate. The S6b prompt builder imports this same dict so
# the guardrail and the instructions given to the proposer cannot drift.
TYPE_PRIORITY = {"event": 4, "decision": 3, "procedure": 2, "fact": 1}

try:
    from memory_staging import hermes_local_today_str
except Exception:  # pragma: no cover - standalone operation tests
    hermes_local_today_str = lambda: datetime.now().date().isoformat()

try:
    from digest_tools import (
        _decision_ruling_from_body,
        parse_rendered_body_slots,
        render_body_from_slots,
        validate_worker_slot_args,
        validate_worker_tool_args,
    )
except Exception:  # pragma: no cover - standalone imports
    parse_rendered_body_slots = None  # type: ignore[assignment]
    render_body_from_slots = None  # type: ignore[assignment]
    validate_worker_slot_args = None  # type: ignore[assignment]
    validate_worker_tool_args = None  # type: ignore[assignment]
    _decision_ruling_from_body = None  # type: ignore[assignment]


@dataclass
class Operation:
    operation: str
    block: dict[str, Any] | None = None
    id: str | None = None
    changes: dict[str, Any] = field(default_factory=dict)
    survivor_id: str | None = None
    absorbed_ids: list[str] = field(default_factory=list)
    reason: str | None = None
    # Option A merge nests (exactly one matching survivor type).
    event: dict[str, Any] | None = None
    procedure: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    fact: dict[str, Any] | None = None
    helper_id: str | None = None
    target_id: str | None = None
    correction: str | None = None
    confidence: str | None = None
    unknown_fields: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"operation": self.operation}
        for name in (
            "block", "id", "changes", "survivor_id", "absorbed_ids", "reason",
            "event", "procedure", "decision", "fact",
            "helper_id", "target_id", "correction", "confidence",
        ):
            value = getattr(self, name)
            if value not in (None, {}, []):
                result[name] = copy.deepcopy(value)
        return result


def normalize_operation(operation: Operation | Mapping[str, Any]) -> Operation:
    if isinstance(operation, Operation):
        return copy.deepcopy(operation)
    if not isinstance(operation, Mapping):
        raise TypeError("operation must be a mapping or Operation")
    data = dict(operation)
    data["operation"] = str(data.get("operation", "")).strip().lower()
    if "block" in data and isinstance(data["block"], Mapping):
        data["block"] = dict(data["block"])
    if isinstance(data.get("changes"), Mapping):
        data["changes"] = dict(data["changes"])
    if isinstance(data.get("absorbed_ids"), (tuple, set)):
        data["absorbed_ids"] = list(data["absorbed_ids"])
    for nest_key in MERGE_NEST_KEYS:
        nest = data.get(nest_key)
        if isinstance(nest, Mapping):
            data[nest_key] = dict(nest)
        elif nest_key in data and nest is not None:
            # Non-mapping nest becomes unknown via exclusion below.
            pass
    allowed = {
        field_name for field_name in Operation.__dataclass_fields__
        if field_name != "unknown_fields"
    }
    unknown = tuple(sorted(set(data) - allowed))
    # Drop non-mapping nests from kwargs so Operation() does not TypeError;
    # they surface as unknown_fields when key was present with bad type.
    cleaned = {key: value for key, value in data.items() if key in allowed}
    for nest_key in MERGE_NEST_KEYS:
        if nest_key in cleaned and not isinstance(cleaned.get(nest_key), (dict, type(None))):
            cleaned.pop(nest_key, None)
            if nest_key not in unknown:
                unknown = tuple(sorted(set(unknown) | {nest_key}))
    return Operation(**cleaned, unknown_fields=unknown)


def _nonempty_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _id_type_segment(block_type: Any) -> str:
    """Normalize block type into an ID segment; fall back to ``op``."""
    raw = str(block_type or "").strip().lower()
    raw = _ID_TYPE_ALIASES.get(raw, raw)
    return raw if raw in _ID_TYPES else "op"


def _new_id(
    id_factory: Callable[[], str] | None,
    occupied: set[str],
    block_type: Any = None,
) -> str:
    try:
        candidate = id_factory() if id_factory else ""
    except Exception:
        candidate = ""
    if not isinstance(candidate, str) or not re.fullmatch(_CANONICAL_ID_RE, candidate):
        candidate = ""
    type_seg = _id_type_segment(block_type)
    while not candidate or candidate in occupied:
        candidate = (
            f"mem-{hermes_local_today_str()}-{type_seg}-"
            f"{uuid.uuid4().hex[:12].upper()}"
        )
    return candidate


def _replace_id(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_id(item, replacements) for item in value]
    return value


def _rewrite_create_ids(
    operations: Iterable[Operation | Mapping[str, Any]],
) -> list[Operation]:
    normalized = [normalize_operation(operation) for operation in operations]
    replacements: dict[str, str] = {}
    used: set[str] = set()
    rewritten: list[Operation] = []
    for operation in normalized:
        operation = copy.deepcopy(operation)
        if operation.operation == "create" and isinstance(operation.block, dict):
            old_id = operation.block.get("id")
            new_id = _new_id(None, used, operation.block.get("type"))
            used.add(new_id)
            if _nonempty_id(old_id) and old_id not in replacements:
                replacements[old_id] = new_id
            operation.block["id"] = new_id
        rewritten.append(operation)
    for operation in rewritten:
        if operation.operation == "create" and isinstance(operation.block, dict):
            for key in REFERENCE_FIELDS:
                if key in operation.block:
                    operation.block[key] = _replace_id(
                        operation.block[key], replacements
                    )
        elif operation.operation == "drop":
            operation.id = replacements.get(operation.id, operation.id)
        elif operation.operation == "update":
            operation.id = replacements.get(operation.id, operation.id)
            operation.changes = {
                key: _replace_id(value, replacements)
                for key, value in operation.changes.items()
            }
        elif operation.operation == "merge":
            operation.survivor_id = replacements.get(
                operation.survivor_id, operation.survivor_id
            )
            operation.absorbed_ids = [
                replacements.get(item, item) for item in operation.absorbed_ids
            ]
        elif operation.operation == "supersede":
            operation.helper_id = replacements.get(
                operation.helper_id, operation.helper_id
            )
            operation.target_id = replacements.get(
                operation.target_id, operation.target_id
            )
    return rewritten


def validate_operation(
    operation: Operation | Mapping[str, Any],
    existing_ids: Iterable[str],
    *,
    available_ids: Iterable[str] | None = None,
    reserved_ids: Iterable[str] | None = None,
    blocks_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Return schema and snapshot errors without mutating the operation."""
    try:
        op = normalize_operation(operation)
    except (TypeError, ValueError) as exc:
        return [str(exc)]
    existing = {str(item) for item in existing_ids}
    available = existing | {str(item) for item in (available_ids or ())}
    reserved = {str(item) for item in (reserved_ids or ())}
    errors: list[str] = []

    if op.operation not in OPERATION_TYPES:
        return [f"operation must be one of {sorted(OPERATION_TYPES)}"]

    allowed_fields = {
        "create": {"operation", "block"},
        "update": {"operation", "id", "changes"},
        "merge": {
            "operation", "survivor_id", "absorbed_ids", "reason",
            *MERGE_NEST_KEYS,
        },
        "supersede": {
            "operation", "helper_id", "target_id", "correction", "confidence",
        },
        "drop": {"operation", "id", "reason"},
    }[op.operation]
    errors.extend(f"unknown or irrelevant field: {field}" for field in op.unknown_fields)
    # Fields represented by dataclass defaults are still rejected when supplied
    # to the wrong operation shape.
    supplied = {
        field_name for field_name, value in (
            ("block", op.block), ("id", op.id), ("changes", op.changes),
            ("survivor_id", op.survivor_id), ("absorbed_ids", op.absorbed_ids),
            ("reason", op.reason),
            ("event", op.event), ("procedure", op.procedure),
            ("decision", op.decision), ("fact", op.fact),
            ("helper_id", op.helper_id),
            ("target_id", op.target_id), ("correction", op.correction),
            ("confidence", op.confidence),
        ) if value not in (None, {}, [])
    }
    errors.extend(
        f"unknown or irrelevant field: {field}"
        for field in sorted(supplied - (allowed_fields - {"operation"}))
    )

    if op.operation == "create":
        if not isinstance(op.block, dict):
            errors.append("create requires block")
        else:
            block_id = op.block.get("id")
            if not _nonempty_id(block_id):
                errors.append("create block requires id")
            elif not re.fullmatch(_CANONICAL_ID_RE, block_id):
                errors.append(
                    "create block id must match canonical mem-YYYY-MM-DD-opaque format"
                )
            elif block_id in existing or block_id in reserved:
                errors.append(f"create id already exists: {block_id}")
            if "body" not in op.block or not str(op.block.get("body", "")).strip():
                errors.append("create block requires body")
    elif op.operation == "update":
        if not _nonempty_id(op.id):
            errors.append("update requires id")
        elif op.id not in available:
            errors.append(f"update target does not exist: {op.id}")
        if not isinstance(op.changes, dict) or not op.changes:
            errors.append("update requires non-empty changes")
        elif "id" in op.changes:
            errors.append("update cannot change immutable id")
    elif op.operation == "merge":
        if not _nonempty_id(op.survivor_id):
            errors.append("merge requires survivor_id")
        elif op.survivor_id not in available:
            errors.append(f"merge survivor does not exist: {op.survivor_id}")
        if not isinstance(op.absorbed_ids, list) or not op.absorbed_ids:
            errors.append("merge requires absorbed_ids")
        else:
            if len(set(op.absorbed_ids)) != len(op.absorbed_ids):
                errors.append("merge absorbed_ids must be unique")
            for absorbed in op.absorbed_ids:
                if absorbed == op.survivor_id:
                    errors.append("merge survivor and absorbed IDs must differ")
                elif absorbed not in available:
                    errors.append(f"merge absorbed target does not exist: {absorbed}")
        if not isinstance(op.reason, str) or not op.reason.strip():
            errors.append("merge requires reason")
        errors.extend(
            _validate_merge_nests(
                op,
                blocks_by_id=blocks_by_id,
            )
        )
    elif op.operation == "drop":
        if not _nonempty_id(op.id):
            errors.append("drop requires id")
        elif op.id not in available:
            errors.append(f"drop target does not exist: {op.id}")
        if not isinstance(op.reason, str) or not op.reason.strip():
            errors.append("drop requires reason")
    else:
        for field_name, label in (("helper_id", "helper"), ("target_id", "target")):
            value = getattr(op, field_name)
            if not _nonempty_id(value):
                errors.append(f"supersede requires {field_name}")
            elif value not in available:
                errors.append(f"supersede {label} does not exist: {value}")
        if op.helper_id == op.target_id and op.helper_id:
            errors.append("supersede helper and target IDs must differ")
        if not isinstance(op.correction, str) or not op.correction.strip():
            errors.append("supersede requires correction")
        if op.confidence != "explicit":
            errors.append("supersede requires confidence: explicit")

    return errors


def _truncate_body(text: str) -> str:
    body = str(text or "").strip()
    if len(body) <= MAX_BODY_CHARS:
        return body
    trimmed = body[: MAX_BODY_CHARS - 1].rstrip()
    return f"{trimmed}…"


def fact_merge_requires_narration(
    survivor: Mapping[str, Any],
    absorbed_blocks: Iterable[Mapping[str, Any]],
) -> bool:
    """True when a fact merge must use nest kind=Narration (cast/story)."""
    body = str(survivor.get("body", "")).strip()
    if body.startswith("Narration:"):
        return True
    slots: dict[str, str] = {}
    if parse_rendered_body_slots is not None:
        try:
            slots = parse_rendered_body_slots("fact", body)
        except Exception:
            slots = {}
    if str(slots.get("kind", "")).strip() == "Narration":
        return True
    involves_lists = [survivor.get("involves")]
    for block in absorbed_blocks:
        involves_lists.append(block.get("involves"))
    return len(union_involves(*involves_lists)) >= 2


def _present_merge_nests(op: Operation) -> list[str]:
    present: list[str] = []
    for key in MERGE_NEST_KEYS:
        nest = getattr(op, key, None)
        if isinstance(nest, dict) and nest:
            present.append(key)
    return present


def _validate_merge_nests(
    op: Operation,
    *,
    blocks_by_id: Mapping[str, Mapping[str, Any]] | None,
) -> list[str]:
    """Option A: exactly one nest matching survivor type; nonempty slots."""
    errors: list[str] = []
    present = _present_merge_nests(op)
    survivor_type = ""
    absorbed_blocks: list[Mapping[str, Any]] = []
    if blocks_by_id and _nonempty_id(op.survivor_id):
        survivor = blocks_by_id.get(str(op.survivor_id))
        if survivor is not None:
            survivor_type = _block_type(survivor)
            for absorbed_id in op.absorbed_ids:
                absorbed = blocks_by_id.get(str(absorbed_id))
                if absorbed is not None:
                    absorbed_blocks.append(absorbed)

    if survivor_type and survivor_type in MERGE_NEST_KEYS:
        if survivor_type not in present:
            errors.append(
                f"merge requires nested {survivor_type} object with required "
                f"non-empty slots (do not use free-form body)"
            )
        wrong = [key for key in present if key != survivor_type]
        if wrong:
            errors.append(
                f"merge nest must match survivor type {survivor_type!r}; "
                f"unexpected nest(s): {', '.join(wrong)}"
            )
        nest = getattr(op, survivor_type, None)
        if isinstance(nest, dict) and nest:
            nest_validator = (
                validate_worker_tool_args
                if survivor_type == "decision" and validate_worker_tool_args is not None
                else validate_worker_slot_args
            )
            if nest_validator is not None:
                for err in nest_validator(survivor_type, nest):
                    errors.append(f"merge.{survivor_type}: {err}")
            if survivor_type == "fact" and fact_merge_requires_narration(
                blocks_by_id.get(str(op.survivor_id), {}),  # type: ignore[arg-type]
                absorbed_blocks,
            ):
                kind = str(nest.get("kind", "")).strip()
                if kind != "Narration":
                    errors.append(
                        "merge.fact: kind must be Narration when absorbing "
                        "into a cast/story (survivor already Narration or "
                        "unioned involves >= 2); Factual loser goes in absorbed_ids"
                    )
    elif present:
        # No survivor type resolved (missing blocks snapshot) — still reject
        # multiple nests and empty required keys on whichever nest is present.
        if len(present) != 1:
            errors.append(
                "merge requires exactly one nest matching survivor type "
                f"(event|procedure|decision|fact); got {present}"
            )
        else:
            nest_key = present[0]
            nest = getattr(op, nest_key, None)
            if isinstance(nest, dict):
                nest_validator = (
                    validate_worker_tool_args
                    if nest_key == "decision" and validate_worker_tool_args is not None
                    else validate_worker_slot_args
                )
                if nest_validator is not None:
                    for err in nest_validator(nest_key, nest):
                        errors.append(f"merge.{nest_key}: {err}")
    else:
        # Code-owned merge_into may omit nest when blocks snapshot is absent
        # (unit tests of id/reason only). When blocks are provided, nest is
        # required above. Without blocks, require nest only if any was expected
        # by callers that pass blocks_by_id.
        if blocks_by_id is not None and _nonempty_id(op.survivor_id):
            # Survivor missing from snapshot — nest still required as opaque.
            if not present:
                errors.append(
                    "merge requires nested event|procedure|decision|fact "
                    "object matching survivor type (non-empty slots)"
                )
    return errors


def _involves_entity_name(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("entity", "")).strip()
    if isinstance(item, str):
        return item.strip()
    return ""


def _normalize_involves_entry(item: Any) -> dict[str, str] | None:
    if isinstance(item, str):
        ent = item.strip()
        return {"entity": ent} if ent else None
    if isinstance(item, Mapping):
        ent = str(item.get("entity", "")).strip()
        if not ent:
            return None
        entry: dict[str, str] = {"entity": ent}
        role = str(item.get("role", "")).strip()
        if role:
            entry["role"] = role
        return entry
    return None


def union_involves(*lists: Any) -> list[dict[str, str]]:
    """Union involves by entity name; keep a non-empty role when either side has one."""
    merged: list[dict[str, str]] = []
    index: dict[str, dict[str, str]] = {}
    for raw in lists:
        if not isinstance(raw, list):
            continue
        for item in raw:
            entry = _normalize_involves_entry(item)
            if entry is None:
                continue
            ent = entry["entity"]
            existing = index.get(ent)
            if existing is None:
                index[ent] = entry
                merged.append(entry)
            elif "role" not in existing and "role" in entry:
                existing["role"] = entry["role"]
    return merged


def _ensure_narration_body_for_cast(survivor: dict[str, Any]) -> None:
    """Mechanical safety net: multi-cast fact survivors must use Narration: body."""
    if str(survivor.get("type", "")).strip() != "fact":
        return
    involves = survivor.get("involves")
    if not isinstance(involves, list) or len(involves) < 2:
        return
    body = str(survivor.get("body", "")).strip()
    if body.startswith("Narration:"):
        return
    if body.startswith("Factual:"):
        body = body[len("Factual:") :].strip()
    prefixed = f"Narration: {body}".strip()
    if len(prefixed) > MAX_BODY_CHARS:
        trimmed = prefixed[: MAX_BODY_CHARS - 1].rstrip()
        prefixed = f"{trimmed}…"
    survivor["body"] = prefixed


def _retarget(value: Any, replacements: Mapping[str, str]) -> Any:
    if not isinstance(value, list):
        return value
    result: list[Any] = []
    for item in value:
        if isinstance(item, str):
            item = replacements.get(item, item)
        if item not in result:
            result.append(item)
    return result


def apply_operation(
    operation: Operation | Mapping[str, Any],
    blocks: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply one already-validated operation to an in-memory block snapshot."""
    op = normalize_operation(operation)
    result = [copy.deepcopy(dict(block)) for block in blocks]
    by_id = {block.get("id"): block for block in result}
    if op.operation == "create":
        result.append(copy.deepcopy(op.block or {}))
        return result
    if op.operation == "update":
        target = by_id.get(op.id)
        if target is not None:
            target.update(copy.deepcopy(op.changes))
        return result
    if op.operation == "drop":
        # Mark only. purge_dropped_blocks does the removal once every operation
        # in the commit has been applied, so a later op can still read the block.
        target = by_id.get(op.id)
        if target is not None:
            target["status"] = DROPPED_STATUS
        return result
    if op.operation == "merge":
        replacements = {absorbed: op.survivor_id for absorbed in op.absorbed_ids}
        survivor = by_id.get(op.survivor_id)
        absorbed_blocks = [by_id[item] for item in op.absorbed_ids if item in by_id]
        if survivor is not None:
            for block in absorbed_blocks:
                for key in ("sources", "related", "supersedes", "participants"):
                    merged = list(survivor.get(key, [])) + list(block.get(key, []))
                    if not merged:
                        # Writing an empty list here fails _validate_block, which
                        # requires these keys to be non-empty when present. Only
                        # reachable since S2 allows event-to-event merges.
                        continue
                    survivor[key] = _retarget(merged, replacements)
                involves_union = union_involves(
                    survivor.get("involves"), block.get("involves")
                )
                if involves_union:
                    survivor["involves"] = involves_union
                for key, value in block.items():
                    if key in {
                        "id",
                        "body",
                        "sources",
                        "involves",
                        "related",
                        "supersedes",
                        "participants",
                    }:
                        continue
                    if key not in survivor or survivor[key] in (None, ""):
                        survivor[key] = copy.deepcopy(value)
                    elif isinstance(survivor[key], list) and isinstance(value, list):
                        survivor[key] = _retarget(
                            list(survivor[key]) + list(value), replacements
                        )
                    elif key == "importance":
                        survivor[key] = max(survivor[key], value)
                    elif key == "valid_from":
                        survivor[key] = min(str(survivor[key]), str(value))
                    elif key == "valid_to":
                        if survivor[key] != "open" and value == "open":
                            survivor[key] = "open"
                        elif survivor[key] != "open":
                            survivor[key] = max(str(survivor[key]), str(value))
            # Meaning rewrite via nest (no string concat). Missing nest keeps
            # survivor body for code-owned / legacy apply paths; cast fix still runs.
            nest_type = _block_type(survivor)
            nest = None
            if nest_type in MERGE_NEST_KEYS:
                candidate = getattr(op, nest_type, None)
                if isinstance(candidate, dict) and candidate:
                    nest = candidate
            if nest is not None and render_body_from_slots is not None:
                survivor["body"] = _truncate_body(
                    render_body_from_slots(nest_type, nest)
                )
            _ensure_narration_body_for_cast(survivor)
            if survivor.get("body"):
                survivor["body"] = _truncate_body(str(survivor["body"]))
        result = [block for block in result if block.get("id") not in set(op.absorbed_ids)]
        for block in result:
            for key in REFERENCE_FIELDS:
                if key in block:
                    retargeted = _retarget(block[key], replacements)
                    if block.get("id") == op.survivor_id:
                        # The absorbed block may have cited the survivor, which
                        # retargeting turns into a self-reference.
                        retargeted = [
                            item for item in retargeted if item != op.survivor_id
                        ]
                    if retargeted:
                        block[key] = retargeted
                    else:
                        # Validation rejects a present-but-empty list, so the key
                        # has to go rather than be left as [].
                        block.pop(key, None)
        return result
    target = by_id.get(op.target_id)
    if target is not None:
        target["body"] = op.correction
        target["confidence"] = op.confidence
        # Code-owned audit stamp; workers never emit it.
        target["superseded_at"] = hermes_local_today_str()
    helper = by_id.get(op.helper_id)
    if helper is not None:
        # The helper only carried the correction into the target. Retire it on
        # the same path as a duplicate loser: purge_dropped_blocks removes it in
        # this commit, so its related/sources go with it.
        refs = list(helper.get("supersedes", []))
        consumed_targets = _retarget(refs + [op.target_id], {})
        helper["related"] = _retarget(
            list(helper.get("related", [])) + consumed_targets, {}
        )
        helper.pop("supersedes", None)
        helper["confidence"] = "explicit"
        helper["status"] = DROPPED_STATUS
        for block in result:
            if str(block.get("type", "")).strip() == "event":
                related = block.get("related")
                if isinstance(related, list):
                    block["related"] = [
                        item for item in related if item != op.helper_id
                    ]
    return result


def purge_dropped_blocks(
    blocks: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Delete ``status: dropped`` blocks and scrub references to them.

    Pruning is part of the purge rather than a separate pass because
    ``build_update_operations`` auto-links detail blocks into their event's
    ``related``, so a dropped detail is usually still cited and the file
    validator rejects dangling references. Returns the surviving blocks and the
    purged ids, in encounter order.
    """
    result = [copy.deepcopy(dict(block)) for block in blocks]
    purged = [
        str(block.get("id"))
        for block in result
        if str(block.get("status", "")).strip() == DROPPED_STATUS
        and _nonempty_id(block.get("id"))
    ]
    survivors = [
        block
        for block in result
        if str(block.get("status", "")).strip() != DROPPED_STATUS
    ]
    if not purged:
        return survivors, []
    gone = set(purged)
    for block in survivors:
        for key in REFERENCE_FIELDS:
            value = block.get(key)
            if not isinstance(value, list):
                continue
            kept = [item for item in value if str(item) not in gone]
            if kept:
                block[key] = kept
            else:
                block.pop(key, None)
    return survivors, purged


def _block_diff(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    ignored = {"id"}
    return {
        key: copy.deepcopy(value)
        for key, value in new.items()
        if key not in ignored and old.get(key) != value
    }


def _touches_hypothesis(
    op: Operation, existing_by_id: Mapping[str, Mapping[str, Any]]
) -> bool:
    """Hypothesis is owned by memory-weekly; digest never touches it."""

    def is_hypothesis(block_id: Any) -> bool:
        block = existing_by_id.get(str(block_id))
        return bool(block) and _block_type(block) == "hypothesis"

    if op.operation == "create" and isinstance(op.block, dict):
        return _block_type(op.block) == "hypothesis"
    if op.operation in {"update", "drop"}:
        return is_hypothesis(op.id)
    if op.operation == "merge":
        return is_hypothesis(op.survivor_id) or any(
            is_hypothesis(item) for item in op.absorbed_ids
        )
    if op.operation == "supersede":
        return is_hypothesis(op.helper_id) or is_hypothesis(op.target_id)
    return False


def finalize_operations(
    existing_blocks: Iterable[Mapping[str, Any]],
    proposed: Iterable[Operation | Mapping[str, Any]],
    *,
    touched: Iterable[tuple[dict[str, Any], bool]] | None = None,
    id_factory: Callable[[], str] | None = None,
) -> list[Operation]:
    """Apply the invariants a proposer would otherwise bypass.

    ``related`` linking treats importance 0 as spent so create-time 1 stays
    associable on the 1–5 write scale.
    """
    existing = [dict(block) for block in existing_blocks]
    existing_by_id = {str(block.get("id")): block for block in existing}
    operations = [
        op
        for op in (normalize_operation(item) for item in proposed)
        if not _touches_hypothesis(op, existing_by_id)
    ]

    occupied = set(existing_by_id)
    replacements: dict[str, str] = {}
    for op in operations:
        if op.operation != "create" or not isinstance(op.block, dict):
            continue
        block_id = op.block.get("id")
        if (
            _nonempty_id(block_id)
            and re.fullmatch(_CANONICAL_ID_RE, str(block_id))
            and str(block_id) not in occupied
        ):
            occupied.add(str(block_id))
            continue
        minted = _new_id(id_factory, occupied, op.block.get("type"))
        occupied.add(minted)
        if _nonempty_id(block_id):
            replacements[str(block_id)] = minted
        op.block["id"] = minted
    if replacements:
        for op in operations:
            if op.operation == "create" and isinstance(op.block, dict):
                for key in REFERENCE_FIELDS:
                    if key in op.block:
                        op.block[key] = _replace_id(op.block[key], replacements)
            elif op.operation in {"update", "drop"}:
                op.id = replacements.get(op.id, op.id)
                op.changes = {
                    key: _replace_id(value, replacements)
                    for key, value in op.changes.items()
                }
            elif op.operation == "merge":
                op.survivor_id = replacements.get(op.survivor_id, op.survivor_id)
                op.absorbed_ids = [
                    replacements.get(item, item) for item in op.absorbed_ids
                ]
            elif op.operation == "supersede":
                op.helper_id = replacements.get(op.helper_id, op.helper_id)
                op.target_id = replacements.get(op.target_id, op.target_id)

    if touched is not None:
        prepared = [(block, is_existing) for block, is_existing in touched]
    else:
        prepared = []
        for op in operations:
            if op.operation == "create" and isinstance(op.block, dict):
                prepared.append((op.block, False))
            elif op.operation == "update" and str(op.id) in existing_by_id:
                prepared.append(
                    ({**existing_by_id[str(op.id)], **op.changes}, True)
                )

    non_associable_ids = {
        str(block.get("id"))
        for block in existing + [block for block, _ in prepared]
        if block.get("id") and (
            block.get("supersedes")
            or str(block.get("status", "")).strip() == "rejected"
            or block.get("importance") == 0
        )
    }
    detail_ids = [
        str(block["id"])
        for block, _is_existing in prepared
        if str(block.get("type", "")).strip() in {"fact", "procedure", "decision"}
        and str(block["id"]) not in non_associable_ids
    ]
    type_by_id = {
        str(block.get("id")): str(block.get("type", "")).strip()
        for block in existing + [block for block, _ in prepared]
        if block.get("id")
    }
    for block, is_existing in prepared:
        if str(block.get("type", "")).strip() != "event":
            continue
        related = block.get("related", [])
        related = related if isinstance(related, list) else []
        related = [
            str(item)
            for item in related
            if (
                type_by_id.get(str(item))
                and type_by_id.get(str(item)) != "event"
                and str(item) not in non_associable_ids
            )
        ]
        for detail_id in detail_ids:
            if detail_id not in related:
                related.append(detail_id)
        related = related[:MAX_RELATED]
        if is_existing:
            for operation in operations:
                if operation.operation == "update" and operation.id == block["id"]:
                    operation.changes["related"] = related
                    break
            else:
                if related:
                    operations.append(
                        Operation("update", id=block["id"], changes={"related": related})
                    )
        elif related:
            # Write through the operation, not the touched block: normalizing the
            # proposal deep-copies it, so the two are no longer the same object.
            for operation in operations:
                if (
                    operation.operation == "create"
                    and isinstance(operation.block, dict)
                    and operation.block.get("id") == block["id"]
                ):
                    operation.block["related"] = related
                    break
            else:
                block["related"] = related
    return operations


def build_update_operations(
    existing_blocks: Iterable[Mapping[str, Any]],
    validated_new_blocks: Iterable[Mapping[str, Any]],
    *,
    id_factory: Callable[[], str] | None = None,
) -> list[Operation]:
    """Emit creates plus same-file supersede so leftover on-disk pointers still overwrite.

    Clock Phase-2 loads the whole today file with new_blocks empty; without an
    existing-board scan those pointers would never become apply operations.
    """
    existing = [dict(block) for block in existing_blocks]
    existing_by_id = {block.get("id"): block for block in existing}
    operations: list[Operation] = []
    seen_new: set[str] = set()
    prepared: list[tuple[dict[str, Any], bool]] = []
    for raw_block in validated_new_blocks:
        block = copy.deepcopy(dict(raw_block))
        merge_into = block.pop("merge_into", None)
        block_id = block.get("id")
        # Hypothesis is owned by memory-weekly; digest never creates, updates,
        # or merges it.
        if str(block.get("type", "")).strip() == "hypothesis":
            continue
        if (
            _nonempty_id(block_id)
            and _nonempty_id(merge_into)
            and block_id in existing_by_id
            and merge_into in existing_by_id
            and block_id != merge_into
            and str(existing_by_id[block_id].get("type", "")).strip()
            == str(existing_by_id[merge_into].get("type", "")).strip()
        ):
            if str(existing_by_id[block_id].get("type", "")).strip() == "hypothesis":
                continue
            survivor = existing_by_id[merge_into]
            survivor_type = _block_type(survivor)
            nest_slots = _merge_into_nest_slots(survivor, survivor_type)
            merge_kwargs: dict[str, Any] = {
                "survivor_id": str(merge_into),
                "absorbed_ids": [str(block_id)],
                "reason": "validated duplicate identity",
            }
            if nest_slots is not None and survivor_type in MERGE_NEST_KEYS:
                merge_kwargs[survivor_type] = nest_slots
            operations.append(Operation("merge", **merge_kwargs))
            continue
        if block_id in existing_by_id:
            # Existing identity is the only ID an input block may preserve.
            if str(existing_by_id[block_id].get("type", "")).strip() == "hypothesis":
                continue
            pass
        else:
            block_id = _new_id(
                id_factory,
                set(existing_by_id) | seen_new,
                block.get("type"),
            )
            block["id"] = block_id
        seen_new.add(block_id)
        is_existing = block_id in existing_by_id
        prepared.append((block, is_existing))
        if block_id in existing_by_id:
            changes = _block_diff(existing_by_id[block_id], block)
            if changes:
                operations.append(Operation("update", id=block_id, changes=changes))
        else:
            operations.append(Operation("create", block=block))
        for target_id in block.get("supersedes", []):
            if (
                target_id in existing_by_id
                and target_id != block_id
                and isinstance(block.get("body"), str)
            ):
                operations.append(
                    Operation(
                        "supersede",
                        helper_id=str(block_id),
                        target_id=str(target_id),
                        correction=block["body"],
                        confidence="explicit",
                    )
                )

    queued_pairs = {
        (op.helper_id, op.target_id)
        for op in operations
        if op.operation == "supersede"
    }
    for block in existing:
        block_id = block.get("id")
        if not _nonempty_id(block_id):
            continue
        if str(block.get("type", "")).strip() == "hypothesis":
            continue
        raw_supersedes = block.get("supersedes", [])
        if not isinstance(raw_supersedes, list):
            continue
        body = block.get("body")
        if not isinstance(body, str):
            continue
        helper_type = _block_type(block)
        for target_id in raw_supersedes:
            target = existing_by_id.get(target_id)
            if (
                target is None
                or target_id == block_id
                or (str(block_id), str(target_id)) in queued_pairs
            ):
                continue
            if str(target.get("type", "")).strip() == "hypothesis":
                continue
            if helper_type != _block_type(target):
                continue
            operations.append(
                Operation(
                    "supersede",
                    helper_id=str(block_id),
                    target_id=str(target_id),
                    correction=body,
                    confidence="explicit",
                )
            )
            queued_pairs.add((str(block_id), str(target_id)))

    return finalize_operations(
        existing, operations, touched=prepared, id_factory=id_factory
    )


def _block_type(block: Mapping[str, Any]) -> str:
    """Read the declared type. Never infer from the id prefix.

    Machine-generated ids carry an ``op-`` segment and ~142 legacy staging ids
    are ``mem-20260801-<slug>``, which does not even match the canonical id
    pattern, so prefix inference is wrong in both formats.
    """
    raw = str(block.get("type", "")).strip().lower()
    return _ID_TYPE_ALIASES.get(raw, raw)


def _importance(block: Mapping[str, Any]) -> int:
    try:
        return int(block.get("importance", 0))
    except (TypeError, ValueError):
        return 0


def _outranks(winner: Mapping[str, Any], loser: Mapping[str, Any]) -> bool:
    """True when ``winner`` legitimately displaces ``loser``.

    Type precedence first; on a tie, importance, then sources count, then the
    earlier ``valid_from``. The tie-break key is importance, not confidence:
    importance is what the survivor rule turns on.
    """
    winner_priority = TYPE_PRIORITY.get(_block_type(winner), 0)
    loser_priority = TYPE_PRIORITY.get(_block_type(loser), 0)
    if winner_priority != loser_priority:
        return winner_priority > loser_priority
    if _importance(winner) != _importance(loser):
        return _importance(winner) > _importance(loser)
    winner_sources = len(winner.get("sources") or [])
    loser_sources = len(loser.get("sources") or [])
    if winner_sources != loser_sources:
        return winner_sources > loser_sources
    return str(winner.get("valid_from", "")) < str(loser.get("valid_from", ""))


_EXTERNAL_UPDATE_KEYS = frozenset({"valid_to", "status", "rejected_reason"})
_REJECTED_REASON_RE = re.compile(
    r"^rejected by (mem-[A-Za-z0-9\-]+|user's correction)$"
)


def check_type_rules(
    existing_blocks: Iterable[Mapping[str, Any]],
    proposed: Iterable[Operation | Mapping[str, Any]],
    *,
    current_day: str | None = None,
    retrieval_ids: Iterable[str] | None = None,
) -> list[str]:
    """Enforce the type-priority rules on proposed operations.

    ``build_update_operations`` enforced same-type merge inline, but a proposer
    replaces it entirely, so the rule has to live somewhere that still runs.
    Optional ``current_day`` / ``retrieval_ids`` reject cross-day body rewrites
    so a past contradiction can only close metadata, never merge or overwrite.
    """
    operations = []
    for raw in proposed:
        try:
            operations.append(normalize_operation(raw))
        except (TypeError, ValueError):
            # Schema problems belong to validate_operations, not here.
            continue

    snapshot: dict[str, dict[str, Any]] = {
        str(block.get("id")): dict(block)
        for block in existing_blocks
        if _nonempty_id(block.get("id"))
    }
    for op in operations:
        if op.operation == "create" and isinstance(op.block, dict):
            block_id = op.block.get("id")
            if _nonempty_id(block_id):
                snapshot[str(block_id)] = dict(op.block)
    for op in operations:
        if op.operation == "update" and str(op.id) in snapshot:
            snapshot[str(op.id)].update(op.changes)

    errors: list[str] = []
    for index, op in enumerate(operations):
        label = f"operation[{index}]"
        if op.operation == "merge":
            survivor = snapshot.get(str(op.survivor_id))
            if survivor is None:
                continue
            for absorbed_id in op.absorbed_ids:
                absorbed = snapshot.get(str(absorbed_id))
                if absorbed is None:
                    continue
                if _block_type(survivor) != _block_type(absorbed):
                    errors.append(
                        f"{label}: merge must not cross types: "
                        f"{op.survivor_id} is {_block_type(survivor)!r}, "
                        f"{absorbed_id} is {_block_type(absorbed)!r}; "
                        f"use drop for a cross-type extension"
                    )
                elif _importance(absorbed) > _importance(survivor):
                    errors.append(
                        f"{label}: merge survivor {op.survivor_id} has lower "
                        f"importance ({_importance(survivor)}) than absorbed "
                        f"{absorbed_id} ({_importance(absorbed)}); "
                        f"the higher-importance block must survive"
                    )
            if not (op.reason or "").strip():
                errors.append(f"{label}: merge requires a non-empty reason")
        elif op.operation == "drop":
            dropped = snapshot.get(str(op.id))
            if dropped is None:
                continue
            if _block_type(dropped) == "event":
                errors.append(
                    f"{label}: must not drop event {op.id}; an event is the "
                    f"request skeleton and never loses to a detail block"
                )
            elif not any(
                _outranks(other, dropped)
                for other_id, other in snapshot.items()
                if other_id != str(op.id)
            ):
                errors.append(
                    f"{label}: drop of {op.id} ({_block_type(dropped)}) has no "
                    f"block that outranks it by type priority or tie-break; "
                    f"nothing displaces it"
                )
            if not (op.reason or "").strip():
                errors.append(f"{label}: drop requires a non-empty reason")
        if current_day:
            retrieved = (
                {str(item) for item in retrieval_ids}
                if retrieval_ids is not None
                else None
            )
            if op.operation == "update":
                target = snapshot.get(str(op.id))
                target_day = ""
                if target is not None:
                    target_day = str(target.get("valid_from") or "")[:10]
                if target_day and target_day < current_day:
                    changes = op.changes if isinstance(op.changes, dict) else {}
                    extra = set(changes) - _EXTERNAL_UPDATE_KEYS
                    if extra:
                        errors.append(
                            f"{label}: past-day update may only set "
                            f"valid_to/status/rejected_reason; got {sorted(extra)}"
                        )
                    if str(changes.get("status") or "").strip() != "rejected":
                        errors.append(
                            f"{label}: past-day update must set status: rejected"
                        )
                    reason = str(changes.get("rejected_reason") or "").strip()
                    if not _REJECTED_REASON_RE.fullmatch(reason):
                        errors.append(
                            f"{label}: rejected_reason must be "
                            "'rejected by <mem-id>' or \"rejected by user's correction\""
                        )
                    valid_to = str(changes.get("valid_to") or "").strip()
                    if valid_to and valid_to != "open" and valid_to < target_day:
                        errors.append(
                            f"{label}: valid_to must not precede valid_from"
                        )
                    if retrieved is not None and str(op.id) not in retrieved:
                        errors.append(
                            f"{label}: past-day update target was not in the "
                            "fresh retrieval set"
                        )
            elif op.operation == "merge":
                days = []
                for mem_id in [op.survivor_id, *list(op.absorbed_ids or [])]:
                    block = snapshot.get(str(mem_id))
                    if block is not None:
                        days.append(str(block.get("valid_from") or "")[:10])
                if any(day and day < current_day for day in days):
                    errors.append(
                        f"{label}: merge must not span past-day cards"
                    )
            elif op.operation in {"drop", "supersede"}:
                ids = (
                    [op.id]
                    if op.operation == "drop"
                    else [op.helper_id, op.target_id]
                )
                for mem_id in ids:
                    block = snapshot.get(str(mem_id))
                    day = str((block or {}).get("valid_from") or "")[:10]
                    if day and day < current_day:
                        errors.append(
                            f"{label}: {op.operation} must not target past-day cards"
                        )
    return errors


def validate_operations(
    operations: Iterable[Operation | Mapping[str, Any]],
    existing_ids: Iterable[str],
    *,
    existing_blocks: Iterable[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Validate a sequence, accounting for IDs created earlier in the sequence."""
    errors: list[str] = []
    known = set(existing_ids)
    reserved: set[str] = set()
    blocks_by_id: dict[str, dict[str, Any]] = {}
    if existing_blocks is not None:
        for block in existing_blocks:
            bid = block.get("id")
            if _nonempty_id(bid):
                blocks_by_id[str(bid)] = dict(block)
    for index, raw in enumerate(operations):
        try:
            op = normalize_operation(raw)
        except (TypeError, ValueError) as exc:
            errors.append(f"operation[{index}]: {exc}")
            continue
        if op.operation == "create" and isinstance(op.block, dict) and _nonempty_id(
            op.block.get("id")
        ):
            blocks_by_id[str(op.block["id"])] = dict(op.block)
        item_errors = validate_operation(
            op,
            known,
            available_ids=reserved,
            reserved_ids=reserved,
            blocks_by_id=blocks_by_id or None,
        )
        errors.extend(f"operation[{index}]: {error}" for error in item_errors)
        if op.operation == "create" and isinstance(op.block, dict) and _nonempty_id(op.block.get("id")):
            reserved.add(op.block["id"])
    return errors


_OP_ALLOWED_FIELDS = {
    "create": {"operation", "block"},
    "update": {"operation", "id", "changes"},
    "merge": {
        "operation", "survivor_id", "absorbed_ids", "reason",
        *MERGE_NEST_KEYS,
    },
    "supersede": {
        "operation", "helper_id", "target_id", "correction", "confidence",
    },
    "drop": {"operation", "id", "reason"},
}


def _snapshot_for_type_rules(
    existing_blocks: Iterable[Mapping[str, Any]],
    operations: Iterable[Operation],
) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {
        str(block.get("id")): dict(block)
        for block in existing_blocks
        if _nonempty_id(block.get("id"))
    }
    for op in operations:
        if op.operation == "create" and isinstance(op.block, dict):
            block_id = op.block.get("id")
            if _nonempty_id(block_id):
                snapshot[str(block_id)] = dict(op.block)
    for op in operations:
        if op.operation == "update" and str(op.id) in snapshot:
            snapshot[str(op.id)].update(op.changes)
    return snapshot


def _known_ids_for_ops(
    existing_ids: Iterable[str],
    operations: Iterable[Operation],
) -> set[str]:
    known = {str(item) for item in existing_ids if str(item).strip()}
    for op in operations:
        if op.operation == "create" and isinstance(op.block, dict):
            block_id = op.block.get("id")
            if _nonempty_id(block_id):
                known.add(str(block_id))
    return known


def sanitize_operations_list(
    proposed: Iterable[Operation | Mapping[str, Any]],
    existing_ids: Iterable[str],
) -> list[Operation]:
    """Drop junk ops and strip extra fields. Never LLM."""
    raw_ops: list[Operation] = []
    for raw in proposed:
        try:
            raw_ops.append(normalize_operation(raw))
        except (TypeError, ValueError):
            continue
    known = _known_ids_for_ops(existing_ids, raw_ops)
    out: list[Operation] = []
    for op in raw_ops:
        allowed = _OP_ALLOWED_FIELDS.get(op.operation)
        if not allowed:
            out.append(op)
            continue
        trimmed = {
            key: value
            for key, value in op.to_dict().items()
            if key in allowed
        }
        try:
            cleaned = normalize_operation(trimmed)
        except (TypeError, ValueError):
            continue
        if cleaned.operation == "update":
            if not _nonempty_id(cleaned.id) or str(cleaned.id) not in known:
                continue
            if not isinstance(cleaned.changes, dict) or not cleaned.changes:
                continue
        elif cleaned.operation == "drop":
            if not _nonempty_id(cleaned.id) or str(cleaned.id) not in known:
                continue
        elif cleaned.operation == "merge":
            if not _nonempty_id(cleaned.survivor_id) or str(cleaned.survivor_id) not in known:
                continue
            absorbed = [
                str(item)
                for item in (cleaned.absorbed_ids or [])
                if _nonempty_id(item)
                and str(item) in known
                and str(item) != str(cleaned.survivor_id)
            ]
            if not absorbed:
                continue
            cleaned.absorbed_ids = absorbed
        elif cleaned.operation == "supersede":
            if (
                not _nonempty_id(cleaned.helper_id)
                or not _nonempty_id(cleaned.target_id)
                or str(cleaned.helper_id) not in known
                or str(cleaned.target_id) not in known
            ):
                continue
        out.append(cleaned)
    return out


def rewrite_type_priority_ops(
    existing_blocks: Iterable[Mapping[str, Any]],
    proposed: Iterable[Operation | Mapping[str, Any]],
) -> list[Operation]:
    """Rewrite cross-type merge / bad survivor / illegal drops before check_type_rules."""
    operations: list[Operation] = []
    for raw in proposed:
        try:
            operations.append(normalize_operation(raw))
        except (TypeError, ValueError):
            continue
    snapshot = _snapshot_for_type_rules(existing_blocks, operations)
    out: list[Operation] = []
    for op in operations:
        if op.operation == "merge":
            rewritten = _rewrite_merge_type_priority(op, snapshot)
            out.extend(rewritten)
        elif op.operation == "drop":
            dropped = snapshot.get(str(op.id))
            if dropped is None:
                continue
            if _block_type(dropped) == "event":
                continue
            if not any(
                _outranks(other, dropped)
                for other_id, other in snapshot.items()
                if other_id != str(op.id)
            ):
                continue
            out.append(op)
        else:
            out.append(op)
    return out


def _rewrite_merge_type_priority(
    op: Operation,
    snapshot: Mapping[str, Mapping[str, Any]],
) -> list[Operation]:
    survivor = snapshot.get(str(op.survivor_id))
    if survivor is None:
        return [op]
    members: list[tuple[str, Mapping[str, Any]]] = [(str(op.survivor_id), survivor)]
    for absorbed_id in op.absorbed_ids or []:
        absorbed = snapshot.get(str(absorbed_id))
        if absorbed is None:
            continue
        members.append((str(absorbed_id), absorbed))
    if len(members) < 2:
        return [op]
    types = {_block_type(block) for _bid, block in members}
    if len(types) > 1:
        winner_id, _winner = max(
            members,
            key=lambda item: (
                TYPE_PRIORITY.get(_block_type(item[1]), 0),
                _importance(item[1]),
            ),
        )
        drops: list[Operation] = []
        reason = op.reason if isinstance(op.reason, str) else ""
        for bid, block in members:
            if bid == winner_id:
                continue
            if _block_type(block) == "event":
                continue
            drops.append(
                normalize_operation(
                    {"operation": "drop", "id": bid, "reason": reason}
                )
            )
        return drops
    current_survivor_id = str(op.survivor_id)
    current_importance = _importance(survivor)
    best_id = current_survivor_id
    best_importance = current_importance
    for bid, block in members:
        if bid == current_survivor_id:
            continue
        if _importance(block) > best_importance:
            best_id = bid
            best_importance = _importance(block)
    if best_id == current_survivor_id:
        return [op]
    absorbed = [current_survivor_id] + [
        str(item)
        for item in (op.absorbed_ids or [])
        if str(item) != best_id
    ]
    swapped = op.to_dict()
    swapped["survivor_id"] = best_id
    swapped["absorbed_ids"] = absorbed
    return [normalize_operation(swapped)]


def _merge_into_nest_slots(
    survivor: Mapping[str, Any],
    survivor_type: str,
) -> dict[str, Any] | None:
    """Build Option A nest from survivor body for code-owned merge_into."""
    stype = survivor_type.strip().lower()
    if stype not in MERGE_NEST_KEYS:
        return None
    body = str(survivor.get("body", "")).strip()
    slots: dict[str, Any] = {}
    if parse_rendered_body_slots is not None:
        slots = dict(parse_rendered_body_slots(stype, body))
    if validate_worker_slot_args is not None and not validate_worker_slot_args(
        stype, slots
    ):
        return slots
    # Fallback so code-owned merge still carries a nest for validate/apply.
    if stype == "event":
        return {
            "beginning": body or ".",
            "course": ".",
            "outcome": ".",
        }
    if stype == "procedure":
        return {"obstacle": body or ".", "solution": "."}
    if stype == "decision":
        if _decision_ruling_from_body is not None:
            ruling = _decision_ruling_from_body(body, "user") or "."
        else:
            ruling = "."
        return {
            "kind": "Decision",
            "subject": "user",
            "ruling": ruling,
        }
    return {"kind": "Factual", "content": body or "."}


def prepare_operations(
    existing_blocks: Iterable[Mapping[str, Any]],
    validated_new_blocks: Iterable[Mapping[str, Any]],
    *,
    session_id: str,
    run_id: str,
    session_dir: Path,
    proposer: Callable[..., Iterable[Operation | Mapping[str, Any]]] | None = None,
    max_attempts: int = 5,
    alive_ids: set[str] | None = None,
    soft_merge_threshold: int = 15,
) -> tuple[list[Operation], Path]:
    """Build/validate operations with dry-run composition + standalone decay.

    On exhaustion with a last usable proposal, accept it dirty (audit-stored)
    rather than raising — digest continues.
    """
    from composition import (
        append_standalone_decay_ops,
        composition_errors_after_dry_run,
        dry_run_apply,
        retiring_ids_from_operations,
        scrub_related_on_blocks,
        standalone_ids_from_blocks,
    )

    existing = [dict(block) for block in existing_blocks]
    new_blocks = [dict(block) for block in validated_new_blocks]
    existing_ids = {str(block.get("id")) for block in existing if block.get("id")}
    last_errors: list[str] = []
    last_usable: list[Operation] | None = None
    for attempt in range(1, max_attempts + 1):
        pressure: list[str] = []
        if (
            attempt == 1
            and soft_merge_threshold > 0
            and len(existing) + len(new_blocks) >= soft_merge_threshold
        ):
            pressure = [
                f"soft merge pressure: projected "
                f"{len(existing) + len(new_blocks)} blocks ≥ {soft_merge_threshold}; "
                "prefer merge/drop where safe"
            ]
        if proposer is None:
            proposed = build_update_operations(existing, new_blocks)
        else:
            try:
                proposal = proposer(
                    existing,
                    new_blocks,
                    errors=tuple(list(last_errors) + pressure),
                    attempt=attempt,
                )
                proposed = list(proposal) if proposal is not None else None
            except Exception as exc:
                proposed = None
                last_errors = [f"operation proposal error: {exc}"]
        if proposed is None:
            last_errors = last_errors or ["operation proposal must be a list"]
            continue
        try:
            normalized = _rewrite_create_ids(proposed)
        except (TypeError, ValueError) as exc:
            last_errors = [str(exc)]
            continue
        normalized = sanitize_operations_list(normalized, existing_ids)
        normalized = rewrite_type_priority_ops(existing, normalized)
        # Keep last list even when checks fail — dirty hand-in on exhaust.
        last_usable = list(normalized)
        last_errors = check_type_rules(existing, normalized)
        last_errors.extend(
            validate_operations(
                normalized,
                existing_ids,
                existing_blocks=existing,
            )
        )
        if last_errors:
            continue
        seed_blocks = list(existing)
        pre_purge, post_purge = dry_run_apply(seed_blocks, normalized)
        keep_ids = {
            str(block.get("id")).strip()
            for block in post_purge
            if str(block.get("id") or "").strip()
        }
        if alive_ids:
            keep_ids |= set(alive_ids)
        keep_ids -= retiring_ids_from_operations(normalized)
        pre_purge = scrub_related_on_blocks(pre_purge, keep_ids=keep_ids)
        comp_errors = composition_errors_after_dry_run(
            pre_purge, normalized, alive_ids=alive_ids,
        )
        if comp_errors:
            last_errors = comp_errors
            continue
        referenced: set[str] = set()
        for block in pre_purge:
            related = block.get("related")
            if isinstance(related, list):
                for ref in related:
                    rid = str(ref).strip()
                    if rid:
                        referenced.add(rid)
        _ = standalone_ids_from_blocks(existing)
        normalized_maps = append_standalone_decay_ops(
            normalized,
            existing,
            eligible_ids=existing_ids,
            referenced_ids=referenced,
        )
        normalized = [normalize_operation(op) for op in normalized_maps]
        decay_errors = validate_operations(
            normalized,
            existing_ids,
            existing_blocks=existing,
        )
        if decay_errors:
            last_errors = decay_errors
            last_usable = list(normalized)
            continue
        return normalized, store_operations(
            normalized,
            session_dir=session_dir,
            session_id=session_id,
            run_id=run_id,
        )
    if last_usable is not None:
        # Fail-open: hand in last proposal so commit can still proceed.
        # Do not import digest (circular); keep a plain audit line on stderr.
        import sys

        print(
            f"proposer_accepted_dirty session={session_id} run={run_id} "
            f"attempts={max_attempts} errors={'; '.join(last_errors[:3])}",
            file=sys.stderr,
        )
        return last_usable, store_operations(
            last_usable,
            session_dir=session_dir,
            session_id=session_id,
            run_id=run_id,
        )
    raise ValueError(
        f"operation validation failed after {max_attempts} attempts: "
        + "; ".join(last_errors[:8])
    )



def store_operations(
    operations: Iterable[Operation | Mapping[str, Any]],
    *,
    session_dir: Path,
    session_id: str,
    run_id: str,
) -> Path:
    """Persist only validated operations in the operation artifact namespace."""
    normalized = [normalize_operation(op) for op in operations]
    path = session_dir / "operations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "run_id": run_id,
        "operations": [op.to_dict() for op in normalized],
        "status": "validated",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    fd, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path
