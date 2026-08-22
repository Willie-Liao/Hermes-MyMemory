"""Shared helper-LLM reentry guard for memory plugins.

Nested ``AIAgent.run_conversation`` still fires ``pre_llm_call`` hooks.
Callers wrap helper LLM turns in ``worker_llm_scope()``; hooks bail via
``in_worker_llm()`` so urgency / relatedness / digest classifiers do not nest.

Forced tool-call workers (daily digest + weekly distill)
--------------------------------------------------------
``run_worker_llm_tools`` equips the nested ``AIAgent`` with **only** the
forced tool (plus ``skip_*`` companions from the same toolset), after
``skip_tool_search_assembly=True`` so Hermes progressive disclosure cannot
replace schemas with ``tool_search`` / ``tool_describe`` / ``tool_call``.

Also sets ``tool_choice`` to that function name. Main chat agents never call
this module — no effect on interactive tool catalogs.

Default ``run_worker_llm`` remains text-only (``enabled_toolsets=[]``).

UI Tighten / Rephrase / Merge use ``run_worker_llm_oneshot``: one
OpenAI-compatible ``chat.completions`` call, no nested ``AIAgent``.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ENV_WORKER_LLM_DEPTH = "HERMES_WORKER_LLM_DEPTH"
_worker_llm_depth = 0
_worker_llm_depth_lock = threading.Lock()


@contextmanager
def worker_llm_scope() -> Iterator[None]:
    """Mark this thread as inside a helper/worker LLM turn."""
    global _worker_llm_depth
    with _worker_llm_depth_lock:
        _worker_llm_depth += 1
        os.environ[_ENV_WORKER_LLM_DEPTH] = str(_worker_llm_depth)
    try:
        yield
    finally:
        with _worker_llm_depth_lock:
            _worker_llm_depth -= 1
            if _worker_llm_depth > 0:
                os.environ[_ENV_WORKER_LLM_DEPTH] = str(_worker_llm_depth)
            else:
                os.environ.pop(_ENV_WORKER_LLM_DEPTH, None)


def in_worker_llm() -> bool:
    """True when a helper LLM turn is active on this thread / process."""
    with _worker_llm_depth_lock:
        if _worker_llm_depth > 0:
            return True
    try:
        return int(os.environ.get(_ENV_WORKER_LLM_DEPTH, "0") or "0") > 0
    except ValueError:
        return False


def _hermes_home() -> Path:
    """Prefer HERMES_HOME env; else ~/.hermes."""
    val = (os.environ.get("HERMES_HOME") or "").strip()
    return Path(val).resolve() if val else (Path.home() / ".hermes").resolve()


def _ledger_path() -> Path:
    return _hermes_home() / "metrics" / "llm-usage.jsonl"


def _runtime_from_mapping(bag: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(bag, Mapping):
        return {}
    out: dict[str, str] = {}
    model = str(bag.get("model") or "").strip()
    provider = str(bag.get("provider") or "").strip()
    if model:
        out["model"] = model
    if provider:
        out["provider"] = provider
    return out


def _worker_llm_lane(plugin: str, purpose: str) -> str | None:
    """Map ledger purpose → config.yaml worker_llm.<lane>."""
    plug = str(plugin or "").strip()
    why = str(purpose or "").strip()
    if plug == "memory-digest":
        if why.startswith("digest-phase1"):
            return "phase1"
        if why.startswith("digest-wrapup"):
            return "wrapup"
        if why.startswith("digest-dedup"):
            return "phase2"
        return None
    if plug == "memory-weekly":
        return "weekly"
    if plug == "memory-monthly":
        return "monthly"
    return None


def _plugin_worker_runtime(plugin: str, purpose: str = "") -> dict[str, str]:
    """Optional model/provider from config.yaml plugins.entries.<plugin>.

    Overlay: entry.model/provider, then worker_llm.<lane> for phase1 / phase2 /
    wrapup / weekly when the purpose matches.
    """
    name = str(plugin or "").strip()
    if not name:
        return {}
    path = _hermes_home() / "config.yaml"
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    plugins = raw.get("plugins") if isinstance(raw, dict) else None
    if not isinstance(plugins, dict):
        return {}
    table = plugins.get("entries")
    if not isinstance(table, dict):
        return {}
    entry = table.get(name)
    if not isinstance(entry, dict):
        nested_key = {
            "memory-digest": "digest",
            "memory-weekly": "weekly",
            "memory-retention": "retention",
            "memory-monthly": "monthly",
        }.get(name)
        my_memory = table.get("MyMemory")
        if nested_key and isinstance(my_memory, dict):
            entry = my_memory.get(nested_key)
        if not isinstance(entry, dict):
            return {}
    out = _runtime_from_mapping(entry)
    lanes = entry.get("worker_llm")
    if isinstance(lanes, dict):
        out.update(_runtime_from_mapping(lanes.get("default")))
        lane = _worker_llm_lane(name, purpose)
        if lane:
            out.update(_runtime_from_mapping(lanes.get(lane)))
    return out


def record_worker_usage(record: dict[str, Any]) -> None:
    """Append one JSONL usage record. Fail-open — never raise to callers."""
    try:
        path = _ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # fail-open


_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _loads_json_object(raw: Any) -> dict[str, Any] | None:
    """Parse a JSON object from a dict, JSON string, fence, or trailing-comma blob.

    MiMo v2.5 often returns tool payloads as fenced / slightly-invalid JSON in
    assistant text instead of OpenAI ``tool_calls``. Weekly UI tighten needs a
    dict it can render.
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    seen: set[str] = set()
    for c in candidates:
        for attempt in (c, _TRAILING_COMMA_RE.sub(r"\1", c)):
            if attempt in seen:
                continue
            seen.add(attempt)
            try:
                val = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(val, Mapping):
                return dict(val)
            if isinstance(val, str):
                nested = _loads_json_object(val)
                if nested is not None:
                    return nested
    return None


