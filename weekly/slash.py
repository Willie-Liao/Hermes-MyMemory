"""``/weekly`` slash command — UI-first remote control.

Public subcommands (single flat ``/weekly`` command, parsed from ``raw_args``):

    /weekly                  help (four commands)
    /weekly help             same
    /weekly ui               ensure UI server up; return openable URL
    /weekly update [week]    mid-week draft refresh (default: current ISO)
    /weekly close [week]     close anytime (default: current ISO)
    /weekly reopen [week]    reopen a closed week (default: current ISO)

Every handler returns plain text and never raises.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_section_dir = str(Path(__file__).resolve().parent)
if _section_dir not in sys.path:
    sys.path.insert(0, _section_dir)

try:  # package import (normal plugin load)
    from . import weekly as weekly_mod
    from . import weekly_actions
except ImportError:  # pragma: no cover - direct pytest collection path
    _weekly_path = Path(__file__).with_name("weekly.py")
    _weekly_spec = importlib.util.spec_from_file_location(
        "memory_weekly_for_slash", _weekly_path
    )
    if _weekly_spec is None or _weekly_spec.loader is None:
        raise
    weekly_mod = importlib.util.module_from_spec(_weekly_spec)
    _weekly_spec.loader.exec_module(weekly_mod)

    _path = Path(__file__).with_name("weekly_actions.py")
    _spec = importlib.util.spec_from_file_location("memory_weekly_actions", _path)
    if _spec is None or _spec.loader is None:
        raise
    weekly_actions = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(weekly_actions)

_DEFAULT_UI_URL = "http://127.0.0.1:3000"
_UI_PROBE_TIMEOUT = 1.0
_UI_START_WAIT_SECONDS = 60.0
_UI_INSTALL_WAIT_SECONDS = 180.0
_UI_POLL_INTERVAL = 0.5
# Always health-check loopback so public share URLs (NAT / phone) do not break start detection.
_LOCAL_UI_URL = "http://127.0.0.1:3000"
_TUNNEL_START_WAIT_SECONDS = 25.0
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)


def handle_weekly(raw_args: str) -> str:
    """Entry point registered as the ``/weekly`` slash command handler."""
    try:
        tokens = shlex.split(raw_args or "")
    except ValueError:
        tokens = (raw_args or "").split()

    if not tokens or tokens[0].lower() in ("help", "?"):
        return _help()

    sub = tokens[0].lower()
    rest = tokens[1:]

    if sub == "ui":
        return _ui()
    if sub == "update":
        return _update(rest)
    if sub == "close":
        return _close(rest)
    if sub == "reopen":
        return _reopen(rest)
    return f"Unknown /weekly subcommand: {sub}\n\n{_help()}"


def _week_arg(args: list[str]) -> str | None:
    return next((a for a in args if not a.startswith("-")), None)


def _brief_from_update_result(result: dict) -> str:
    """Prefer generate_week brief; else Chronicle rows from the written week YAML."""
    brief = result.get("brief")
    if isinstance(brief, str) and brief.strip():
        return brief.strip()
    path = result.get("path")
    if not path:
        return ""
    try:
        md = Path(path).read_text(encoding="utf-8")
        payload = weekly_mod.weekly_json.loads(md)
    except Exception:
        return ""
    lines: list[str] = []
    for row in payload.summary:
        days = ", ".join(row.weekdays)
        lines.append(f"- {row.text} ({days})" if days else f"- {row.text}")
    return "\n".join(lines)


def _update(args: list[str]) -> str:
    week_key = _week_arg(args)
    result = weekly_actions.update_week(week_key, reason="update")
    outcome = result.get("outcome")

    if outcome == "bad_week":
        return f"/weekly update: '{result.get('week')}' is not a valid YYYY-Www week."
    if outcome == "no_daily":
        return (
            f"/weekly update: The newsroom is quiet — no daily digests for "
            f"{result.get('week')} yet. Chat with Hermes first, then update again."
        )
    if outcome == "failed":
        return f"/weekly update: generation failed for {result.get('week')} (validation/API)."
    if outcome == "generated":
        brief = _brief_from_update_result(result)
        # Slash update = user opted into staging review tools → unlock.
        # Hermes slash handlers do not pass session_id; bind on first chat turn.
        state = weekly_mod._load_state()
        presentation = weekly_mod._presentation_state(state)
        presentation["staging_unlocked"] = True
        presentation["staging_session_id"] = weekly_mod.SLASH_STAGING_SESSION
        presentation.pop("vibe_unlocked", None)
        presentation.pop("vibe_session_id", None)
        weekly_mod._save_state(state)
        footer = (
            f"/weekly update: wrote {result.get('week')} from "
            f"{result.get('sources')} daily file(s) -> {result.get('path')}"
        )
        if brief:
            return f"{brief}\n\n{footer}"
        return footer
    return f"/weekly update: unexpected outcome {outcome!r}."


def _close(args: list[str]) -> str:
    week_key = _week_arg(args)
    result = weekly_actions.close_week(week_key, enforce_sunday=False)
    outcome = result.get("outcome")
    week = result.get("week")

    if outcome == "bad_week":
        return f"/weekly close: '{week}' is not a valid YYYY-Www week."
    if outcome == "already_closed":
        return (
            f"/weekly close: {week} is already closed. "
            f"Reopen the review? Run `/weekly reopen {week}`."
        )
    if outcome == "no_draft":
        return (
            f"/weekly close: no draft for {week}. "
            "Run `/weekly update` first, then close."
        )
    if outcome == "closed":
        if result.get("empty_week"):
            return (
                f"/weekly close: closed empty week {week} "
                f"(no current news) -> {result.get('path')}"
            )
        return f"/weekly close: closed {week} -> {result.get('path')}"
    return f"/weekly close: unexpected outcome {outcome!r}."


def _reopen(args: list[str]) -> str:
    week_key = _week_arg(args)
    result = weekly_actions.reopen_week(week_key)
    outcome = result.get("outcome")
    week = result.get("week")

    if outcome == "bad_week":
        return f"/weekly reopen: '{week}' is not a valid YYYY-Www week."
    if outcome == "no_reviewed_file":
        return f"/weekly reopen: no reviewed file for {week}."
    if outcome == "reopened":
        restored = result.get("restored_blocks") or []
        return (
            f"/weekly reopen: reopened {week} "
            f"({len(restored)} block(s) restored) -> {result.get('path')}"
        )
    return f"/weekly reopen: unexpected outcome {outcome!r}."


def _ui_share_url() -> str:
    """Static fallback share URL (WEEKLY_UI_URL). Tunnel URL is preferred when available."""
    return (os.environ.get("WEEKLY_UI_URL") or _DEFAULT_UI_URL).rstrip("/")


def _ui_probe_url() -> str:
    """Always probe the process on this host (not the public share URL)."""
    return _LOCAL_UI_URL


def _ui_probe(url: str) -> bool:
    """Return True if something is already listening at ``url``."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=_UI_PROBE_TIMEOUT) as resp:
            return 200 <= getattr(resp, "status", 200) < 600
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _ui_dir() -> Path:
    return Path(__file__).resolve().parent / "ui"


