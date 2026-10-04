"""Foul-touched closure + attempt-2 proposer prompt shape."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parent
if str(_PLUGIN) not in sys.path:
    sys.path.insert(0, str(_PLUGIN))


def _load(name: str):
    path = _PLUGIN / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dedup_prompt = _load("dedup_prompt")


def _board():
    return {
        "E1": {
            "id": "E1",
            "type": "event",
            "related": ["F1"],
            "body": "Beginning: e1; Course: e1; Outcome: e1",
        },
        "E2": {
            "id": "E2",
            "type": "event",
            "related": [],
            "body": "Beginning: e2; Course: e2; Outcome: e2",
        },
        "E3": {
            "id": "E3",
            "type": "event",
            "related": ["F9"],
            "body": "Beginning: e3; Course: e3; Outcome: e3",
        },
        "F1": {
            "id": "F1",
            "type": "fact",
            "related": ["E1", "Fx"],
            "body": "Factual: f1",
        },
        "F2": {
            "id": "F2",
            "type": "fact",
            "related": ["E1", "D1"],
            "body": "Factual: f2",
        },
        "D1": {
            "id": "D1",
            "type": "decision",
            "related": ["E1"],
            "supersedes": ["D0"],
            "body": "Decision: user keep",
        },
        "N1": {"id": "N1", "type": "fact", "related": [], "body": "Factual: n1"},
        "N2": {
            "id": "N2",
            "type": "procedure",
            "related": ["E2"],
            "body": "Obstacle: n2; Solution: n2",
        },
        "N3": {"id": "N3", "type": "fact", "related": [], "body": "Factual: n3"},
    }


def test_foul_touched_one_hop_outbound_only():
    board = _board()
    existing = [board[k] for k in ("E1", "E2", "E3", "F1", "F2", "D1")]
    new = [board[k] for k in ("N1", "N2", "N3")]
    previous_ops = [
        {
            "operation": "merge",
            "survivor_id": "N1",
            "absorbed_ids": ["E1"],
            "reason": "same topic",
        },
        {
            "operation": "merge",
            "survivor_id": "E2",
            "absorbed_ids": ["N2"],
            "reason": "extend",
        },
    ]
    errors = [
        "operation[0]: cannot absorb event E1 into fact N1",
        "operation[1]: illegal merge E2 <- N2",
    ]
    closure = dedup_prompt.foul_touched_block_ids(
        errors, previous_ops, existing, new
    )
    assert closure == {"E1", "N1", "E2", "N2", "F1"}
    # Second hop from F1.related Fx / reverse neighbor F2 stay out
    assert "Fx" not in closure
    assert "F2" not in closure
    assert "E3" not in closure
    assert "D1" not in closure
    assert "N3" not in closure
    pending = dedup_prompt.pending_new_ids(new, closure)
    assert pending == ["N3"]


def test_foul_touched_empty_errors_returns_empty():
    board = _board()
    existing = [board["E1"]]
    new = [board["N1"]]
    assert (
        dedup_prompt.foul_touched_block_ids(
            ["soft merge pressure only"],
            [{"operation": "merge", "survivor_id": "N1", "absorbed_ids": ["E1"]}],
            existing,
            new,
        )
        == set()
    )


def test_attempt2_prompt_omits_unrelated_bodies():
    board = _board()
    existing = [board[k] for k in ("E1", "E2", "E3", "F1")]
    new = [board[k] for k in ("N1", "N2", "N3")]
    previous_ops = [
        {
            "operation": "merge",
            "survivor_id": "N1",
            "absorbed_ids": ["E1"],
            "reason": "same topic",
        },
        {
            "operation": "merge",
            "survivor_id": "E2",
            "absorbed_ids": ["N2"],
            "reason": "extend",
        },
    ]
    errors = [
        "operation[0]: cannot absorb event E1 into fact N1",
        "operation[1]: illegal merge E2 <- N2",
    ]
    closure = dedup_prompt.foul_touched_block_ids(
        errors, previous_ops, existing, new
    )
    by_id = {b["id"]: b for b in existing + new}
    filtered_existing = [by_id[i] for i in sorted(closure) if i in {e["id"] for e in existing}]
    filtered_new = [by_id[i] for i in sorted(closure) if i in {n["id"] for n in new}]
    pending = dedup_prompt.pending_new_ids(new, closure)
    prompt = dedup_prompt.build_proposer_prompt(
        filtered_existing,
        filtered_new,
        errors=errors,
        attempt=2,
        previous_operations=previous_ops,
        pending_account_ids=pending,
    )
    assert "## Previous operations" in prompt
    assert '"survivor_id": "N1"' in prompt
    assert "### Existing events" in prompt or "### Existing facts" in prompt or "E1" in prompt
    assert "Factual: f1" in prompt  # F1 one-hop body
    assert "Beginning: e3" not in prompt
    assert "Factual: n3" not in prompt
    assert "## Still must account for" in prompt
    assert "- N3" in prompt
