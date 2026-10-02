#!/usr/bin/env python3
"""Mimic Weekly UI Tighten and split wall time.

The UI POST /api/approval/tighten (and /api/hot/:file/tighten) waits on
an async python3 bridge_cli.py child. This probe times:

  1. Cold Python import of weekly_actions (paid on every click)
  2. In-process oneshot LLM only
  3. Full in-process tighten_hot_entry
  4. spawn python3 bridge_cli.py (same as the UI minus Express)
  5. HTTP POST like the page, with concurrent GET /api/status pings
     as a freeze regression check (event_loop_blocked must be false)

Usage:
  python3 hermes-home/plugins/MyMemory/weekly/scripts/probe_tighten_ui_latency.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (REPO / "hermes-home"))
BRIDGE = HERMES_HOME / "plugins" / "MyMemory" / "weekly" / "bridge_cli.py"
UI = os.environ.get("WEEKLY_UI_URL") or "http://127.0.0.1:3000"

SAMPLE_TEXT = (
    "Beginning: User asked why Tighten still freezes the Weekly UI; "
    "Course: Assistant traced spawnSync python3 bridge_cli.py holding the "
    "Node event loop until the polisher returns; "
    "Outcome: Probe script measures import vs LLM vs HTTP freeze."
)
GUIDANCE = "make it concise."
PAYLOAD = {
    "text": SAMPLE_TEXT,
    "guidance": GUIDANCE,
    "entryType": "event",
}


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(HERMES_HOME)
    plugins = str(HERMES_HOME / "plugins")
    agent = str(HERMES_HOME / "hermes-agent")
    extra = [plugins, agent]
    env["PYTHONPATH"] = os.pathsep.join(
        extra + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    )
    return env


def phase_import_split() -> dict[str, int]:
    code = r"""
import json, os, sys, time
from pathlib import Path
home = Path(os.environ["HERMES_HOME"])
sys.path.insert(0, str(home / "plugins"))
sys.path.insert(0, str(home / "plugins" / "MyMemory"))
sys.path.insert(0, str(home / "hermes-agent"))
sys.path.insert(0, str(home / "plugins" / "MyMemory" / "weekly"))
marks = {}
t0 = time.perf_counter()
try:
    from hermes_cli.env_loader import load_hermes_dotenv
    load_hermes_dotenv(hermes_home=home)
except Exception:
    pass
marks["dotenv_ms"] = int((time.perf_counter() - t0) * 1000)
t1 = time.perf_counter()
import weekly_actions
marks["import_weekly_actions_ms"] = int((time.perf_counter() - t1) * 1000)
t2 = time.perf_counter()
from worker_llm import run_worker_llm_oneshot
marks["import_worker_llm_ms"] = int((time.perf_counter() - t2) * 1000)
marks["import_total_ms"] = int((time.perf_counter() - t0) * 1000)
print(json.dumps(marks))
"""
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    wall = _ms(started)
    if proc.returncode != 0:
        return {
            "ok": 0,
            "wall_ms": wall,
            "error": (proc.stderr or proc.stdout or "import failed")[-800],
        }
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    row["ok"] = 1
    row["wall_ms"] = wall
    return row


def phase_inprocess_oneshot() -> dict[str, object]:
    code = r"""
