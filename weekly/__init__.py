"""Weekly section of MyMemory — generate, Brief, and UI bridges. Not a Hermes plugin root."""

from __future__ import annotations

import importlib.util
from pathlib import Path

try:
    from . import slash, weekly
except ImportError:  # pragma: no cover - direct pytest collection path
    def _load(name: str):
        module_path = Path(__file__).with_name(f"{name}.py")
        spec = importlib.util.spec_from_file_location(f"memory_weekly_{name}", module_path)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    weekly = _load("weekly")
    slash = _load("slash")
