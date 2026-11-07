"""MyMemory: slash on PluginManager, recall still only via memory.provider.

kind must be standalone (and listed in plugins.enabled) so gateway
discover_plugins() actually imports this module. exclusive skip is why
/digest never showed after restart — slash lookup runs before any agent
initialize.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("mymemory.runtime")

# The file is the list. distribution() reads metadata only: importing
# tokenizers here would load the GTE stack into the gateway before register().
_REQUIREMENTS_FILE = Path(__file__).resolve().parent / "requirements.txt"
_LOAD_STARTED = time.perf_counter()
# Tests join this so a background install can be asserted without blocking load.
_DEPS_INSTALL_THREAD: threading.Thread | None = None


def ensure_runtime_deps(*, background: bool = True) -> None:
    """A missing wheel must not spend Hermes's 10s plugin-load deadline.

    distribution() only reads metadata. The pip install and gateway restart
    run on a daemon thread so register() still happens this process; the next
    process imports the new packages. Tests pass background=False. Skip under
    pytest so collection does not download wheels.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get(
        "MYMEMORY_SKIP_RUNTIME_DEPS"
    ) == "1":
        return
    req = _REQUIREMENTS_FILE
    if not req.is_file():
        logger.error("MyMemory requirements.txt missing at %s", req)
        return
    try:
        text = req.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("MyMemory requirements.txt unreadable at %s: %s", req, exc)
        return
    started = time.perf_counter()
    index_args: list[str] = []
    wanted: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            index_args.extend(shlex.split(line))
            continue
        spec, _, marker = line.partition(";")
        spec = spec.strip()
        marker = marker.strip()
        if not spec:
            continue
        if marker:
            parts = marker.split()
            if (
                len(parts) == 3
                and parts[0] == "sys_platform"
                and parts[1] in {"==", "!="}
            ):
                quoted = parts[2].strip("\"'")
                matches = sys.platform == quoted
                applies = matches if parts[1] == "==" else not matches
                if not applies:
                    continue
            else:
                logger.warning(
                    "MyMemory requirements marker kept (unrecognized): %s",
                    marker,
                )
        name = spec
        for index, ch in enumerate(spec):
            if ch in "=<>!~[":
                name = spec[:index].strip()
                break
        if name:
            wanted.append((name, spec))
    missing_names: list[str] = []
    missing_specs: list[str] = []
    for name, spec in wanted:
        try:
            importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            missing_names.append(name)
            missing_specs.append(spec)
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
    logger.info(
        "%d specs in %dms, missing=%d",
        len(wanted),
        elapsed_ms,
        len(missing_names),
    )
    if not missing_names:
        return
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    notice_path = home / "logs" / "mymemory-runtime-deps-notice.json"
    try:
        previous = json.loads(notice_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = None
    if isinstance(previous, dict):
        requested = previous.get("restart_requested_at")
        if isinstance(requested, (int, float)) and not isinstance(requested, bool):
            age = time.time() - float(requested)
            if age < 15 * 60:
                logger.error(
                    "MyMemory still missing %s after a restart requested %.0fs ago; not installing again",
                    ", ".join(missing_names),
                    age,
                )
                return

    def _install_then_restart() -> None:
        """Install off the load thread, then restart so the next process can register.

        A litellm wheel can take longer than Hermes's 10s plugin cap. Running
        here keeps that cost off import.
        """
        logger.info(
            "MyMemory missing: %s. Installing those requirements, then restarting the gateway so the plugin can register.",
            ", ".join(missing_names),
        )
        if importlib.util.find_spec("pip") is not None:
            cmd = [sys.executable, "-m", "pip", "install", *index_args, *missing_specs]
        else:
            uv = shutil.which("uv")
            if uv is None:
                home_uv = Path.home() / ".local" / "bin" / "uv"
                if home_uv.is_file():
                    uv = str(home_uv)
            if not uv:
                logger.error(
                    "MyMemory requirements not installed: %s has no pip and uv is missing",
                    sys.executable,
                )
                return
            cmd = [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                *index_args,
                *missing_specs,
            ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("MyMemory requirements install failed: %s", exc)
            return
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or "").strip()[-600:]
            logger.error("MyMemory requirements install failed: %s", tail)
            return
        importlib.invalidate_caches()
        still: list[str] = []
        for name in missing_names:
            try:
                importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                still.append(name)
        if still:
            logger.error(
                "MyMemory requirements install finished but still missing %s",
                ", ".join(still),
            )
            return
        missing = missing_names
        notice = {
            "missing": missing,
            "sent": False,
            "restart_requested_at": time.time(),
        }
        try:
            notice_path.parent.mkdir(parents=True, exist_ok=True)
            notice_path.write_text(json.dumps(notice), encoding="utf-8")
        except OSError as exc:
            logger.error("MyMemory could not record the runtime-deps notice: %s", exc)
        try:
            from .weekly.weekly_clock import send_weekly_brief_weixin

            result = send_weekly_brief_weixin(
                "MyMemory was missing: "
                + ", ".join(missing)
                + ". Installed them and restarting the gateway so memory prefetch can register."
            )
        except Exception as exc:
            logger.warning("MyMemory could not send the missing-module notice: %s", exc)
            result = None
        if isinstance(result, dict) and result.get("outcome") == "sent":
            notice["sent"] = True
            try:
                notice_path.write_text(json.dumps(notice), encoding="utf-8")
            except OSError as exc:
                logger.error("MyMemory could not record the sent runtime-deps notice: %s", exc)
        if (
            os.path.isdir("/run/systemd/system")
            and shutil.which("systemctl")
            and shutil.which("systemd-run")
        ):
            restart_cmd = [
                "systemd-run",
                "--collect",
                "--unit",
                f"mymemory-runtime-deps-restart-{os.getpid()}",
                "systemctl",
                "restart",
                "hermes-gateway",
            ]
        else:
            hermes = shutil.which("hermes")
            if not hermes:
                home_hermes = Path.home() / ".local" / "bin" / "hermes"
                if home_hermes.is_file():
                    hermes = str(home_hermes)
            if not hermes:
                logger.error(
                    "MyMemory installed runtime deps but could not restart the gateway: hermes CLI not found"
                )
                return
            restart_cmd = [hermes, "gateway", "restart"]
        try:
            subprocess.Popen(
                restart_cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.error("MyMemory gateway restart failed to start: %s", exc)

    if not background:
        _install_then_restart()
        return
    global _DEPS_INSTALL_THREAD
    _DEPS_INSTALL_THREAD = threading.Thread(
        target=_install_then_restart,
        name="mymemory-runtime-deps",
        daemon=True,
    )
    _DEPS_INSTALL_THREAD.start()


ensure_runtime_deps()

from .prefetch_timeout import apply_external_prefetch_timeout
from .provider import MyMemoryProvider, _slash_off_loop


def register(ctx) -> None:
    """Attach /digest, /weekly, and /monthly on a real PluginContext; keep the provider on the collector.

    The pre-restart WeChat line can miss the adapter, so an unsent runtime-deps
    notice is delivered only once this load actually registers the provider.
    PluginContext has no register_memory_provider (mempalace crash). The
    memory loader's collector has no register_command. hasattr so one
    register() serves both loaders without dual recall hooks.
    Re-apply prefetch join budget here so a Hermes upgrade cannot restore 8s.
    Handlers are coroutines so a long slash run yields the gateway loop.
    Restore a missing worker block here: Hermes can recreate config.yaml
    without these lanes, and this load is the first MyMemory code that runs after.
    Log plugin-load seconds here because Hermes records a load only when it
    exceeds plugins.load_timeout_seconds.
    """
    apply_external_prefetch_timeout()
    try:
        from .worker_config import ensure_worker_block

        ensure_worker_block()
    except Exception:
        pass
    if hasattr(ctx, "register_command"):
        from .digest import slash as digest_slash
        from .weekly import slash as weekly_slash

        ctx.register_command(
            "digest",
            _slash_off_loop(digest_slash.handle_digest),
            description=(
                "Force a digest run, manage bookmark, or estimate/run history backfill"
            ),
            args_hint="[status|bookmark|history|help]",
        )
        ctx.register_command(
            "weekly",
            _slash_off_loop(weekly_slash.handle_weekly),
            description="Weekly memory: ui / update / close / reopen",
            args_hint="[ui|update [week]|close [week]|reopen [week]|help]",
        )
        from .monthly.monthly_actions import handle_monthly

        ctx.register_command(
            "monthly",
            _slash_off_loop(handle_monthly),
            description="Monthly guidance: update / show",
            args_hint="[update [YYYY-MM]|show [YYYY-MM]|help]",
        )
    if hasattr(ctx, "register_memory_provider"):
        ctx.register_memory_provider(MyMemoryProvider())
        home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
        notice_path = home / "logs" / "mymemory-runtime-deps-notice.json"
        try:
            raw = json.loads(notice_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict):
            if raw.get("sent"):
                try:
                    notice_path.unlink()
                except OSError:
                    pass
            else:
                names = raw.get("missing")
                if not isinstance(names, list):
                    names = []
                text = (
                    "MyMemory installed the missing modules ("
                    + ", ".join(str(name) for name in names)
                    + ") and restarted. Memory prefetch is registered."
                )

                def _deliver_runtime_deps_notice() -> None:
                    """Retry only while the adapter is not up; a real send error will not succeed if repeated.

                    Leave the notice in place when delivery never succeeds so the next boot can try again.
                    Stop on the first error so a dead WeChat session is not hammered for two minutes.
                    """
                    from .weekly.weekly_clock import send_weekly_brief_weixin

                    waiting = {"no_runner", "no_adapter", "no_loop"}
                    deadline = time.time() + 120
                    delivered = False
                    while time.time() < deadline:
                        try:
                            result = send_weekly_brief_weixin(text)
                        except Exception as exc:
                            logger.warning(
                                "MyMemory runtime-deps notice send failed: %s", exc
                            )
                            return
                        if not isinstance(result, dict):
                            result = {}
                        if result.get("outcome") == "sent" or result.get("reason") == "no_chat_id":
                            delivered = True
                            break
                        if result.get("outcome") == "error" or result.get("reason") not in waiting:
                            return
                        time.sleep(1)
                    if not delivered:
                        return
                    try:
                        notice_path.unlink()
                    except OSError:
                        pass

                threading.Thread(
                    target=_deliver_runtime_deps_notice,
                    name="mymemory-runtime-deps-notice",
                    daemon=True,
                ).start()
    logger.info("MyMemory plugin load %.3fs", time.perf_counter() - _LOAD_STARTED)