import json, os, sys, time
from pathlib import Path
home = Path(os.environ["HERMES_HOME"])
sys.path.insert(0, str(home / "plugins"))
sys.path.insert(0, str(home / "plugins" / "MyMemory"))
sys.path.insert(0, str(home / "hermes-agent"))
sys.path.insert(0, str(home / "plugins" / "MyMemory" / "weekly"))
text = os.environ["PROBE_TEXT"]
guide = os.environ["PROBE_GUIDANCE"]
t0 = time.perf_counter()
import weekly_actions
import tighten_tools
from worker_llm import run_worker_llm_oneshot
t_import = int((time.perf_counter() - t0) * 1000)
kind = tighten_tools.infer_tighten_kind(text, "event")
force = tighten_tools.force_tool_for_kind(kind)
prompt = "CURRENT_JSON:\n" + tighten_tools.current_json_for_prompt(kind, text)
t1 = time.perf_counter()
out = run_worker_llm_oneshot(
    prompt,
    plugin="memory-weekly",
    purpose="ui_tighten",
    force_tool_name=force,
    tool_schema=tighten_tools.tool_schema_for_kind(kind),
)
t_llm = int((time.perf_counter() - t1) * 1000)
print(json.dumps({
    "import_ms": t_import,
    "oneshot_ms": t_llm,
    "failed": bool(out.get("failed")),
    "tool_name": out.get("tool_name"),
    "has_args": bool(out.get("tool_args")),
    "preview": str(out.get("tool_args") or out.get("final_response") or "")[:240],
}))
"""
    env = _env()
    env["PROBE_TEXT"] = SAMPLE_TEXT
    env["PROBE_GUIDANCE"] = GUIDANCE
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    wall = _ms(started)
    if proc.returncode != 0:
        return {
            "ok": False,
            "wall_ms": wall,
            "error": (proc.stderr or proc.stdout or "oneshot failed")[-1200],
        }
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    row["ok"] = not row.get("failed")
    row["wall_ms"] = wall
    return row


def phase_inprocess_tighten() -> dict[str, object]:
    code = r"""
