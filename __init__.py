"""MyMemory: slash on PluginManager, recall still only via memory.provider.

kind must be standalone (and listed in plugins.enabled) so gateway
discover_plugins() actually imports this module. exclusive skip is why
/digest never showed after restart — slash lookup runs before any agent
initialize.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("mymemory.runtime")

# digest.py imports yaml at load. A Hermes update's new environment does not
# ship these, so the plugin dies before register() can run.
_RUNTIME_MODULES = (
    "yaml",
    "onnxruntime",
    "tokenizers",
    "numpy",
    "sentence_transformers",
)


def ensure_runtime_deps() -> None:
    """A Hermes update's new environment omits PyYAML, so importing digest crashes the plugin.

    Install requirements.txt into this interpreter before provider.py loads digest.
    Skip under pytest so collection does not download torch.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get(
        "MYMEMORY_SKIP_RUNTIME_DEPS"
    ) == "1":
        return
    missing: list[str] = []
    for name in _RUNTIME_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if not missing:
        return
    req = Path(__file__).resolve().parent / "requirements.txt"
    if not req.is_file():
        logger.error("MyMemory requirements.txt missing at %s", req)
        return
    logger.info(
        "installing MyMemory requirements into %s; missing %s",
        sys.executable,
        ", ".join(missing),
    )
    if importlib.util.find_spec("pip") is not None:
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(req)]
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
            "--index-strategy",
            "unsafe-best-match",
            "-r",
            str(req),
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


ensure_runtime_deps()

from .prefetch_timeout import apply_external_prefetch_timeout
from .provider import MyMemoryProvider, _slash_off_loop


def register(ctx) -> None:
    """Attach /digest, /weekly, and /monthly on a real PluginContext; keep the provider on the collector.

    PluginContext has no register_memory_provider (mempalace crash). The
    memory loader's collector has no register_command. hasattr so one
    register() serves both loaders without dual recall hooks.
    Re-apply prefetch join budget here so a Hermes upgrade cannot restore 8s.
    Handlers are coroutines so a long slash run yields the gateway loop.
    Restore a missing worker block here: Hermes can recreate config.yaml
    without these lanes, and this load is the first MyMemory code that runs after.
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
