"""Weekly Worker 1 tool schemas + merge/render helpers.

Tool schemas are the templates; models fill argument values via forced tool
calls. Attempt 1 = submit_*; attempt 2+ = patch_* only (merge_field_patch).
Registration uses Hermes ``tools.registry.registry`` with toolset
``memory_weekly``. Capture path: ``run_worker_llm_tools``.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

CONFIDENCE_ENUM = ("explicit", "high", "medium", "low")
WORKER_STATUS_ENUM = ("candidate",)
EVENT_REQUIRED_ROLES = ("requester", "executor")
WEEKLY_TOOLSET = "memory_weekly"

_MEM_ID_RE = re.compile(
    r"(mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-[\w-]+)",
    re.IGNORECASE,
)

_REGISTERED = False


def merge_field_patch(
    previous: Mapping[str, Any],
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    """Shallow merge: keys in ``patch`` overwrite; others keep previous values."""
    merged = dict(previous)
    for key, value in patch.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def _confidence_prop() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": list(CONFIDENCE_ENUM),
        "description": "Confidence: explicit|high|medium|low",
    }


def _status_prop() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": list(WORKER_STATUS_ENUM),
        "description": "Worker status — candidate only (or omit)",
    }


def _participant_item() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "entity": {"type": "string"},
            "role": {
                "type": "string",
                "enum": list(EVENT_REQUIRED_ROLES),
                "description": "User/Assistant rows: requester|executor",
            },
        },
        "required": ["entity", "role"],
    }


def _event_item_props() -> dict[str, Any]:
    return {
        "entity": {"type": "string"},
        "predicate": {"type": "string"},
        "participants": {
            "type": "array",
            "items": _participant_item(),
        },
        "valid_from": {"type": "string", "description": "YYYY-MM-DD"},
        "valid_to": {"type": "string"},
        "confidence": _confidence_prop(),
        "status": _status_prop(),
        "sources": {"type": "array", "items": {"type": "string"}},
        "related": {"type": "array", "items": {"type": "string"}},
        "beginning": {"type": "string"},
        "course": {"type": "string"},
        "outcome": {"type": "string"},
        "id": {
            "type": "string",
            "description": "Optional week-local event id; code may assign",
        },
    }


def _schema(
    name: str, description: str, props: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required
    return {"name": name, "description": description, "parameters": parameters}


def submit_weekly_event_schema() -> dict[str, Any]:
    """One forced call may include many events (week partition)."""
    return _schema(
        "submit_weekly_event",
        "Submit week event extractor results (events-only). Fill events array.",
        {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": _event_item_props(),
                    "required": [
                        "entity",
                        "predicate",
                        "participants",
                        "valid_from",
                        "valid_to",
                        "confidence",
                        "beginning",
                        "course",
                        "outcome",
                        "related",
                        "sources",
                    ],
                },
                "description": "Zero or more type:event blocks for assigned days",
            }
        },
        ["events"],
    )


def patch_weekly_event_schema() -> dict[str, Any]:
    return _schema(
        "patch_weekly_event",
        "Patch ONLY changed fields on the previous submit_weekly_event args.",
        {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": _event_item_props(),
                },
                "description": "Optional full events list replacement",
            }
        },
        [],
    )


def skip_weekly_event_schema() -> dict[str, Any]:
    return _schema(
        "skip_weekly_event",
        "Skip event extraction for this partition (empty is OK).",
        {"skip": {"type": "boolean"}},
        ["skip"],
    )


def _thread_step_props() -> dict[str, Any]:
    return {
        "seq": {"type": "integer"},
        "date": {"type": "string"},
        "event_id": {"type": "string"},
        "text": {"type": "string"},
        "via": {"type": "string", "enum": ["evolves", "invalidates"]},
        "to_seq": {"type": "integer"},
    }


def _thread_item_props() -> dict[str, Any]:
    return {
        "id": {"type": "string"},
        "label": {"type": "string"},
        "entity_keys": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _thread_step_props(),
                "required": ["seq", "date", "event_id", "text"],
            },
        },
        "outcome": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "text": {"type": "string"},
            },
        },
    }


def submit_weekly_thread_schema(
    event_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Lock event_id to this week's daily event mem-ids when provided so the model cannot mint w-evt-*."""
    schema = _schema(
        "submit_weekly_thread",
        "Submit canonical entities and cross-day-thread chains. Copy event_id from the candidate index. Do not invent wrap-ups or legend.",
        {
            "entities": {
                "type": "array",
                "description": "Canonical entity roster for the week.",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical": {"type": "string"},
                        "aliases": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["canonical"],
                },
            },
            "cross-day-thread": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": _thread_item_props(),
                    "required": ["id", "label", "steps"],
                },
            }
        },
        ["cross-day-thread"],
    )
    allowed = sorted({str(i).strip() for i in (event_ids or ()) if str(i).strip()})
    if not allowed:
        return schema
    schema = copy.deepcopy(schema)
    step_props = schema["parameters"]["properties"]["cross-day-thread"]["items"][
        "properties"
    ]["steps"]["items"]["properties"]
    step_props["event_id"] = {
        "type": "string",
        "enum": allowed,
        "description": "Daily type:event mem-id from the candidate index.",
    }
    return schema


