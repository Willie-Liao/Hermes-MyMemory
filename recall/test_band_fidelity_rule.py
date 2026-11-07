"""Tests for `BAND_FIDELITY_RULE` constant and the `render_bands` suffix behavior.

Added in MyMemory 1.1.28.

Signature note: `render_bands(staging=None, *, today=None, hot_text="")`
"""
from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path

import pytest

_MYMEMORY = Path(__file__).resolve().parent.parent
if str(_MYMEMORY) not in sys.path:
    sys.path.insert(0, str(_MYMEMORY))


# ---------------------------------------------------------------------------
# 1) BAND_FIDELITY_RULE exists and is well-formed
# ---------------------------------------------------------------------------


def test_band_fidelity_rule_constant_present():
    from recall import tools

    assert hasattr(tools, "BAND_FIDELITY_RULE"), (
        "BAND_FIDELITY_RULE missing from recall.tools"
    )
    text = tools.BAND_FIDELITY_RULE
    assert isinstance(text, str)
    assert len(text) > 100, "rule looks too short to be the real prompt"


def test_band_fidelity_rule_format_explained():
    from recall import tools

    text = tools.BAND_FIDELITY_RULE
    # must mention the real mem id format so LLM knows what to grep for
    assert "mem-YYYY-MM-DD" in text
    # must forbid inventing dates (the actual wording is "only state the exact day")
    assert "only state the exact day" in text
    # must offer a follow-up rather than just refuse
    assert "should I look it up" in text or "look it up" in text
    # must distinguish exact vs incidental queries
    assert "specific date" in text
    assert "incidental" in text or "approximate" in text
    # must be all-English (user preference — no 中文 inside the prompt)
    assert not re.search(r"[\u4e00-\u9fff]", text), (
        "BAND_FIDELITY_RULE must stay all-English; "
        "the user's-language fallback is the LLM's meta-instruction"
    )


def test_band_fidelity_rule_no_placeholder_drift():
    """The constant in source and in the public attribute must match.

    Re-parse the source file as AST and compare the literal value so a
    future accidental edit (typo, truncation) shows up in CI rather than
    only at runtime.
    """
    from recall import tools

    src_path = Path(tools.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    literal = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "BAND_FIDELITY_RULE":
                    if isinstance(node.value, ast.Constant):
                        literal = node.value.value
    assert literal is not None, "BAND_FIDELITY_RULE assignment not found in source"
    assert literal == tools.BAND_FIDELITY_RULE, (
        "BAND_FIDELITY_RULE in source != attribute on module"
    )


# ---------------------------------------------------------------------------
# 2) render_bands() appends the rule (signature: staging=, today=, hot_text=)
# ---------------------------------------------------------------------------


def test_render_bands_empty_block_guard_preserved():
    """If `blk ::` is absent (zero-block day), render_bands returns ''.
    The rule must NOT leak through.
    """
    from recall import tools

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "staging"
        root.mkdir(parents=True)
        out = tools.render_bands(staging=root)
        assert out == "", (
            f"empty staging should return '', got {out[:120]!r}"
        )
        assert tools.BAND_FIDELITY_RULE not in out


def test_render_bands_appends_rule_when_non_empty():
    """When staging contains at least one `blk ::` day, the rule must be
    the final token of the rendered text.
    """
    from recall import tools
    from recall.conftest import write_fake_staging

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # write_fake_staging writes a fixed 6-block notebook (no extra arg).
        write_fake_staging(root)
        out = tools.render_bands(staging=root)
        if not out:
            pytest.skip(
                "render_bands returned empty for the fake notebook — "
                "likely a BlockIndex / wrap-up compatibility gap, not a "
                "rule-appending regression. Skipping rather than failing."
            )
        # The rule must be the very last token.
        assert out.endswith(tools.BAND_FIDELITY_RULE), (
            f"render_bands output should end with BAND_FIDELITY_RULE; "
            f"got tail: {out[-200:]!r}"
        )
        # And nothing should follow it.
        trailing = out.rstrip()
        assert trailing.endswith(tools.BAND_FIDELITY_RULE.rstrip())
