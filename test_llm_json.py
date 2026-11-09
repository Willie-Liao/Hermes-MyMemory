"""Lenient model-JSON parsing must accept small slips without another model call."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import llm_json  # noqa: E402


def test_strict_json_stays_on_the_fast_path():
    assert llm_json.loads_lenient('{"a": 1}') == {"a": 1}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("{a: 1,}", {"a": 1}),
        ("{'a': 'b'}", {"a": "b"}),
        ("{a: 1, // note\n}", {"a": 1}),
        ("{a: 1, /* note */}", {"a": 1}),
        ("{a: +1}", {"a": 1}),
        ("{a: 0x10}", {"a": 16}),
    ],
)
def test_json5_slips(raw, expected):
    assert llm_json.loads_lenient(raw) == expected


def test_trailing_comma_fallback_when_json5_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "json5", None)
    assert llm_json.loads_lenient('{"a": 1,}') == {"a": 1}


def test_total_failure_raises_value_error():
    with pytest.raises(ValueError):
        llm_json.loads_lenient("not json")
