"""Live Xiaomi oneshot on the full 2026-08-15 daily board (opt-in).

Enable with ``HERMES_DIGEST_LIVE_LLM=1``. Copies the production daily file
byte-for-byte; fails if the copy was truncated. Prints a token comparison
for mixed-list baseline vs typed-array Phase 2 vs wrap-up.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest
from conftest import load_plugin_module

pytestmark = pytest.mark.skipif(
    os.environ.get("HERMES_DIGEST_LIVE_LLM", "").strip() not in {"1", "true", "yes"},
    reason="Set HERMES_DIGEST_LIVE_LLM=1 to run live typed-prompt tests",
)

_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "memories"
    / "staging"
    / "daily"
    / "2026-08-15.md"
)

_MIXED_BASELINE = {
    "label": "mixed-list Phase 2 (prior probe, no max_tokens)",
    "input_tokens": 10717,
    "output_tokens": 9508,
    "reasoning_tokens": 9320,
    "total_tokens": 20225,
    "wall_seconds": 232,
    "max_tokens": None,
}


def _digest():
    return load_plugin_module("digest.py", "memory_digest_live_typed_prompt")


def _dedup():
    return load_plugin_module("dedup_prompt.py", "memory_digest_live_typed_dedup")


def test_live_typed_phase2_and_wrapup_on_full_2026_08_15(tmp_path, monkeypatch):
    digest = _digest()
    dedup = _dedup()
    assert _SOURCE.is_file(), f"missing source daily file {_SOURCE}"
    source_text = _SOURCE.read_text(encoding="utf-8")
    source_n = len(digest._daily_blocks(source_text))
    dest = tmp_path / "memories" / "staging" / "daily" / "2026-08-15.md"
    dest.parent.mkdir(parents=True)
    shutil.copyfile(_SOURCE, dest)
    copied = dest.read_text(encoding="utf-8")
    blocks = digest._daily_blocks(copied)
    assert copied == source_text
    assert len(copied) >= 18000
    assert len(blocks) == source_n == 34

    prompt = dedup.build_proposer_prompt(blocks, [])
    typed_n = 0
    for kind in ("events", "facts", "procedures", "decisions"):
        heading = f"### Existing {kind}"
        assert heading in prompt
        section = prompt.split(heading, 1)[1].split("###", 1)[0].strip()
        payload = json.loads(section) if section.startswith("[") else []
        typed_n += len(payload)
    assert typed_n == 34
    assert "Existing blocks already in the file" not in prompt
    assert "submit_operations" in prompt

    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    captures: list[dict] = []
    orig = digest._invoke_digest_oneshot_tool

    def wrapping(prompt_text, platform, *, purpose="", force_tool_name="", **kwargs):
        t0 = time.perf_counter()
        out = orig(
            prompt_text,
            platform,
            purpose=purpose,
            force_tool_name=force_tool_name,
            **kwargs,
        )
        packed = dict(out)
        packed["wall_seconds"] = round(time.perf_counter() - t0, 1)
        packed["purpose"] = purpose
        packed["force_tool_name"] = force_tool_name
        captures.append(packed)
        return packed

    monkeypatch.setattr(digest, "_invoke_digest_oneshot_tool", wrapping)

    t_p2 = time.perf_counter()
    proposer = digest.make_oneshot_proposer(session_id="live-typed", run_id="step7")
    ops = proposer(blocks, [], attempt=1)
    p2_wall = round(time.perf_counter() - t_p2, 1)
    assert isinstance(ops, list)
    p2 = next(c for c in captures if c.get("force_tool_name") == "submit_operations")
    assert p2.get("failed") is False
    args = p2.get("tool_args") or {}
    assert isinstance(args.get("operations"), list)

    t_w = time.perf_counter()
    wrap = digest.run_day_wrapup(dest)
    wrap_wall = round(time.perf_counter() - t_w, 1)
    assert wrap.get("outcome") == "written"
    phrase = str(wrap.get("phrase") or "")
    assert phrase
    for line in phrase.splitlines():
        assert len(line.lstrip("- ").strip()) <= digest.MAX_WRAPUP_CHARS
    wcap = next(c for c in captures if c.get("force_tool_name") == "submit_day_wrapup")
    assert wcap.get("failed") is False

    def _row(label, rec, wall, cap):
        return (
            f"{label}: input={rec.get('input_tokens')} output={rec.get('output_tokens')} "
            f"reasoning={rec.get('reasoning_tokens')} total={rec.get('total_tokens')} "
            f"wall={wall}s max_tokens={cap} finish_reason={rec.get('finish_reason')}"
        )

    p2_reason = int(p2.get("reasoning_tokens") or 0)
    base_reason = int(_MIXED_BASELINE["reasoning_tokens"])
    delta = p2_reason - base_reason
    pct = (100.0 * delta / base_reason) if base_reason else 0.0
    summary = "\n".join(
        [
            "=== STEP7 TOKEN COMPARISON (full 2026-08-15.md, 34 cards) ===",
            _row(
                _MIXED_BASELINE["label"],
                _MIXED_BASELINE,
                _MIXED_BASELINE["wall_seconds"],
                _MIXED_BASELINE["max_tokens"],
            ),
            _row(
                "typed-array Phase 2 (this run)",
                p2,
                p2.get("wall_seconds") or p2_wall,
                digest.ONESHOT_DIGEST_MAX_TOKENS,
            ),
            _row(
                "wrap-up oneshot (this run)",
                wcap,
                wcap.get("wall_seconds") or wrap_wall,
                digest.ONESHOT_WRAPUP_MAX_TOKENS,
            ),
            f"reasoning delta vs mixed-list: {delta:+d} ({pct:+.1f}%)",
            f"wrap-up phrase: {phrase}",
        ]
    )
    print(summary)
    dest_text = dest.read_text(encoding="utf-8")
    assert digest.DAY_WRAPUP_HEADING in dest_text
    assert phrase in dest_text
