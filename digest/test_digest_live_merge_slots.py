"""Live LLM nested-merge teach→patch (opt-in).

Enable with ``HERMES_DIGEST_LIVE_LLM=1``. Optional model override:
``HERMES_DIGEST_LIVE_MODEL=mimo-v2.5`` (must not accidentally use pro for KP6).
"""

from __future__ import annotations

import os

import pytest
from conftest import load_plugin_module

pytestmark = pytest.mark.skipif(
    os.environ.get("HERMES_DIGEST_LIVE_LLM", "").strip() not in {"1", "true", "yes"},
    reason="Set HERMES_DIGEST_LIVE_LLM=1 to run live nested-merge tests",
)


def _operations():
    return load_plugin_module("operations.py", "memory_digest_live_merge_ops")


def _tools():
    return load_plugin_module("digest_tools.py", "memory_digest_live_merge_tools")


def test_live_merge_slots_validate_wrong_nest_teach_shape():
    """Offline-shaped check that teach text is produced for wrong nest (live gate)."""
    operations = _operations()
    tools = _tools()
    survivor = {
        "id": "mem-e-surv",
        "type": "event",
        "body": "Beginning: a; Course: b; Outcome: c",
        "importance": 5,
    }
    absorbed = {
        "id": "mem-e-abs",
        "type": "event",
        "body": "Beginning: x; Course: y; Outcome: z",
        "importance": 4,
    }
    blocks = {b["id"]: b for b in (survivor, absorbed)}
    errors = operations.validate_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-e-surv",
            "absorbed_ids": ["mem-e-abs"],
            "reason": "C evolution",
            "fact": {"kind": "Factual", "content": "wrong"},
        },
        blocks,
        blocks_by_id=blocks,
    )
    assert errors
    teach = tools.operations_failed_teach(errors, attempt=2, max_attempts=5)
    assert "patch_operations" in teach
    assert any("event" in e or "nest" in e for e in errors)


def test_live_merge_slots_factual_into_narration_fixture():
    operations = _operations()
    survivor = {
        "id": "mem-n-surv",
        "type": "fact",
        "body": "Narration: Jordan lives in a school dorm so dates default outside.",
        "involves": [{"entity": "Alex Chen"}, {"entity": "Roommate"}],
        "importance": 4,
        "sources": ["s1"],
        "confidence": "high",
        "status": "candidate",
    }
    absorbed = {
        "id": "mem-f-abs",
        "type": "fact",
        "body": "Factual: Jordan dislikes cilantro.",
        "importance": 3,
        "sources": ["s2"],
        "confidence": "high",
        "status": "candidate",
    }
    blocks = {b["id"]: b for b in (survivor, absorbed)}
    bad = operations.validate_operation(
        {
            "operation": "merge",
            "survivor_id": "mem-n-surv",
            "absorbed_ids": ["mem-f-abs"],
            "reason": "cast absorb",
            "fact": {"kind": "Factual", "content": "bad kind"},
        },
        blocks,
        blocks_by_id=blocks,
    )
    assert any("Narration" in e for e in bad)
    good_op = {
        "operation": "merge",
        "survivor_id": "mem-n-surv",
        "absorbed_ids": ["mem-f-abs"],
        "reason": "cast absorb",
        "fact": {
            "kind": "Narration",
            "content": (
                "Jordan lives in a school dorm so dates default outside; "
                "dislikes cilantro"
            ),
        },
    }
    assert operations.validate_operation(good_op, blocks, blocks_by_id=blocks) == []
    merged = operations.apply_operation(good_op, [survivor, absorbed])
    assert {b["id"] for b in merged} == {"mem-n-surv"}
    assert merged[0]["body"].startswith("Narration:")
