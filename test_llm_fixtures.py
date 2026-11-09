"""Recorded replies must keep parsing after a provider envelope changes."""

from __future__ import annotations

import json
from pathlib import Path

from test_worker_llm import _load_worker_llm

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm"


def test_recorded_replies_extract():
    wl = _load_worker_llm()
    good = json.loads((_FIXTURES / "openai_tool_call.json").read_text(encoding="utf-8"))
    assert wl.extract_tool_calls_from_result(good) == [
        ("submit_tighten_fact", {"kind": "Factual", "content": "kept"})
    ]
    wrapped = json.loads((_FIXTURES / "text_wrapped.json").read_text(encoding="utf-8"))
    assert wl.extract_tool_calls_from_result(
        wrapped, default_tool_name="submit_tighten_event"
    ) == [("submit_tighten_event", {"beginning": "a", "course": "b", "outcome": "c"})]