def _pair_from_tool_call(
    tc: Mapping[str, Any],
    default_tool_name: str | None,
) -> tuple[str, dict[str, Any]] | None:
    fn = tc.get("function") if isinstance(tc.get("function"), Mapping) else {}
    name = str(
        (fn.get("name") if fn else None) or tc.get("name") or default_tool_name or ""
    ).strip()
    raw_args: Any = None
    for key in ("arguments", "parameters", "input"):
        if fn and key in fn:
            raw_args = fn.get(key)
            break
        if key in tc:
            raw_args = tc.get(key)
            break
    args = _loads_json_object(raw_args) or {}
    if not name:
        return None
    return (name, args)


def _pair_from_json_blob(
    blob: Any,
    default_tool_name: str | None,
) -> tuple[str, dict[str, Any]] | None:
    obj = _loads_json_object(blob)
    if not obj:
        return None
    nested_name = str(obj.get("name") or "").strip()
    nested_raw = obj.get("arguments")
    if nested_raw is None:
        nested_raw = obj.get("parameters")
    if nested_raw is None:
        nested_raw = obj.get("input")
    nested_args = _loads_json_object(nested_raw) if nested_raw is not None else None
    if nested_name and nested_args is not None:
        return (nested_name, nested_args)
    name = nested_name or str(default_tool_name or "").strip()
    if not name:
        return None
    return (name, obj)


