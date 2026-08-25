# test_bridge_cli.py
from __future__ import annotations
import importlib.util
import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

BRIDGE = Path(__file__).with_name("bridge_cli.py")


def _run_bridge(payload: str, bridge: Path = BRIDGE) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(bridge)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


def _load_bridge_module():
    spec = importlib.util.spec_from_file_location("memory_weekly_bridge_cli", BRIDGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bridge_adds_hermes_agent_to_sys_path(tmp_path, monkeypatch):
    """Loading bridge with HERMES_HOME set must put hermes-agent on sys.path."""
    hermes_home = tmp_path / "hermes_home"
    agent_root = hermes_home / "hermes-agent"
    gateway_pkg = agent_root / "gateway"
    gateway_pkg.mkdir(parents=True)
    (gateway_pkg / "__init__.py").write_text(
        'STUB_MARKER = "bridge-path-test"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    sys.modules.pop("gateway", None)
    # Drop any prior hermes-agent roots so we only accept the stub after bootstrap.
    sys.path[:] = [p for p in sys.path if Path(p).name != "hermes-agent"]

    _load_bridge_module()

    import gateway  # noqa: PLC0415 — must resolve via bridge bootstrap

    assert gateway.STUB_MARKER == "bridge-path-test"
    assert str(agent_root) in sys.path

    sys.modules.pop("gateway", None)
    if str(agent_root) in sys.path:
        sys.path.remove(str(agent_root))


def test_bridge_unknown_op():
    proc = _run_bridge(json.dumps({"op": "nope", "args": {}}))
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "unknown" in payload.get("error", "").casefold()


@pytest.mark.parametrize("raw", ["null", "[]", '"x"'])
def test_bridge_non_object_json_returns_error_envelope(raw):
    proc = _run_bridge(raw)

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "object" in payload.get("error", "").casefold()


def test_bridge_import_failure_returns_error_envelope(tmp_path):
    isolated_bridge = tmp_path / "bridge_cli.py"
    isolated_bridge.write_text(BRIDGE.read_text(encoding="utf-8"), encoding="utf-8")

    proc = _run_bridge(json.dumps({"op": "weekly_status"}), isolated_bridge)

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload.get("error")


def test_bridge_dispatch_failure_returns_error_envelope(monkeypatch, capsys):
    bridge = _load_bridge_module()

    class FailingActions:
        @staticmethod
        def weekly_status():
            raise RuntimeError("dispatch failed")

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FailingActions)
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({"op": "weekly_status"})))

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "error": "dispatch failed"}


def test_bridge_dispatches_hot_health_refresh(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured = {}

    class FakeActions:
        @staticmethod
        def run_hot_health(*, reason="bridge"):
            captured["reason"] = reason
            return {"MEMORY.md": [], "USER.md": [], "HERMES.md": []}

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"op": "hot_health", "args": {"reason": "manual"}})),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "result": {"MEMORY.md": [], "USER.md": [], "HERMES.md": []},
    }
    assert captured["reason"] == "manual"


def test_bridge_dispatches_hot_source_changed(monkeypatch, capsys):
    bridge = _load_bridge_module()

    class FakeActions:
        @staticmethod
        def hot_source_changed():
            return {"changed": True}

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"op": "hot_source_changed", "args": {}})),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "result": {"changed": True}}


def test_bridge_dispatches_reopen_week(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured = {}

    class FakeActions:
        @staticmethod
        def reopen_week(week_key=None):
            captured["week_key"] = week_key
            return {"outcome": "reopened", "week": week_key or "2026-W29", "restored_blocks": []}

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"op": "reopen_week", "args": {"week_key": "2026-W28"}})),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "reopened"
    assert captured == {"week_key": "2026-W28"}


def test_bridge_dispatches_digest_staleness(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured = {}

    class FakeActions:
        @staticmethod
        def digest_staleness(week_key=None):
            captured["week_key"] = week_key
            return {
                "outcome": "ok",
                "week": week_key or "2026-W29",
                "stale": False,
                "empty_digests": False,
                "fingerprint": "abc",
                "last_fingerprint": "abc",
            }

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"op": "digest_staleness", "args": {"week_key": "2026-W28"}})),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "ok"
    assert captured == {"week_key": "2026-W28"}


def test_bridge_dispatches_weekly_json(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured = {}

    class FakeActions:
        @staticmethod
        def load_weekly_json(week_key=None):
            captured["week_key"] = week_key
            return {
                "outcome": "ok",
                "week": week_key or "2026-W33",
                "payload": {"week_key": week_key, "legend": {}},
            }

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"op": "weekly_json", "args": {"week_key": "2026-W33"}})),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "ok"
    assert captured == {"week_key": "2026-W33"}
    assert "md" not in str(payload["result"]).casefold()


def test_bridge_dispatches_generate_week_with_update_reason(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured = {}

    class FakeActions:
        @staticmethod
        def generate_week(week_key=None, *, reason="bridge", background=False):
            captured["week_key"] = week_key
            captured["reason"] = reason
            captured["background"] = background
            return {"outcome": "generated", "week": week_key or "2026-W29"}

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "generate_week",
                    "args": {"week_key": "2026-W28", "reason": "update"},
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "generated"
    assert captured == {"week_key": "2026-W28", "reason": "update", "background": False}


def test_bridge_dispatches_tighten_hot_entry(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured = {}

    class FakeActions:
        @staticmethod
        def tighten_hot_entry(**kwargs):
            captured.update(kwargs)
            return {"tightened": "short"}

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "tighten_hot_entry",
                    "args": {
                        "mode": "tighten",
                        "text": "long",
                        "guidance": "half length",
                    },
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "result": {"tightened": "short"}}
    assert captured["text"] == "long"
    assert captured["guidance"] == "half length"


def test_bridge_stdout_stays_json_when_action_prints(monkeypatch, capsys):
    bridge = _load_bridge_module()

    class FakeActions:
        @staticmethod
        def list_weekly_review_status():
            print("🧾 Request debug dump written to: /tmp/request_dump.json")
            return {"outcome": "listed", "weeks": []}

    monkeypatch.setattr(bridge, "_load_weekly_actions", lambda: FakeActions)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"op": "list_weekly_review_status", "args": {}})),
    )

    assert bridge.main() == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {"ok": True, "result": {"outcome": "listed", "weeks": []}}
    assert "Request debug dump" in captured.err
    assert "Request debug dump" not in captured.out
