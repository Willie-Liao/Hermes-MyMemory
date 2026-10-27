"""Start a slash update on a daemon thread and return before the LLM call.

The gateway awaits the slash coroutine. If that wait includes the model run,
the next chat message sits behind the update.
"""

from __future__ import annotations

import contextvars
import logging
import os
import threading
from collections.abc import Callable

_LOG = logging.getLogger("mymemory.slash_jobs")
_LOCK = threading.Lock()
_THREADS: dict[str, threading.Thread] = {}


def start_slash_job(name: str, work: Callable[[], str]) -> bool:
    """Return False when ``name`` is still running so a second slash does not double-call the model.

    The thread copies the slash turn's contextvars. A raw thread would lose the
    gateway session id as soon as the slash reply returns.
    """
    ctx = contextvars.copy_context()

    def _target() -> None:
        try:
            text = ctx.run(work)
            _LOG.info("%s finished: %s", name, text)
        except Exception:
            _LOG.exception("%s failed", name)
        finally:
            with _LOCK:
                if _THREADS.get(name) is threading.current_thread():
                    _THREADS.pop(name, None)

    with _LOCK:
        existing = _THREADS.get(name)
        if existing is not None and existing.is_alive():
            return False
        thread = threading.Thread(target=_target, name=f"mymemory-{name}", daemon=True)
        _THREADS[name] = thread
        thread.start()
    return True


def run_or_background(
    name: str,
    work: Callable[[], str],
    *,
    sync: bool | None = None,
) -> str:
    """Return the update text under tests; otherwise free the chat turn before the model call.

    The gateway awaits the slash coroutine. Waiting on the model keeps the next
    message queued behind digest, weekly, or monthly update.
    """
    inline = bool(os.environ.get("PYTEST_CURRENT_TEST")) if sync is None else sync
    if inline:
        return work()
    if not start_slash_job(name, work):
        return f"{name} is already running."
    return f"{name} started in the background. You can keep chatting."


def join_slash_job(name: str, timeout: float = 5.0) -> None:
    """Wait for a test's background slash so the assertion sees the worker finish."""
    with _LOCK:
        thread = _THREADS.get(name)
    if thread is not None:
        thread.join(timeout)
