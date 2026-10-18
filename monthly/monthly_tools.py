"""Forced-tool schemas for the monthly map and reduce stages."""

from __future__ import annotations

import sys
from typing import Any

_weekly = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "weekly")
if _weekly not in sys.path:
    sys.path.insert(0, _weekly)

from weekly_tools import _schema, merge_field_patch  # noqa: E402

__all__ = [
    "merge_field_patch",
    "patch_month_note_schema",
    "patch_month_synthesis_schema",
    "submit_month_note_schema",
    "submit_month_suggest_schema",
    "submit_month_synthesis_schema",
]


def _note_item_props() -> dict[str, Any]:
    return {
        "kind": {"type": "string"},
        "what": {"type": "string"},
        "why_it_mattered": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    }


def submit_month_note_schema() -> dict[str, Any]:
    """Keep at most six evidence-bound notes so reduce never re-reads the batch."""
    return _schema(
        "submit_month_note",
        "Select at most 6 items that really matter from this week-slice batch. Cite only ids from the prompt.",
        {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": _note_item_props(),
                    "required": ["kind", "what", "evidence"],
                },
            }
        },
        ["items"],
    )


def patch_month_note_schema() -> dict[str, Any]:
    return _schema(
        "patch_month_note",
        "Patch ONLY changed fields on the previous submit_month_note args.",
        {"items": {"type": "array", "items": {"type": "object", "properties": _note_item_props()}}},
        [],
    )


def _judgment_string(description: str) -> dict[str, Any]:
    """Reject a blank string in the tool schema so "" cannot pose as a filled judgment."""
    return {
        "type": "string",
        "minLength": 1,
        "description": f"{description} An empty string is not a judgment.",
    }


def _evidence_text_props() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": _judgment_string("Judgment about the user."),
            "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": ["text", "evidence"],
    }


