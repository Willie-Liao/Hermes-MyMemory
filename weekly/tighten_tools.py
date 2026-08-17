"""Forced JSON tools for Weekly UI Tighten (body slots per memory type)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

WORKER_TYPES = ("event", "fact", "procedure", "decision")
FACT_KIND_ENUM = ("Factual", "Narration")
DECISION_KIND_ENUM = ("Preference", "Decision")
DEFAULT_GUIDANCE = "make it concise."

_TYPE_LINE_RE = re.compile(
    r"^type:\s*(event|fact|procedure|decision)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _schema(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
        "required": required,
    }
    return {"name": name, "description": description, "parameters": parameters}


def submit_tighten_event_schema() -> dict[str, Any]:
    return _schema(
        "submit_tighten_event",
        "Return tightened event body slots. Code renders Beginning:/Course:/Outcome:.",
        {
            "beginning": {"type": "string"},
            "course": {"type": "string"},
            "outcome": {"type": "string"},
        },
        ["beginning", "course", "outcome"],
    )


def submit_tighten_fact_schema() -> dict[str, Any]:
    return _schema(
        "submit_tighten_fact",
        "Return tightened fact body slots. Code renders Factual:/Narration:.",
        {
            "kind": {"type": "string", "enum": list(FACT_KIND_ENUM)},
            "content": {"type": "string"},
        },
        ["kind", "content"],
    )


def submit_tighten_procedure_schema() -> dict[str, Any]:
    return _schema(
        "submit_tighten_procedure",
        "Return tightened procedure body slots. Code renders Obstacle:/Solution:.",
        {
            "obstacle": {"type": "string"},
            "solution": {"type": "string"},
        },
        ["obstacle", "solution"],
    )


def submit_tighten_decision_schema() -> dict[str, Any]:
    return _schema(
        "submit_tighten_decision",
        "Return tightened decision body slots. Code renders Preference:/Decision:.",
        {
            "kind": {"type": "string", "enum": list(DECISION_KIND_ENUM)},
            "subject": {"type": "string"},
            "ruling": {"type": "string"},
        },
        ["kind", "subject", "ruling"],
    )


def submit_tighten_text_schema() -> dict[str, Any]:
    return _schema(
        "submit_tighten_text",
        "Return tightened free-form entry text (hot MEMORY/USER/HERMES or merge).",
        {"text": {"type": "string"}},
        ["text"],
    )


def all_tighten_tool_schemas() -> list[dict[str, Any]]:
    return [
        submit_tighten_event_schema(),
        submit_tighten_fact_schema(),
        submit_tighten_procedure_schema(),
        submit_tighten_decision_schema(),
        submit_tighten_text_schema(),
    ]


def force_tool_for_kind(kind: str) -> str:
    mapping = {
        "event": "submit_tighten_event",
        "fact": "submit_tighten_fact",
        "procedure": "submit_tighten_procedure",
        "decision": "submit_tighten_decision",
        "text": "submit_tighten_text",
    }
    return mapping.get(kind, "submit_tighten_text")


def tool_schema_for_kind(kind: str) -> dict[str, Any]:
    name = force_tool_for_kind(kind)
    for schema in all_tighten_tool_schemas():
        if schema["name"] == name:
            return schema
    return submit_tighten_text_schema()


def infer_tighten_kind(text: str, explicit: str | None = None) -> str:
    hinted = str(explicit or "").strip().casefold()
    if hinted in WORKER_TYPES:
        return hinted
    if hinted == "text":
        return "text"
    match = _TYPE_LINE_RE.search(text or "")
    if match:
        return match.group(1).casefold()
    body = text or ""
    if (
        re.search(r"\bBeginning:", body)
        and re.search(r"\bCourse:", body)
        and re.search(r"\bOutcome:", body)
    ):
        return "event"
    if re.search(r"\bObstacle:", body) and re.search(r"\bSolution:", body):
        return "procedure"
    if re.search(r"^(Preference|Decision):", body.strip(), re.MULTILINE):
        return "decision"
    if re.search(r"^(Factual|Narration):", body.strip(), re.MULTILINE):
        return "fact"
    return "text"


def parse_body_slots(kind: str, body: str) -> dict[str, str]:
    """Inverse of Phase-1 ``render_body_from_slots`` (same regex as digest_tools)."""
    wt = str(kind or "").strip().lower()
    text = str(body or "").strip()
    slots: dict[str, str] = {}
    if wt == "event":
        match = re.match(
            r"^Beginning:\s*(.*?);\s*Course:\s*(.*?);\s*Outcome:\s*(.*)$",
            text,
            flags=re.DOTALL,
        )
        if not match:
            match = re.search(
                r"Beginning:\s*(.*?)\s*Course:\s*(.*?)\s*Outcome:\s*(.*)$",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )
        if match:
            slots = {
                "beginning": match.group(1).strip().rstrip(";").strip(),
                "course": match.group(2).strip().rstrip(";").strip(),
                "outcome": match.group(3).strip(),
            }
    elif wt == "procedure":
        match = re.match(
            r"^Obstacle:\s*(.*?);\s*Solution:\s*(.*)$",
            text,
            flags=re.DOTALL,
        )
        if match:
            slots = {
                "obstacle": match.group(1).strip(),
                "solution": match.group(2).strip(),
            }
    elif wt == "decision":
        match = re.match(
            r"^(Preference|Decision):\s*(\S+)\s*(.*)$",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            prefix = match.group(1)
            slots = {
                "kind": "Preference" if prefix.lower() == "preference" else "Decision",
                "subject": match.group(2).strip(),
                "ruling": match.group(3).strip(),
            }
    elif wt == "fact":
        if text.startswith("Narration:"):
            slots = {"kind": "Narration", "content": text[len("Narration:") :].strip()}
        elif text.startswith("Factual:"):
            slots = {"kind": "Factual", "content": text[len("Factual:") :].strip()}
        elif text:
            slots = {"kind": "Factual", "content": text}
    return slots


def current_json_for_prompt(kind: str, body: str) -> str:
    if kind == "text":
        return json.dumps({"text": body}, ensure_ascii=False, indent=2)
    slots = parse_body_slots(kind, body)
    if not slots:
        slots = {"text": body}
    return json.dumps(slots, ensure_ascii=False, indent=2)


def _json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        val = json.loads(text)
    except json.JSONDecodeError:
        return None
    return dict(val) if isinstance(val, Mapping) else None


_SLOT_KEYS = {
    "event": ("beginning", "course", "outcome"),
    "fact": ("kind", "content"),
    "procedure": ("obstacle", "solution"),
    "decision": ("kind", "subject", "ruling"),
    "text": ("text",),
}


def coerce_tighten_args(
    kind: str,
    args: Mapping[str, Any] | None,
    *,
    extra_text: str = "",
) -> dict[str, Any]:
    """Accept nested JSON, capitalized keys, or a prefixed body string."""
    bag = dict(args or {})
    events = bag.get("events")
    if isinstance(events, list) and events and isinstance(events[0], Mapping):
        bag.update(dict(events[0]))
    for wrap in ("arguments", "parameters", "input", "slots", kind):
        nested = _json_object(bag.get(wrap))
        if nested:
            bag.update(nested)
    folded = {str(key).strip().lower(): value for key, value in bag.items()}
    for key in _SLOT_KEYS.get(kind, ()):
        if key in folded and key not in bag:
            bag[key] = folded[key]
        elif key in folded:
            bag[key] = folded[key]
    if kind != "text":
        blobs = [
            bag.get("text"),
            bag.get("body"),
            bag.get("entry"),
            extra_text,
        ]
        for blob in blobs:
            parsed = parse_body_slots(kind, str(blob or ""))
            if not parsed:
                nested = _json_object(blob)
                if nested:
                    parsed = coerce_tighten_args(kind, nested)
            for key, value in (parsed or {}).items():
                if value and not str(bag.get(key) or "").strip():
                    bag[key] = value
    elif extra_text and not str(bag.get("text") or "").strip():
        bag["text"] = extra_text
    return bag


def normalize_tighten_args(
    kind: str,
    args: Mapping[str, Any] | None,
    *,
    current: Mapping[str, Any] | None = None,
    extra_text: str = "",
) -> dict[str, Any]:
    bag = coerce_tighten_args(kind, args, extra_text=extra_text)
    nested = bag.get(kind)
    if isinstance(nested, dict):
        bag = {**bag, **nested}
    if kind != "text":
        raw_text = str(bag.get("text") or "").strip()
        if raw_text:
            parsed = parse_body_slots(kind, raw_text)
            for key, value in parsed.items():
                if value and not str(bag.get(key) or "").strip():
                    bag[key] = value
    merged = dict(current or {})
    for key, value in bag.items():
        if value is None:
            continue
        text = str(value).strip() if not isinstance(value, (dict, list)) else value
        if text == "" or text is None:
            continue
        merged[key] = text
    return merged


def _render_typed_body(kind: str, bag: Mapping[str, Any]) -> str:
    if kind == "event":
        return (
            f"Beginning: {str(bag.get('beginning', '')).strip()}; "
            f"Course: {str(bag.get('course', '')).strip()}; "
            f"Outcome: {str(bag.get('outcome', '')).strip()}"
        )
    if kind == "procedure":
        return (
            f"Obstacle: {str(bag.get('obstacle', '')).strip()}; "
            f"Solution: {str(bag.get('solution', '')).strip()}"
        )
    if kind == "decision":
        return (
            f"{str(bag.get('kind', '')).strip()}: "
            f"{str(bag.get('subject', '')).strip()} "
            f"{str(bag.get('ruling', '')).strip()}"
        ).strip()
    fact_kind = str(bag.get("kind") or "").strip()
    content = str(bag.get("content") or "").strip()
    return f"{fact_kind}: {content}".strip()


def render_tighten_args(kind: str, args: Mapping[str, Any] | None) -> str:
    bag = dict(args or {})
    if kind == "text":
        text = str(bag.get("text") or "").strip()
        if not text:
            raise ValueError("tighten text is empty")
        return text
    required = {
        "event": ("beginning", "course", "outcome"),
        "fact": ("kind", "content"),
        "procedure": ("obstacle", "solution"),
        "decision": ("kind", "subject", "ruling"),
    }.get(kind, ())
    missing = [key for key in required if not str(bag.get(key) or "").strip()]
    if missing:
        raise ValueError(f"tighten {kind} missing {', '.join(missing)}")
    if kind == "fact" and str(bag.get("kind") or "").strip() not in FACT_KIND_ENUM:
        raise ValueError("fact kind must be Factual or Narration")
    if kind == "decision" and str(bag.get("kind") or "").strip() not in DECISION_KIND_ENUM:
        raise ValueError("decision kind must be Preference or Decision")
    rendered = _render_typed_body(kind, bag).strip()
    if not rendered:
        raise ValueError("empty tighten render")
    return rendered
