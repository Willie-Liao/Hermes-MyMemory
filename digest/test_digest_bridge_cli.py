# test_digest_bridge_cli.py
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
    spec = importlib.util.spec_from_file_location("memory_digest_bridge_cli", BRIDGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_bridge_dispatches_request_weekly_reorganise(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured: dict = {}

    class FakeRun:
        @staticmethod
        def request_weekly_reorganise(*, date_str=None, session_key=None, force=True):
            captured.update(
                {
                    "date_str": date_str,
                    "session_key": session_key,
                    "force": force,
                }
            )
            return {"outcome": "rewritten", "date": date_str, "path": "/tmp/x.md"}

    monkeypatch.setattr(bridge, "_load_digest_run", lambda: FakeRun)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "request_weekly_reorganise",
                    "args": {
                        "date_str": "2026-07-20",
                        "session_key": "s1",
                        "force": True,
                    },
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "rewritten"
    assert captured == {
        "date_str": "2026-07-20",
        "session_key": "s1",
        "force": True,
    }


def test_bridge_dispatches_request_resummarise(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured: dict = {}

    class FakeRun:
        @staticmethod
        def request_weekly_reorganise(*, date_str=None, session_key=None, force=True):
            captured.update(
                {
                    "date_str": date_str,
                    "session_key": session_key,
                    "force": force,
                }
            )
            return {"outcome": "rewritten", "date": date_str, "path": "/tmp/x.md"}

    monkeypatch.setattr(bridge, "_load_digest_run", lambda: FakeRun)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "request_resummarise",
                    "args": {
                        "session_key": "weekly-ui",
                        "session_id": "weekly-ui",
                        "date_str": "2026-07-20",
                        "force": True,
                    },
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "rewritten"
    assert captured == {
        "date_str": "2026-07-20",
        "session_key": None,
        "force": True,
    }


def test_bridge_dispatches_list_weekly_span_candidates(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured: dict = {}

    class FakeRun:
        @staticmethod
        def list_weekly_span_candidates(week_key):
            captured["week_key"] = week_key
            return {"week_key": week_key, "outcome": "listed", "candidates": []}

    monkeypatch.setattr(bridge, "_load_digest_run", lambda: FakeRun)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {"op": "list_weekly_span_candidates", "args": {"week_key": "2026-W29"}}
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "listed"
    assert captured["week_key"] == "2026-W29"


def test_bridge_dispatches_validate_weekly_spans(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured: dict = {}

    class FakeRun:
        @staticmethod
        def validate_weekly_spans(week_key, candidates):
            captured.update({"week_key": week_key, "candidates": candidates})
            return {"week_key": week_key, "outcome": "validated", "results": []}

    monkeypatch.setattr(bridge, "_load_digest_run", lambda: FakeRun)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "validate_weekly_spans",
                    "args": {"week_key": "2026-W29", "candidates": [{"id": "mem-1"}]},
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "validated"
    assert captured == {"week_key": "2026-W29", "candidates": [{"id": "mem-1"}]}


def test_bridge_dispatches_resolve_weekly_span(monkeypatch, capsys):
    bridge = _load_bridge_module()
    captured: dict = {}

    class FakeRun:
        @staticmethod
        def resolve_weekly_span(
            week_key,
            block_id,
            action,
            *,
            proposed_valid_to=None,
            interval=None,
            due_date=None,
            idempotency_key=None,
        ):
            captured.update(
                {
                    "week_key": week_key,
                    "block_id": block_id,
                    "action": action,
                    "proposed_valid_to": proposed_valid_to,
                    "interval": interval,
                    "due_date": due_date,
                    "idempotency_key": idempotency_key,
                }
            )
            return {"outcome": "applied", "valid_to": "2026-08-01"}

    monkeypatch.setattr(bridge, "_load_digest_run", lambda: FakeRun)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "resolve_weekly_span",
                    "args": {
                        "week_key": "2026-W29",
                        "block_id": "mem-1",
                        "action": "confirm",
                        "proposed_valid_to": "2026-08-01",
                    },
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result"]["outcome"] == "applied"
    assert captured == {
        "week_key": "2026-W29",
        "block_id": "mem-1",
        "action": "confirm",
        "proposed_valid_to": "2026-08-01",
        "interval": None,
        "due_date": None,
        "idempotency_key": None,
    }


def test_bridge_dispatch_failure_returns_error_envelope(monkeypatch, capsys):
    bridge = _load_bridge_module()

    class FailingRun:
        @staticmethod
        def request_weekly_reorganise(**_k):
            raise RuntimeError("dispatch failed")

    monkeypatch.setattr(bridge, "_load_digest_run", lambda: FailingRun)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(
            json.dumps(
                {
                    "op": "request_weekly_reorganise",
                    "args": {"date_str": "2026-07-20", "force": True},
                }
            )
        ),
    )

    assert bridge.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "error": "dispatch failed"}
