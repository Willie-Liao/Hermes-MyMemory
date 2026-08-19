"""Digest worker/proposer tool schemas + merge/render helpers.

Tool schemas are the templates; models fill argument values via forced tool
calls. Patch tools expose the same slots as optional properties; code merges
via ``merge_field_patch``.

Registration uses Hermes ``tools.registry.registry`` with toolset
``memory_digest``. Capture path: ``run_worker_llm_tools`` /
``request_overrides.tool_choice`` (see ``plugins/worker_llm.py``).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger("plugins.memory-digest")

CONFIDENCE_ENUM = ("explicit", "high", "medium", "low")
FACT_KIND_ENUM = ("Factual", "Narration")
DECISION_KIND_ENUM = ("Preference", "Decision")
WORKER_STATUS_ENUM = ("candidate",)
# Phase-2 LLM ops only. create is Phase-1 persist (legacy if a proposer still emits it).
OP_ENUM = ("update", "merge", "drop", "supersede")
EVENT_REQUIRED_ROLES = ("requester", "executor")
DIGEST_TOOLSET = "memory_digest"
IMPORTANCE_WRITE_MIN = 1
IMPORTANCE_DEFAULT = 3
IMPORTANCE_DIRTY = 2
IMPORTANCE_MAX = 5
PHASE1_MAX_VALIDATION_ATTEMPTS = 3
PHASE1_SUBMIT_TOOL = "submit_digest_blocks"
PHASE1_PATCH_TOOL = "patch_digest_blocks"
PHASE1_TOOL_NAMES = (
    PHASE1_SUBMIT_TOOL,
    PHASE1_PATCH_TOOL,
    "skip_digest_worker",
)

WORKER_TYPES = ("event", "fact", "procedure", "decision")

# Semantic body slots owned by tool args (code renders prefixes).
WORKER_REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "event": ("beginning", "course", "outcome"),
    "procedure": ("obstacle", "solution"),
    "decision": ("kind", "subject", "ruling"),
    "fact": ("kind", "content"),
}

# Keep in sync with operations.MAX_BODY_CHARS (cannot import operations here).
RENDERED_BODY_MAX = 500
# Slot caps so assembled prefixed bodies stay <= RENDERED_BODY_MAX.
SLOT_MAX_LENGTH: dict[str, int] = {
    "beginning": 156,
    "course": 156,
    "outcome": 156,
    "content": 489,
    "obstacle": 239,
    "solution": 239,
    "subject": 40,
    "ruling": 440,
}
_EVENT_STAGE_KEYS = ("beginning", "course", "outcome")
_EXTRA_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"])")
_MEM_TYPE_IN_ID_RE = re.compile(
    r"^mem-(?:\d{4}-\d{2}-\d{2}|\d{8})-(event|fact|procedure|decision)-"
)

# Option A: merge rewrite nests on the shared submit_operations op item.
MERGE_NEST_KEYS = ("event", "procedure", "decision", "fact")


def _truncate_rendered_body(text: str) -> str:
    body = str(text or "").strip()
    if len(body) <= RENDERED_BODY_MAX:
        return body
    trimmed = body[: RENDERED_BODY_MAX - 1].rstrip()
    return f"{trimmed}…"


def render_body_from_slots(worker_type: str, slots: Mapping[str, Any] | None) -> str:
    """Code-owned one-line body from semantic slots (workers + merge apply)."""
    wt = worker_type.strip().lower()
    bag = dict(slots or {})
    if wt == "event":
        body = (
            f"Beginning: {str(bag.get('beginning', '')).strip()}; "
            f"Course: {str(bag.get('course', '')).strip()}; "
            f"Outcome: {str(bag.get('outcome', '')).strip()}"
        )
        return _truncate_rendered_body(body)
    if wt == "procedure":
        body = (
            f"Obstacle: {str(bag.get('obstacle', '')).strip()}; "
            f"Solution: {str(bag.get('solution', '')).strip()}"
        )
        return _truncate_rendered_body(body)
    if wt == "decision":
        kind = str(bag.get("kind") or "").strip()
        if kind not in DECISION_KIND_ENUM:
            kind = kind or "Decision"
        subject = str(bag.get("subject", "")).strip()
        ruling = str(bag.get("ruling", "")).strip()
        return _truncate_rendered_body(f"{kind}: {subject} {ruling}".strip())
    # fact
    kind = str(bag.get("kind") or "").strip()
    content = str(bag.get("content") or "").strip()
    narration = str(bag.get("narration") or "").strip()
    legacy_body = str(bag.get("body") or "").strip()
    if not kind or not content:
        if narration:
            kind = kind or "Narration"
            if not content:
                content = (
                    narration[len("Narration:") :].strip()
                    if narration.startswith("Narration:")
                    else narration
                )
        elif legacy_body:
            kind = kind or "Factual"
            if not content:
                content = (
                    legacy_body[len("Factual:") :].strip()
                    if legacy_body.startswith("Factual:")
                    else legacy_body
                )
    if not kind:
        kind = "Factual"
    return _truncate_rendered_body(f"{kind}: {content}".strip())


def merge_slots_for_type(
    op: Mapping[str, Any] | None,
    block_type: str,
) -> dict[str, Any] | None:
    """Return the nest matching ``block_type`` if present and a mapping."""
    wt = block_type.strip().lower()
    if wt not in MERGE_NEST_KEYS:
        return None
    bag = dict(op or {})
    nest = bag.get(wt)
    if isinstance(nest, Mapping):
        return dict(nest)
    return None


def _merge_nest_object_schema(worker_type: str) -> dict[str, Any]:
    """JSON Schema for one Option A merge nest (required + minLength: 1)."""
    wt = worker_type.strip().lower()
    if wt == "event":
        return {
            "type": "object",
            "additionalProperties": False,
            "description": (
                "Use ONLY when survivor type is event. All fields REQUIRED "
                "non-empty. Do not also send procedure/decision/fact."
            ),
            "required": ["beginning", "course", "outcome"],
            "properties": {
                "beginning": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "REQUIRED; one concise sentence for merged Beginning"
                    ),
                },
                "course": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "REQUIRED; one concise sentence for merged Course"
                    ),
                },
                "outcome": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "REQUIRED; one concise sentence for merged Outcome"
                    ),
                },
            },
        }
    if wt == "procedure":
        return {
            "type": "object",
            "additionalProperties": False,
            "description": (
                "Use ONLY when survivor type is procedure. All fields REQUIRED "
                "non-empty. Do not also send event/decision/fact."
            ),
            "required": ["obstacle", "solution"],
            "properties": {
                "obstacle": {
                    "type": "string",
                    "minLength": 1,
                    "description": "REQUIRED non-empty; merged Obstacle meaning",
                },
                "solution": {
                    "type": "string",
                    "minLength": 1,
                    "description": "REQUIRED non-empty; merged Solution meaning",
                },
            },
        }
    if wt == "decision":
        return {
            "type": "object",
            "additionalProperties": False,
            "description": (
                "Use ONLY when survivor type is decision. All fields REQUIRED "
                "non-empty. Do not also send event/procedure/fact."
            ),
            "required": ["kind", "subject", "ruling"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(DECISION_KIND_ENUM),
                    "description": "REQUIRED; Preference|Decision",
                },
                "subject": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "REQUIRED; user/User or USER.md alias; grammatical "
                        "subject of the assembled clause"
                    ),
                },
                "ruling": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "REQUIRED predicate for subject (must/must-not/"
                        "standing pref); do not repeat subject; do not make "
                        "a third party the agent"
                    ),
                },
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "Use ONLY when survivor type is fact. Factual may absorb into "
            "Narration (kind=Narration; drop Factual ids)."
        ),
        "required": ["kind", "content"],
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(FACT_KIND_ENUM),
                "description": (
                    "REQUIRED; use Narration when cast/story / involves>=2 / "
                    "survivor already Narration"
                ),
            },
            "content": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "REQUIRED non-empty; text after prefix "
                    "(code renders from kind)"
                ),
            },
        },
    }

_REGISTERED = False

DECISION_OWNERSHIP_TEACH = (
    "decision bodies must start with Preference:/Decision: whose first subject "
    "token is user/User (plus aliases from USER.md); third-party traits belong to "
    "fact (Narration: + involves cast); only the user's ruling/preference for "
    "agent behavior is decision"
)
DECISION_LINK_TEACH = (
    "ruling must be the predicate for subject so `{kind}: {subject} {ruling}` "
    "is one clause (e.g. Decision: user must not auto-drop events); do not "
    "repeat subject at the start of ruling; do not make a third party the "
    "agent of the ruling; look in the transcript for the user's must / "
    "must-not / standing prefs for agent behavior"
)
_DECISION_KIND_PREFIX_RE = re.compile(
    r"^(Preference|Decision):\s*",
    re.IGNORECASE,
)
_DECISION_PREDICATE_STARTERS = frozenset(
    {
        "a", "an", "the",
        "must", "mustn't", "mustnt",
        "don't", "dont", "do", "never", "always",
        "wants", "want", "prefer", "prefers", "preferred",
        "keep", "use", "allow", "require", "required",
        "ruled", "instruct", "instructs", "hand",
        "translate", "append", "should", "shall", "will",
        "not", "no",
    }
)
FACT_NARRATION_TEACH = (
    "multi-cast facts need kind=Narration (body prefix Narration:) and a "
    "non-empty involves entity collection (optional roles); Factual facts use "
    "kind=Factual and keep involves empty or a single related entity"
)
_DECISION_OBSERVATIONAL_RE = re.compile(
    r"\b(?:stated|said|mentioned)\b",
    re.IGNORECASE,
)
_DECISION_CONSTRAINT_CUE_RE = re.compile(
    r"\b(?:ruled|must not|must|wants|instruct|prefer)\b",
    re.IGNORECASE,
)
_PROCEDURE_TOOL_LOG_TOKENS = ("[tool]", "tool_call", "raw tool log")


def _decision_strip_leading_subject(subject: str, ruling: str) -> str:
    """Drop a duplicated leading subject token from ruling (never invent text)."""
    subj = str(subject or "").strip()
    text = str(ruling or "").strip()
    if not subj or not text:
        return text
    pattern = re.compile(rf"^{re.escape(subj)}\b[\s,.:;]*", re.IGNORECASE)
    return pattern.sub("", text, count=1).strip()


def _decision_ruling_from_body(body: str, subject: str = "user") -> str:
    """Predicate ruling from a rendered or raw decision body."""
    text = str(body or "").strip()
    match = _DECISION_KIND_PREFIX_RE.match(text)
    if match:
        text = text[match.end() :].strip()
    stripped = _decision_strip_leading_subject(subject, text)
    return stripped or text


def _decision_third_party_agent(subject: str, ruling: str, allowed_subjects: set[str]) -> bool:
    """True when ruling's first token looks like someone other than subject."""
    text = str(ruling or "").strip()
    if not text:
        return False
    first = text.split()[0].strip(".,;:")
    if not first:
        return False
    folded = first.casefold()
    if folded in allowed_subjects or folded == str(subject or "").strip().casefold():
        return False
    if folded in _DECISION_PREDICATE_STARTERS:
        return False
    return bool(first[:1].isupper() and first[1:].isalpha() and len(first) > 1)


