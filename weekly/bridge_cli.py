"""JSON stdin/stdout bridge for memory-manager → memory-weekly."""
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


def _load_hermes_env() -> None:
    """Load $HERMES_HOME/.env so UI-spawned python has provider keys."""
    home = os.environ.get("HERMES_HOME") or str(Path(__file__).resolve().parents[3])
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv(hermes_home=home)
    except Exception:
        pass


_load_hermes_env()


def _load_weekly_actions() -> Any:
    try:  # package import (normal plugin load)
        from . import weekly_actions

        return weekly_actions
    except ImportError:  # direct script / pytest subprocess path
        path = Path(__file__).with_name("weekly_actions.py")
        spec = importlib.util.spec_from_file_location("memory_weekly_actions", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load weekly_actions from {path}")
        weekly_actions = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(weekly_actions)
        return weekly_actions


def _load_monthly_actions() -> Any:
    """Load sibling MyMemory monthly pack without making weekly import monthly at module load."""
    path = _mymemory / "monthly" / "monthly_actions.py"
    spec = importlib.util.spec_from_file_location("memory_monthly_actions_bridge", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load monthly_actions from {path}")
    monthly_actions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monthly_actions)
    return monthly_actions


def _load_retention() -> Any:
    """Load sibling MyMemory retention pack."""
    path = _mymemory / "retention" / "retention.py"
    spec = importlib.util.spec_from_file_location("memory_retention_bridge", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load retention from {path}")
    retention = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(retention)
    return retention


def _approve_purge(*, queue: bool, snapshots: bool) -> dict[str, Any]:
    retention = _load_retention()
    return retention.approve_and_purge_over_retention(queue=queue, snapshots=snapshots)


def _purge_old_logs(*, months: int) -> dict[str, Any]:
    retention = _load_retention()
    return retention.purge_old_logs(months=months)


def _emit(payload: dict[str, Any], leaked: str = "") -> None:
    if leaked:
        sys.stderr.write(leaked if leaked.endswith("\n") else leaked + "\n")
    print(json.dumps(payload, default=str), file=sys.stdout, flush=True)


def _handle_raw(raw: str) -> tuple[dict[str, Any], str]:
    """Dispatch one JSON request so --serve can reply without waiting for stdin EOF.

    One-shot mode used to read() until close; that made Node wait for generate to
    finish even after the JSON result was known.
    """
    junk = io.StringIO()
    with redirect_stdout(junk):
        try:
            req = json.loads(raw or "{}")
            if not isinstance(req, dict):
                raise ValueError("request must be a JSON object")

            op = str(req.get("op") or "")
            args = req.get("args") if isinstance(req.get("args"), dict) else {}
            weekly_actions = _load_weekly_actions()

            dispatch: dict[str, Callable[..., dict[str, Any]]] = {
                "weekly_status": lambda **_: weekly_actions.weekly_status(),
                "list_weekly_review_status": lambda **_: weekly_actions.list_weekly_review_status(),
                "generate_week": lambda **a: weekly_actions.generate_week(
                    a.get("week_key"),
                    reason=a.get("reason") or "bridge",
                    background=a.get("background", False),
                ),
                "review_week": lambda **a: weekly_actions.review_week(a.get("week_key")),
                "snooze_week": lambda **a: weekly_actions.snooze_week(seconds=a.get("seconds"), session_id=a.get("session_id") or ""),
                "skip_week": lambda **_: weekly_actions.skip_week(),
                "close_week": lambda **a: weekly_actions.close_week(
                    a.get("week_key"),
                    enforce_sunday=bool(a.get("enforce_sunday")),
                ),
                "approve_and_purge_over_retention": lambda **a: _approve_purge(
                    queue=bool(a.get("queue")),
                    snapshots=bool(a.get("snapshots")),
                ),
                "purge_old_logs": lambda **a: _purge_old_logs(
                    months=int(a.get("months") or 0),
                ),
                "reopen_week": lambda **a: weekly_actions.reopen_week(a.get("week_key")),
                "digest_staleness": lambda **a: weekly_actions.digest_staleness(a.get("week_key")),
                "list_tidy_candidates": lambda **a: weekly_actions.list_tidy_candidates(a.get("week_key")),
                "hot_health": lambda **a: weekly_actions.run_hot_health(
                    reason=a.get("reason") or "bridge"
                ),
                "hot_source_changed": lambda **_: weekly_actions.hot_source_changed(),
                "chronicle": lambda **a: weekly_actions.get_or_refresh_chronicle(
                    a.get("week_key"), force=bool(a.get("force"))
                ),
                "weekly_json": lambda **a: weekly_actions.load_weekly_json(
                    a.get("week_key")
                ),
                "tighten_hot_entry": lambda **a: weekly_actions.tighten_hot_entry(
                    mode=a.get("mode") or "tighten",
                    text=a.get("text") or "",
                    guidance=a.get("guidance") or "",
                    entry_type=a.get("entry_type") or "",
                    source_text=a.get("source_text") or "",
                    peer_text=a.get("peer_text") or "",
                    peer_entries=a.get("peer_entries")
                    if isinstance(a.get("peer_entries"), list)
                    else None,
                    reason=a.get("reason") or "",
                    actions=a.get("actions") if isinstance(a.get("actions"), list) else None,
                    source_ref=a.get("source_ref") or "",
                    peer_ref=a.get("peer_ref") or "",
                ),
                "monthly_json": lambda **a: _load_monthly_actions().load_monthly_yaml(
                    a.get("month_key")
                ),
                "generate_month": lambda **a: _load_monthly_actions().generate_month(
                    a.get("month_key"), reason=a.get("reason") or "bridge"
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
    return payload, junk.getvalue()


def main(argv: list[str] | None = None) -> int:
    """One JSON request from stdin, or --serve NDJSON until EOF/blank line."""
    args = sys.argv[1:] if argv is None else argv
    if "--serve" in args:
        while True:
            line = sys.stdin.readline()
            if line == "" or not line.strip():
                return 0
            payload, leaked = _handle_raw(line)
            _emit(payload, leaked)
    payload, leaked = _handle_raw(sys.stdin.read())
    _emit(payload, leaked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
