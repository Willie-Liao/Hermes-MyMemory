# test_weekly_brief_validate.py
from pathlib import Path
import importlib.util


def _load():
    path = Path(__file__).with_name("weekly_brief_validate.py")
    spec = importlib.util.spec_from_file_location("weekly_brief_validate", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_format_brief_for_chat_strips_hashes_keeps_cites():
    v = _load()
    raw = "### Events\n- X [1].\n\n### Hypothesis\n- None.\n"
    out = v.format_brief_for_chat(raw)
    assert "###" not in out
    assert "#" not in out.split("Events")[0]  # no leading hashes on theme
    assert "Events" in out
    assert "[1]" in out


def test_format_brief_for_chat_leaves_four_part_day_headers():
    v = _load()
    raw = (
        "Monday — 2026-08-03 · Events [1]\n"
        "**Digest stall**\n"
        "Alex asked why digest stalled.\n\n"
        "Conflict\n"
        "- None.\n"
    )
    out = v.format_brief_for_chat(raw)
    assert "Monday — 2026-08-03 · Events [1]" in out
    assert "**Digest stall**" in out
    assert "Conflict" in out