def _fact_kind_and_content(bag: Mapping[str, Any]) -> tuple[str, str]:
    """Resolve fact kind/content including legacy narration/body shims."""
    kind = str(bag.get("kind", "")).strip()
    if not kind:
        if str(bag.get("narration") or "").strip():
            kind = "Narration"
        elif str(bag.get("body") or "").strip():
            kind = "Factual"
    content = str(bag.get("content") or "").strip()
    if not content:
        if kind == "Narration":
            content = str(bag.get("narration") or "").strip()
            if content.startswith("Narration:"):
                content = content[len("Narration:") :].strip()
        elif kind == "Factual":
            content = str(bag.get("body") or "").strip()
            if content.startswith("Factual:"):
                content = content[len("Factual:") :].strip()
    return kind, content


def validate_worker_slot_args(
    worker_type: str,
    args: Mapping[str, Any] | None,
) -> list[str]:
    """Equal field checks on tool-arg slots: enums + non-empty required keys."""
    wt = worker_type.strip().lower()
    if wt not in WORKER_REQUIRED_SLOTS:
        return [f"unknown worker type {worker_type!r}"]
    bag = dict(args or {})
    errors: list[str] = []

    if wt == "fact":
        kind, content = _fact_kind_and_content(bag)
        if kind and kind not in FACT_KIND_ENUM:
            errors.append(f"invalid kind {kind!r}")
        elif not kind:
            errors.append("kind must be non-empty")
        if not content:
            errors.append("content must be non-empty")
        return errors

    if wt == "decision":
        kind = str(bag.get("kind", "")).strip()
        if kind and kind not in DECISION_KIND_ENUM:
            errors.append(f"invalid kind {kind!r}")
        elif not kind:
            errors.append("kind must be non-empty")

    for key in WORKER_REQUIRED_SLOTS[wt]:
        if wt == "decision" and key == "kind":
            continue  # handled above
        val = bag.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            errors.append(f"{key} must be non-empty")
        elif isinstance(val, (list, dict)) and not val:
            errors.append(f"{key} must be non-empty")
    return errors


def validate_worker_tool_args(
    worker_type: str,
    args: Mapping[str, Any] | None,
    *,
    user_subjects: frozenset[str] | set[str] | None = None,
) -> list[str]:
    """Primary worker gate on tool-arg dict (slots + semantic ownership rules)."""
    wt = worker_type.strip().lower()
    errors = list(validate_worker_slot_args(wt, args))
    bag = dict(args or {})
    allowed_subjects = {
        str(s).strip().casefold()
        for s in (user_subjects if user_subjects is not None else {"user"})
        if str(s).strip()
    } or {"user"}

    if wt == "event":
        participants = bag.get("participants")
        if not isinstance(participants, list) or not participants:
            errors.append("event worker requires non-empty participants")
        else:
            participant_roles = {
                (
                    str(part.get("entity", "")).strip().casefold(),
                    str(part.get("role", "")).strip().casefold(),
                )
                for part in participants
                if isinstance(part, dict)
            }
            required_participants = {
                ("user", "requester"),
                ("assistant", "executor"),
            }
            if not required_participants.issubset(participant_roles):
                errors.append(
                    "event worker requires User/requester and "
                    "Assistant/executor participants"
                )

    elif wt == "procedure":
        blob = (
            f"{bag.get('obstacle', '')} {bag.get('solution', '')}".casefold()
        )
        if any(token in blob for token in _PROCEDURE_TOOL_LOG_TOKENS):
            errors.append("procedure body must not contain raw tool logs")

    elif wt == "decision":
        subject = str(bag.get("subject", "")).strip()
        if subject and subject.casefold() not in allowed_subjects:
            errors.append(DECISION_OWNERSHIP_TEACH)
        else:
            ruling = str(bag.get("ruling", "")).strip()
            body = render_body_from_slots("decision", bag)
            if _DECISION_OBSERVATIONAL_RE.search(body) and not (
                _DECISION_CONSTRAINT_CUE_RE.search(ruling)
                or _DECISION_CONSTRAINT_CUE_RE.search(body)
            ):
                errors.append(DECISION_OWNERSHIP_TEACH)
            elif _decision_third_party_agent(subject, ruling, allowed_subjects):
                errors.append(DECISION_LINK_TEACH)

    elif wt == "fact":
        kind, _content = _fact_kind_and_content(bag)
        involves = bag.get("involves")
        involve_count = len(involves) if isinstance(involves, list) else 0
        is_narration = kind == "Narration"
        if involve_count >= 2 and not is_narration:
            errors.append(FACT_NARRATION_TEACH)
        elif is_narration and involve_count < 1:
            errors.append(FACT_NARRATION_TEACH)

    return list(dict.fromkeys(errors))


