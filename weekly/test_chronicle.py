from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("chronicle.py")
WEEKLY_ACTIONS_PATH = Path(__file__).with_name("weekly_actions.py")
BRIDGE_PATH = Path(__file__).with_name("bridge_cli.py")


def _load_chronicle():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_chronicle_test", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_weekly_actions():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_chronicle_actions_test", WEEKLY_ACTIONS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_bridge():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_chronicle_bridge_test", BRIDGE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _md_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_build_prompt_asks_for_json_items():
    chronicle = _load_chronicle()
    prompt = chronicle._build_prompt("2026-W28", "# Week\n- did stuff\n")
    lowered = prompt.casefold()
    assert '"items"' in prompt
    assert "json" in lowered
    assert "3–6" in prompt or "3-6" in prompt
    assert "what" in lowered and "did" in lowered
    assert "avoid figures" in lowered or "n=" in lowered
    assert "good evening" in lowered
    assert "news-anchor" not in lowered


def test_chronicle_schema_summary_skips_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    staging = tmp_path / "memories" / "staging"
    weekly = staging / "weekly"
    weekly.mkdir(parents=True)
    md = (
        "schema_version: 2\n"
        "week_key: 2026-W34\n"
        "cross-day-thread: []\n"
        "intra-day-thread: []\n"
        "summary:\n"
        "  - text: weekly ui has been updated to second version discarding legend and jump to\n"
        "    weekdays:\n"
        "      - Monday\n"
        "      - Tuesday\n"
    )
    (weekly / "2026-W34.md").write_text(md, encoding="utf-8")
    chronicle = _load_chronicle()
    calls: list[str] = []
    monkeypatch.setattr(
        chronicle, "_call_llm", lambda prompt: calls.append(prompt) or "should not run"
    )
    out = chronicle.get_or_refresh_chronicle("2026-W34")
    assert out["outcome"] == "ok"
    assert "weekly ui has been updated" in out["summary"]
    assert "(Monday, Tuesday)" in out["summary"]
    assert calls == []


def test_parse_chronicle_items_accepts_fenced_json():
    chronicle = _load_chronicle()
    items = chronicle._parse_chronicle_items(
        '```json\n{"items": ["Shipped distill", "Closed review"]}\n```'
    )
    assert items == ["Shipped distill", "Closed review"]
    assert chronicle._summary_from_items(items) == "- Shipped distill\n- Closed review"


def test_parse_chronicle_items_rejects_prose():
    chronicle = _load_chronicle()
    try:
        chronicle._parse_chronicle_items("- Shipped distill\n- Closed review")
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-JSON chronicle output")


def test_chronicle_invalid_json_is_llm_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    weekly = tmp_path / "memories" / "staging" / "weekly"
    weekly.mkdir(parents=True)
    (weekly / "2026-W28.md").write_text("# Week\nNo brief.\n", encoding="utf-8")

    chronicle = _load_chronicle()
    monkeypatch.setattr(chronicle, "_call_llm", lambda _p: "- prose bullet")

    out = chronicle.get_or_refresh_chronicle("2026-W28")

    assert out["outcome"] == "llm_failed"
    assert out["summary"] == ""


def test_chronicle_hash_miss_calls_llm_and_writes_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    weekly = tmp_path / "memories" / "staging" / "weekly"
    weekly.mkdir(parents=True)
    md = "# Week\n\nSomething happened.\n"
    (weekly / "2026-W28.md").write_text(md, encoding="utf-8")

    chronicle = _load_chronicle()
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"items": ["Shipped distill", "Closed the week review"]})

    monkeypatch.setattr(chronicle, "_call_llm", fake_llm)

    out = chronicle.get_or_refresh_chronicle("2026-W28")

    assert out["week"] == "2026-W28"
    assert out["cached"] is False
    assert out["summary"] == "- Shipped distill\n- Closed the week review"
    assert out["md_hash"] == _md_hash(md)
    assert len(calls) == 1
    assert "Something happened" in calls[0]

    sidecar = tmp_path / "memories" / "staging" / ".weekly-chronicle.json"
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    assert stored["2026-W28"]["summary"] == out["summary"]
    assert stored["2026-W28"]["md_hash"] == out["md_hash"]


