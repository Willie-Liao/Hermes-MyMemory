"""Turn up to four month files into a numbered suggestion list and a chat guide.

The list is the whole command. Create-skill, add, replace, and remove stay in the
next chat turn, so this module never writes MEMORY.md, USER.md, HERMES.md, or a skill.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

_monthly = Path(__file__).resolve().parent
_mymemory = _monthly.parent
for path in (_monthly, _mymemory, _mymemory / "weekly"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from monthly_actions import parse_month_key  # noqa: E402
from monthly_state import atomic_json_write, monthly_staging_dir  # noqa: E402
from monthly_tools import submit_month_suggest_schema  # noqa: E402
from monthly_writer import load_month  # noqa: E402

CallOneshot = Callable[..., dict[str, Any]]
SUGGEST_MAX_TOKENS = 4096
MAX_MONTHS = 4
_BUCKETS = frozenset({"skill", "HERMES.md", "USER.md", "MEMORY.md"})
GUIDE = (
    "## How to continue\n"
    "You can say create a skill from a number, add a number into USER.md, MEMORY.md, "
    "or HERMES.md, replace an existing entry with a number, or remove a number.\n"
    "No further slash command.\n"
    "Before any write, read the target, say whether the change is a new entry, a merge "
    "into an existing entry, or a replace of a contradicting or dated entry, and show "
    "the exact text. Write only after the user agrees.\n"
    "A skill request is a new SKILL.md draft, or a replace of an existing skill file "
    "when one already covers the same trigger. Show that draft and wait for agreement too."
)
_PREFIX = (
    "Read the month JSON and assign each kept row to one bucket. Cite only evidence ids present in the JSON.\n"
    "skill: a procedure that repeats across more than one day and has a trigger and a solution. "
    "The stored solution is one daily sentence cut at 200 characters. The evidence ids are the real steps.\n"
    "USER.md: a preference about the user that spans more than one day, such as language, how they decide, "
    "or how they like explanations. Portrait text is often empty; use the decision rows.\n"
    "HERMES.md: a rule for how Hermes must behave in this workspace, such as paths, tools, or write gates.\n"
    "MEMORY.md: a situation they will hit again, such as a class, a WeChat template, or a phrase that should route to a skill. "
    "Summary bullets are month stories and stay out of MEMORY.md.\n"
    "Skip a row that fits none of these."
)


def _default_oneshot(prompt: str, **kwargs: Any) -> dict[str, Any]:
    from worker_llm import run_worker_llm_oneshot

    return run_worker_llm_oneshot(
        prompt,
        plugin="memory-monthly",
        purpose=kwargs.get("purpose") or "monthly-suggest",
        force_tool_name=kwargs.get("force_tool_name"),
        tool_schema=kwargs.get("tool_schema"),
        max_tokens=int(kwargs.get("max_tokens") or SUGGEST_MAX_TOKENS),
    )


def suggest_path() -> Path:
    """Keep the numbered list beside month files so a later chat turn can reload the numbers."""
    return monthly_staging_dir() / ".suggest.json"


def _split_month_tokens(tokens: list[str]) -> list[str]:
    parts: list[str] = []
    for token in tokens:
        for piece in str(token).split(","):
            text = piece.strip()
            if text:
                parts.append(text)
    return parts


def _same_day(row: Any) -> bool:
    """Drop a row whose first and last sighting are one date so a one-day task is not proposed as a standing rule."""
    first = str(getattr(row, "first_seen", "") or "").strip()[:10]
    last = str(getattr(row, "last_seen", "") or "").strip()[:10]
    return bool(first) and first == last


def _decision_row(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "text": row.text,
        "context": row.context,
        "exceptions": row.exceptions,
        "occurrence_n": row.occurrence_n,
        "strength": row.strength,
        "evidence": list(row.evidence),
        "valid_to": row.valid_to,
        "first_seen": row.first_seen,
        "last_seen": row.last_seen,
    }


def _procedure_row(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "trigger": row.trigger,
        "problem": row.problem,
        "solution": row.solution,
        "insight": row.insight,
        "occurrence_n": row.occurrence_n,
        "strength": row.strength,
        "weeks": list(row.weeks),
        "evidence": list(row.evidence),
        "first_seen": row.first_seen,
        "last_seen": row.last_seen,
    }


def compact_month(payload: Any) -> dict[str, Any]:
    """Send story, rulings, and portrait text only so entity rosters cannot drown the suggest call.

    Same-day rows stay out of this object. A one-day task would otherwise look as durable
    as a preference that lasted the month.
    """
    image = payload.user_image
    return {
        "month_key": payload.key,
        "summary": [{"text": row.text, "weeks": list(row.weeks)} for row in payload.summary],
        "key_decisions": [_decision_row(row) for row in payload.key_decisions if not _same_day(row)],
        "key_procedures": [_procedure_row(row) for row in payload.key_procedures if not _same_day(row)],
        "user_image": {
            "goal_alignment": image.goal_alignment.text,
            "decision_preference": image.decision_preference.text,
            "behavior_pattern": image.behavior_pattern.text,
        },
    }


def _allowed_ids(months: list[dict[str, Any]]) -> set[str]:
    allowed: set[str] = set()
    for month in months:
        for row in month.get("key_decisions") or []:
            allowed.add(str(row.get("id") or ""))
            allowed.update(str(item) for item in (row.get("evidence") or []))
        for row in month.get("key_procedures") or []:
            allowed.add(str(row.get("id") or ""))
            allowed.update(str(item) for item in (row.get("evidence") or []))
    allowed.discard("")
    return allowed


def _clean_items(raw: Any, allowed: set[str]) -> list[dict[str, Any]]:
    """Keep only buckets and evidence ids the prompt contained, so chat cannot quote a made-up card."""
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        bucket = str(row.get("bucket") or "").strip()
        text = " ".join(str(row.get("text") or "").split())
        evidence = [str(item) for item in (row.get("evidence") or []) if str(item) in allowed]
        if bucket not in _BUCKETS or not text or not evidence:
            continue
        items.append(
            {
                "n": len(items) + 1,
                "bucket": bucket,
                "text": text,
                "evidence": evidence,
                "why": " ".join(str(row.get("why") or "").split()),
            }
        )
    return items


def _format_reply(record: dict[str, Any]) -> str:
    lines = ["## Monthly suggest"]
    missing = record.get("missing") or []
    if missing:
        lines.append("missing " + ", ".join(str(item) for item in missing))
    for item in record.get("items") or []:
        evidence = ", ".join(item.get("evidence") or [])
        why = f" — {item['why']}" if item.get("why") else ""
        lines.append(f"{item['n']}. [{item['bucket']}] {item['text']}{why} ({evidence})")
    if not record.get("items"):
        lines.append("(no suggestions)")
    lines.append("")
    lines.append(GUIDE)
    return "\n".join(lines)


def suggest_from_tokens(tokens: list[str], *, call_oneshot: CallOneshot | None = None) -> str:
    """Reject a fifth month before any model call, then print the list and the conversation guide."""
    keys = _split_month_tokens(tokens)
    if not keys:
        return "expected at least one YYYY-MM"
    if len(keys) > MAX_MONTHS:
        return f"at most {MAX_MONTHS} months"
    parsed: list[str] = []
    for key in keys:
        month = parse_month_key(key)
        if month is None:
            return f"bad month {key!r}; expected YYYY-MM"
        parsed.append(month)
    loaded: list[dict[str, Any]] = []
    missing: list[str] = []
    for key in parsed:
        try:
            loaded.append(compact_month(load_month(key)))
        except FileNotFoundError:
            missing.append(key)
    if not loaded:
        return "missing " + ", ".join(missing)
    prompt = f"{_PREFIX}\n\n---\n{json.dumps(loaded, ensure_ascii=False)}"
    caller = call_oneshot or _default_oneshot
    schema = submit_month_suggest_schema()
    result = caller(
        prompt,
        purpose="monthly-suggest",
        force_tool_name=schema["name"],
        tool_schema=schema,
        max_tokens=SUGGEST_MAX_TOKENS,
    )
    if result.get("failed"):
        return "suggest failed"
    args = result.get("tool_args") if isinstance(result.get("tool_args"), dict) else {}
    items = _clean_items(args.get("items"), _allowed_ids(loaded))
    record = {"months": [row["month_key"] for row in loaded], "missing": missing, "items": items}
    monthly_staging_dir().mkdir(parents=True, exist_ok=True)
    atomic_json_write(suggest_path(), record)
    return _format_reply(record)