def parse_rendered_body_slots(worker_type: str, body: str) -> dict[str, str]:
    """Parse code-rendered body into semantic slots (legacy fact = Factual)."""
    wt = worker_type.strip().lower()
    text = str(body or "").strip()
    slots: dict[str, str] = {}
    if wt == "event":
        m = re.match(
            r"^Beginning:\s*(.*?);\s*Course:\s*(.*?);\s*Outcome:\s*(.*)$",
            text,
            flags=re.DOTALL,
        )
        if m:
            slots = {
                "beginning": m.group(1).strip(),
                "course": m.group(2).strip(),
                "outcome": m.group(3).strip(),
            }
    elif wt == "procedure":
        m = re.match(
            r"^Obstacle:\s*(.*?);\s*Solution:\s*(.*)$",
            text,
            flags=re.DOTALL,
        )
        if m:
            slots = {
                "obstacle": m.group(1).strip(),
                "solution": m.group(2).strip(),
            }
    elif wt == "decision":
        m = re.match(
            r"^(Preference|Decision):\s*(\S+)\s*(.*)$",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            kind = m.group(1)
            kind = "Preference" if kind.lower() == "preference" else "Decision"
            slots = {
                "kind": kind,
                "subject": m.group(2).strip(),
                "ruling": m.group(3).strip(),
            }
    elif wt == "fact":
        if text.startswith("Narration:"):
            slots = {
                "kind": "Narration",
                "content": text[len("Narration:") :].strip(),
            }
        elif text.startswith("Factual:"):
            slots = {
                "kind": "Factual",
                "content": text[len("Factual:") :].strip(),
            }
        else:
            # Legacy unprefixed plain fact → Factual on read.
            slots = {"kind": "Factual", "content": text}
    return slots


def validate_rendered_body_slots(worker_type: str, body: str) -> list[str]:
    """Non-empty / enum checks on rendered body slots (equal field errors)."""
    wt = worker_type.strip().lower()
    slots = parse_rendered_body_slots(wt, body)
    if not slots and str(body or "").strip():
        # Unparseable structured body — fall back to whole-body emptiness only.
        return []
    return validate_worker_slot_args(wt, slots)


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


def merge_operations_patch(
    previous: Sequence[Any],
    patch: Mapping[str, Any],
) -> list[Any]:
    """Merge a patch_operations payload onto the last proposed ops list.

    - If ``operations`` is present, replace the full list (one call can fix many).
    - Else ``replace_index`` + ``operation_patch`` shallow-merges one item.
    """
    if "operations" in patch and isinstance(patch.get("operations"), list):
        return [dict(op) if isinstance(op, Mapping) else op for op in patch["operations"]]
    merged: list[Any] = [
        dict(op) if isinstance(op, Mapping) else op for op in previous
    ]
    if "replace_index" in patch and isinstance(patch.get("operation_patch"), Mapping):
        try:
            idx = int(patch["replace_index"])
        except (TypeError, ValueError):
            return merged
        if 0 <= idx < len(merged) and isinstance(merged[idx], Mapping):
            merged[idx] = merge_field_patch(merged[idx], patch["operation_patch"])
    return merged


def operations_failed_teach(
    errors: Sequence[str],
    *,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> str:
    """Compact teach for proposer patch retries."""
    header = "VALIDATION FAILED — call patch_operations with fixes only."
    if attempt is not None and max_attempts is not None:
        header = (
            f"VALIDATION FAILED (attempt {attempt} of {max_attempts}). "
            "Call patch_operations; one patch may fix many errors."
        )
    lines = [
        header,
        "Prefer replacing the full operations list when several ops are wrong.",
        "Do not emit free-form JSON outside the tool call.",
        "Errors:",
    ]
    for err in list(errors)[:12]:
        lines.append(f"- {err}")
    return "\n".join(lines)


def _confidence_prop() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": list(CONFIDENCE_ENUM),
        "description": "Confidence: explicit|high|medium|low",
    }


def _fact_kind_prop() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": list(FACT_KIND_ENUM),
        "description": "Fact body kind: Factual|Narration (code renders the prefix)",
    }


def _slot_string(description: str, max_length: int) -> dict[str, Any]:
    return {
        "type": "string",
        "maxLength": max_length,
        "description": description,
    }


def _event_related_prop() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Event only: fact/procedure/decision mem-ids or same-batch temp_ids. "
            "Never another event id (episode-merge instead). Associative only; "
            "does not drop or supersede."
        ),
    }


def _importance_prop() -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": IMPORTANCE_WRITE_MIN,
        "maximum": IMPORTANCE_MAX,
        "description": f"Importance {IMPORTANCE_WRITE_MIN}–{IMPORTANCE_MAX} at create",
    }


def _participant_item() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "entity": {"type": "string"},
            "role": {
                "type": "string",
                "description": (
                    "Role string. User/Assistant required rows use "
                    "requester/executor; secondary roles are free text."
                ),
            },
        },
        "required": ["entity"],
    }


def _involves_item() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "entity": {"type": "string"},
            "role": {"type": "string"},
        },
        "required": ["entity"],
    }


def _shared_optional_props() -> dict[str, Any]:
    return {
        "related": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Associative mem-ids (week-alive window)",
        },
        "sources": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional file: or sheet: tags for real artifacts. "
                "Not memories/staging dailies. Code stamps "
                "session <id>#<start>-<end> from the digest window."
            ),
        },
        "valid_from": {"type": "string", "description": "YYYY-MM-DD"},
        "valid_to": {
            "type": "string",
            "description": 'YYYY-MM-DD or "open"',
        },
        "supersedes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Correction targets; requires confidence=explicit",
        },
    }


def _event_props(*, required: bool) -> dict[str, Any]:
    props = {
        "entity": {"type": "string"},
        "predicate": {
            "type": "string",
            "description": "snake_case user intent (e.g. user_requested_*)",
        },
        "participants": {
            "type": "array",
            "items": _participant_item(),
            "description": "Must include User/requester and Assistant/executor",
        },
        "beginning": _slot_string(
            "One concise sentence for Beginning (code renders Beginning:). "
            "Not a paragraph. Combined Beginning/Course/Outcome line <= 500 chars",
            SLOT_MAX_LENGTH["beginning"],
        ),
        "course": _slot_string(
            "One concise sentence for Course (code renders Course:). Not a paragraph",
            SLOT_MAX_LENGTH["course"],
        ),
        "outcome": _slot_string(
            "One concise sentence for Outcome (code renders Outcome:). Not a paragraph",
            SLOT_MAX_LENGTH["outcome"],
        ),
        "confidence": _confidence_prop(),
        "importance": _importance_prop(),
        **_shared_optional_props(),
    }
    props["related"] = _event_related_prop()
    return props


def _fact_props() -> dict[str, Any]:
    return {
        "entity": {"type": "string"},
        "kind": _fact_kind_prop(),
        "content": _slot_string(
            "Fact text after the Factual:/Narration: prefix; assembled body <= 500 chars",
            SLOT_MAX_LENGTH["content"],
        ),
        "involves": {
            "type": "array",
            "items": _involves_item(),
            "description": "Required cast when kind=Narration; optional for Factual",
        },
        "confidence": _confidence_prop(),
        "importance": _importance_prop(),
        **_shared_optional_props(),
    }


