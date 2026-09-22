"""Turn up to four month files into a sectioned suggestion list and a chat guide.

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
_SCHEMAS = (
    "cognition_change",
    "behavior_pattern",
    "core_progress",
    "key_decision",
    "key_procedure",
    "comparison_with_last_month",
)
_SCHEMA_SET = frozenset(_SCHEMAS)
GUIDE = (
    "## How to continue\n"
    "Name the sentence. You can ask to add that sentence into USER.md, MEMORY.md, "
    "or HERMES.md, create a skill from that sentence, replace an existing entry with "
    "that sentence, or remove that sentence.\n"
    "No further slash command.\n"
    "Before any write, read the target, say whether the change is a new entry, a merge "
    "into an existing entry, or a replace of a contradicting or dated entry, and show "
    "the exact text. Write only after the user agrees.\n"
    "A skill request is a new SKILL.md draft, or a replace of an existing skill file "
    "when one already covers the same trigger. Show that draft and wait for agreement too."
)
_PREFIX = (
    "This body is one monthly section. schema names the section. "
    "Fill only text and bucket for this body.\n"
    "skill: a procedure with a trigger and a solution.\n"
    "USER.md: a preference about the user, such as language, how they decide, or how they like explanations.\n"
    "HERMES.md: a rule for how Hermes must behave in this workspace, such as paths, tools, or write gates.\n"
    "MEMORY.md: a situation they will hit again, such as a class, a WeChat template, or a phrase that should route to a skill."
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
    """Keep the sectioned list beside month files so a later chat turn can reload the sentences."""
    return monthly_staging_dir() / ".suggest.json"


def _split_month_tokens(tokens: list[str]) -> list[str]:
    parts: list[str] = []
    for token in tokens:
        for piece in str(token).split(","):
            text = piece.strip()
            if text:
                parts.append(text)
    return parts


def _tagged(schema: str, body: str) -> dict[str, str] | None:
    """Keep a section only when it has prose, so an empty portrait cannot look like a suggestion."""
    text = " ".join(str(body or "").split())
    if not text:
        return None
    return {"schema": schema, "body": text}


def compact_month(payload: Any) -> dict[str, Any]:
    """Send section bodies with a schema tag so the model knows what each sentence is.

    Strength, story bullets, and entity rosters stay out. A same-day decision is still a body
    the model can assign, because dropping it left August with almost nothing to cluster.
    """
    sections: list[dict[str, str]] = []
    image = payload.user_image
    for row in image.cognition_change:
        tagged = _tagged("cognition_change", row.text)
        if tagged:
            sections.append(tagged)
    pattern = _tagged("behavior_pattern", image.behavior_pattern.text)
    if pattern:
        sections.append(pattern)
    for row in payload.core_progress:
        tagged = _tagged("core_progress", row.body)
        if tagged:
            sections.append(tagged)
    for row in payload.key_decisions:
        tagged = _tagged("key_decision", row.text)
        if tagged:
            sections.append(tagged)
    for row in payload.key_procedures:
        tagged = _tagged("key_procedure", row.solution or row.problem)
        if tagged:
            sections.append(tagged)
    comparison = payload.comparison_with_last_month
    for row in comparison.changed:
        tagged = _tagged("comparison_with_last_month", row.text)
        if tagged:
            sections.append(tagged)
    for row in comparison.unchanged:
        tagged = _tagged("comparison_with_last_month", row.text)
        if tagged:
            sections.append(tagged)
    suggestion = _tagged("comparison_with_last_month", comparison.suggestion)
    if suggestion:
        sections.append(suggestion)
    return {"month_key": payload.key, "sections": sections}


def _raw_items(args: Any) -> list[Any]:
    """Turn a nested items array or one flat text/bucket object into the same list.

    Some models wrap every body in items. MiniMax returned one flat object and
    the array path treated that as no suggestions.
    """
    if isinstance(args, list):
        return args
    if not isinstance(args, dict):
        return []
    nested = args.get("items")
    if isinstance(nested, list):
        return nested
    if str(args.get("text") or "").strip() and str(args.get("bucket") or "").strip():
        return [args]
    return []


def _clean_items(raw: Any, sections: list[str]) -> list[dict[str, Any]]:
    """Keep a bucketed sentence, taking the section from the body when the model omits it.

    The printed heading is the monthly section. Models that only fill text and bucket
    still group under the body they were shown.
    """
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            continue
        schema = str(row.get("schema") or "").strip()
        if schema not in _SCHEMA_SET:
            schema = sections[index] if index < len(sections) else ""
        bucket = str(row.get("bucket") or "").strip()
        text = " ".join(str(row.get("text") or "").split())
        if schema not in _SCHEMA_SET or bucket not in _BUCKETS or not text:
            continue
        items.append({"schema": schema, "text": text, "bucket": bucket})
    return items


def _items_from_result(result: dict[str, Any], sections: list[str]) -> list[dict[str, Any]]:
    """Read every tool call, nested or flat, so a later call does not erase an earlier one."""
    raw: list[Any] = []
    calls = result.get("tool_calls") or []
    if isinstance(calls, list):
        for call in calls:
            args = call[1] if isinstance(call, (list, tuple)) and len(call) > 1 else call
            raw.extend(_raw_items(args))
    if not raw:
        raw.extend(_raw_items(result.get("tool_args")))
    return _clean_items(raw, sections)


def _format_reply(record: dict[str, Any]) -> str:
    """Print each monthly section on its own, with the bucket on the following line."""
    lines = ["## Monthly suggest"]
    missing = record.get("missing") or []
    if missing:
        lines.append("missing " + ", ".join(str(item) for item in missing))
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in _SCHEMAS}
    for item in record.get("items") or []:
        grouped.setdefault(str(item.get("schema") or ""), []).append(item)
    wrote = False
    for name in _SCHEMAS:
        rows = grouped.get(name) or []
        if not rows:
            continue
        wrote = True
        lines.append(f"### {name}")
        for item in rows:
            lines.append(f"- {item['text']}")
            lines.append(f"  suggested to add to: [{item['bucket']}]")
    if not wrote:
        lines.append("(no suggestions)")
    lines.append("")
    lines.append(GUIDE)
    return "\n".join(lines)


def _bodies(months: list[dict[str, Any]]) -> list[dict[str, str]]:
    """One prompt per body, because one tool call returns one object and drops the rest."""
    rows: list[dict[str, str]] = []
    for month in months:
        for row in month.get("sections") or []:
            schema = str(row.get("schema") or "")
            body = str(row.get("body") or "")
            if schema in _SCHEMA_SET and body:
                rows.append({"schema": schema, "body": body})
    return rows


def suggest_from_tokens(tokens: list[str], *, call_oneshot: CallOneshot | None = None) -> str:
    """Reject a fifth month before any model call, then print one line per body."""
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
    caller = call_oneshot or _default_oneshot
    schema = submit_month_suggest_schema()
    items: list[dict[str, Any]] = []
    failed = 0
    for row in _bodies(loaded):
        prompt = f"{_PREFIX}\n\nschema: {row['schema']}\nbody: {row['body']}"
        result = caller(
            prompt,
            purpose="monthly-suggest",
            force_tool_name=schema["name"],
            tool_schema=schema,
            max_tokens=SUGGEST_MAX_TOKENS,
        )
        if result.get("failed"):
            failed += 1
            continue
        filled = _items_from_result(result, [row["schema"]])
        if filled:
            filled[0]["schema"] = row["schema"]
            items.append(filled[0])
    if not items and failed:
        return "suggest failed"
    record = {"months": [row["month_key"] for row in loaded], "missing": missing, "items": items}
    monthly_staging_dir().mkdir(parents=True, exist_ok=True)
    atomic_json_write(suggest_path(), record)
    return _format_reply(record)
