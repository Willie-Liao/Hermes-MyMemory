from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("hot_health.py")
WEEKLY_ACTIONS_PATH = Path(__file__).with_name("weekly_actions.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("memory_weekly_hot_health_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_weekly_actions():
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_hot_health_actions_test", WEEKLY_ACTIONS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_hot_files(tmp_path: Path) -> None:
    memories = tmp_path / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    (memories / "MEMORY.md").write_text(
        "Current timed fact\nvalid_to: 2026-08-01\n§\n"
        "Expired timed fact\nvalid_to: 2026-07-01\n",
        encoding="utf-8",
    )
    (memories / "USER.md").write_text("Preference one\n§\nPreference two\n", encoding="utf-8")
    (tmp_path.parent / "HERMES.md").write_text(
        "## Rules\nKeep paths clean.\n\n## Memory\nHot health covers three files.\n",
        encoding="utf-8",
    )


def test_hot_health_writes_validated_json_and_expiry_floor(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # HERMES.md lives beside hermes_home (parent)
    memories = hermes_home / "memories"
    memories.mkdir()
    (memories / "MEMORY.md").write_text(
        "Current timed fact\nvalid_to: 2026-08-01\n§\n"
        "Expired timed fact\nvalid_to: 2026-07-01\n",
        encoding="utf-8",
    )
    (memories / "USER.md").write_text("Preference one\n§\nPreference two\n", encoding="utf-8")
    (tmp_path / "HERMES.md").write_text(
        "## Rules\nKeep paths clean.\n\n## Memory\nHot health covers three files.\n",
        encoding="utf-8",
    )

    hot_health = _load_module()
    monkeypatch.setattr(hot_health, "_today", lambda: date(2026, 7, 12))
    monkeypatch.setattr(
        hot_health,
        "_call_llm",
        lambda prompt: json.dumps(
            {
                "MEMORY.md": [
                    {
                        "index": 0,
                        "kinds": ["move_to_user", "not_allowed"],
                        "reason": "Durable preference.",
                        "actions": ["move_to_user"],
                    }
                ],
                "USER.md": [
                    {
                        "index": 0,
                        "kinds": ["merge"],
                        "peers": [1],
                        "reason": "Overlapping entries.",
                        "actions": ["merge"],
                    }
                ],
                "HERMES.md": [
                    {
                        "index": 1,
                        "kinds": ["rephrase"],
                        "reason": "Tighten Memory section.",
                        "actions": ["rephrase"],
                    }
                ],
            }
        ),
    )

    out = hot_health.run_hot_health(reason="test")

    assert out["MEMORY.md"][0]["kinds"] == ["move_to_user"]
    assert out["MEMORY.md"][1] == {
        "index": 1,
        "kinds": ["outdated"],
        "reason": "valid_to 2026-07-01 is past",
        "actions": ["purge", "extend"],
    }
    assert out["USER.md"][0]["peers"] == [1]
    assert out["USER.md"][0]["peer_groups"] == [[1]]
    assert out["HERMES.md"][0]["index"] == 1
    assert "source_hash" in out
    path = memories / "staging" / ".hot-health.json"
    assert json.loads(path.read_text(encoding="utf-8")) == out
    assert hot_health.load_hot_health() == out


def test_hot_file_path_prefers_hermes_md_inside_home(tmp_path, monkeypatch):
    """Cloud layout: HERMES.md lives at ~/.hermes/HERMES.md, not the parent."""
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    (hermes_home / "HERMES.md").write_text("## Inside\nCloud rules.\n", encoding="utf-8")
    (tmp_path / "HERMES.md").write_text("## Beside\nShould not win.\n", encoding="utf-8")

    hot_health = _load_module()
    assert hot_health._hot_file_path("HERMES.md") == hermes_home / "HERMES.md"
    assert hot_health._read_hot_file("HERMES.md").startswith("## Inside")


def test_hot_health_accepts_fenced_json_and_uses_latest_weekly_context(
    tmp_path, monkeypatch
):
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    memories = hermes_home / "memories"
    weekly = memories / "staging" / "weekly"
    weekly.mkdir(parents=True)
    (memories / "MEMORY.md").write_text("One", encoding="utf-8")
    (memories / "USER.md").write_text("Two", encoding="utf-8")
    (tmp_path / "HERMES.md").write_text("## A\nAlpha\n", encoding="utf-8")
    (weekly / "2026-W26.md").write_text("older", encoding="utf-8")
    (weekly / "2026-W27 reviewed.md").write_text("latest context", encoding="utf-8")

    hot_health = _load_module()
    prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return '```json\n{"MEMORY.md": [], "USER.md": [], "HERMES.md": []}\n```'

    monkeypatch.setattr(hot_health, "_call_llm", fake_llm)

    out = hot_health.run_hot_health(reason="bridge")
    assert out["MEMORY.md"] == []
    assert out["USER.md"] == []
    assert out["HERMES.md"] == []
    assert "source_hash" in out
    assert "latest context" in prompts[0]
    assert "Reason: bridge" in prompts[0]
    assert "HERMES.md:\n[0] ## A\nAlpha" in prompts[0]


def test_hot_health_prompt_numbers_entries_with_zero_based_indices():
    hot_health = _load_module()

    prompt = hot_health._build_prompt(
        "Memory zero\n§\nMemory one",
        "User zero\n§\nUser one",
        "## Zero\nHermes zero\n\n## One\nHermes one\n",
        "",
        reason="test",
    )

    assert "zero-based" in prompt
    assert "MEMORY.md:\n[0] Memory zero\n\n[1] Memory one" in prompt
    assert "USER.md:\n[0] User zero\n\n[1] User one" in prompt
    assert "HERMES.md:\n[0] ## Zero\nHermes zero\n\n[1] ## One\nHermes one" in prompt
    assert "peer_groups" in prompt
    assert "Prefer one peer_groups entry with a single peer" in prompt
    assert "only when entries are too scattered" in prompt


def test_normalize_peer_groups_and_legacy_peers_wrap():
    hot_health = _load_module()
    counts = {"MEMORY.md": 5, "USER.md": 4, "HERMES.md": 3}
    out = hot_health._normalize_suggestions(
        {
            "MEMORY.md": [
                {
                    "index": 0,
                    "kinds": ["merge"],
                    "reason": "multi",
                    "actions": ["merge"],
                    "peer_groups": [[1, 2], [2, 4], [0], ["x"], []],
                }
            ],
            "USER.md": [
                {
                    "index": 0,
                    "kinds": ["merge"],
                    "reason": "legacy",
                    "actions": ["merge"],
                    "peers": [1, 1, 0, 9],
                }
            ],
            "HERMES.md": [],
        },
        counts,
    )
    mem = out["MEMORY.md"][0]
    assert mem["peer_groups"] == [[1, 2], [4]]
    assert mem["peers"] == [1, 2, 4]
    user = out["USER.md"][0]
    assert user["peer_groups"] == [[1]]
    assert user["peers"] == [1]


def test_hot_health_skips_llm_when_source_hash_unchanged(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    memories = hermes_home / "memories"
    memories.mkdir()
    (memories / "MEMORY.md").write_text("One", encoding="utf-8")
    (memories / "USER.md").write_text("Two", encoding="utf-8")
    (tmp_path / "HERMES.md").write_text("## A\nAlpha\n", encoding="utf-8")

    hot_health = _load_module()
    calls = {"n": 0}

    def fake_llm(prompt: str) -> str:
        calls["n"] += 1
        return json.dumps({"MEMORY.md": [], "USER.md": [], "HERMES.md": []})

    monkeypatch.setattr(hot_health, "_call_llm", fake_llm)

    first = hot_health.run_hot_health(reason="first")
    second = hot_health.run_hot_health(reason="second")

    assert calls["n"] == 1
    assert first == second
    assert hot_health.hot_source_changed() is False

    (memories / "USER.md").write_text("Two changed", encoding="utf-8")
    assert hot_health.hot_source_changed() is True
    third = hot_health.run_hot_health(reason="third")
    assert calls["n"] == 2
    assert third["source_hash"] != first["source_hash"]


def test_generate_week_does_not_refresh_hot_health(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    actions = _load_weekly_actions()
    daily = tmp_path / "memories" / "staging" / "daily" / "2026-07-06.md"
    daily.parent.mkdir(parents=True)
    daily.write_text("source", encoding="utf-8")
    target = tmp_path / "memories" / "staging" / "weekly" / "2026-W28.md"
    refreshed: list[str] = []

    monkeypatch.setattr(actions, "_purge_orphan_daily_blocks_before_generate", lambda: None)
    monkeypatch.setattr(actions.weekly, "_parse_week_key", lambda _: (2026, 28))
    monkeypatch.setattr(actions.weekly, "_daily_files_for_week", lambda *_: [daily])
    monkeypatch.setattr(actions.weekly, "_usable_daily_files", lambda files: files)
    monkeypatch.setattr(actions.weekly, "_weekly_path", lambda *_: target)
    monkeypatch.setattr(actions.weekly, "_generate_weekly_content", lambda *_a, **_k: "ok")
    monkeypatch.setattr(actions.weekly, "_load_state", lambda: {})
    monkeypatch.setattr(actions.weekly, "_save_state", lambda _state: None)
    monkeypatch.setattr(actions.weekly, "_log", lambda _message: None)
    monkeypatch.setattr(actions.weekly, "_digest_fingerprint_for_files", lambda _files: "fp")
    monkeypatch.setattr(actions.weekly, "_presentation_state", lambda state: state.setdefault("presentation", {}) or state["presentation"])
    monkeypatch.setattr(actions.weekly, "_store_digest_fingerprint", lambda *_a, **_k: None)
    monkeypatch.setattr(actions.weekly, "ensure_week_open_mark", lambda *_a, **_k: None)
    monkeypatch.setattr(actions.weekly, "_now", lambda: date(2026, 7, 12))
    monkeypatch.setattr(actions, "_refresh_chronicle_after_generate", lambda *_a, **_k: None)
    monkeypatch.setattr(
        actions,
        "run_hot_health",
        lambda *, reason="bridge": refreshed.append(reason) or {},
    )

    result = actions.generate_week("2026-W28", reason="test")

    assert result["outcome"] == "generated"
    assert refreshed == []
    # Hook is a no-op even if called.
    actions._refresh_hot_health_after_generate("test")
    assert refreshed == []


def test_join_and_write_hot_entries_roundtrip(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes_home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    (hermes_home / "memories").mkdir()
    hot_health = _load_module()

    joined = hot_health._join_entries(["alpha", "beta"])
    assert joined == "alpha\n§\nbeta"

    hot_health.write_hot_entries("MEMORY.md", ["alpha", "beta"])
    path = hermes_home / "memories" / "MEMORY.md"
    assert path.read_text(encoding="utf-8") == "alpha\n§\nbeta"
    assert hot_health._split_entries(path.read_text(encoding="utf-8")) == ["alpha", "beta"]
