"""Hermes memory provider that owns digest extract, weekly generate, and retention sweep.

Without this wrap, PluginManager would load three sibling plugins and inject
recall twice (pre_llm_call plus prefetch). Exclusive MyMemory is the only
inject path Hermes wraps as ``<memory-context>``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_mymemory = Path(__file__).resolve().parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))
_plugins_root = _mymemory.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))
_hermes_agent = _plugins_root.parent / "hermes-agent"
if _hermes_agent.is_dir() and str(_hermes_agent) not in sys.path:
    sys.path.insert(0, str(_hermes_agent))

from agent.memory_provider import MemoryProvider

from .digest import digest, slash as digest_slash
from .retention import retention
from .weekly import slash as weekly_slash
from .weekly import weekly
from .weekly import weekly_tools

_SKIP_WRITE_CONTEXTS = frozenset({"cron", "subagent", "flush"})
_BOOTSTRAPPED = False

_SYSTEM_PROMPT_BLOCK = (
    "Digest recall bands (recent wrap-ups, entity index, week ladder) inject "
    "once per civil day. Fetch mem- ids from that index with recall_memory / "
    "expand_memory; read daily YAML for bodies. MEMORY.md and USER.md are "
    "already in the system prompt — do not expect those hot files in the bands."
)


class MyMemoryProvider(MemoryProvider):
    """Selectable ``memory.provider: MyMemory`` backend for this Hermes home."""

    def __init__(self) -> None:
        self._hermes_home = ""
        self._session_id = ""
        self._platform = ""
        self._agent_context = "primary"
        self._prefetch_seen: set[str] = set()

    @property
    def name(self) -> str:
        return "MyMemory"

    def is_available(self) -> bool:
        """Always on — this pack is local files, not a remote memory API."""
        return True

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return []

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """No native config file — worker LLM lives under plugins.entries.MyMemory."""
        return None

    def initialize(self, session_id: str, **kwargs) -> None:
        """Start digest clock plus weekly/retention catch-up once per process.

        Exclusive plugins are not imported at PluginManager boot; the first
        primary-agent initialize is what arms the civil clock. Slash commands
        still register here for cron/subagent/flush so chat handlers exist
        even when those contexts skip the clock.
        """
        self._session_id = str(session_id or "")
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._platform = str(kwargs.get("platform") or "")
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        _register_slash_commands()
        if self._agent_context in _SKIP_WRITE_CONTEXTS:
            return
        _bootstrap_background()

    def system_prompt_block(self) -> str:
        """Cache-stable instructions only — daily file names would bust the prefix cache."""
        return _SYSTEM_PROMPT_BLOCK

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Compose Bands A–C plus weekly inject when those hooks would fire.

        Hermes wraps the return value; pre-wrapping ``<memory-context>`` would nest fences.
        """
        sid = str(session_id or self._session_id or "")
        first = sid not in self._prefetch_seen
        digest_hit = digest.on_pre_llm_call(
            user_message=query,
            is_first_turn=first,
            session_id=sid,
            platform=self._platform,
        )
        weekly_hit = weekly.on_pre_llm_call(
            user_message=query,
            is_first_turn=first,
            session_id=sid,
            platform=self._platform,
        )
        if sid:
            self._prefetch_seen.add(sid)
        parts: list[str] = []
        for hit in (digest_hit, weekly_hit):
            if isinstance(hit, dict):
                text = str(hit.get("context") or "").strip()
                if text:
                    parts.append(text)
        joined = "\n\n".join(parts)
        if "<memory-context>" in joined:
            joined = joined.replace("<memory-context>", "").replace("</memory-context>", "")
        return joined.strip()

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Queue digest extract after a primary chat turn — must not block the API loop.

        Passes the user text so a just-recalled dated card can be rejected after
        the turn without waiting for the 12-message Phase-1 batch.
        """
        del assistant_content, messages
        if self._agent_context in _SKIP_WRITE_CONTEXTS:
            return
        sid = str(session_id or self._session_id or "")
        digest.on_agent_end(
            {
                "session_id": sid,
                "session_key": sid,
                "platform": self._platform,
                "user_content": user_content,
            }
        )

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Session boundary leftover extract plus weekly/retention sweeps."""
        del messages
        if self._agent_context in _SKIP_WRITE_CONTEXTS:
            return
        ctx = {
            "session_id": self._session_id,
            "session_key": self._session_id,
            "platform": self._platform,
        }
        digest.on_session_boundary(ctx, reason="on_session_finalize")
        weekly.run_async("on_session_finalize")
        retention.run_async("on_session_finalize")

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Keep provider session id in sync and re-arm weekly/retention like the old hooks."""
        del parent_session_id, rewound, kwargs
        self._session_id = str(new_session_id or "")
        self._prefetch_seen.discard(self._session_id)
        if self._agent_context in _SKIP_WRITE_CONTEXTS:
            return
        reason = "on_session_reset" if reset else "on_session_start"
        weekly.run_async(reason)
        retention.run_async(reason)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """LLM tools for digest/weekly plus recall_memory / expand_memory."""
        from recall.tools import TOOL_SCHEMAS

        return [
            {
                "name": "mymemory_digest",
                "description": (
                    "Run a digest now, inspect bookmark/status, or estimate/run history backfill. "
                    "Pass the same tokens as /digest (empty, status, bookmark, history …)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "string",
                            "description": "Digest subcommand tokens, e.g. 'status' or 'bookmark show'.",
                        }
                    },
                },
            },
            {
                "name": "mymemory_weekly",
                "description": (
                    "Weekly memory: ui, update, close, reopen. "
                    "Pass the same tokens as the old /weekly command."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "string",
                            "description": "Weekly subcommand tokens, e.g. 'ui' or 'update 2026-W33'.",
                        }
                    },
                },
            },
            *TOOL_SCHEMAS,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Dispatch chat tools to slash handlers or the recall ladder.

        After recall, persist at most 8 ordered mem-ids on digest state so a
        later user correction can bind to the co-retrieved set without mutating
        card bodies in the recall path.
        """
        if tool_name in {"recall_memory", "expand_memory"}:
            from recall.tools import handle_tool, _MEM_ID_RE

            text = handle_tool(tool_name, args)
            sid = str(kwargs.get("session_id") or self._session_id or "")
            ordered: list[str] = []
            for match in _MEM_ID_RE.findall(text or ""):
                mem_id = match if isinstance(match, str) else match[0]
                if not str(mem_id).startswith("mem-"):
                    continue
                if mem_id in ordered:
                    continue
                ordered.append(mem_id)
                if len(ordered) >= digest.RETRIEVAL_ID_CAP:
                    break
            if sid and ordered:
                import time as time_mod

                with digest._digest_lock:
                    state = digest._load_state()
                    sessions = state.setdefault("sessions", {})
                    entry = dict(sessions.get(sid) or {})
                    entry["session_id"] = sid
                    entry["retrieval"] = {
                        "ids": ordered,
                        "query": str(
                            (args or {}).get("query")
                            or (args or {}).get("id_or_key")
                            or ""
                        ),
                        "recorded_at": time_mod.time(),
                        "consumed": False,
                    }
                    sessions[sid] = entry
                    digest._save_state(state)
            return text
        raw = str((args or {}).get("args") or "")
        if tool_name == "mymemory_digest":
            return json.dumps(
                {"ok": True, "text": digest_slash.handle_digest(raw)},
                ensure_ascii=False,
            )
        if tool_name == "mymemory_weekly":
            return json.dumps(
                {"ok": True, "text": weekly_slash.handle_weekly(raw)},
                ensure_ascii=False,
            )
        raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

    def shutdown(self) -> None:
        """Stop the digest clock only under pytest so production daemons keep ticking."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            digest.stop_digest_clock_thread()


def _register_slash_commands() -> None:
    """Put /digest and /weekly on PluginManager; exclusive load never calls register_command.

    The memory loader's fake context has no register_command. Without this,
    those names never enter _plugin_commands and chat treats them as unknown.
    Fail-open so a missing hermes_cli cannot block provider init. Never
    rediscover with force=True (that wipes the command table).
    """
    try:
        from hermes_cli.plugins import (
            PluginContext,
            PluginManifest,
            get_plugin_manager,
        )
    except Exception:
        return
    try:
        mgr = get_plugin_manager()
        mgr.discover_and_load()
        loaded = mgr._plugins.get("MyMemory")
        manifest = (
            loaded.manifest if loaded is not None else PluginManifest(name="MyMemory")
        )
        ctx = PluginContext(manifest, mgr)
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
    except Exception:
        return


def _bootstrap_background() -> None:
    """Idempotent clock + weekly/retention plugin_load equivalent."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    try:
        from memory_staging import migrate_all_weekly_files

        home = digest.get_hermes_home()
        migrate_all_weekly_files(home)
    except Exception:
        pass
    digest.start_digest_clock_thread()
    try:
        weekly_tools.ensure_weekly_tools_registered()
    except Exception:
        pass
    try:
        from .digest import digest_tools

        digest_tools.ensure_digest_tools_registered()
    except Exception:
        pass
    weekly.run_async("plugin_load")
    retention.run_async("plugin_load")


def reset_bootstrap_for_tests() -> None:
    """Let provider tests assert initialize starts clock/weekly/retention once."""
    global _BOOTSTRAPPED
    _BOOTSTRAPPED = False
