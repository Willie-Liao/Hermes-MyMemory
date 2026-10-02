"""Day wrap-up trailer is not a memory block and survives daily rewrites."""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import load_plugin_module

_plugin_dir = Path(__file__).resolve().parent
if str(_plugin_dir) not in sys.path:
    sys.path.insert(0, str(_plugin_dir))


def _digest():
    return load_plugin_module("digest.py", "memory_digest_wrapup_test")


def _fence(block_id: str = "mem-a", body: str = "Factual: keep.") -> str:
    return (
        f"---\nid: {block_id}\ntype: fact\nentity: Casey\n"
        "confidence: high\nimportance: 3\nstatus: candidate\n"
        "sources: [session s1]\n---\n"
        f"{body}\n"
    )


def test_split_join_roundtrip_and_daily_blocks_ignore_trailer():
    digest = _digest()
    fences = _fence()
    joined = digest.join_daily_wrapup(fences, "Xiaohongshu infographic as HTML cards")
    assert digest.DAY_WRAPUP_HEADING in joined
    body, phrase = digest.split_daily_wrapup(joined)
    assert phrase == "- Xiaohongshu infographic as HTML cards"
    assert digest.DAY_WRAPUP_HEADING not in body
    blocks = digest._daily_blocks(joined)
    assert len(blocks) == 1
    assert blocks[0]["id"] == "mem-a"
    assert "Day wrap-up" not in str(blocks[0].get("body", ""))


def test_append_inserts_new_fences_before_wrapup(tmp_path):
    digest = _digest()
    daily = tmp_path / "daily.md"
    daily.write_text(
        digest.join_daily_wrapup(_fence("mem-a"), "old phrase"),
        encoding="utf-8",
    )
    digest._append_daily_digest(daily, _fence("mem-b", "Factual: new."))
    text = daily.read_text(encoding="utf-8")
    assert text.rstrip().endswith("old phrase")
    assert text.index("mem-a") < text.index("mem-b") < text.index(digest.DAY_WRAPUP_HEADING)
    assert len(digest._daily_blocks(text)) == 2


def test_commit_rewrites_fences_and_reattaches_wrapup(tmp_path, monkeypatch):
    digest = _digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    original = digest.join_daily_wrapup(_fence("mem-keep"), "keep this phrase")
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-15.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(original, encoding="utf-8")
    ok, errors = digest._commit_candidate_once(
        daily,
        [],
        [
            {
                "operation": "update",
                "id": "mem-keep",
                "changes": {"importance": 4},
            }
        ],
        session_id="s1",
        run_id="r1",
        base_content=original,
    )
    assert ok, errors
    text = daily.read_text(encoding="utf-8")
    assert "keep this phrase" in text
    assert text.rstrip().endswith("keep this phrase")
    assert digest.DAY_WRAPUP_HEADING in text
    blocks = digest._daily_blocks(text)
    assert len(blocks) == 1
    assert blocks[0]["importance"] == 4


def test_run_day_wrapup_writes_clamped_phrase(tmp_path, monkeypatch):
    digest = _digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-08-15.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(_fence("mem-keep"), encoding="utf-8")

    def fake_oneshot(prompt, platform, *, purpose, force_tool_name="", **_k):
        assert force_tool_name == "submit_day_wrapup"
        assert purpose == "digest-wrapup"
        assert "### Today events" in prompt or "### Today facts" in prompt
        return {
            "tool_name": "submit_day_wrapup",
            "tool_args": {
                "phrases": [
                    "The user discussed an infographic about the memory plugin.",
                    "The user asked for dialects listed in a table.",
                ]
            },
            "failed": False,
        }

    monkeypatch.setattr(digest, "_invoke_digest_oneshot_tool", fake_oneshot)
    digest.run_day_wrapup(daily)
    text = daily.read_text(encoding="utf-8")
    assert digest.DAY_WRAPUP_HEADING in text
    assert "- The user discussed an infographic about the memory plugin." in text
    assert "- The user asked for dialects listed in a table." in text
    assert text.rstrip().endswith("- The user asked for dialects listed in a table.")


def test_clamp_wrapup_phrase_keeps_identifiers():
    digest = _digest()
    assert digest.clamp_wrapup_phrase("") == ""
    intact = "switched to mimo-v2.5 then patched worker_llm.py."
    assert digest.clamp_wrapup_phrase(intact) == intact
    assert "mimo-v2.5" in digest.clamp_wrapup_phrase(intact)
    assert "worker_llm.py" in digest.clamp_wrapup_phrase(intact)
    assert digest.MAX_WRAPUP_CHARS == 200
    kept = "x" * 200
    assert digest.clamp_wrapup_phrase(kept) == kept
    assert digest.clamp_wrapup_phrase("y" * 201) == "y" * 200


def test_join_preserves_independent_event_bullets():
    digest = _digest()
    body = (
        "- The user discussed an infographic about the memory plugin.\n"
        "- The user asked for dialects listed in a table."
    )
    joined = digest.join_daily_wrapup(_fence("mem-a"), body)
    _fences, phrase = digest.split_daily_wrapup(joined)
    assert "- The user discussed an infographic about the memory plugin." in phrase
    assert "- The user asked for dialects listed in a table." in phrase
    assert phrase.count("\n") == 1


def test_build_wrapup_prompt_lists_every_event_predicate():
    dedup = load_plugin_module("dedup_prompt.py", "memory_digest_wrapup_prompt_test")
    prompt = dedup.build_wrapup_prompt(
        [
            {
                "id": "mem-e1",
                "type": "event",
                "predicate": "user_requested_recipe_ingredients",
            },
            {
                "id": "mem-e2",
                "type": "event",
                "predicate": "user_requested_progressive_recall_procedure",
            },
            {"id": "mem-f1", "type": "fact", "entity": "Casey"},
        ]
    )
    assert "Events — cover each; related same-day events may share one bullet" in prompt
    assert "user_requested_recipe_ingredients" in prompt
    assert "user_requested_progressive_recall_procedure" in prompt
    assert "same sitting" in prompt
    assert "one markdown bullet" in prompt
    assert "Do not glue" not in prompt
    assert "one independent sentence each" not in prompt
    assert "One sentence per event on the checklist" not in prompt
    assert "phrases" in prompt
    assert "### Today events" in prompt
    assert "submit_day_wrapup" in prompt
    assert "200" in prompt


def test_submit_day_wrapup_schema_allows_grouped_bullets():
    tools = load_plugin_module("digest_tools.py", "memory_digest_wrapup_schema_test")
    schema = tools.submit_day_wrapup_schema()
    blob = str(schema)
    assert "share one bullet" in blob.casefold() or "same-day" in blob.casefold()
    assert "Not one glued clause" not in blob
    assert "one independent short sentence per event" not in blob
    items = schema["parameters"]["properties"]["phrases"]["items"]
    assert items["maxLength"] == 200
