"""MyMemory exclusive provider — one folder so digest/weekly/retention cannot dual-run as PluginManager plugins."""

from __future__ import annotations

from .provider import MyMemoryProvider


def register(ctx) -> None:
    """Register only the memory provider so exclusive load skips general hooks."""
    ctx.register_memory_provider(MyMemoryProvider())
