"""Lenient parse for worker-model text. Plugin-written JSON stays on json.loads.

A quote or trailing comma from the model should not cost another completion.
Strict json first because it is fast and covers almost every reply.
"""

from __future__ import annotations

import contextvars
import json
import re
from typing import Any

_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
parse_path: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mymemory_llm_json_parse_path", default=""
)


def loads_lenient(text: str) -> Any:
    """Accept JSON5-style slips from worker models so a quote or comma typo does not cost a retry.

    Strict json first: it is fast and covers almost every reply. json5 only on failure.
    Fail-open to the trailing-comma regex if json5 is not installed yet (first load before
    ensure_runtime_deps restarts the gateway).
    """
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    else:
        parse_path.set("strict")
        return value
    try:
        import json5
    except ImportError:
        json5 = None  # type: ignore[assignment]
    if json5 is not None:
        try:
            value = json5.loads(text)
        except Exception as exc:
            raise ValueError(f"lenient JSON parse failed: {exc}") from exc
        parse_path.set("json5")
        return value
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"lenient JSON parse failed: {exc}") from exc
    parse_path.set("trailing_comma")
    return value
