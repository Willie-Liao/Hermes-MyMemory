"""Weekly UI latency suite — config, bridge spawn, E2E timing, report.

Default ops match the two slow Weekly UI buttons:
  - rescan     → generate_week(reason=rescan)
  - reorganise → request_weekly_reorganise (Phase-2 oneshot; no generate_week)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BUDGETS_MS: dict[str, int] = {
    "rescan": 120_000,
    "reorganise": 120_000,
    # Optional legacy / low-level bridge ops if listed in config:
    "generate_week": 120_000,
    "tighten_hot_entry": 30_000,
    "hot_health": 60_000,
    "chronicle": 60_000,
    "request_weekly_reorganise": 120_000,
    "request_resummarise": 120_000,  # compatibility alias
}

_DEFAULT_OPS = [
    "rescan",
    "reorganise",
]

_BRIDGE_TIMEOUT_SEC = 300


def _current_iso_week_key() -> str:
    today = date.today()
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def _default_config() -> dict[str, Any]:
    return {
        "week_key": _current_iso_week_key(),
        "digest_date": date.today().isoformat(),
        "tighten": {
            "mode": "tighten",
            "text": "Keep entries short; prefer nouns over prose.",
            "guidance": "Tighten to one line; drop filler adjectives.",
        },
        "chronicle_force": True,
        "hot_health_reason": "latency_suite",
        "ops": list(_DEFAULT_OPS),
        "budgets_ms": dict(DEFAULT_BUDGETS_MS),
    }


def _read_config_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        loaded = yaml.safe_load(text) or {}
    else:
        loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"config must be a mapping: {path}")
    return loaded


def _merge_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if key == "budgets_ms" and isinstance(value, dict):
            budgets = dict(merged.get("budgets_ms") or DEFAULT_BUDGETS_MS)
            budgets.update(value)
            merged["budgets_ms"] = budgets
        elif key == "tighten" and isinstance(value, dict):
            tighten = dict(merged.get("tighten") or {})
            tighten.update(value)
            merged["tighten"] = tighten
        else:
            merged[key] = value
    if "budgets_ms" not in merged:
        merged["budgets_ms"] = dict(DEFAULT_BUDGETS_MS)
    return merged


def load_suite_config(path: Path | None) -> dict[str, Any]:
    """Load suite config from path, HERMES_SUITE_CONFIG, or built-in defaults."""
    resolved = path
    if resolved is None:
        env_path = os.environ.get("HERMES_SUITE_CONFIG")
        if env_path:
            resolved = Path(env_path)

    defaults = _default_config()
    if resolved is None:
        return defaults

    if not resolved.is_file():
        raise ValueError(f"suite config not found: {resolved}")

    return _merge_config(defaults, _read_config_file(resolved))


def _spawn_bridge(
    hermes_home: Path,
    plugin_dir: str,
    payload: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    bridge_path = hermes_home / "plugins" / plugin_dir / "bridge_cli.py"
    if not bridge_path.is_file():
        raise FileNotFoundError(f"bridge not found: {bridge_path}")

    child_env = {**os.environ, **env, "HERMES_HOME": str(hermes_home)}
    proc = subprocess.run(
        [sys.executable, str(bridge_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=child_env,
        timeout=_BRIDGE_TIMEOUT_SEC,
    )
    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise RuntimeError(proc.stderr.strip() or "bridge returned empty output")

    data = json.loads(stdout)
    if not isinstance(data, dict):
        raise RuntimeError("bridge returned non-object JSON")
    return data


def run_bridge_op(
    hermes_home: Path,
    plugin: str,
    op: str,
    args: dict[str, Any],
    env: dict[str, str],
) -> dict[str, Any]:
    """Run one bridge op with E2E timing; fail-open on any error."""
    started = time.perf_counter()
    try:
        bridge_result = _spawn_bridge(
            hermes_home,
            plugin,
            {"op": op, "args": args},
            env,
        )
        e2e_ms = int((time.perf_counter() - started) * 1000)
        ok = bool(bridge_result.get("ok"))
        return {
            "ok": ok,
            "result": bridge_result.get("result") if ok else None,
            "error": None if ok else str(bridge_result.get("error") or "bridge failed"),
            "e2e_ms": e2e_ms,
            "stages": {},
        }
    except Exception as exc:  # noqa: BLE001 — suite must fail-open per op
        e2e_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "result": None,
            "error": str(exc),
            "e2e_ms": e2e_ms,
            "stages": {},
        }


def write_report(path: Path, run_id: str, rows: list[dict[str, Any]]) -> None:
    """Write JSON report and sibling Markdown summary table."""
    payload = {"suite_run_id": run_id, "rows": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    md_path = path.with_suffix(".md")
    lines = [
        f"# Weekly UI latency suite — {run_id}",
        "",
        "| op | ok | e2e_ms | budget_ms | over_budget | stages | error |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        error = row.get("error")
        error_cell = "" if error is None else str(error).replace("|", "\\|")
        stages = row.get("stages") or {}
        if isinstance(stages, dict) and stages:
            stage_cell = ", ".join(f"{k}={v}" for k, v in stages.items())
        else:
            stage_cell = ""
        lines.append(
            "| {op} | {ok} | {e2e_ms} | {budget_ms} | {over_budget} | {stages} | {error} |".format(
                op=row.get("op", ""),
                ok=row.get("ok"),
                e2e_ms=row.get("e2e_ms"),
                budget_ms=row.get("budget_ms"),
                over_budget=row.get("over_budget"),
                stages=stage_cell.replace("|", "\\|"),
                error=error_cell,
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _args_for_bridge_op(op: str, cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Map low-level bridge op name to (plugin_dir, bridge args)."""
    week_key = cfg["week_key"]
    if op == "generate_week":
        return "MyMemory/weekly", {"week_key": week_key, "reason": "latency_suite"}
    if op == "tighten_hot_entry":
        tighten = dict(cfg.get("tighten") or {})
        tighten["reason"] = "latency_suite"
        return "MyMemory/weekly", tighten
    if op == "hot_health":
        return "MyMemory/weekly", {"reason": cfg.get("hot_health_reason") or "latency_suite"}
    if op == "chronicle":
        return "MyMemory/weekly", {
            "week_key": week_key,
            "force": bool(cfg.get("chronicle_force")),
        }
    if op == "request_weekly_reorganise":
        return "MyMemory/digest", {"date_str": cfg["digest_date"]}
    if op == "request_resummarise":
        return "MyMemory/digest", {"date_str": cfg["digest_date"]}
    raise ValueError(f"unknown op: {op}")


