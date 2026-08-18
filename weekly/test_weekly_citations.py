"""Tests for continuous week-global distill citation numbering."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_citations():
    path = Path(__file__).with_name("weekly_citations.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_citations", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_continuous_numbers_across_two_events():
    cite = _load_citations()
    blocks = [
        {
            "frontmatter": {
                "id": "evt-a",
                "type": "event",
                "related": ["mem-2026-06-29-a", "mem-2026-06-29-b"],
                "sources": ["session x"],
            },
            "body": "First [9]. Second [1].",
        },
        {
            "frontmatter": {
                "id": "evt-b",
                "type": "event",
                "related": ["mem-2026-06-30-c"],
                "sources": ["session y"],
            },
            "body": "Third claim [1].",
        },
    ]
    out, legend = cite.normalize_event_citations(blocks)
    assert legend == {
        1: "mem-2026-06-29-a",
        2: "mem-2026-06-29-b",
        3: "mem-2026-06-30-c",
    }
    assert out[0]["frontmatter"]["related"] == [
        "[1] mem-2026-06-29-a",
        "[2] mem-2026-06-29-b",
    ]
    assert "[1]" in out[0]["body"] and "[2]" in out[0]["body"]
    assert out[1]["frontmatter"]["related"] == ["[3] mem-2026-06-30-c"]
    assert "[3]" in out[1]["body"]


def test_related_already_prefixed_stripped_then_renumbered():
    cite = _load_citations()
    blocks = [
        {
            "frontmatter": {
                "id": "evt-a",
                "type": "event",
                "related": ["[7] mem-2026-06-29-a"],
                "sources": ["session x"],
            },
            "body": "Claim [7].",
        },
    ]
    out, legend = cite.normalize_event_citations(blocks)
    assert legend == {1: "mem-2026-06-29-a"}
    assert out[0]["frontmatter"]["related"] == ["[1] mem-2026-06-29-a"]
    assert cite.extract_cite_numbers(out[0]["body"]) == [1]


def test_non_event_blocks_passthrough():
    cite = _load_citations()
    blocks = [
        {
            "frontmatter": {"id": "h1", "type": "hypothesis", "related": ["evt-a"]},
            "body": "Open question [1].",
        },
        {
            "frontmatter": {
                "id": "evt-a",
                "type": "event",
                "related": ["mem-2026-06-29-a"],
                "sources": ["session x"],
            },
            "body": "Happened [99].",
        },
    ]
    out, legend = cite.normalize_event_citations(blocks)
    assert out[0]["body"] == "Open question [1]."
    assert out[0]["frontmatter"]["related"] == ["evt-a"]
    assert legend == {1: "mem-2026-06-29-a"}
    assert out[1]["frontmatter"]["related"] == ["[1] mem-2026-06-29-a"]
