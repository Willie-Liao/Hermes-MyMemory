"""Hermes 8s prefetch join must be raised from MyMemory, not by editing hermes-agent."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))


def test_apply_sets_module_constant_wraps_init_and_existing_instance(monkeypatch):
    mm = types.ModuleType("agent.memory_manager")
    mm._EXTERNAL_PREFETCH_TIMEOUT_S = 8.0

    class MemoryManager:
        def __init__(self, *, external_prefetch_timeout=None):
            self._external_prefetch_timeout = (
                mm._EXTERNAL_PREFETCH_TIMEOUT_S
                if external_prefetch_timeout is None
                else float(external_prefetch_timeout)
            )

    mm.MemoryManager = MemoryManager
    agent_pkg = types.ModuleType("agent")
    monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "agent.memory_manager", mm)

    existing = MemoryManager()
    assert existing._external_prefetch_timeout == 8.0

    from MyMemory import prefetch_timeout as pt

    importlib.reload(pt)
    assert pt.apply_external_prefetch_timeout(20.0) == 20.0
    assert mm._EXTERNAL_PREFETCH_TIMEOUT_S == 20.0
    assert existing._external_prefetch_timeout == 20.0
    later = MemoryManager()
    assert later._external_prefetch_timeout == 20.0


def test_register_calls_apply():
    text = (_HERE / "__init__.py").read_text(encoding="utf-8")
    assert "apply_external_prefetch_timeout" in text