import json, os, sys, time
from pathlib import Path
home = Path(os.environ["HERMES_HOME"])
sys.path.insert(0, str(home / "plugins"))
sys.path.insert(0, str(home / "plugins" / "MyMemory"))
sys.path.insert(0, str(home / "hermes-agent"))
sys.path.insert(0, str(home / "plugins" / "MyMemory" / "weekly"))
t0 = time.perf_counter()
import weekly_actions
t_import = int((time.perf_counter() - t0) * 1000)
t1 = time.perf_counter()
out = weekly_actions.tighten_hot_entry(
    text=os.environ["PROBE_TEXT"],
    guidance=os.environ["PROBE_GUIDANCE"],
    entry_type="event",
)
t_call = int((time.perf_counter() - t1) * 1000)
print(json.dumps({
    "import_ms": t_import,
    "tighten_ms": t_call,
    "kind": out.get("kind"),
    "preview": str(out.get("tightened") or "")[:240],
}))
"""
    env = _env()
    env["PROBE_TEXT"] = SAMPLE_TEXT
    env["PROBE_GUIDANCE"] = GUIDANCE
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    wall = _ms(started)
    if proc.returncode != 0:
        return {
            "ok": False,
            "wall_ms": wall,
            "error": (proc.stderr or proc.stdout or "tighten failed")[-1200],
        }
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    row["ok"] = True
    row["wall_ms"] = wall
    return row


def phase_bridge_spawn() -> dict[str, object]:
    payload = json.dumps(
        {"op": "tighten_hot_entry", "args": {
            "mode": "tighten",
            "text": SAMPLE_TEXT,
            "guidance": GUIDANCE,
            "entry_type": "event",
        }}
    )
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=payload,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    wall = _ms(started)
    stdout = (proc.stdout or "").strip()
    parsed = None
    if stdout:
        try:
            start, end = stdout.find("{"), stdout.rfind("}")
            parsed = json.loads(stdout[start : end + 1] if start >= 0 else stdout)
        except json.JSONDecodeError:
            parsed = None
    return {
        "ok": proc.returncode == 0 and bool(parsed and parsed.get("ok")),
        "wall_ms": wall,
        "returncode": proc.returncode,
        "preview": str((parsed or {}).get("result") or stdout)[:240],
        "stderr_tail": (proc.stderr or "")[-400:],
        "error": None if proc.returncode == 0 else (proc.stderr or stdout)[-800],
    }


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 5.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return resp.status, raw


def phase_http_with_pings() -> dict[str, object]:
    """POST tighten like the page; ping /api/status with curl (urllib 503s under concurrency)."""
    pings: list[dict[str, object]] = []
    stop = threading.Event()

    def ping_loop() -> None:
        while not stop.is_set():
            t0 = time.perf_counter()
            proc = subprocess.run(
                [
                    "curl", "-sS", "-o", "/dev/null", "-w", "%{http_code} %{time_total}",
                    "--max-time", "60",
                    UI + "/api/status",
                ],
                capture_output=True,
                text=True,
            )
            parts = (proc.stdout or "").strip().split()
            status = int(parts[0]) if parts and parts[0].isdigit() else 0
            curl_s = float(parts[1]) if len(parts) > 1 else 0.0
            pings.append(
                {
                    "ms": int(curl_s * 1000) or _ms(t0),
                    "status": status,
                    "error": (proc.stderr or "").strip(),
                    "t": time.time(),
                }
            )
            stop.wait(0.3)

    thread = threading.Thread(target=ping_loop, daemon=True)
    thread.start()
    time.sleep(0.8)
    warmup = list(pings)
    pings.clear()
    started = time.perf_counter()
    proc = subprocess.run(
        [
            "curl", "-sS", "-w", "\n%{http_code}",
            "--max-time", "180",
            "-X", "POST",
            UI + "/api/approval/tighten",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(PAYLOAD),
        ],
        capture_output=True,
        text=True,
    )
    wall = _ms(started)
    stop.set()
    thread.join(timeout=2.0)
    stdout = proc.stdout or ""
    lines = stdout.strip().splitlines()
    http_status = int(lines[-1]) if lines and lines[-1].isdigit() else 0
    body_text = "\n".join(lines[:-1]) if lines else ""
    try:
        body = json.loads(body_text) if body_text else {}
    except json.JSONDecodeError:
        body = {"raw": body_text[:400]}
    ping_ms = [int(p["ms"]) for p in pings]
    return {
        "ok": http_status == 200,
        "http_status": http_status,
        "wall_ms": wall,
        "error": "" if http_status == 200 else (proc.stderr or body_text)[-400:],
        "preview": str(body.get("tightened") if isinstance(body, dict) else body)[:240],
        "warmup_ping_ms": [int(p["ms"]) for p in warmup],
        "ping_count": len(pings),
        "ping_ms_max": max(ping_ms) if ping_ms else None,
        "ping_ms_median": sorted(ping_ms)[len(ping_ms) // 2] if ping_ms else None,
        "pings_over_1s": sum(1 for ms in ping_ms if ms >= 1000),
        "event_loop_blocked": bool(ping_ms) and max(ping_ms) >= 1000,
    }


def main() -> int:
    print(f"HERMES_HOME={HERMES_HOME}")
    print(f"UI={UI}")
    print(f"BRIDGE={BRIDGE}")
    report: dict[str, object] = {
        "hermes_home": str(HERMES_HOME),
        "ui": UI,
        "sample_chars": len(SAMPLE_TEXT),
    }
    skip = {p.strip() for p in os.environ.get("PROBE_SKIP", "").split(",") if p.strip()}
    if "import" not in skip:
        print("\n== 1. cold import split ==")
        report["import"] = phase_import_split()
        print(json.dumps(report["import"], ensure_ascii=False, indent=2))
    if "oneshot" not in skip:
        print("\n== 2. in-process oneshot (import + LLM) ==")
        report["oneshot"] = phase_inprocess_oneshot()
        print(json.dumps(report["oneshot"], ensure_ascii=False, indent=2))
    if "tighten" not in skip:
        print("\n== 3. in-process tighten_hot_entry ==")
        report["tighten"] = phase_inprocess_tighten()
        print(json.dumps(report["tighten"], ensure_ascii=False, indent=2))
    if "bridge" not in skip:
        print("\n== 4. spawn bridge_cli.py (UI python child) ==")
        report["bridge"] = phase_bridge_spawn()
        print(json.dumps(report["bridge"], ensure_ascii=False, indent=2))
    if "http" not in skip:
        print("\n== 5. HTTP POST /api/approval/tighten + GET / pings ==")
        report["http"] = phase_http_with_pings()
        print(json.dumps(report["http"], ensure_ascii=False, indent=2))

    out = HERMES_HOME / "metrics" / "probe-tighten-ui-latency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")
    http = report.get("http")
    if isinstance(http, dict) and http.get("event_loop_blocked"):
        print(
            "\nCAUSE: Node event loop blocked "
            f"(max GET / ping {http.get('ping_ms_max')} ms while Tighten ran)."
        )
        return 1
    keys = [k for k in ("import", "oneshot", "tighten", "bridge", "http") if k in report]
    return 0 if keys and all(
        isinstance(report[k], dict) and report[k].get("ok") for k in keys
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
