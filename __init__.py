"""MyMemory: slash on PluginManager, recall still only via memory.provider.

kind must be standalone (and listed in plugins.enabled) so gateway
discover_plugins() actually imports this module. exclusive skip is why
/digest never showed after restart — slash lookup runs before any agent
initialize.
"""

from __future__ import annotations

from .provider import MyMemoryProvider


def register(ctx) -> None:
    """Attach /digest, /weekly, and /monthly on a real PluginContext; keep the provider on the collector.

    PluginContext has no register_memory_provider (mempalace crash). The
    memory loader's collector has no register_command. hasattr so one
    register() serves both loaders without dual recall hooks.
    """
    if hasattr(ctx, "register_command"):
        from .digest import slash as digest_slash
        from .weekly import slash as weekly_slash

        ctx.register_command(
            "digest",
            digest_slash.handle_digest,
            description=(
                "Force a digest run, manage bookmark, or estimate/run history backfill"
            ),
            args_hint="[status|bookmark|history|help]",
        )
        ctx.register_command(
            "weekly",
            weekly_slash.handle_weekly,
            description="Weekly memory: ui / update / close / reopen",
            args_hint="[ui|update [week]|close [week]|reopen [week]|help]",
        )
        from .monthly.monthly_actions import handle_monthly

        ctx.register_command(
            "monthly",
            handle_monthly,
            description="Monthly guidance: update / show",
            args_hint="[update [YYYY-MM]|show [YYYY-MM]|help]",
        )
    if hasattr(ctx, "register_memory_provider"):
        ctx.register_memory_provider(MyMemoryProvider())
