"""Raise Hermes' external prefetch join without editing hermes-agent.

Hermes copies ``_EXTERNAL_PREFETCH_TIMEOUT_S`` (8s) onto each MemoryManager.
A core upgrade restores 8s. Re-apply from this plugin on every register/init
so cloud chat can wait for MyMemory bands + Channel 4.
"""

from __future__ import annotations

import gc
import os
from typing import Any

DEFAULT_PREFETCH_TIMEOUT_S = 20.0
_ENV = "MYMEMORY_PREFETCH_TIMEOUT_S"


def _wanted_seconds() -> float:
    """Honor MYMEMORY_PREFETCH_TIMEOUT_S so cloud can raise 20s without another code push."""
    raw = (os.environ.get(_ENV) or "").strip()
    if raw:
        try:
            val = float(raw)
        except ValueError:
            val = DEFAULT_PREFETCH_TIMEOUT_S
        if val > 0:
            return val
    return DEFAULT_PREFETCH_TIMEOUT_S


def apply_external_prefetch_timeout(seconds: float | None = None) -> float:
    """Patch Hermes MemoryManager join budget; fail-open if agent is not importable."""
    wanted = float(seconds) if seconds is not None else _wanted_seconds()
    if wanted <= 0:
        wanted = DEFAULT_PREFETCH_TIMEOUT_S
    try:
        from agent import memory_manager as mm
    except Exception:
        return 0.0
    try:
        mm._EXTERNAL_PREFETCH_TIMEOUT_S = wanted
        cls = getattr(mm, "MemoryManager", None)
        if cls is None:
            return wanted
        _wrap_init(cls, mm, wanted)
        for obj in gc.get_objects():
            if isinstance(obj, cls):
                obj._external_prefetch_timeout = wanted
    except Exception:
        return 0.0
    return wanted


def _wrap_init(cls: Any, mm: Any, wanted: float) -> None:
    """Force new managers to 20s even when Hermes still constructs with no kwargs."""
    current = cls.__init__
    if getattr(current, "_mymemory_prefetch_timeout", False):
        current._mymemory_seconds = wanted  # type: ignore[attr-defined]
        return
    orig = current

    def wrapped(self, *args, **kwargs):
        secs = float(getattr(wrapped, "_mymemory_seconds", wanted))
        if "external_prefetch_timeout" not in kwargs:
            kwargs["external_prefetch_timeout"] = secs
        return orig(self, *args, **kwargs)

    wrapped._mymemory_prefetch_timeout = True  # type: ignore[attr-defined]
    wrapped._mymemory_seconds = wanted  # type: ignore[attr-defined]
    cls.__init__ = wrapped
    del mm