def test_chronicle_hash_hit_skips_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    staging = tmp_path / "memories" / "staging"
    weekly = staging / "weekly"
    weekly.mkdir(parents=True)
    md = "# Week\n\nUnchanged body.\n"
    (weekly / "2026-W28.md").write_text(md, encoding="utf-8")
    digest = _md_hash(md)
    (staging / ".weekly-chronicle.json").write_text(
        json.dumps(
            {
                "2026-W28": {
                    "md_hash": digest,
                    "summary": "Cached news-anchor brief.",
                    "generated_at": "2026-07-13T04:00:00+08:00",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    chronicle = _load_chronicle()
    calls: list[str] = []
    monkeypatch.setattr(
        chronicle, "_call_llm", lambda prompt: calls.append(prompt) or "should not run"
    )

    out = chronicle.get_or_refresh_chronicle("2026-W28")

    assert out["cached"] is True
    assert out["summary"] == "Cached news-anchor brief."
    assert out["md_hash"] == digest
    assert calls == []


def test_chronicle_force_refreshes_even_on_hash_hit(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    staging = tmp_path / "memories" / "staging"
    weekly = staging / "weekly"
    weekly.mkdir(parents=True)
    md = "# Week\n\nBody.\n"
    (weekly / "2026-W28.md").write_text(md, encoding="utf-8")
    digest = _md_hash(md)
    (staging / ".weekly-chronicle.json").write_text(
        json.dumps(
            {
                "2026-W28": {
                    "md_hash": digest,
                    "summary": "Old brief.",
                    "generated_at": "2026-07-12T04:00:00+08:00",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    chronicle = _load_chronicle()
    monkeypatch.setattr(
        chronicle,
        "_call_llm",
        lambda _p: json.dumps({"items": ["Fresh brief"]}),
    )

    out = chronicle.get_or_refresh_chronicle("2026-W28", force=True)

    assert out["cached"] is False
    assert out["summary"] == "- Fresh brief"
    stored = json.loads(
        (staging / ".weekly-chronicle.json").read_text(encoding="utf-8")
    )
    assert stored["2026-W28"]["summary"] == "- Fresh brief"


def test_chronicle_prefers_brief_section_skips_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    weekly = tmp_path / "memories" / "staging" / "weekly"
    weekly.mkdir(parents=True)
    brief_body = (
        "### What happened\n\n"
        "Shipped the distill pipeline [1].\n\n"
        "### Open questions\n\n"
        "Confirm whether Brief seeds Chronicle.\n"
    )
    md = (
        "# Weekly distill 2026-W28\n\n"
        "## Distill\n\n"
        "---\nid: evt-a\ntype: event\nsources: [session s1]\n"
        'related:\n  - "[1] mem-2026-06-29-a"\n---\n'
        "Body [1].\n\n"
        "## Brief\n\n"
        f"{brief_body}"
    )
    (weekly / "2026-W28.md").write_text(md, encoding="utf-8")

    chronicle = _load_chronicle()
    calls: list[str] = []
    monkeypatch.setattr(
        chronicle, "_call_llm", lambda prompt: calls.append(prompt) or "LLM brief"
    )

    out = chronicle.get_or_refresh_chronicle("2026-W28")

    assert out["outcome"] == "ok"
    assert out["cached"] is False
    assert out["summary"] == brief_body.strip()
    assert out["md_hash"] == _md_hash(md)
    assert calls == []

    sidecar = tmp_path / "memories" / "staging" / ".weekly-chronicle.json"
    stored = json.loads(sidecar.read_text(encoding="utf-8"))
    assert stored["2026-W28"]["summary"] == brief_body.strip()
    assert stored["2026-W28"]["md_hash"] == out["md_hash"]


def test_chronicle_force_with_brief_still_skips_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    staging = tmp_path / "memories" / "staging"
    weekly = staging / "weekly"
    weekly.mkdir(parents=True)
    brief_body = "### What happened\n\nForce refresh should reuse Brief [1].\n"
    md = (
        "# Weekly distill 2026-W28\n\n"
        "## Distill\n\n---\nid: evt-a\ntype: event\n---\nBody.\n\n"
        "## Brief\n\n"
        f"{brief_body}"
    )
    (weekly / "2026-W28.md").write_text(md, encoding="utf-8")
    digest = _md_hash(md)
    (staging / ".weekly-chronicle.json").write_text(
        json.dumps(
            {
                "2026-W28": {
                    "md_hash": digest,
                    "summary": "Stale cached summary.",
                    "generated_at": "2026-07-12T04:00:00+08:00",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    chronicle = _load_chronicle()
    calls: list[str] = []
    monkeypatch.setattr(
        chronicle, "_call_llm", lambda prompt: calls.append(prompt) or "should not run"
    )

    out = chronicle.get_or_refresh_chronicle("2026-W28", force=True)

    assert out["cached"] is False
    assert out["summary"] == brief_body.strip()
    assert calls == []
    stored = json.loads(
        (staging / ".weekly-chronicle.json").read_text(encoding="utf-8")
    )
    assert stored["2026-W28"]["summary"] == brief_body.strip()


def test_chronicle_missing_md_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "memories" / "staging" / "weekly").mkdir(parents=True)

    chronicle = _load_chronicle()
    calls: list[str] = []
    monkeypatch.setattr(
        chronicle, "_call_llm", lambda prompt: calls.append(prompt) or "nope"
    )

    out = chronicle.get_or_refresh_chronicle("2026-W28")

    assert out["outcome"] == "no_md"
    assert out["week"] == "2026-W28"
    assert out.get("summary", "") == ""
    assert calls == []


def test_successful_generate_week_refreshes_chronicle(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-07-06.md"
    daily.parent.mkdir(parents=True)
    daily.write_text("source", encoding="utf-8")
    target = tmp_path / "memories" / "staging" / "weekly" / "2026-W28.md"
    refreshed: list[tuple[str, bool]] = []

    monkeypatch.setattr(actions, "_purge_orphan_daily_blocks_before_generate", lambda: None)
    monkeypatch.setattr(actions.weekly, "_parse_week_key", lambda _: (2026, 28))
    monkeypatch.setattr(actions.weekly, "_daily_files_for_week", lambda *_: [daily])
    monkeypatch.setattr(actions.weekly, "_usable_daily_files", lambda files: files)
    monkeypatch.setattr(actions.weekly, "_weekly_path", lambda *_: target)
    monkeypatch.setattr(actions.weekly, "_generate_weekly_content", lambda *_a, **_k: "ok")
    monkeypatch.setattr(actions.weekly, "_load_state", lambda: {})
    monkeypatch.setattr(actions.weekly, "_save_state", lambda _state: None)
    monkeypatch.setattr(actions.weekly, "_log", lambda _message: None)
    monkeypatch.setattr(actions.weekly, "_digest_fingerprint_for_files", lambda _f: "fp")
    monkeypatch.setattr(
        actions.weekly,
        "_store_digest_fingerprint",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(actions.weekly, "_presentation_state", lambda state: state)
    monkeypatch.setattr(actions.weekly, "ensure_week_open_mark", lambda *_a, **_k: None)
    monkeypatch.setattr(actions.weekly, "_now", lambda: __import__("datetime").datetime(2026, 7, 12))
    monkeypatch.setattr(actions, "run_hot_health", lambda *, reason="bridge": {})
    monkeypatch.setattr(
        actions,
        "get_or_refresh_chronicle",
        lambda week_key, *, force=False: refreshed.append((week_key, force))
        or {"cached": False, "summary": "x", "md_hash": "y", "week": week_key},
    )

    result = actions.generate_week("2026-W28", reason="update")

    assert result["outcome"] == "generated"
    assert refreshed == [("2026-W28", True)]


def test_bridge_dispatches_chronicle(monkeypatch, capsys):
    from io import StringIO
    import sys

    bridge = _load_bridge()
    captured: dict = {}

    class FakeActions:
        @staticmethod
        def get_or_refresh_chronicle(week_key=None, *, force=False):
            captured["week_key"] = week_key
            captured["force"] = force
            return {
                "week": week_key or "2026-W28",
                "cached": True,
                "summary": "Brief",
                "md_hash": "abc",
            }

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "chronicle",
                    "args": {"week_key": "2026-W28", "force": False},
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["summary"] == "Brief"
    assert captured == {"week_key": "2026-W28", "force": False}
