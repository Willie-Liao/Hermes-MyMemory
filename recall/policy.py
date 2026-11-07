"""Stateless Simple/Hybrid/Complex planner plus integer gate around recall.

TiMem's ablation: either step alone regresses. Both calls are forced tool
oneshots so a prose reply cannot drop the scope label or the keep indices.
Fail-open: unparseable planner → hybrid; unparseable gate → keep all.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

SCOPE_BUDGETS = {
    "simple": {"L1": 20, "L2": 4, "L4": 1},
    "hybrid": {"L1": 20, "L2": 4, "L3": 2, "L4": 1},
    "complex": {"L1": 20, "L2": 8, "L3": 4, "L4": 2},
}
DEFAULT_SCOPE = "hybrid"
_SCOPE_RE = re.compile(r"\b(simple|hybrid|complex)\b", re.I)
_INT_RE = re.compile(r"-?\d+")


def parse_scope(raw: str | None) -> str:
    """First matching label wins; garbage → hybrid so recall still runs."""
    text = str(raw or "")
    m = _SCOPE_RE.search(text)
    if not m:
        return DEFAULT_SCOPE
    return m.group(1).casefold()


def parse_gate_keep(raw: str | None, n_candidates: int) -> list[int] | None:
    """Return 0-based keep indices, or None meaning retain-all (parse fail).

    Out-of-range integers are discarded so a hallucinated id cannot sneak in.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    nums = [int(x) for x in _INT_RE.findall(text)]
    if not nums:
        return None
    zero_based = 0 in nums
    keep: list[int] = []
    for v in nums:
        idx = v if zero_based else v - 1
        if 0 <= idx < n_candidates and idx not in keep:
            keep.append(idx)
    return keep


def plan_scope(
    user_message: str,
    *,
    runner=None,
    force_raw: str | None = None,
) -> tuple[str, dict[str, int]]:
    """Pick a scope. runner(prompt) -> str must be run_worker_llm-shaped (text only)."""
    if force_raw is not None:
        scope = parse_scope(force_raw)
        return scope, dict(SCOPE_BUDGETS[scope])
    prompt = (
        "Classify recall scope for this user message as exactly one of: "
        "Simple, Hybrid, Complex.\n"
        "Simple: look up one id or type.\n"
        "Hybrid: a short stretch of related days/weeks.\n"
        "Complex: evolution across months.\n\n"
        f"User: {user_message}"
    )
    raw = ""
    if runner is not None:
        raw = str(runner(prompt) or "")
    scope = parse_scope(raw)
    return scope, dict(SCOPE_BUDGETS[scope])


def gate_candidates(
    user_message: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    runner=None,
    force_raw: str | None = None,
) -> list[Any]:
    """Drop irrelevant candidates; parse fail keeps the full list (cost, not empty)."""
    items = list(candidates)
    if not items:
        return []
    raw = force_raw
    if raw is None and runner is not None:
        lines = "\n".join(
            f"{i}: {c.get('id') or c.get('entity') or c}" for i, c in enumerate(items)
        )
        prompt = (
            "Return the integer indices of candidates that help answer the user. "
            "Integers only.\n"
            f"User: {user_message}\nCandidates:\n{lines}"
        )
        raw = str(runner(prompt) or "")
    keep = parse_gate_keep(raw, len(items))
    if keep is None:
        return items
    return [items[i] for i in keep]


def text_runner(purpose: str = "recall-plan"):
    """Forced tool oneshot so the prefetch gate does not scrape free-form integers.

    Scope prompts ask for a label. Gate prompts ask for indices. A text-only
    nested agent made both of those a parse guess on the recall hot path.
    """

    def _run(prompt: str) -> str:
        from worker_llm import run_worker_llm_oneshot

        gate = "integer indices" in prompt.casefold() or "integers only" in prompt.casefold()
        if gate:
            schema = {
                "name": "keep_indices",
                "description": "Indices of candidates that help answer the user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keep": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["keep"],
                },
            }
            force = "keep_indices"
        else:
            schema = {
                "name": "classify_scope",
                "description": "Recall scope label.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": ["Simple", "Hybrid", "Complex"]},
                    },
                    "required": ["scope"],
                },
            }
            force = "classify_scope"
        out = run_worker_llm_oneshot(
            prompt,
            plugin="MyMemory",
            purpose=purpose,
            force_tool_name=force,
            tool_schema=schema,
            system_prompt=(
                "Reply with one tool call. Put the decision in the arguments, not in prose."
            ),
            timeout_s=20,
            max_retries=0,
        )
        args = out.get("tool_args") if isinstance(out.get("tool_args"), dict) else {}
        if gate:
            keep = args.get("keep") if isinstance(args, dict) else None
            if isinstance(keep, list):
                return " ".join(str(item) for item in keep)
            return ""
        return str((args or {}).get("scope") or "")

    return _run