def _procedure_props() -> dict[str, Any]:
    return {
        "obstacle": _slot_string(
            "Procedure slot; code renders Obstacle:. Combined Obstacle/Solution "
            "line <= 500 chars",
            SLOT_MAX_LENGTH["obstacle"],
        ),
        "solution": _slot_string(
            "Procedure slot; code renders Solution:. Combined line <= 500 chars",
            SLOT_MAX_LENGTH["solution"],
        ),
        "confidence": _confidence_prop(),
        "importance": _importance_prop(),
        **_shared_optional_props(),
    }


def _decision_props() -> dict[str, Any]:
    return {
        "kind": {
            "type": "string",
            "enum": list(DECISION_KIND_ENUM),
            "description": "Body prefix kind: Preference|Decision",
        },
        "subject": _slot_string(
            "Grammatical subject; must be user/User (or USER.md aliases). "
            "Scan the transcript for the user's must/must-not/standing prefs.",
            SLOT_MAX_LENGTH["subject"],
        ),
        "ruling": _slot_string(
            "Predicate for subject so `{kind}: {subject} {ruling}` is one "
            "clause (e.g. must not auto-drop events). Do not repeat subject. "
            "Assembled Preference:/Decision: line <= 500 chars",
            SLOT_MAX_LENGTH["ruling"],
        ),
        "confidence": _confidence_prop(),
        "importance": _importance_prop(),
        **_shared_optional_props(),
    }


def _schema(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = required
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
    }


def submit_schema(worker_type: str) -> dict[str, Any]:
    wt = worker_type.strip().lower()
    if wt == "event":
        return _schema(
            "submit_event_block",
            "Submit a complete event memory block (full slot fill).",
            _event_props(required=True),
            [
                "entity",
                "predicate",
                "participants",
                "beginning",
                "course",
                "outcome",
                "confidence",
                "importance",
                "valid_from",
                "valid_to",
            ],
        )
    if wt == "fact":
        return _schema(
            "submit_fact_block",
            "Submit a complete fact memory block (full slot fill).",
            _fact_props(),
            ["entity", "kind", "content", "confidence", "importance"],
        )
    if wt == "procedure":
        return _schema(
            "submit_procedure_block",
            "Submit a complete procedure memory block (full slot fill).",
            _procedure_props(),
            ["obstacle", "solution", "confidence", "importance"],
        )
    if wt == "decision":
        return _schema(
            "submit_decision_block",
            "Submit a complete decision memory block (full slot fill).",
            _decision_props(),
            ["kind", "subject", "ruling", "confidence", "importance"],
        )
    raise ValueError(f"unknown worker type: {worker_type!r}")


def patch_schema(worker_type: str) -> dict[str, Any]:
    wt = worker_type.strip().lower()
    props_fn = {
        "event": lambda: _event_props(required=False),
        "fact": _fact_props,
        "procedure": _procedure_props,
        "decision": _decision_props,
    }.get(wt)
    if props_fn is None:
        raise ValueError(f"unknown worker type: {worker_type!r}")
    return _schema(
        f"patch_{wt}_block",
        f"Patch only changed fields on the previous {wt} submit (sparse).",
        props_fn(),
        [],  # all optional
    )


def skip_schema() -> dict[str, Any]:
    return _schema(
        "skip_digest_worker",
        "Skip this worker — nothing durable for the assigned type.",
        {"skip": {"type": "boolean"}},
        ["skip"],
    )


def _operations_op_item_schema() -> dict[str, Any]:
    """Shared op-item schema for submit/patch (Option A merge nests visible)."""
    props: dict[str, Any] = {
        "operation": {"type": "string", "enum": list(OP_ENUM)},
        "id": {"type": "string"},
        "block": {"type": "object"},
        "changes": {"type": "object"},
        "survivor_id": {"type": "string"},
        "absorbed_ids": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "helper_id": {"type": "string"},
        "target_id": {"type": "string"},
        "correction": {"type": "string"},
        "confidence": _confidence_prop(),
    }
    for nest_key in MERGE_NEST_KEYS:
        props[nest_key] = _merge_nest_object_schema(nest_key)
    return {
        "type": "object",
        "properties": props,
        "required": ["operation"],
        "additionalProperties": False,
    }


def submit_operations_schema() -> dict[str, Any]:
    return _schema(
        "submit_operations",
        (
            "Submit phase-2 consolidate ops (update|merge|drop|supersede). "
            "Do not emit create — new cards are already on the daily file. "
            "Empty operations[] keeps them. For merge: fill exactly one nest "
            "matching survivor type (event|procedure|decision|fact); every "
            "nest field REQUIRED non-empty; code renders body (no free-form "
            "body, no concat)."
        ),
        {"operations": {"type": "array", "items": _operations_op_item_schema()}},
        ["operations"],
    )


def submit_day_wrapup_schema() -> dict[str, Any]:
    """Force catalog bullets so 23:55 wrap-up cannot dump a paragraph or YAML.

    Related same-day events may share one phrase; unrelated events stay
    separate. Length matches digest.MAX_WRAPUP_CHARS (200).
    """
    return _schema(
        "submit_day_wrapup",
        (
            "Submit phrases[]: one short sentence per bullet. Related "
            "same-day events may share one bullet. Unrelated events stay "
            "separate. Not a paragraph. No YAML. Each phrase ≤ 200 chars."
        ),
        {
            "phrases": {
                "type": "array",
                "description": (
                    "Catalog bullets: related same-day events may share one "
                    "bullet; unrelated stay separate; each ≤ 200 chars"
                ),
                "items": {"type": "string", "maxLength": 200},
            },
            "phrase": {
                "type": "string",
                "description": "Legacy single line; prefer phrases[]",
            },
        },
        ["phrases"],
    )


def patch_operations_schema() -> dict[str, Any]:
    return _schema(
        "patch_operations",
        (
            "Sparse patch for operations proposal (changed fields / ops only). "
            "When replacing operations, merge items use the same nested slot "
            "objects as submit_operations."
        ),
        {
            "operations": {
                "type": "array",
                "items": _operations_op_item_schema(),
                "description": "Replacement operations list when correcting",
            },
            "replace_index": {"type": "integer"},
            "operation_patch": {
                "type": "object",
                "description": (
                    "Shallow patch onto one prior op; may include the correct "
                    "type nest (event|procedure|decision|fact)"
                ),
            },
        },
        [],
    )


_FLAT_REQUIRED: dict[str, list[str]] = {
    "event": [
        "entity",
        "predicate",
        "participants",
        "beginning",
        "course",
        "outcome",
        "confidence",
        "importance",
        "valid_from",
        "valid_to",
    ],
    "fact": ["entity", "kind", "content", "confidence", "importance"],
    "procedure": ["obstacle", "solution", "confidence", "importance"],
    "decision": ["kind", "subject", "ruling", "confidence", "importance"],
}

_TEMP_ID_PROP: dict[str, Any] = {
    "type": "string",
    "description": (
        "Optional same-batch id (e.g. tmp-event-1) for other blocks' "
        "related[]. Code maps to real mem- ids on render."
    ),
}

# Turn-local state for Phase-1 type-A handlers (same worker chat).
_phase1_turn_state: dict[str, Any] = {
    "previous_args": {},
    "accepted_args": None,
    "fail_count": 0,
    "session_id": "",
}


def reset_phase1_turn_state(*, session_id: str = "") -> None:
    """Clear Phase-1 same-turn handler state before a worker chat."""
    _phase1_turn_state["previous_args"] = {}
    _phase1_turn_state["accepted_args"] = None
    _phase1_turn_state["fail_count"] = 0
    _phase1_turn_state["session_id"] = str(session_id or "")


def get_phase1_turn_state() -> dict[str, Any]:
    """Return a shallow copy of Phase-1 turn-local handler state."""
    return {
        "previous_args": dict(_phase1_turn_state.get("previous_args") or {}),
        "accepted_args": (
            dict(_phase1_turn_state["accepted_args"])
            if isinstance(_phase1_turn_state.get("accepted_args"), Mapping)
            else None
        ),
        "fail_count": int(_phase1_turn_state.get("fail_count") or 0),
        "session_id": str(_phase1_turn_state.get("session_id") or ""),
    }


