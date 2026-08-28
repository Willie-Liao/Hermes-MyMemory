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


def _evidence_text_props() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "string"}},
        },
    }


def submit_month_synthesis_schema() -> dict[str, Any]:
    """One reduce call writes every narrative field; mechanical facts stay out of this tool."""
    return _schema(
        "submit_month_synthesis",
        "Synthesize the month from notes and mechanical facts. Cite only ids you were given. "
        "summary is one-line bullets (text + weeks); never one paragraph.",
        {
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
            "user_image": {
                "type": "object",
                "properties": {
                    "goal_alignment": _evidence_text_props(),
                    "cognition_change": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "date": {"type": "string"},
                                "evidence": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "decision_preference": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "evidence": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "behavior_pattern": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "evidence": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
            "core_progress": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "state": {"type": "string"},
                        "weeks": {"type": "array", "items": {"type": "string"}},
                        "entity_keys": {"type": "array", "items": {"type": "string"}},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "key_decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "key_procedures": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "problem": {"type": "string"},
                        "insight": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
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
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "level": {"type": "string"},
                        "suggestion": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "comparison_with_last_month": {
                "type": "object",
                "properties": {
                    "unchanged": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "evidence": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "changed": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "evidence": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "suggestion": {"type": "string"},
                },
            },
            "next_month_focus": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "content": {"type": "string"},
                        "target": {"type": "string"},
                        "priority": {"type": "string"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
        ["summary"],
    )


def patch_month_synthesis_schema() -> dict[str, Any]:
    schema = submit_month_synthesis_schema()
    schema["name"] = "patch_month_synthesis"
    schema["description"] = "Patch ONLY changed fields on previous submit_month_synthesis args."
    schema["parameters"].pop("required", None)
    return schema