def submit_month_synthesis_schema() -> dict[str, Any]:
    """One reduce call writes every narrative field; mechanical facts stay out of this tool.

    The worker returns JSON tool arguments. YAML is rendered later by dump_yaml.
    """
    return _schema(
        "submit_month_synthesis",
        "Synthesize the month as JSON tool arguments, not a YAML document. "
        "Cite only ids you were given. "
        "summary is one-line bullets (text + weeks); never one paragraph. "
        "user_image.goal_alignment, decision_preference, and behavior_pattern must be "
        "objects inside these tool arguments. A sentence in thinking does not count. "
        "key_decisions.evidence is decision ids only. key_procedures.evidence is procedure ids only. "
        "Notes mix both kinds in one list. Keep only -decision- ids on a decision row "
        "and only -procedure- ids on a procedure row. One leftover id of the other kind fails the row. "
        "comparison_with_last_month.summary is one summary of this month's user_image "
        "against last month's user_image lines. Leave unchanged and changed empty.",
        {
            "user_image": {
                "type": "object",
                "description": (
                    "Three objects in these tool arguments, never null: goal_alignment, "
                    "decision_preference, and behavior_pattern. Each is text plus evidence. "
                    "A sentence in thinking does not count. Write these before the comparison summary."
                ),
                "properties": {
                    "goal_alignment": _evidence_text_props(),
                    "cognition_change": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": _judgment_string("How the user's understanding changed."),
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "date": {"type": "string"},
                                "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                            },
                        },
                    },
                    "decision_preference": {
                        "type": "object",
                        "properties": {
                            "text": _judgment_string("Judgment about the user."),
                            "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        },
                        "required": ["text", "evidence"],
                    },
                    "behavior_pattern": {
                        "type": "object",
                        "properties": {
                            "text": _judgment_string("Judgment about the user."),
                            "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        },
                        "required": ["text", "evidence"],
                    },
                },
                "required": ["goal_alignment", "decision_preference", "behavior_pattern"],
            },
            "key_decisions": {
                "type": "array",
                "description": (
                    "Standing rules synthesized from the month's decision cards, "
                    "chosen after reading story_seeds. At most 12. text is the "
                    "month's synthesis, not one daily clause. Do not set id."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "text": _judgment_string("Synthesis of the cited decision cards."),
                        "why_it_matters": _judgment_string(
                            "Required. At most 40 words. Why this rule would still change the next time."
                        ),
                        "context": _judgment_string("Where this rule applies."),
                        "exceptions": _judgment_string("When this rule does not apply."),
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Decision card ids copied from a card line. "
                                "Each id must contain -decision-. A procedure id or a changed date fails this row."
                            ),
                        },
                    },
                    "required": ["text", "why_it_matters", "context", "exceptions", "evidence"],
                },
            },
            "key_procedures": {
                "type": "array",
                "description": (
                    "Reusable cases synthesized from the month's procedure cards. "
                    "A finished one-day act is not a key row. At most 8. solution "
                    "is the month's synthesis. Do not set id."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "trigger": _judgment_string("What starts this procedure."),
                        "problem": _judgment_string("The problem this procedure addresses."),
                        "obstacles": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "solution": _judgment_string("Synthesis of the cited procedure cards."),
                        "insight": _judgment_string(
                            "Required. At most 40 words. Why this procedure would still change the next time."
                        ),
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": (
                                "Procedure card ids copied from a card line. "
                                "Each id must contain -procedure-. A decision id or a changed date fails this row."
                            ),
                        },
                    },
                    "required": ["trigger", "problem", "obstacles", "solution", "insight", "evidence"],
                },
            },
            "summary": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "weeks": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["text"],
                },
            },
            "core_progress": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": _judgment_string("Short name of the progress."),
                        "body": _judgment_string("Judgment about how the user moved this forward."),
                        "state": _judgment_string("Where this progress stands."),
                        "weeks": {"type": "array", "items": {"type": "string"}},
                        "entity_keys": {"type": "array", "items": {"type": "string"}},
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Card-line ids only. Do not change the date inside an id.",
                        },
                    },
                    "required": ["title", "body", "state", "weeks", "entity_keys", "evidence"],
                },
            },
            "cross_week_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "start_period": {"type": "string"},
                        "current_status": {"type": "string"},
                        "expected_end_period": {"type": "string"},
                        "progress_this_month": {"type": "string"},
                        "block_reason": {"type": "string"},
                        "weeks": {"type": "array", "items": {"type": "string"}},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "problems_and_risks": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "content": _judgment_string("The risk for the user."),
                        "level": _judgment_string("How serious the risk is."),
                        "suggestion": _judgment_string("What the user should do about it."),
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Card-line ids only. Do not change the date inside an id.",
                        },
                    },
                    "required": ["content", "level", "suggestion", "evidence"],
                },
            },
            "comparison_with_last_month": {
                "type": "object",
                "description": (
                    "One summary of this month's user_image against last month's "
                    "user_image lines on the carry card."
                ),
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": (
                            "How goal alignment, decision preference, behavior pattern, "
                            "and cognition changed from last month's user image to this one."
                        ),
                    },
                },
                "required": ["summary"],
            },
            "next_month_focus": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "content": _judgment_string("What the user should carry forward."),
                        "target": _judgment_string("When or where this focus lands."),
                        "priority": _judgment_string("How soon this focus matters."),
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["content", "target", "priority", "depends_on"],
                },
            },
        },
        [
            "user_image",
            "core_progress",
            "key_decisions",
            "key_procedures",
            "problems_and_risks",
            "comparison_with_last_month",
            "next_month_focus",
        ],
    )


def submit_month_suggest_schema() -> dict[str, Any]:
    """Force one flat fill-in per body so a model cannot stop after a single array element.

    The section name stays on the body the code sent. The model fills the sentence and the file.
    """
    return _schema(
        "submit_month_suggest",
        "Fill text and bucket for this one body. "
        "bucket is skill, USER.md, HERMES.md, or MEMORY.md.",
        {
            "text": {"type": "string"},
            "bucket": {"type": "string"},
        },
        ["text", "bucket"],
    )


def patch_month_synthesis_schema(keys: list[str] | None = None) -> dict[str, Any]:
    """Expose only the sections that failed so a retry cannot replace the whole month.

    A full clone of submit_month_synthesis dropped the first payload and the file
    then copied story seeds. keys are top-level JSON names, not dotted fields.
    """
    full = submit_month_synthesis_schema()
    wanted = [key for key in (keys or []) if key in full["parameters"]["properties"]]
    props = {key: full["parameters"]["properties"][key] for key in wanted}
    schema = _schema(
        "patch_month_synthesis",
        "Return JSON for only these failed sections. Do not repeat sections that already passed.",
        props,
        wanted,
    )
    return schema
