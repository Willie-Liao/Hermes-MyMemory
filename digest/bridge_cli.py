"""JSON stdin/stdout bridge for memory-manager → memory-digest."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))


def _ensure_hermes_agent_path() -> None:
    """Put hermes-agent on sys.path so bare python can import gateway, etc."""
    home = os.environ.get("HERMES_HOME")
    agent_root = (
        Path(home) / "hermes-agent"
        if home
        else Path(__file__).resolve().parents[3] / "hermes-agent"
    )
    if not agent_root.is_dir():
        return
    path_str = str(agent_root)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


_ensure_hermes_agent_path()


def _load_digest_run() -> Any:
    try:  # package import (normal plugin load)
        from . import digest_run

        return digest_run
    except ImportError:  # direct script / pytest subprocess path
        path = Path(__file__).with_name("digest_run.py")
        spec = importlib.util.spec_from_file_location("memory_digest_run_bridge", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load digest_run from {path}")
        digest_run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(digest_run)
        return digest_run


def _emit(payload: dict[str, Any], leaked: str = "") -> None:
    if leaked:
        sys.stderr.write(leaked if leaked.endswith("\n") else leaked + "\n")
    print(json.dumps(payload, default=str), file=sys.stdout, flush=True)


def main() -> int:
    junk = io.StringIO()
    with redirect_stdout(junk):
        try:
            raw = sys.stdin.read()
            req = json.loads(raw or "{}")
            if not isinstance(req, dict):
                raise ValueError("request must be a JSON object")

            op = str(req.get("op") or "")
            args = req.get("args") if isinstance(req.get("args"), dict) else {}
            digest_run = _load_digest_run()

            dispatch: dict[str, Callable[..., dict[str, Any]]] = {
                "request_digest": lambda **a: digest_run.request_digest(
                    str(a.get("session_key") or ""),
                    reason=str(a.get("reason") or "bridge_force"),
                    force=bool(a.get("force", True)),
                    sync=bool(a.get("sync", True)),
                ),
                "request_weekly_reorganise": lambda **a: digest_run.request_weekly_reorganise(
                    date_str=a.get("date_str"),
                    session_key=a.get("session_key"),
                    force=bool(a.get("force", True)),
                    wait=a.get("wait", True),
                    status_only=a.get("status_only", False),
                ),
                "request_resummarise": lambda **a: digest_run.request_weekly_reorganise(
                    date_str=a.get("date_str"),
                    session_key=(
                        None
                        if str(a.get("session_key") or "") in {"", "weekly-ui"}
                        else str(a.get("session_key"))
                    ),
                    force=bool(a.get("force", True)),
                    wait=a.get("wait", True),
                    status_only=a.get("status_only", False),
                ),
                "list_weekly_span_candidates": lambda **a: digest_run.list_weekly_span_candidates(
                    str(a.get("week_key") or "")
                ),
                "validate_weekly_spans": lambda **a: digest_run.validate_weekly_spans(
                    str(a.get("week_key") or ""),
                    a.get("candidates") if isinstance(a.get("candidates"), list) else None,
                ),
                "resolve_weekly_span": lambda **a: digest_run.resolve_weekly_span(
                    str(a.get("week_key") or ""),
                    str(a.get("block_id") or ""),
                    str(a.get("action") or ""),
                    proposed_valid_to=a.get("proposed_valid_to"),
                    interval=a.get("interval"),
                    due_date=a.get("due_date"),
                    idempotency_key=a.get("idempotency_key"),
                ),
            }
            fn = dispatch.get(op)
            if fn is None:
                payload: dict[str, Any] = {"ok": False, "error": f"unknown op: {op}"}
            else:
                payload = {"ok": True, "result": fn(**args)}
        except json.JSONDecodeError as exc:
            payload = {"ok": False, "error": f"invalid json: {exc}"}
        except Exception as exc:  # noqa: BLE001 — bridge must never raise to Node
            payload = {"ok": False, "error": str(exc)}

    _emit(payload, junk.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
