"""Tests for weekly_cite Brief extract, cite-map, staging load, and hot dig-in."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))


def _load_cite():
    module_path = Path(__file__).with_name("weekly_cite.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_cite_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _distill_brief_fixture() -> str:
    return """# Weekly Memory Review — 2026-W26

## Distill

---
id: evt-andrae
type: event
entity: Andrae
related:
  - "[1] mem-2026-06-29-andrae-feedback-summary"
  - "[2] mem-2026-06-29-parent-grading-package-delivered"
sources:
  - session sess-a
confidence: high
status: candidate
valid_from: 2026-06-29
valid_to: 2026-06-29
---
Andrae feedback landed [1]. Parent grading package delivered [2].

---
id: evt-follow
type: event
entity: Alex
related:
  - "[3] mem-2026-06-30-career-pivot-note"
sources:
  - session sess-b
confidence: medium
status: candidate
valid_from: 2026-06-30
valid_to: 2026-06-30
---
Career pivot note [3].

## Brief

Andrae feedback landed [1]. Parent grading package delivered [2].
Career pivot note [3].

What stood out was the grading handoff timing.
"""


def test_extract_brief_returns_brief_only_ignores_distill():
    cite = _load_cite()
    md = _distill_brief_fixture()

    brief = cite.extract_brief(md)

    assert "Andrae feedback landed [1]" in brief
    assert "grading handoff timing" in brief
    assert "type: event" not in brief
    assert "## Distill" not in brief
    assert "mem-2026-06-29-andrae-feedback-summary" not in brief
    assert "## Brief" not in brief


def test_extract_brief_missing_section_returns_empty():
    cite = _load_cite()
    md = "# Weekly\n\n## Distill\n\n---\nid: x\ntype: event\n---\nbody\n"

    assert cite.extract_brief(md) == ""


def test_extract_brief_keeps_theme_headings_until_action_ledger():
    """Worker 2 sometimes emits ## Events under Brief; cites must still extract."""
    cite = _load_cite()
    md = (
        "# Weekly\n\n## Distill\n\n---\nid: e1\ntype: event\n---\nbody\n\n"
        "## Brief\n\n"
        "## Events\n"
        "- Shipped weekly cite UI [1].\n\n"
        "## Hypothesis\n"
        "- Cache may hide Brief [2].\n\n"
        "## Action ledger\n\n"
        "| ID | Action |\n|---|---|\n"
    )

    brief = cite.extract_brief(md)

    assert "## Events" in brief
    assert "Shipped weekly cite UI [1]" in brief
    assert "Cache may hide Brief [2]" in brief
    assert "## Action ledger" not in brief
    assert "type: event" not in brief


def test_load_cite_map_from_related_markers():
    cite = _load_cite()
    md = _distill_brief_fixture()

    cite_map = cite.load_cite_map(md)

    assert cite_map == {
        1: "mem-2026-06-29-andrae-feedback-summary",
        2: "mem-2026-06-29-parent-grading-package-delivered",
        3: "mem-2026-06-30-career-pivot-note",
    }


def test_load_cite_map_missing_distill_returns_empty():
    cite = _load_cite()
    md = "# Weekly\n\n## Brief\n\nJust a brief with [1].\n"

    assert cite.load_cite_map(md) == {}


def test_find_staging_block_returns_body_and_sources(tmp_path, monkeypatch):
    cite = _load_cite()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    mem_id = "mem-2026-06-29-andrae-feedback-summary"
    block = (
        "---\n"
        f"id: {mem_id}\n"
        "type: fact\n"
        "entity: Andrae\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session sess-a, file:notes.md]\n"
        "---\n"
        "Andrae gave concrete feedback on the draft.\n"
    )
    (daily / "2026-06-29.md").write_text(block, encoding="utf-8")

    found = cite.find_staging_block(mem_id)

    assert found is not None
    assert "Andrae gave concrete feedback" in found["body"]
    assert "session sess-a" in found["sources"] or "session sess-a" in str(found["sources"])


def test_find_staging_block_missing_returns_none(tmp_path, monkeypatch):
    cite = _load_cite()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-06-29.md").write_text(
        "---\nid: mem-2026-06-29-other\ntype: fact\nconfidence: high\n"
        "status: candidate\nsources: []\n---\nother\n",
        encoding="utf-8",
    )

    assert cite.find_staging_block("mem-2026-06-29-andrae-feedback-summary") is None


# --- Hot retrieval: dig-in hot fields + arm_hot_action -----------------


def test_arm_hot_action_sets_hot_dig_in_fields(tmp_path, monkeypatch):
    cite = _load_cite()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    out = cite.arm_hot_action(
        file="MEMORY.md",
        index=3,
        before="§ entry before snapshot",
        session_id="s-hot",
    )
    assert out is not None
    dig = cite.get_dig_in()
    assert dig is not None
    assert dig.get("active") is True
    assert dig.get("target_kind") == "hot"
    assert dig.get("action_pending") is True
    assert dig.get("action_file") == "MEMORY.md"
    assert dig.get("action_index") == 3
    assert dig.get("action_before") == "§ entry before snapshot"


def test_clear_action_wipes_hot_fields(tmp_path, monkeypatch):
    cite = _load_cite()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    cite.arm_hot_action(
        file="USER.md",
        index=1,
        before="before text",
        session_id="s-hot",
    )
    cleared = cite.set_dig_in_progress(clear_action=True)
    assert cleared is not None
    dig = cite.get_dig_in()
    assert dig is not None
    assert dig.get("action_pending") is not True
    assert dig.get("action_file") is None
    assert dig.get("action_index") is None
    assert dig.get("action_before") is None
    assert dig.get("target_kind") == "staging"