def _flat_block_variant(worker_type: str) -> dict[str, Any]:
    """One flat oneOf variant: type const + sibling fields (no nest object)."""
    wt = worker_type.strip().lower()
    props_fn = {
        "event": lambda: _event_props(required=True),
        "fact": _fact_props,
        "procedure": _procedure_props,
        "decision": _decision_props,
    }[wt]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", *_FLAT_REQUIRED[wt]],
        "properties": {
            "type": {"const": wt},
            "temp_id": dict(_TEMP_ID_PROP),
            **props_fn(),
        },
    }


def _digest_block_item_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            _flat_block_variant("event"),
            _flat_block_variant("fact"),
            _flat_block_variant("procedure"),
            _flat_block_variant("decision"),
        ]
    }


def submit_digest_blocks_schema() -> dict[str, Any]:
    return _schema(
        "submit_digest_blocks",
        (
            "Submit all durable Phase-1 memory cards for this transcript in one "
            "call. Put event cards first in blocks[]. Each item is flat: type "
            "plus sibling fields (oneOf by type const). Details may related: "
            "same-batch temp ids or week-alive mem-ids. Empty blocks[] means "
            "nothing durable (same as skip). Do not emit free-form body; do not "
            "nest under event/fact/procedure/decision keys."
        ),
        {
            "blocks": {
                "type": "array",
                "description": (
                    "0..N flat cards. Order: all events first, then "
                    "fact/procedure/decision. Cap soft: prefer sparse durable "
                    "cards (daily file still MAX_BLOCKS_PER_FILE=30)."
                ),
                "items": _digest_block_item_schema(),
            }
        },
        ["blocks"],
    )


def patch_digest_blocks_schema() -> dict[str, Any]:
    return _schema(
        "patch_digest_blocks",
        (
            "Sparse correction after validation failure. Prefer replacing full "
            "blocks[] when several cards are wrong; else patch_index + "
            "block_patch shallow-merges one flat item."
        ),
        {
            "blocks": {
                "type": "array",
                "description": "Full replacement list when present (same flat oneOf item schema as submit)",
                "items": _digest_block_item_schema(),
            },
            "patch_index": {"type": "integer", "minimum": 0},
            "block_patch": {
                "type": "object",
                "description": (
                    "Shallow merge onto blocks[patch_index]; flat fields only "
                    "(no type nests)"
                ),
            },
        },
        [],
    )


def tool_names_for_phase1(*, mode: str | None = None) -> list[str]:
    """Phase-1 type A always exposes submit + patch + skip in one turn."""
    del mode  # kept for call-site compatibility; type A ignores mode.
    return list(PHASE1_TOOL_NAMES)


