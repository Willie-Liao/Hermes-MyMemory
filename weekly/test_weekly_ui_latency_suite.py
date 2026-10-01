import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent / "scripts"
SUITE = SCRIPTS / "weekly_ui_latency_suite.py"


def _load_suite():
    spec = importlib.util.spec_from_file_location("weekly_ui_latency_suite", SUITE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_suite_config_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_SUITE_CONFIG", raising=False)
    mod = _load_suite()
    cfg = mod.load_suite_config(None)
    assert "week_key" in cfg
    assert cfg["ops"] == ["rescan", "reorganise"]
    assert cfg["budgets_ms"]["rescan"] == 120_000
    assert cfg["budgets_ms"]["reorganise"] == 120_000


def test_load_suite_config_missing_path_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_SUITE_CONFIG", raising=False)
    mod = _load_suite()
    missing = tmp_path / "no-such-suite.yaml"
    with pytest.raises(ValueError, match="suite config not found"):
        mod.load_suite_config(missing)


def test_run_bridge_op_fail_open(monkeypatch, tmp_path):
    mod = _load_suite()

    def boom(*_a, **_k):
        raise RuntimeError("bridge down")

    monkeypatch.setattr(mod, "_spawn_bridge", boom)
    row = mod.run_bridge_op(
        tmp_path, "memory-weekly", "generate_week", {"week_key": "2026-W30"}, env={}
    )
    assert row["ok"] is False
    assert "bridge down" in (row["error"] or "")
    assert isinstance(row["e2e_ms"], int)
    assert row["e2e_ms"] >= 0


def test_write_report_json_and_md(tmp_path):
    mod = _load_suite()
    out = tmp_path / "report.json"
    rows = [
        {
            "op": "rescan",
            "ok": True,
            "e2e_ms": 100,
            "budget_ms": 120_000,
            "over_budget": False,
            "stages": {"generate_week_ms": 100},
            "error": None,
        }
    ]
    mod.write_report(out, "run-1", rows)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["suite_run_id"] == "run-1"
    assert data["rows"][0]["op"] == "rescan"
    md = out.with_suffix(".md")
    assert md.exists()
    assert "rescan" in md.read_text(encoding="utf-8")


def test_token_sums_from_usage(tmp_path):
    mod = _load_suite()
    ledger = tmp_path / "metrics" / "llm-usage.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps({"purpose": "worker1_event", "input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
        + "\n"
        + json.dumps({"purpose": "worker1_thread", "input_tokens": 5, "output_tokens": 3, "total_tokens": 8})
        + "\n"
        + json.dumps({"purpose": "worker1_summary", "input_tokens": 99, "output_tokens": 99, "total_tokens": 198})
        + "\n",
        encoding="utf-8",
    )
    sums = mod.token_sums_from_usage(tmp_path)
    assert sums["input_tokens"] == 15
    assert sums["output_tokens"] == 5
    assert sums["total_tokens"] == 20


def test_build_op_jobs_defaults_to_rescan_reorganise():
    mod = _load_suite()
    cfg = {
        "week_key": "2026-W30",
        "digest_date": "2026-07-21",
        "ops": ["rescan", "reorganise"],
    }
    assert mod.build_op_jobs(cfg) == ["rescan", "reorganise"]


def test_run_suite_op_rescan_uses_generate_week_reason(monkeypatch, tmp_path):
    mod = _load_suite()
    seen = []

    def fake_run(hermes_home, plugin, op, args, env):
        seen.append((plugin, op, dict(args), dict(env)))
        return {"ok": True, "result": {}, "error": None, "e2e_ms": 50, "stages": {}}

    monkeypatch.setattr(mod, "run_bridge_op", fake_run)
    cfg = {"week_key": "2026-W30", "digest_date": "2026-07-27"}
    row = mod.run_suite_op(tmp_path, "rescan", cfg, "run-x")
    assert row["ok"] is True
    assert row["stages"]["generate_week_ms"] == 50
    assert seen[0][0] == "MyMemory/weekly"
    assert seen[0][1] == "generate_week"
    assert seen[0][2]["reason"] == "rescan"
    assert seen[0][3]["HERMES_SUITE_OP"] == "rescan"


def test_run_suite_op_reorganise_stages_phase2_only(monkeypatch, tmp_path):
    mod = _load_suite()
    seen = []

    def fake_run(hermes_home, plugin, op, args, env):
        seen.append((plugin, op, env.get("HERMES_SUITE_OP"), env.get("HERMES_SUITE_STAGE")))
        return {"ok": True, "result": {}, "error": None, "e2e_ms": 10, "stages": {}}

    monkeypatch.setattr(mod, "run_bridge_op", fake_run)
    cfg = {"week_key": "2026-W30", "digest_date": "2026-07-27"}
    row = mod.run_suite_op(tmp_path, "reorganise", cfg, "run-y")
    assert row["ok"] is True
    assert row["stages"]["request_weekly_reorganise_ms"] == 10
    assert "generate_week_ms" not in row["stages"]
    assert seen == [
        ("MyMemory/digest", "request_weekly_reorganise", "reorganise", "request_weekly_reorganise"),
    ]


def test_main_sets_suite_env_and_continues(monkeypatch, tmp_path):
    mod = _load_suite()
    seen = []

    def fake_run_suite(hermes_home, suite_op, cfg, run_id):
        seen.append(suite_op)
        if suite_op == "reorganise":
            return {
                "ok": False,
                "e2e_ms": 1,
                "stages": {"request_weekly_reorganise_ms": 1},
                "error": "skip",
            }
        return {
            "ok": True,
            "e2e_ms": 2,
            "stages": {"generate_week_ms": 2},
            "error": None,
        }

    monkeypatch.setattr(mod, "run_suite_op", fake_run_suite)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "week_key: '2026-W30'\n"
        "digest_date: '2026-07-21'\n"
        "ops: [rescan, reorganise]\n"
        "budgets_ms: {rescan: 120000, reorganise: 240000}\n",
        encoding="utf-8",
    )
    report = tmp_path / "out.json"
    rc = mod.main(["--config", str(cfg), "--report", str(report)])
    assert rc == 0
    assert seen == ["rescan", "reorganise"]
    data = json.loads(report.read_text(encoding="utf-8"))
    assert len(data["rows"]) == 2
    assert data["rows"][1]["ok"] is False