def patch_weekly_thread_schema(
    event_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Same event_id enum as submit so a patch cannot introduce a new mem-id."""
    schema = _schema(
        "patch_weekly_thread",
        "Patch ONLY changed fields on previous submit_weekly_thread args.",
        {
            "cross-day-thread": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": _thread_item_props(),
                },
            }
        },
        [],
    )
    allowed = sorted({str(i).strip() for i in (event_ids or ()) if str(i).strip()})
    if not allowed:
        return schema
    schema = copy.deepcopy(schema)
    step_props = schema["parameters"]["properties"]["cross-day-thread"]["items"][
        "properties"
    ]["steps"]["items"]["properties"]
    step_props["event_id"] = {
        "type": "string",
        "enum": allowed,
        "description": "Daily type:event mem-id from the candidate index.",
    }
    return schema


def all_tool_schemas() -> list[dict[str, Any]]:
    try:
        from . import tighten_tools
    except ImportError:
        import tighten_tools  # type: ignore

    return [
        submit_weekly_event_schema(),
        patch_weekly_event_schema(),
        skip_weekly_event_schema(),
        submit_weekly_thread_schema(),
        patch_weekly_thread_schema(),
        *tighten_tools.all_tighten_tool_schemas(),
    ]


def _noop_handler(args: dict[str, Any], **_kwargs: Any) -> str:
    return json.dumps({"ok": True, "received": args}, ensure_ascii=False)


def ensure_weekly_tools_registered() -> None:
    """Idempotently register weekly tools on the Hermes tool registry."""
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from tools.registry import registry
    except ImportError:
        return
    for schema in all_tool_schemas():
        name = schema["name"]
        try:
            registry.register(
                name=name,
                toolset=WEEKLY_TOOLSET,
                schema=schema,
                handler=_noop_handler,
            )
        except Exception:
            pass
    _REGISTERED = True


def forced_tool_choice(tool_name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": tool_name}}


def is_skip_event(name: str | None, args: Mapping[str, Any] | None) -> bool:
    if not name:
        return False
    if name == "skip_weekly_event":
        return bool((args or {}).get("skip"))
    if name == "submit_weekly_event" and isinstance(args, Mapping):
        events = args.get("events")
        if isinstance(events, list) and len(events) == 0:
            return True
    return False


def validate_closed_choice_args(args: Mapping[str, Any], *, role: str) -> list[str]:
    """Reject non-enum confidence/status/role (no silent coerce)."""
    errors: list[str] = []
    items_key = {
        "event": "events",
        "conflict": "conflicts",
        "hypothesis": "hypotheses",
        "thread": "cross-day-thread",
    }.get(role, "")
    items = args.get(items_key) if items_key else None
    if not isinstance(items, list):
        return errors
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"{role}[{i}] must be an object")
            continue
        conf = str(item.get("confidence") or "").strip()
        if conf and conf not in CONFIDENCE_ENUM:
            errors.append(
                f"{role}[{i}] confidence must be one of {', '.join(CONFIDENCE_ENUM)}"
            )
        status = item.get("status")
        if status is not None and str(status).strip() and str(status).strip() not in WORKER_STATUS_ENUM:
            errors.append(
                f"{role}[{i}] status must be candidate (or omit)"
            )
        if role == "thread":
            for step in item.get("steps") or []:
                if not isinstance(step, Mapping):
                    continue
                via = step.get("via")
                if via is not None and str(via).strip() not in {"evolves", "invalidates"}:
                    errors.append(
                        f"{role}[{i}] step via must be evolves or invalidates"
                    )
        if role == "event":
            parts = item.get("participants")
            if isinstance(parts, list):
                for j, p in enumerate(parts):
                    if not isinstance(p, Mapping):
                        continue
                    role_s = str(p.get("role") or "").strip()
                    ent = str(p.get("entity") or "").strip().casefold()
                    if ent in {"user", "assistant"} and role_s not in EVENT_REQUIRED_ROLES:
                        errors.append(
                            f"{role}[{i}].participants[{j}] role must be "
                            f"requester|executor for User/Assistant"
                        )
    return errors


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(ch in text for ch in (":", "#", "\n", "{", "}", "[", "]")) or text != text.strip():
        return json.dumps(text, ensure_ascii=False)
    return text


def _format_list(values: Any) -> str:
    if not isinstance(values, list):
        return "[]"
    items = [str(v).strip() for v in values if str(v).strip()]
    return "[" + ", ".join(_yaml_scalar(i) if ":" in i else i for i in items) + "]"


def _format_participants(items: Any) -> list[str]:
    lines = ["participants:"]
    if not isinstance(items, list):
        return lines
    for item in items:
        if isinstance(item, Mapping):
            ent = str(item.get("entity", "")).strip()
            role = str(item.get("role", "")).strip()
            lines.append(f"  - entity: {ent}")
            if role:
                lines.append(f"    role: {role}")
        else:
            lines.append(f"  - {item}")
    return lines


def render_event_block_from_args(
    args: Mapping[str, Any],
    *,
    index: int = 0,
) -> dict[str, Any]:
    """One Distill-shaped block dict from a single event arg object."""
    bag = dict(args)
    event_id = str(bag.get("id") or "").strip() or f"w-evt-{index + 1}"
    status = str(bag.get("status") or "candidate").strip() or "candidate"
    if status not in WORKER_STATUS_ENUM:
        status = "candidate"
    related = bag.get("related") if isinstance(bag.get("related"), list) else []
    sources = bag.get("sources") if isinstance(bag.get("sources"), list) else []
    participants = (
        bag.get("participants")
        if isinstance(bag.get("participants"), list)
        else []
    )
    body = (
        f"Beginning: {str(bag.get('beginning', '')).strip()}; "
        f"Course: {str(bag.get('course', '')).strip()}; "
        f"Outcome: {str(bag.get('outcome', '')).strip()}"
    )
    fm = {
        "id": event_id,
        "type": "event",
        "entity": str(bag.get("entity") or "").strip() or "Unknown",
        "predicate": str(bag.get("predicate") or "").strip() or "recorded",
        "participants": participants,
        "valid_from": str(bag.get("valid_from") or "").strip(),
        "valid_to": str(bag.get("valid_to") or "").strip(),
        "confidence": str(bag.get("confidence") or "").strip(),
        "status": status,
        "sources": sources,
        "related": related,
    }
    return {"frontmatter": fm, "body": body}


_EVENT_FIELD_KEYS = frozenset(
    {
        "entity",
        "predicate",
        "participants",
        "valid_from",
        "valid_to",
        "confidence",
        "sources",
        "related",
        "beginning",
        "course",
        "outcome",
        "status",
        "id",
    }
)
_WRAPPER_KEYS = ("arguments", "parameters", "input")


def coerce_event_tool_args(args: Any, *, _depth: int = 0) -> dict[str, Any]:
    """Hoist malformed weekly event bags into schema-shaped events[] so render cannot silently drop a real submit.

    Without this, a flat root (entity+predicate, no events key) yields 0 blocks and Worker 1 logs ok events=0.
    Structure-only and idempotent: no vendor branches. Recurse at most twice for wrappers/JSON strings.
    """
    if _depth > 2:
        return dict(args) if isinstance(args, Mapping) else {}

    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return coerce_event_tool_args(parsed, _depth=_depth + 1)

    if not isinstance(args, Mapping):
        return {}

    bag: dict[str, Any] = dict(args)
    events = bag.get("events")
    if not isinstance(events, (list, Mapping, str)):
        for key in _WRAPPER_KEYS:
            inner = bag.get(key)
            if isinstance(inner, (Mapping, str)):
                return coerce_event_tool_args(inner, _depth=_depth + 1)

    events = bag.get("events")
    if isinstance(events, str):
        try:
            parsed_events = json.loads(events)
        except (json.JSONDecodeError, TypeError, ValueError):
            return bag
        bag["events"] = parsed_events
        return coerce_event_tool_args(bag, _depth=_depth + 1)

    if isinstance(events, Mapping):
        bag["events"] = [dict(events)]
        return bag

    if isinstance(events, list):
        return bag

    entity = bag.get("entity")
    predicate = bag.get("predicate")
    if (
        isinstance(entity, str)
        and entity.strip()
        and isinstance(predicate, str)
        and predicate.strip()
    ):
        event: dict[str, Any] = {}
        rest: dict[str, Any] = {}
        for key, value in bag.items():
            if key in _EVENT_FIELD_KEYS:
                event[key] = value
            else:
                rest[key] = value
        rest["events"] = [event]
        return rest

    return bag


def render_events_from_tool_args(args: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Render event blocks from tool args after coercing non-schema shapes.

    Callers (Worker 1) used to miss a whole submit when events was missing but
    entity/predicate sat at the root.
    """
    coerced = coerce_event_tool_args(args)
    events = coerced.get("events")
    if not isinstance(events, list):
        return []
    return [
        render_event_block_from_args(ev, index=i)
        for i, ev in enumerate(events)
        if isinstance(ev, Mapping)
    ]


def failed_fields_teach(
    errors: Sequence[str],
    previous: Mapping[str, Any],
    *,
    role: str,
    patch_tool: str,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> str:
    """Compact retry teach: patch-only, no full resubmit / scratch regenerate."""
    header = (
        f"VALIDATION FAILED — call {patch_tool} with ONLY fields that must change."
    )
    if attempt is not None and max_attempts is not None:
        header = (
            f"VALIDATION FAILED (attempt {attempt} of {max_attempts}). "
            f"Call {patch_tool} with ONLY fields that must change. "
            "Do NOT call submit again. Do NOT regenerate the full block from scratch."
        )
    lines = [
        header,
        f"Assigned role: {role}",
        f"confidence must be one of: {', '.join(CONFIDENCE_ENUM)}",
        "status must be candidate (or omit)",
        "Do not resend unchanged fields. Do not emit free-form YAML.",
        "Errors:",
    ]
    for err in list(errors)[:12]:
        lines.append(f"- {err}")
    if (
        role == "event"
        and "entity" in previous
        and not isinstance(previous.get("events"), list)
    ):
        lines.append(
            'Wrap the event object in a top-level "events" array; do not leave entity at the root.'
        )
    if previous:
        lines.append("Prior accepted/partial args (reference only):")
        preview = json.dumps(previous, ensure_ascii=False)
        if len(preview) > 800:
            preview = preview[:797] + "..."
        lines.append(preview)
    return "\n".join(lines) + "\n"


def related_has_mem_id(related: Any) -> bool:
    if not isinstance(related, list) or not related:
        return False
    return any(_MEM_ID_RE.search(str(entry)) for entry in related)


__all__ = [
    "CONFIDENCE_ENUM",
    "WEEKLY_TOOLSET",
    "WORKER_STATUS_ENUM",
    "all_tool_schemas",
    "coerce_event_tool_args",
    "ensure_weekly_tools_registered",
    "failed_fields_teach",
    "forced_tool_choice",
    "is_skip_event",
    "merge_field_patch",
    "patch_weekly_event_schema",
    "patch_weekly_thread_schema",
    "submit_weekly_thread_schema",
    "render_events_from_tool_args",
    "submit_weekly_event_schema",
    "validate_closed_choice_args",
]
