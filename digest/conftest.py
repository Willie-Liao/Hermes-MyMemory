"""Shared helpers for memory-digest pytest modules."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

DEDUP_PROMPT_MARKER = "You MUST call submit_operations"


def fail_closed_tool_capture(*_a, **_k):
    """Stub for ``_invoke_digest_worker_tool`` — force legacy text path in tests."""
    return {
        "tool_name": None,
        "tool_args": None,
        "tool_calls": [],
        "messages": [],
        "failed": True,
    }


def sample_phase1_tool_args() -> dict:
    """Minimal valid submit_digest_blocks payload (flat oneOf event + details)."""
    return {
        "blocks": [
            {
                "type": "event",
                "temp_id": "tmp-e1",
                "entity": "Project",
                "predicate": "user_requested_review",
                "participants": [
                    {"entity": "User", "role": "requester"},
                    {"entity": "Assistant", "role": "executor"},
                ],
                "beginning": "user requested review",
                "course": "assistant reviewed sources",
                "outcome": "draft delivered",
                "confidence": "explicit",
                "importance": 4,
                "valid_from": "2026-08-02",
                "valid_to": "open",
            },
            {
                "type": "fact",
                "entity": "Casey",
                "kind": "Factual",
                "content": "Casey chose home-packed lunch.",
                "confidence": "high",
                "importance": 3,
                "related": ["tmp-e1"],
            },
            {
                "type": "procedure",
                "obstacle": "source notes were scattered",
                "solution": "use an abstract source-triage checklist",
                "confidence": "explicit",
                "importance": 3,
                "related": ["tmp-e1"],
            },
            {
                "type": "decision",
                "kind": "Decision",
                "subject": "user",
                "ruling": "prefers concise review summaries",
                "confidence": "explicit",
                "importance": 3,
                "related": ["tmp-e1"],
            },
        ]
    }


def phase1_tool_capture(payload: dict | None = None, prompts: list | None = None):
    """Return a stub that serves submit_digest_blocks; dedup falls through."""
    body = payload if payload is not None else sample_phase1_tool_args()

    def _capture(
        prompt,
        platform,
        *,
        purpose="",
        force_tool_name="",
        allowed_tool_names=None,
        **_k,
    ):
        if force_tool_name in ("submit_operations", "patch_operations"):
            return fail_closed_tool_capture()
        if prompts is not None:
            prompts.append(prompt)
        if force_tool_name == "skip_digest_worker":
            return {
                "tool_name": "skip_digest_worker",
                "tool_args": {"skip": True},
                "tool_calls": [],
                "messages": [],
            }
        return {
            "tool_name": "submit_digest_blocks",
            "tool_args": body,
            "tool_calls": [("submit_digest_blocks", body)],
            "messages": [],
        }

    return _capture


def is_dedup_prompt(prompt: str) -> bool:
    """True when a fake LLM is being asked for a consolidation proposal."""
    return DEDUP_PROMPT_MARKER in str(prompt)


def _blocks_under(prompt: str, heading: str) -> list[dict]:
    if heading not in prompt:
        return []
    section = prompt.split(heading, 1)[1].split("###", 1)[0].strip()
    if section.startswith("["):
        parsed = json.loads(section)
        return [item for item in parsed if isinstance(item, dict)]
    return [
        json.loads(line)
        for line in section.splitlines()
        if line.startswith("{")
    ]


def stub_dedup_proposal(prompt: str) -> str:
    """Answer a dedup prompt with no consolidation at all.

    Empty ops keep Phase-1-persisted new cards. Updates only when the id is
    already in the file. Lets a test exercise proposer plumbing without
    depending on model judgement.
    """
    existing_ids: set[str] = set()
    for kind in ("events", "facts", "procedures", "decisions"):
        existing_ids |= {
            str(block.get("id"))
            for block in _blocks_under(prompt, f"### Existing {kind}")
        }
    operations = []
    for kind in ("events", "facts", "procedures", "decisions"):
        for block in _blocks_under(prompt, f"### New {kind}"):
            block_id = str(block.get("id"))
            if block_id in existing_ids:
                changes = {key: value for key, value in block.items() if key != "id"}
                operations.append(
                    {"operation": "update", "id": block_id, "changes": changes}
                )
    return json.dumps(operations, ensure_ascii=False)


def load_plugin_module(filename: str, module_name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
