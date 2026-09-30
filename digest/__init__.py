"""Digest section of MyMemory — extract, clock, and recall index. Not a Hermes plugin root."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

try:
    from . import digest, slash
except ImportError:  # pragma: no cover - direct pytest collection path
    def _load(name: str):
        module_path = Path(__file__).with_name(f"{name}.py")
        mod_name = f"memory_digest_{name}"
        spec = importlib.util.spec_from_file_location(mod_name, module_path)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    digest = _load("digest")
    slash = _load("slash")