def build_op_jobs(cfg: dict[str, Any]) -> list[str]:
    """Return ordered suite op names from config (defaults: rescan, reorganise)."""
    ops = cfg.get("ops") or list(_DEFAULT_OPS)
    return [str(op) for op in ops]


def _suite_env(run_id: str, suite_op: str, stage: str | None = None) -> dict[str, str]:
    env = {
        "HERMES_SUITE_WEEKLY_UI": "1",
        "HERMES_SUITE_OP": suite_op,
        "HERMES_SUITE_RUN_ID": run_id,
    }
    if stage:
        env["HERMES_SUITE_STAGE"] = stage
    return env


def run_suite_op(
    hermes_home: Path,
    suite_op: str,
    cfg: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Run one suite-level op (UI button or low-level bridge name)."""
    if suite_op == "rescan":
        env = _suite_env(run_id, "rescan")
        result = run_bridge_op(
            hermes_home,
            "MyMemory/weekly",
            "generate_week",
            {"week_key": cfg["week_key"], "reason": "rescan"},
            env,
        )
        return {
            "ok": bool(result.get("ok")),
            "e2e_ms": int(result.get("e2e_ms") or 0),
            "stages": {"generate_week_ms": int(result.get("e2e_ms") or 0)},
            "error": result.get("error"),
        }

    if suite_op == "reorganise":
        started = time.perf_counter()
        stages: dict[str, int] = {}
        errors: list[str] = []

        resum = run_bridge_op(
            hermes_home,
            "MyMemory/digest",
            "request_weekly_reorganise",
            {"date_str": cfg["digest_date"]},
            _suite_env(run_id, "reorganise", stage="request_weekly_reorganise"),
        )
        stages["request_weekly_reorganise_ms"] = int(resum.get("e2e_ms") or 0)
        if not resum.get("ok"):
            errors.append(f"request_weekly_reorganise: {resum.get('error')}")

        e2e_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": not errors,
            "e2e_ms": e2e_ms,
            "stages": stages,
            "error": "; ".join(errors) if errors else None,
        }

    # Low-level bridge passthrough (optional config overrides).
    plugin, args = _args_for_bridge_op(suite_op, cfg)
    result = run_bridge_op(
        hermes_home,
        plugin,
        suite_op,
        args,
        _suite_env(run_id, suite_op),
    )
    return {
        "ok": bool(result.get("ok")),
        "e2e_ms": int(result.get("e2e_ms") or 0),
        "stages": result.get("stages") or {},
        "error": result.get("error"),
    }


def _resolve_hermes_home(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("HERMES_HOME")
    if not raw:
        raise ValueError("HERMES_HOME is not set and --hermes-home was not provided")
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise ValueError(f"HERMES_HOME is not a directory: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly UI latency suite")
    parser.add_argument("--config", type=Path, default=None, help="Suite config YAML/JSON")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Report JSON path (default: HERMES_HOME/metrics/weekly-ui-latency-last.json)",
    )
    parser.add_argument("--hermes-home", type=str, default=None, help="Override HERMES_HOME")
    args = parser.parse_args(argv)

    try:
        hermes_home = _resolve_hermes_home(args.hermes_home)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        cfg = load_suite_config(args.config)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    report_path = args.report or (hermes_home / "metrics" / "weekly-ui-latency-last.json")
    run_id = str(uuid.uuid4())
    budgets_ms = cfg.get("budgets_ms") or dict(DEFAULT_BUDGETS_MS)
    rows: list[dict[str, Any]] = []

    for suite_op in build_op_jobs(cfg):
        result = run_suite_op(hermes_home, suite_op, cfg, run_id)
        budget_ms = int(budgets_ms.get(suite_op, DEFAULT_BUDGETS_MS.get(suite_op, 0)))
        e2e_ms = int(result.get("e2e_ms") or 0)
        rows.append(
            {
                "op": suite_op,
                "ok": bool(result.get("ok")),
                "e2e_ms": e2e_ms,
                "budget_ms": budget_ms,
                "over_budget": e2e_ms > budget_ms,
                "stages": result.get("stages") or {},
                "error": result.get("error"),
            }
        )

    write_report(report_path, run_id, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