def _tunnel_enabled() -> bool:
    """Auto Cloudflare quick tunnel on by default; set WEEKLY_UI_TUNNEL=0 to disable."""
    raw = (os.environ.get("WEEKLY_UI_TUNNEL") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _tunnel_log_path() -> Path:
    home = (os.environ.get("HERMES_HOME") or "").strip()
    if home:
        path = Path(home) / "cache" / "weekly-ui-tunnel.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return Path("/tmp/weekly-ui-cloudflared.log")
        return path
    return Path("/tmp/weekly-ui-cloudflared.log")


def _tunnel_pid_path() -> Path:
    """Runtime record for the UI-owned cloudflared PID (not a global pgrep match)."""
    home = (os.environ.get("HERMES_HOME") or "").strip()
    if home:
        path = Path(home) / "cache" / "weekly-ui-tunnel.pid"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return Path("/tmp/weekly-ui-cloudflared.pid")
        return path
    return Path("/tmp/weekly-ui-cloudflared.pid")


def _read_tunnel_pid() -> int | None:
    path = _tunnel_pid_path()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def _write_tunnel_pid(pid: int) -> None:
    path = _tunnel_pid_path()
    path.write_text(f"{int(pid)}\n", encoding="utf-8")


def _clear_tunnel_pid() -> None:
    path = _tunnel_pid_path()
    try:
        path.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _tunnel_process_running() -> bool:
    """True only when the UI-tracked cloudflared PID is still alive."""
    pid = _read_tunnel_pid()
    if pid is None:
        return False
    if _pid_alive(pid):
        return True
    _clear_tunnel_pid()
    return False


def _stop_tracked_tunnel() -> bool:
    """Terminate only the UI-owned tunnel PID and clear its record."""
    pid = _read_tunnel_pid()
    if pid is None:
        return False
    stopped = False
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            stopped = True
        except OSError:
            stopped = False
    _clear_tunnel_pid()
    return stopped


def _read_tunnel_url(log_path: Path | None = None) -> str | None:
    path = log_path or _tunnel_log_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = _TUNNEL_URL_RE.findall(text)
    return matches[-1].rstrip("/") if matches else None


def _ensure_cloudflare_tunnel() -> tuple[str | None, str | None]:
    """Start (or reuse) a Cloudflare quick tunnel to the local UI.

    Returns ``(url, error)``. ``(None, None)`` means tunneling is disabled.
    Reuses only the UI-tracked PID; never adopts an unrelated cloudflared.
    """
    if not _tunnel_enabled():
        return None, None

    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        return None, "cloudflared not installed"

    log_path = _tunnel_log_path()
    if _tunnel_process_running():
        existing = _read_tunnel_url(log_path)
        if existing:
            return existing, None
        # Tracked process alive but no URL yet — wait briefly rather than spawn another.
        deadline = time.monotonic() + _TUNNEL_START_WAIT_SECONDS
        while time.monotonic() < deadline:
            url = _read_tunnel_url(log_path)
            if url:
                return url, None
            time.sleep(_UI_POLL_INTERVAL)
        return None, (
            f"tracked cloudflared is running but no trycloudflare.com URL in "
            f"{log_path}"
        )

    # Stale or missing ownership — clear before spawning a new owned tunnel.
    _clear_tunnel_pid()

    try:
        log_path.write_text("", encoding="utf-8")
    except OSError as exc:
        return None, f"cannot write tunnel log {log_path}: {exc}"

    try:
        log_fh = log_path.open("a", encoding="utf-8")
    except OSError as exc:
        return None, f"cannot open tunnel log {log_path}: {exc}"

    try:
        proc = subprocess.Popen(
            [
                cloudflared,
                "tunnel",
                "--url",
                "http://127.0.0.1:3000",
                "--no-autoupdate",
            ],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log_fh.close()
        return None, f"failed to spawn cloudflared: {exc}"
    finally:
        try:
            log_fh.close()
        except OSError:
            pass

    try:
        _write_tunnel_pid(proc.pid)
    except OSError as exc:
        return None, f"cannot write tunnel pid {_tunnel_pid_path()}: {exc}"

    deadline = time.monotonic() + _TUNNEL_START_WAIT_SECONDS
    while time.monotonic() < deadline:
        url = _read_tunnel_url(log_path)
        if url:
            return url, None
        time.sleep(_UI_POLL_INTERVAL)

    return None, (
        f"cloudflared did not print a trycloudflare.com URL within "
        f"{_TUNNEL_START_WAIT_SECONDS:.0f}s (log: {log_path})"
    )


def _format_ui_ready(share_url: str, *, note: str | None = None) -> str:
    local = _LOCAL_UI_URL
    share = share_url.rstrip("/")
    if share == local:
        body = f"/weekly ui: open {local}"
    else:
        body = (
            f"/weekly ui: ready\n"
            f"• phone / WeChat: {share}\n"
            f"• this machine: {local}"
        )
    if note:
        return f"{body}\n({note})"
    return body


def _ensure_ui_server() -> tuple[str, str | None]:
    """Bring loopback :3000 up before Cloudflare, or /weekly ui never starts trycloudflare.

    Cloud copies of this plugin omit gitignored ``node_modules``, so ``tsx`` is missing and
    ``npm run dev`` dies immediately. Tunnel start is gated on this probe succeeding.
    """
    share_url = _ui_share_url()
    probe_url = _ui_probe_url()
    if _ui_probe(probe_url):
        return share_url, None

    ui_dir = _ui_dir()
    if not ui_dir.is_dir():
        return share_url, f"UI directory missing: {ui_dir}"

    env = os.environ.copy()
    if "HERMES_HOME" not in env:
        # Prefer sibling hermes-home when unset (dev / test).
        candidate = Path(__file__).resolve().parents[3]
        if (candidate / "memories").exists() or (candidate / "config.yaml").exists():
            env["HERMES_HOME"] = str(candidate)

    log_path = _tunnel_log_path().with_name("weekly-ui-dev.log")
    if not (ui_dir / "node_modules").is_dir():
        try:
            install = subprocess.run(
                ["npm", "install"],
                cwd=str(ui_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=_UI_INSTALL_WAIT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return share_url, f"npm install failed in {ui_dir}: {exc}"
        try:
            log_path.write_text(
                (install.stdout or "") + (install.stderr or ""),
                encoding="utf-8",
            )
        except OSError:
            pass
        if install.returncode != 0:
            tail = ((install.stderr or install.stdout or "")[-800:]).strip()
            return share_url, (
                f"npm install failed in {ui_dir} (exit {install.returncode})"
                + (f": {tail}" if tail else "")
            )

    try:
        log_fh = log_path.open("a", encoding="utf-8")
    except OSError:
        log_fh = subprocess.DEVNULL
    try:
        subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(ui_dir),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        if log_fh is not subprocess.DEVNULL:
            try:
                log_fh.close()
            except OSError:
                pass
        return share_url, f"failed to spawn `npm run dev` in {ui_dir}: {exc}"
    if log_fh is not subprocess.DEVNULL:
        try:
            log_fh.close()
        except OSError:
            pass

    deadline = time.monotonic() + _UI_START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _ui_probe(probe_url):
            return share_url, None
        time.sleep(_UI_POLL_INTERVAL)

    tail = ""
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:].strip()
    except OSError:
        pass
    detail = f" (log: {log_path}" + (f"; {tail}" if tail else "") + ")"
    return (
        share_url,
        f"UI did not become ready at {probe_url} within {_UI_START_WAIT_SECONDS:.0f}s "
        f"(started `npm run dev` under {ui_dir}){detail}",
    )


def _ui() -> str:
    fallback_url, err = _ensure_ui_server()
    if err:
        return f"/weekly ui: failed — {err}"

    tunnel_url, tunnel_err = _ensure_cloudflare_tunnel()
    if tunnel_url:
        return _format_ui_ready(tunnel_url)

    note = None
    if tunnel_err:
        note = f"tunnel unavailable: {tunnel_err}"
    return _format_ui_ready(fallback_url, note=note)


def _help() -> str:
    return (
        "/weekly commands:\n"
        "  /weekly ui               start UI + Cloudflare tunnel; share phone link\n"
        "  /weekly update [week]    refresh mid-week draft (default: current ISO week)\n"
        "  /weekly close [week]     close week anytime (default: current ISO week)\n"
        "  /weekly reopen [week]    reopen a closed week (default: current ISO week)"
    )
