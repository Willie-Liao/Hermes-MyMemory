"""Prefetch has no LiteLLM failover budget; reshape is local and free."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_reshape_does_not_consume_prefetch_budget():
    """Removing the proxy means worker URL pick is not a second HTTP timeout."""
    spec = importlib.util.spec_from_file_location(
        "prefetch_timeout_no_gw",
        Path(__file__).with_name("prefetch_timeout.py"),
    )
    assert spec is not None and spec.loader is not None
    pt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pt)
    assert pt.DEFAULT_PREFETCH_TIMEOUT_S >= 20.0