def _flatten_block_item(item: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (worker_type, flat field bag) for one blocks[] item."""
    wt = str(item.get("type", "")).strip().lower()
    nest = item.get(wt)
    if isinstance(nest, Mapping):
        # Legacy nest shape — still flatten for dirty/exhaust paths.
        flat = dict(nest)
        temp_id = str(item.get("temp_id") or "").strip()
        if temp_id:
            flat["temp_id"] = temp_id
        return wt, flat
    flat = {
        key: value
        for key, value in item.items()
        if key != "type" and key not in WORKER_TYPES
    }
    return wt, flat


def validate_digest_blocks_args(
    args: Mapping[str, Any] | None,
    *,
    user_subjects: frozenset[str] | set[str] | None = None,
    allow_importance_dirty: bool = False,
) -> list[str]:
    """Validate submit/patch digest_blocks raw args before render (flat oneOf)."""
    bag = dict(args or {})
    if "blocks" not in bag:
        return ["blocks is required"]
    blocks = bag.get("blocks")
    if not isinstance(blocks, list):
        return ["blocks must be an array"]
    errors: list[str] = []
    seen_temp: set[str] = set()
    for index, raw in enumerate(blocks):
        prefix = f"blocks[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        wt = str(raw.get("type", "")).strip().lower()
        if wt not in WORKER_TYPES:
            errors.append(f"{prefix}.type must be one of {list(WORKER_TYPES)}")
            continue
        present_nests = [k for k in WORKER_TYPES if isinstance(raw.get(k), Mapping)]
        if present_nests:
            errors.append(
                f"{prefix}: use flat fields with type={wt}; do not nest under "
                f"{present_nests}"
            )
            continue
        temp_id = str(raw.get("temp_id") or "").strip()
        if temp_id:
            if temp_id in seen_temp:
                errors.append(f"{prefix}.temp_id {temp_id!r} is duplicated")
            seen_temp.add(temp_id)
        flat = {
            key: value
            for key, value in raw.items()
            if key not in {"type", *WORKER_TYPES}
        }
        for err in validate_worker_tool_args(
            wt, flat, user_subjects=user_subjects
        ):
            errors.append(f"{prefix}: {err}")
        if "importance" in flat and flat.get("importance") is not None:
            try:
                imp = int(flat["importance"])
            except (TypeError, ValueError):
                errors.append(f"{prefix}: importance must be an integer")
            else:
                lo = IMPORTANCE_WRITE_MIN
                if allow_importance_dirty:
                    lo = min(IMPORTANCE_DIRTY, IMPORTANCE_WRITE_MIN)
                if imp < lo or imp > IMPORTANCE_MAX:
                    errors.append(
                        f"{prefix}: importance must be integer {lo}–{IMPORTANCE_MAX}"
                    )
    return list(dict.fromkeys(errors))


def merge_digest_blocks_patch(
    previous: Mapping[str, Any],
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge patch_digest_blocks onto previous submit/patch args.

    - If ``blocks`` is a list, replace the full list.
    - Else ``patch_index`` + ``block_patch`` shallow-merges one flat item.
    """
    prev_blocks = previous.get("blocks")
    base: list[Any] = (
        [dict(b) if isinstance(b, Mapping) else b for b in prev_blocks]
        if isinstance(prev_blocks, list)
        else []
    )
    if isinstance(patch.get("blocks"), list):
        return {
            "blocks": [
                dict(b) if isinstance(b, Mapping) else b for b in patch["blocks"]
            ]
        }
    merged_blocks = list(base)
    if "patch_index" in patch and isinstance(patch.get("block_patch"), Mapping):
        try:
            idx = int(patch["patch_index"])
        except (TypeError, ValueError):
            return {"blocks": merged_blocks}
        if 0 <= idx < len(merged_blocks) and isinstance(merged_blocks[idx], Mapping):
            item = dict(merged_blocks[idx])
            for key, value in patch["block_patch"].items():
                if value is None:
                    continue
                item[key] = value
            merged_blocks[idx] = item
    return {"blocks": merged_blocks}


def digest_blocks_failed_teach(
    errors: Sequence[str],
    previous: Mapping[str, Any],
    *,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> str:
    """Compact teach for phase-1 patch_digest_blocks retries (type A)."""
    header = (
        "VALIDATION FAILED — call patch_digest_blocks with flat oneOf fixes."
    )
    if attempt is not None and max_attempts is not None:
        header = (
            f"VALIDATION FAILED (attempt {attempt} of {max_attempts}). "
            "Call patch_digest_blocks; prefer full blocks[] replace when "
            "several cards are wrong. Keep fields flat (type + siblings)."
        )
    lines = [
        header,
        "Do not emit free-form YAML/JSON outside the tool call.",
        "Keep fields flat (type + siblings).",
        f"Closed enums: confidence={list(CONFIDENCE_ENUM)}.",
        "Errors:",
    ]
    for err in list(errors)[:12]:
        lines.append(f"- {err}")
    prev_blocks = previous.get("blocks")
    if isinstance(prev_blocks, list) and prev_blocks:
        lines.append(f"Prior blocks count: {len(prev_blocks)} (reference only).")
    return "\n".join(lines)


def clamp_blocks_importance_dirty(
    args: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a copy of digest args with every block importance clamped to 2."""
    bag = dict(args or {})
    raw_blocks = bag.get("blocks")
    if not isinstance(raw_blocks, list):
        return bag
    out_blocks: list[Any] = []
    for raw in raw_blocks:
        if not isinstance(raw, Mapping):
            out_blocks.append(raw)
            continue
        item = dict(raw)
        present_nests = [k for k in WORKER_TYPES if isinstance(item.get(k), Mapping)]
        if present_nests:
            wt = str(item.get("type", "")).strip().lower()
            nest_key = wt if wt in present_nests else present_nests[0]
            nest = dict(item[nest_key])
            nest["importance"] = IMPORTANCE_DIRTY
            item[nest_key] = nest
        else:
            item["importance"] = IMPORTANCE_DIRTY
        out_blocks.append(item)
    bag["blocks"] = out_blocks
    return bag


def _batch_type_map(blocks: Sequence[Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in blocks:
        if not isinstance(raw, Mapping):
            continue
        wt = str(raw.get("type", "")).strip().lower()
        if wt not in WORKER_TYPES:
            continue
        temp_id = str(raw.get("temp_id") or "").strip()
        if temp_id:
            mapping[temp_id] = wt
        block_id = str(raw.get("id") or "").strip()
        if block_id:
            mapping[block_id] = wt
    return mapping


def _ref_is_event(ref: str, type_map: Mapping[str, str]) -> bool:
    rid = str(ref or "").strip()
    if not rid:
        return False
    if type_map.get(rid) == "event":
        return True
    match = _MEM_TYPE_IN_ID_RE.match(rid)
    return bool(match and match.group(1) == "event")


def _truncate_slot_value(value: Any, max_length: int) -> str:
    text = str(value)
    if len(text) <= max_length:
        return text
    return text[:max_length]


def _first_sentence(value: Any) -> str:
    """Keep the first sentence only. Never invent replacement text."""
    text = str(value or "").strip()
    if not text:
        return text
    parts = _EXTRA_SENTENCE_RE.split(text, maxsplit=1)
    return parts[0].strip()


def accept_phase1_args(
    args: Mapping[str, Any] | None,
    *,
    user_subjects: frozenset[str] | set[str] | None = None,
    allow_importance_dirty: bool = False,
    extra_type_map: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Sanitize then validate Phase-1 JSON. One source of truth for handler + persist."""
    bag, notes = sanitize_digest_blocks_args(
        args, extra_type_map=extra_type_map
    )
    if notes:
        logger.info("phase1 sanitize: %s", "; ".join(notes[:8]))
    errors = validate_digest_blocks_args(
        bag,
        user_subjects=user_subjects,
        allow_importance_dirty=allow_importance_dirty,
    )
    return bag, errors, notes


def _flatten_nested_block_item(item: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Lift legacy type-nests onto sibling fields. Returns (item, note or None)."""
    present_nests = [k for k in WORKER_TYPES if isinstance(item.get(k), Mapping)]
    if not present_nests:
        return dict(item), None
    wt = str(item.get("type", "")).strip().lower()
    nest_key = wt if wt in present_nests else present_nests[0]
    nest = dict(item[nest_key])
    flat = {
        key: value
        for key, value in item.items()
        if key not in WORKER_TYPES
    }
    merged = {**nest, **{k: v for k, v in flat.items() if k != "type"}}
    if wt not in WORKER_TYPES:
        wt = nest_key
    merged["type"] = wt
    return merged, f"flattened nested {nest_key}{{}}"


def _default_block_kind(wt: str, item: dict[str, Any]) -> str | None:
    if wt == "fact":
        kind, _content = _fact_kind_and_content(item)
        if kind not in FACT_KIND_ENUM:
            item["kind"] = "Factual"
            return "kind defaulted to Factual"
        item["kind"] = kind
        return None
    if wt == "decision":
        kind = str(item.get("kind", "")).strip()
        if kind not in DECISION_KIND_ENUM:
            item["kind"] = "Decision"
            return "kind defaulted to Decision"
        item["kind"] = kind
        return None
    return None


def _inject_event_roles(item: dict[str, Any]) -> str | None:
    required = (
        {"entity": "User", "role": "requester"},
        {"entity": "Assistant", "role": "executor"},
    )
    raw = item.get("participants")
    participants: list[Any] = list(raw) if isinstance(raw, list) else []
    roles = {
        (
            str(part.get("entity", "")).strip().casefold(),
            str(part.get("role", "")).strip().casefold(),
        )
        for part in participants
        if isinstance(part, dict)
    }
    added = 0
    for part in required:
        key = (part["entity"].casefold(), part["role"].casefold())
        if key not in roles:
            participants.append(dict(part))
            added += 1
    if added:
        item["participants"] = participants
        return f"injected {added} required event participant(s)"
    return None


def sanitize_digest_blocks_args(
    args: Mapping[str, Any] | None,
    *,
    extra_type_map: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Mechanical Phase-1 fixes (truncate, flatten, defaults). Never LLM."""
    bag = dict(args or {})
    raw_blocks = bag.get("blocks")
    if not isinstance(raw_blocks, list):
        return bag, []
    notes: list[str] = []
    flattened: list[Any] = []
    for index, raw in enumerate(raw_blocks):
        if not isinstance(raw, Mapping):
            flattened.append(raw)
            continue
        item, flatten_note = _flatten_nested_block_item(raw)
        if flatten_note:
            notes.append(f"blocks[{index}]: {flatten_note}")
        flattened.append(item)
    type_map = dict(extra_type_map or {})
    type_map.update(_batch_type_map(flattened))
    seen_temp: set[str] = set()
    out_blocks: list[Any] = []
    for index, raw in enumerate(flattened):
        if not isinstance(raw, Mapping):
            out_blocks.append(raw)
            continue
        item = dict(raw)
        wt = str(item.get("type", "")).strip().lower()
        kind_note = _default_block_kind(wt, item)
        if kind_note:
            notes.append(f"blocks[{index}]: {kind_note}")
        raw_imp = item.get("importance", IMPORTANCE_DEFAULT)
        try:
            imp = int(raw_imp)
        except (TypeError, ValueError):
            imp = IMPORTANCE_DEFAULT
            notes.append(f"blocks[{index}].importance defaulted to {IMPORTANCE_DEFAULT}")
        else:
            if imp < IMPORTANCE_WRITE_MIN or imp > IMPORTANCE_MAX:
                notes.append(
                    f"blocks[{index}].importance {imp}→{IMPORTANCE_DEFAULT}"
                )
                imp = IMPORTANCE_DEFAULT
        item["importance"] = imp
        if wt == "event":
            role_note = _inject_event_roles(item)
            if role_note:
                notes.append(f"blocks[{index}]: {role_note}")
            for key in _EVENT_STAGE_KEYS:
                if key not in item or not isinstance(item[key], str):
                    continue
                original = item[key]
                clipped = _first_sentence(original)
                if clipped != original.strip():
                    item[key] = clipped
                    notes.append(f"blocks[{index}].{key} kept first sentence only")
        if wt == "decision":
            subject = str(item.get("subject", "")).strip() or "user"
            original_ruling = str(item.get("ruling") or "")
            stripped = _decision_strip_leading_subject(subject, original_ruling)
            if stripped != original_ruling.strip():
                item["ruling"] = stripped
                notes.append(
                    f"blocks[{index}].ruling stripped leading subject {subject!r}"
                )
        if wt == "fact":
            involves = item.get("involves")
            involve_count = len(involves) if isinstance(involves, list) else 0
            if str(item.get("kind", "")).strip() == "Factual" and involve_count >= 2:
                item["kind"] = "Narration"
                notes.append(f"blocks[{index}]: Factual+involves→Narration")
        for key, max_length in SLOT_MAX_LENGTH.items():
            if key not in item or not isinstance(item[key], str):
                continue
            original = item[key]
            if len(original) <= max_length:
                continue
            item[key] = _truncate_slot_value(original, max_length)
            notes.append(
                f"blocks[{index}].{key} truncated {len(original)}→{max_length}"
            )
        if wt == "event" and isinstance(item.get("related"), list):
            kept: list[Any] = []
            dropped = 0
            for ref in item["related"]:
                if _ref_is_event(str(ref), type_map):
                    dropped += 1
                    continue
                kept.append(ref)
            if dropped:
                if kept:
                    item["related"] = kept
                else:
                    item.pop("related", None)
                notes.append(
                    f"blocks[{index}].related stripped {dropped} event id(s)"
                )
        temp_id = str(item.get("temp_id") or "").strip()
        if temp_id:
            if temp_id in seen_temp:
                reminted = f"{temp_id}-dup{index}-{uuid.uuid4().hex[:6]}"
                item["temp_id"] = reminted
                notes.append(
                    f"blocks[{index}].temp_id reminted {temp_id!r}→{reminted!r}"
                )
                seen_temp.add(reminted)
            else:
                seen_temp.add(temp_id)
        out_blocks.append(item)
    bag["blocks"] = out_blocks
    return bag, notes


def phase1_handler_payload(
    *,
    ok: bool,
    errors: Sequence[str] | None = None,
    teach: str = "",
    args: Mapping[str, Any] | None = None,
) -> str:
    """JSON string returned by Phase-1 submit/patch handlers."""
    payload: dict[str, Any] = {"ok": bool(ok)}
    if errors is not None:
        payload["errors"] = list(errors)
    if teach:
        payload["teach"] = teach
    if ok and isinstance(args, Mapping):
        payload["args"] = dict(args)
    return json.dumps(payload, ensure_ascii=False)


def handle_submit_digest_blocks(args: dict[str, Any], **_kwargs: Any) -> str:
    """Validate Phase-1 submit args; return ok/errors/teach JSON (no LLM)."""
    bag, errors, _notes = accept_phase1_args(args)
    _phase1_turn_state["previous_args"] = bag
    if errors:
        _phase1_turn_state["fail_count"] = int(
            _phase1_turn_state.get("fail_count") or 0
        ) + 1
        teach = digest_blocks_failed_teach(
            errors,
            bag,
            attempt=int(_phase1_turn_state["fail_count"]),
            max_attempts=PHASE1_MAX_VALIDATION_ATTEMPTS,
        )
        return phase1_handler_payload(ok=False, errors=errors, teach=teach)
    _phase1_turn_state["accepted_args"] = bag
    return phase1_handler_payload(ok=True, errors=[], args=bag)


def handle_patch_digest_blocks(args: dict[str, Any], **_kwargs: Any) -> str:
    """Merge + validate Phase-1 patch args; return ok/errors/teach JSON."""
    patch = dict(args or {})
    previous = dict(_phase1_turn_state.get("previous_args") or {})
    merged = merge_digest_blocks_patch(previous, patch)
    merged, errors, _notes = accept_phase1_args(merged)
    _phase1_turn_state["previous_args"] = merged
    if errors:
        _phase1_turn_state["fail_count"] = int(
            _phase1_turn_state.get("fail_count") or 0
        ) + 1
        teach = digest_blocks_failed_teach(
            errors,
            merged,
            attempt=int(_phase1_turn_state["fail_count"]),
            max_attempts=PHASE1_MAX_VALIDATION_ATTEMPTS,
        )
        return phase1_handler_payload(ok=False, errors=errors, teach=teach)
    _phase1_turn_state["accepted_args"] = merged
    return phase1_handler_payload(ok=True, errors=[], args=merged)


def transform_phase1_tool_result(
    tool_name: str,
    result: Any,
    *,
    session_id: str = "",
) -> str | None:
    """ok→compact JSON (no YAML); fail→teach text; unknown → None.

    Daily staging YAML is written later by ``_persist_phase1_candidates``.
    Echoing cards here only burned tokens for the Phase-1 model.
    """
    del session_id  # kept for hook call-site compatibility
    name = str(tool_name or "").strip()
    if name not in {PHASE1_SUBMIT_TOOL, PHASE1_PATCH_TOOL}:
        return None
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if not isinstance(data, Mapping):
        return text
    if not data.get("ok"):
        teach = str(data.get("teach") or "").strip()
        if teach:
            return teach
        errors = data.get("errors") or []
        if isinstance(errors, list) and errors:
            return digest_blocks_failed_teach(
                [str(e) for e in errors],
                dict(_phase1_turn_state.get("previous_args") or {}),
            )
        return text
    return json.dumps({"ok": True}, ensure_ascii=False)


def all_tool_schemas() -> list[dict[str, Any]]:
    schemas = [
        skip_schema(),
        submit_operations_schema(),
        patch_operations_schema(),
        submit_digest_blocks_schema(),
        patch_digest_blocks_schema(),
    ]
    for wt in WORKER_TYPES:
        schemas.append(submit_schema(wt))
        schemas.append(patch_schema(wt))
    return schemas


def _noop_handler(args: dict[str, Any], **_kwargs: Any) -> str:
    return json.dumps({"ok": True, "received": args}, ensure_ascii=False)


def _handler_for_tool(name: str):
    """Return the registry handler; Phase-1 submit/patch compact/teach here.

    Exclusive MyMemory load drops the Hermes ``transform_tool_result`` hook, so
    the nested Phase-1 model would otherwise see raw handler JSON and never
    get compact-ok or fail-teach in the same turn.
    """
    if name == PHASE1_SUBMIT_TOOL:
        inner = handle_submit_digest_blocks
    elif name == PHASE1_PATCH_TOOL:
        inner = handle_patch_digest_blocks
    else:
        return _noop_handler

    def wrapped(args: dict[str, Any], **kwargs: Any) -> str:
        raw = inner(args, **kwargs)
        out = transform_phase1_tool_result(
            name, raw, session_id=str(kwargs.get("session_id") or "")
        )
        return out if out is not None else raw

    return wrapped


def ensure_digest_tools_registered() -> None:
    """Idempotently register digest tools on the Hermes tool registry."""
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
                toolset=DIGEST_TOOLSET,
                schema=schema,
                handler=_handler_for_tool(name),
            )
        except Exception:
            # Already registered or registry rejects duplicates — continue.
            try:
                # Some registries expose overwrite; ignore failures.
                pass
            except Exception:
                pass
    _REGISTERED = True


def tool_names_for_worker(worker_type: str, *, mode: str) -> list[str]:
    wt = worker_type.strip().lower()
    if mode == "submit":
        return [f"submit_{wt}_block", "skip_digest_worker"]
    if mode == "patch":
        return [f"patch_{wt}_block", "skip_digest_worker"]
    raise ValueError(f"unknown mode: {mode!r}")


def forced_tool_choice(tool_name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": tool_name}}


def is_skip_tool(name: str | None, args: Mapping[str, Any] | None) -> bool:
    if not name or name != "skip_digest_worker":
        return False
    if not args:
        return False
    return bool(args.get("skip"))


def parse_tool_args_from_result(
    result: Mapping[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """Prefer structured capture from run_worker_llm_tools; else scrape messages."""
    if result.get("tool_name"):
        args = result.get("tool_args")
        return (
            str(result["tool_name"]),
            dict(args) if isinstance(args, Mapping) else {},
        )
    messages = result.get("messages") or []
    last_name: str | None = None
    last_args: dict[str, Any] | None = None
    if not isinstance(messages, Sequence):
        return None, None
    for msg in messages:
        if not isinstance(msg, Mapping):
            continue
        if str(msg.get("role", "")).strip() != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, Mapping):
                continue
            fn = tc.get("function") or {}
            if not isinstance(fn, Mapping):
                continue
            name = str(fn.get("name") or "").strip()
            raw = fn.get("arguments")
            if isinstance(raw, Mapping):
                args = dict(raw)
            elif isinstance(raw, str):
                try:
                    parsed = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    parsed = {}
                args = dict(parsed) if isinstance(parsed, Mapping) else {}
            else:
                args = {}
            if name:
                last_name, last_args = name, args
    return last_name, last_args


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
    items = [
        _yaml_scalar(str(v).strip())
        for v in values
        if str(v).strip()
    ]
    return "[" + ", ".join(items) + "]"


def _format_entity_collection(key: str, items: Any) -> list[str]:
    lines = [f"{key}:"]
    if not isinstance(items, list):
        return lines
    for item in items:
        if isinstance(item, Mapping):
            ent = str(item.get("entity", "")).strip()
            role = str(item.get("role", "")).strip()
            if role:
                lines.append(f"  - entity: {ent}")
                lines.append(f"    role: {role}")
            else:
                lines.append(f"  - entity: {ent}")
        else:
            lines.append(f"  - {item}")
    return lines


def _phase1_confidence(bag: Mapping[str, Any]) -> str:
    conf = str(bag.get("confidence", "")).strip()
    if conf in CONFIDENCE_ENUM:
        return conf
    if bag.get("supersedes"):
        return "explicit"
    return "medium"


def _phase1_importance(bag: Mapping[str, Any]) -> int:
    """Keep create scores on 1–5; missing/invalid stay default 3 so 0 is not invented.

    Without this, a missing field would collapse to the floor and look like a
    deliberately low-importance card.
    """
    raw = bag.get("importance", IMPORTANCE_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return IMPORTANCE_DEFAULT
    if n < IMPORTANCE_WRITE_MIN or n > IMPORTANCE_MAX:
        return IMPORTANCE_DEFAULT
    return n


def format_session_source(
    session_id: str,
    message_start_id: int | None = None,
    message_end_id: int | None = None,
) -> str:
    """Locator for sources:. Space after session avoids YAML ``session:`` keys."""
    sid = str(session_id or "").strip()
    if message_start_id is None or message_end_id is None:
        return f"session {sid}"
    return f"session {sid}#{int(message_start_id)}-{int(message_end_id)}"


def session_id_from_source_tag(tag: str) -> str:
    """Extract session id from ``session <id>#start-end`` or ``session:<id>``."""
    text = str(tag or "").strip()
    lowered = text.casefold()
    rest = ""
    if lowered.startswith("session:"):
        rest = text.split(":", 1)[1].strip()
    elif lowered.startswith("session "):
        rest = text[len("session ") :].strip()
    if not rest:
        return ""
    return rest.split("#", 1)[0].strip()


_STAGING_PATH_RE = re.compile(r"memories[/\\]staging", re.IGNORECASE)
_STAGING_BASENAME_RE = re.compile(
    r"^(?:file:)?(?:\./)?(?:\d{4}-\d{2}-\d{2}|\d{4}-W\d{2})\.md$",
    re.IGNORECASE,
)


def is_memory_staging_source_tag(tag: str) -> bool:
    """True when a sources: item points at a daily/weekly staging file."""
    text = str(tag or "").strip().replace("\\", "/")
    if not text:
        return False
    if _STAGING_BASENAME_RE.match(text):
        return True
    payload = text[5:] if text.casefold().startswith("file:") else text
    payload = payload.strip()
    if _STAGING_PATH_RE.search(payload) or _STAGING_PATH_RE.search(text):
        return True
    name = payload.rsplit("/", 1)[-1]
    return bool(_STAGING_BASENAME_RE.match(name) or _STAGING_BASENAME_RE.match(f"file:{name}"))


def _extra_file_sources(sources: Any) -> list[str]:
    extra: list[str] = []
    seen: set[str] = set()
    if not isinstance(sources, list):
        return extra
    for item in sources:
        tag = str(item).strip()
        if not (tag.startswith("file:") or tag.startswith("sheet:")):
            continue
        if is_memory_staging_source_tag(tag):
            continue
        if tag not in seen:
            seen.add(tag)
            extra.append(tag)
    return extra


def render_worker_yaml_from_args(
    worker_type: str,
    args: Mapping[str, Any],
    *,
    session_id: str,
    today: str,
    message_start_id: int | None = None,
    message_end_id: int | None = None,
) -> str:
    """Code-owned YAML frontmatter + one-line body from accepted tool args."""
    wt = worker_type.strip().lower()
    bag = dict(args)
    if is_skip_tool("skip_digest_worker", bag) or bag.get("skip") is True:
        return "skip"

    parsed: dict[str, Any] = {
        "id": str(bag.get("id") or f"mem-{today}-{wt}-pending").strip(),
        "type": wt,
        "confidence": _phase1_confidence(bag),
        "status": "candidate",
        "importance": _phase1_importance(bag),
    }
    locator = format_session_source(
        session_id,
        message_start_id=message_start_id,
        message_end_id=message_end_id,
    )
    parsed["sources"] = [locator, *_extra_file_sources(bag.get("sources"))]

    for key in (
        "entity",
        "predicate",
        "valid_from",
        "valid_to",
        "related",
        "supersedes",
        "participants",
        "involves",
    ):
        if key in bag and bag[key] not in (None, "", []):
            parsed[key] = bag[key]

    body = render_body_from_slots(wt, bag)

    lines = ["---"]
    order = [
        "id",
        "type",
        "entity",
        "predicate",
        "confidence",
        "importance",
        "status",
        "valid_from",
        "valid_to",
        "sources",
        "related",
        "supersedes",
    ]
    seen: set[str] = set()
    for key in order:
        if key not in parsed:
            continue
        seen.add(key)
        val = parsed[key]
        if key in {"sources", "related", "supersedes"}:
            lines.append(f"{key}: {_format_list(val)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(val)}")
    for key in ("participants", "involves"):
        if key in parsed:
            seen.add(key)
            lines.extend(_format_entity_collection(key, parsed[key]))
    for key, val in parsed.items():
        if key in seen:
            continue
        lines.append(f"{key}: {_yaml_scalar(val)}")
    lines.extend(["---", body])
    return "\n".join(lines)


def failed_fields_teach(
    errors: Sequence[str],
    previous: Mapping[str, Any],
    *,
    worker_type: str,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> str:
    """Compact retry teach: failed errors + prior values + enum hints."""
    header = "VALIDATION FAILED — call the patch tool with ONLY fields that must change."
    if attempt is not None and max_attempts is not None:
        header = (
            f"VALIDATION FAILED (attempt {attempt} of {max_attempts}). "
            "Call the patch tool with ONLY fields that must change."
        )
    lines = [
        header,
        f"Assigned type: {worker_type}",
        f"confidence must be one of: {', '.join(CONFIDENCE_ENUM)}",
        f"importance must be integer {IMPORTANCE_WRITE_MIN}–{IMPORTANCE_MAX}",
    ]
    wt = worker_type.strip().lower()
    if wt == "fact":
        lines.append(f"kind must be one of: {', '.join(FACT_KIND_ENUM)}")
    elif wt == "decision":
        lines.append(f"kind must be one of: {', '.join(DECISION_KIND_ENUM)}")
    lines.extend(
        [
            "Do not resend unchanged fields. Do not emit free-form YAML.",
            "Errors:",
        ]
    )
    for err in list(errors)[:12]:
        lines.append(f"- {err}")
    if previous:
        lines.append("Prior accepted/partial args (reference only):")
        for key in sorted(previous):
            val = previous[key]
            preview = json.dumps(val, ensure_ascii=False)
            if len(preview) > 120:
                preview = preview[:117] + "..."
            lines.append(f"  {key}: {preview}")
    return "\n".join(lines) + "\n"