def extract_tool_calls_from_result(
    result: Mapping[str, Any],
    default_tool_name: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(tool_name, args_dict)`` pairs from ``run_conversation`` messages.

    Accepts OpenAI nested ``function`` calls, flat ``name``/``arguments``,
    trailing-comma JSON, and JSON dumped into assistant content / final_response
    (MiMo v2.5).
    """
    found: list[tuple[str, dict[str, Any]]] = []
    default = str(default_tool_name or "").strip() or None
    messages = result.get("messages") or []
    if isinstance(messages, Sequence):
        for msg in messages:
            if not isinstance(msg, Mapping):
                continue
            if str(msg.get("role", "")).strip() != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, Mapping):
                    continue
                pair = _pair_from_tool_call(tc, default)
                if pair:
                    found.append(pair)
            if not any(args for _name, args in found):
                pair = _pair_from_json_blob(msg.get("content"), default)
                if pair:
                    found.append(pair)
    if not any(args for _name, args in found):
        pair = _pair_from_json_blob(result.get("final_response"), default)
        if pair:
            found.append(pair)
    return found


def _normalize_callback_tool_args(args: Any) -> dict[str, Any]:
    """Accept Mapping or JSON-string tool args (MiMo often sends a string)."""
    if isinstance(args, Mapping):
        bag = dict(args)
    else:
        bag = _loads_json_object(args) or {}
    if not isinstance(bag, Mapping):
        return {}
    bag = dict(bag)
    known = (
        "beginning",
        "course",
        "outcome",
        "obstacle",
        "solution",
        "kind",
        "subject",
        "ruling",
        "content",
        "text",
        "events",
        "conflicts",
        "hypotheses",
        "operations",
    )
    if any(key in bag for key in known):
        return bag
    for wrap in ("arguments", "parameters", "input"):
        inner = _loads_json_object(bag.get(wrap))
        if inner:
            return inner
    return bag


def _tool_args_have_values(args: Mapping[str, Any] | None) -> bool:
    """True when args carry a real value (empty strings do not count)."""
    if not isinstance(args, Mapping) or not args:
        return False
    for value in args.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, Mapping) and _tool_args_have_values(value):
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, (int, float, bool)):
            return True
    return False


def _tool_def_name(tool_def: Mapping[str, Any]) -> str:
    fn = tool_def.get("function") if isinstance(tool_def, Mapping) else None
    if not isinstance(fn, Mapping):
        return ""
    return str(fn.get("name") or "").strip()


def allowed_forced_worker_tool_names(force_tool_name: str, catalog: Sequence[Mapping[str, Any]]) -> set[str]:
    """Forced submit/patch name plus any ``skip_*`` tools already in the catalog."""
    allowed = {str(force_tool_name).strip()}
    for tool_def in catalog:
        name = _tool_def_name(tool_def)
        if name.startswith("skip_"):
            allowed.add(name)
    return {n for n in allowed if n}


def bind_worker_tools(
    enabled_toolsets: Sequence[str],
    allowed_tool_names: Sequence[str],
) -> list[dict[str, Any]]:
    """Load real schemas and keep only the named tools (no auto skip inject)."""
    from model_tools import get_tool_definitions

    catalog = get_tool_definitions(
        enabled_toolsets=list(enabled_toolsets),
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    allowed = {str(n).strip() for n in allowed_tool_names if str(n).strip()}
    return [dict(td) for td in catalog if _tool_def_name(td) in allowed]


def bind_forced_worker_tools(
    enabled_toolsets: Sequence[str],
    force_tool_name: str,
) -> list[dict[str, Any]]:
    """Load real schemas (no tool_search bridge) and keep only the forced tool + skips.

    Main-agent ``tool_search`` config is untouched; this only rebuilds the nested
    worker agent's ``tools`` array for one turn.
    """
    from model_tools import get_tool_definitions

    catalog = get_tool_definitions(
        enabled_toolsets=list(enabled_toolsets),
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    allowed = allowed_forced_worker_tool_names(force_tool_name, catalog)
    return [dict(td) for td in catalog if _tool_def_name(td) in allowed]


def _apply_worker_tools(
    agent: Any,
    *,
    enabled_toolsets: Sequence[str],
    force_tool_name: str | None = None,
    allowed_tool_names: Sequence[str] | None = None,
) -> None:
    """Replace ``agent.tools`` / ``valid_tool_names`` with the bound subset."""
    if allowed_tool_names:
        bound = bind_worker_tools(enabled_toolsets, allowed_tool_names)
    elif force_tool_name:
        bound = bind_forced_worker_tools(enabled_toolsets, force_tool_name)
    else:
        return
    agent.tools = bound
    names = {_tool_def_name(td) for td in bound}
    names.discard("")
    agent.valid_tool_names = names


def _apply_forced_worker_tools(
    agent: Any,
    *,
    enabled_toolsets: Sequence[str],
    force_tool_name: str,
) -> None:
    """Replace ``agent.tools`` / ``valid_tool_names`` with the bound subset."""
    _apply_worker_tools(
        agent,
        enabled_toolsets=enabled_toolsets,
        force_tool_name=force_tool_name,
    )


def run_worker_llm(
    prompt: str,
    *,
    plugin: str,
    purpose: str,
    platform: str = "cli",
    max_iterations: int = 10,
    enabled_toolsets: list[str] | None = None,
    request_overrides: Mapping[str, Any] | None = None,
) -> str:
    """Quiet worker AIAgent turn + JSONL usage ledger under Hermes home."""
    result = _run_worker_conversation(
        prompt,
        plugin=plugin,
        purpose=purpose,
        platform=platform,
        max_iterations=max_iterations,
        enabled_toolsets=enabled_toolsets if enabled_toolsets is not None else [],
        request_overrides=request_overrides,
    )
    return str(result.get("final_response") or "").strip()


def run_worker_llm_tools(
    prompt: str,
    *,
    plugin: str,
    purpose: str,
    platform: str = "cli",
    max_iterations: int = 2,
    enabled_toolsets: list[str],
    request_overrides: Mapping[str, Any] | None = None,
    force_tool_name: str | None = None,
    allowed_tool_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run a worker turn that must emit a tool call; return structured capture.

    When ``force_tool_name`` is set, the nested agent is equipped with only that
    tool (+ ``skip_*`` companions) and ``tool_choice`` is forced — daily digest
    Phase-2 and weekly distill share this path.

    When ``allowed_tool_names`` is set (Phase-1 type A), the agent is equipped
    with exactly those tools and may choose among them within one turn.
    """
    overrides: dict[str, Any] = dict(request_overrides or {})
    force = str(force_tool_name or "").strip() or None
    allowed = (
        [str(n).strip() for n in allowed_tool_names if str(n).strip()]
        if allowed_tool_names
        else None
    )
    if force and not allowed and "tool_choice" not in overrides:
        overrides["tool_choice"] = {
            "type": "function",
            "function": {"name": force},
        }
    elif allowed and "tool_choice" not in overrides:
        # Require a tool call but do not force a single name (same-turn repair).
        overrides["tool_choice"] = "required"
    captured: list[tuple[str, dict[str, Any]]] = []

    def _on_tool_start(_tool_call_id: str, name: str, args: Any) -> None:
        parsed = _normalize_callback_tool_args(args)
        if name:
            captured.append((str(name), parsed))

    result = _run_worker_conversation(
        prompt,
        plugin=plugin,
        purpose=purpose,
        platform=platform,
        max_iterations=max_iterations,
        enabled_toolsets=list(enabled_toolsets),
        request_overrides=overrides or None,
        tool_start_callback=_on_tool_start,
        force_tool_name=force if not allowed else None,
        allowed_tool_names=allowed,
    )
    from_messages = extract_tool_calls_from_result(result, default_tool_name=force)
    def _usable(calls: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
        return [(n, a) for n, a in calls if n and _tool_args_have_values(a)]
    tool_calls = _usable(captured) or _usable(from_messages) or from_messages or captured
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    if tool_calls:
        tool_name, tool_args = tool_calls[-1]
    return {
        "final_response": str(result.get("final_response") or "").strip(),
        "tool_name": tool_name,
        "tool_args": tool_args,
        "tool_calls": tool_calls,
        "messages": result.get("messages") or [],
        "failed": bool(result.get("failed")),
    }


ONESHOT_SYSTEM = (
    "You are a text polisher. Reply with exactly one function tool call. "
    "Do not start an agent. Do not load memory, SOUL, plugins, or any other tools."
)

_ONESHOT_PROVIDERS = {
    "xiaomi": ("XIAOMI_API_KEY", "XIAOMI_BASE_URL", "https://api.xiaomimimo.com/v1"),
}


def _ensure_hermes_dotenv() -> None:
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv(hermes_home=_hermes_home())
    except Exception:
        pass


def openai_function_tool(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap a Hermes {name, description, parameters} schema as an OpenAI tool."""
    if str(schema.get("type") or "") == "function" and isinstance(
        schema.get("function"), Mapping
    ):
        return dict(schema)
    return {
        "type": "function",
        "function": {
            "name": str(schema.get("name") or "").strip(),
            "description": str(schema.get("description") or ""),
            "parameters": schema.get("parameters")
            or {"type": "object", "properties": {}},
        },
    }


def _oneshot_credentials(provider: str) -> tuple[str, str]:
    plug = str(provider or "").strip().lower() or "xiaomi"
    key_env, url_env, default_url = _ONESHOT_PROVIDERS.get(
        plug,
        (
            f"{plug.upper().replace('-', '_')}_API_KEY",
            f"{plug.upper().replace('-', '_')}_BASE_URL",
            "",
        ),
    )
    api_key = (os.environ.get(key_env) or "").strip()
    if not api_key:
        raise ValueError(f"missing {key_env} for oneshot worker provider {plug}")
    base_url = (os.environ.get(url_env) or default_url).strip()
    return api_key, base_url


def _usage_int(usage: Any, *names: str) -> int:
    if usage is None:
        return 0
    for name in names:
        val = getattr(usage, name, None)
        if val is None and isinstance(usage, Mapping):
            val = usage.get(name)
        try:
            if val is not None:
                return int(val)
        except (TypeError, ValueError):
            continue
    return 0


def _capture_from_oneshot_message(
    message: Any,
    *,
    force_tool_name: str | None,
) -> dict[str, Any]:
    content = str(getattr(message, "content", None) or "")
    raw_calls = getattr(message, "tool_calls", None) or []
    packed: list[dict[str, Any]] = []
    for tc in raw_calls:
        fn = getattr(tc, "function", None)
        name = str(
            getattr(fn, "name", None) or getattr(tc, "name", None) or force_tool_name or ""
        ).strip()
        arguments = getattr(fn, "arguments", None)
        if arguments is None:
            arguments = getattr(tc, "arguments", None)
        packed.append({"function": {"name": name, "arguments": arguments}})
    assistant: dict[str, Any] = {"role": "assistant", "content": content}
    if packed:
        assistant["tool_calls"] = packed
    result = {
        "final_response": content.strip(),
        "messages": [assistant],
        "failed": False,
    }
    found = extract_tool_calls_from_result(result, default_tool_name=force_tool_name)
    usable = [(n, a) for n, a in found if n and _tool_args_have_values(a)]
    tool_calls = usable or found
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    if tool_calls:
        tool_name, tool_args = tool_calls[-1]
    return {
        "final_response": content.strip(),
        "tool_name": tool_name,
        "tool_args": tool_args,
        "tool_calls": tool_calls,
        "messages": [assistant],
        "failed": False,
    }


def run_worker_llm_oneshot(
    prompt: str,
    *,
    plugin: str,
    purpose: str,
    force_tool_name: str | None = None,
    tool_schema: Mapping[str, Any] | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """One OpenAI-compatible chat.completions call. No nested Hermes AIAgent."""
    _ensure_hermes_dotenv()
    plugin_runtime = _plugin_worker_runtime(plugin, purpose)
    provider = plugin_runtime.get("provider") or "xiaomi"
    model = plugin_runtime.get("model") or "mimo-v2.5"
    force = str(force_tool_name or "").strip() or None
    schema = dict(tool_schema or {})
    if force and not str(schema.get("name") or "").strip():
        fn = schema.get("function") if isinstance(schema.get("function"), Mapping) else None
        if not (fn and str(fn.get("name") or "").strip()):
            schema["name"] = force
    api_key, base_url = _oneshot_credentials(provider)
    from openai import OpenAI

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    wrapped = openai_function_tool(schema) if schema else None
    tool_name = ""
    if wrapped:
        fn = wrapped.get("function") if isinstance(wrapped.get("function"), Mapping) else {}
        tool_name = str((fn or {}).get("name") or "").strip()
    messages = [
        {"role": "system", "content": ONESHOT_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if wrapped and tool_name:
        create_kwargs["tools"] = [wrapped]
        if force:
            create_kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": force},
            }
    with worker_llm_scope():
        try:
            client = OpenAI(**client_kwargs)
            resp = client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            record_worker_usage(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "plugin": plugin,
                    "purpose": purpose,
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "api_calls": 0,
                }
            )
            return {
                "final_response": str(exc),
                "tool_name": None,
                "tool_args": None,
                "tool_calls": [],
                "messages": [],
                "failed": True,
            }
        choice = resp.choices[0]
        captured = _capture_from_oneshot_message(
            choice.message, force_tool_name=force
        )
        usage = getattr(resp, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None) if usage is not None else None
        reasoning = (
            getattr(usage, "completion_tokens_details", None) if usage is not None else None
        )
        usage_fields = {
            "input_tokens": _usage_int(usage, "prompt_tokens"),
            "output_tokens": _usage_int(usage, "completion_tokens"),
            "cache_read_tokens": _usage_int(details, "cached_tokens"),
            "cache_write_tokens": 0,
            "reasoning_tokens": _usage_int(reasoning, "reasoning_tokens"),
            "total_tokens": _usage_int(usage, "total_tokens"),
        }
        record_worker_usage(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "plugin": plugin,
                "purpose": purpose,
                "model": model,
                **usage_fields,
                "cost_usd": 0.0,
                "api_calls": 1,
            }
        )
        captured.update(usage_fields)
        captured["finish_reason"] = getattr(choice, "finish_reason", None)
        captured["max_tokens"] = max_tokens
        return captured


def _run_worker_conversation(
    prompt: str,
    *,
    plugin: str,
    purpose: str,
    platform: str,
    max_iterations: int,
    enabled_toolsets: list[str],
    request_overrides: Mapping[str, Any] | None = None,
    tool_start_callback: Any = None,
    force_tool_name: str | None = None,
    allowed_tool_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv(hermes_home=_hermes_home())
    except Exception:
        pass
    from gateway.run import _resolve_gateway_model, _resolve_runtime_agent_kwargs
    from run_agent import AIAgent

    with worker_llm_scope():
        runtime_kwargs = dict(_resolve_runtime_agent_kwargs() or {})
        runtime_model = runtime_kwargs.pop("model", None)
        runtime_kwargs.pop("enabled_toolsets", None)
        runtime_kwargs.pop("disabled_toolsets", None)
        runtime_kwargs.pop("request_overrides", None)
        runtime_kwargs.pop("tool_start_callback", None)
        plugin_runtime = _plugin_worker_runtime(plugin, purpose)
        if plugin_runtime.get("provider"):
            runtime_kwargs["provider"] = plugin_runtime["provider"]
            runtime_kwargs["requested_provider"] = plugin_runtime["provider"]
            # Gateway kwargs are for the chat provider (e.g. Kimi Anthropic).
            # Mixing them with a plugin worker provider (Xiaomi OpenAI) drops
            # the worker into the wrong client with no API key.
            for key in (
                "api_key",
                "base_url",
                "api_base",
                "api_mode",
                "credential_pool",
            ):
                runtime_kwargs.pop(key, None)
        model = (
            plugin_runtime.get("model")
            or (
                str(runtime_model).strip()
                if runtime_model is not None and str(runtime_model).strip()
                else ""
            )
            or _resolve_gateway_model()
        )
        agent_kwargs: dict[str, Any] = {
            "model": model,
            **runtime_kwargs,
            "platform": platform or "cli",
            "quiet_mode": True,
            "skip_context_files": True,
            "skip_memory": True,
            "enabled_toolsets": list(enabled_toolsets),
            "max_iterations": max_iterations,
        }
        if request_overrides:
            agent_kwargs["request_overrides"] = dict(request_overrides)
        if tool_start_callback is not None:
            agent_kwargs["tool_start_callback"] = tool_start_callback
        agent = AIAgent(**agent_kwargs)
        agent.suppress_status_output = True
        # Worker-only: re-equip after init so tool_search cannot strip schemas.
        # Text-only workers (force_tool_name=None, no allow-list) stay untouched.
        if force_tool_name or allowed_tool_names:
            try:
                _apply_worker_tools(
                    agent,
                    enabled_toolsets=enabled_toolsets,
                    force_tool_name=force_tool_name,
                    allowed_tool_names=allowed_tool_names,
                )
            except Exception:
                # Fail open to init-time tools rather than abort the digest/weekly run.
                pass
        result = agent.run_conversation(prompt)
        record_worker_usage({
            "ts": datetime.now(timezone.utc).isoformat(),
            "plugin": plugin,
            "purpose": purpose,
            "model": getattr(agent, "model", "") or "",
            "input_tokens": int(getattr(agent, "session_input_tokens", 0) or 0),
            "output_tokens": int(getattr(agent, "session_output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(agent, "session_cache_read_tokens", 0) or 0),
            "cache_write_tokens": int(getattr(agent, "session_cache_write_tokens", 0) or 0),
            "reasoning_tokens": int(getattr(agent, "session_reasoning_tokens", 0) or 0),
            "total_tokens": int(getattr(agent, "session_total_tokens", 0) or 0),
            "cost_usd": float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0),
            "api_calls": int(getattr(agent, "session_api_calls", 0) or 0),
        })
        return result if isinstance(result, dict) else {"final_response": str(result or "")}
