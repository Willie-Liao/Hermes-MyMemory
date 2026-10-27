"""Put the MyMemory worker block back when a regenerated config.yaml lacks it.

Hermes seeds a stock file from its example and does not load this plugin, so
the digest, weekly, and monthly lanes would otherwise stay absent until someone
re-ran the installer. A missing config.yaml is left missing: this module only
repairs a file that already exists.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

_PROVIDER = "minimax-cn"
_MODEL = "MiniMax-M2.5"
_LANES = {
    "digest": ("phase1", "phase2", "wrapup"),
    "weekly": ("weekly",),
    "monthly": ("monthly",),
}
_MONTHLY_EXTRAS = (
    "map_batch_tokens",
    "reduce_max_tokens",
    "write_at",
    "checkpoint_days",
    "retry_days",
)


def hermes_home() -> Path:
    """Resolve HERMES_HOME so a test sandbox is not the live gateway home."""
    env = (os.environ.get("HERMES_HOME") or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def pin_path(home: Path | None = None) -> Path:
    """Keep the backup beside config.yaml so deleting the config does not delete the pin."""
    return (home or hermes_home()) / ".mymemory-worker-pin.yaml"


def default_worker_block() -> dict[str, Any]:
    """Fallback lanes when no pin exists yet.

    The live cloud file may already be MiMo; that backup wins. This default is
    only for a regenerated file that has never had a saved worker block.
    """
    lane = {"provider": _PROVIDER, "model": _MODEL}

    def section(names: tuple[str, ...], **extra: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "allow_tool_override": False,
            "provider": _PROVIDER,
            "model": _MODEL,
            "worker_llm": {name: dict(lane) for name in names},
        }
        body.update(extra)
        return body

    return {
        "digest": section(_LANES["digest"]),
        "weekly": section(_LANES["weekly"]),
        "monthly": section(
            _LANES["monthly"],
            map_batch_tokens=8000,
            reduce_max_tokens=16384,
            write_at="00:05",
            checkpoint_days=[10, 20],
            retry_days=3,
        ),
        "retention": {"allow_tool_override": False},
    }


def ensure_worker_block(home: Path | None = None) -> bool:
    """Refresh the pin from a complete config, or merge a pin into an incomplete one.

    Returns False when the file is missing, unreadable, or already had nothing
    to change. Never creates config.yaml.
    """
    root = home or hermes_home()
    path = root / "config.yaml"
    if not path.is_file():
        return False
    try:
        engine, data, kind, original = _read_config(path)
        if not isinstance(data, dict):
            return False
        plugins = _as_dict(data.get("plugins"))
        data["plugins"] = plugins
        entries = _as_dict(plugins.get("entries"))
        plugins["entries"] = entries
        mine = _as_dict(entries.get("MyMemory"))
        entries["MyMemory"] = mine
        if _block_complete(mine):
            _write_pin(root, _pin_from(mine))
            return False
        source = _load_pin(root) or default_worker_block()
        _merge_block(mine, source)
        _ensure_enabled(plugins)
        _ensure_memory_provider(data)
        dumped = _dump(engine, data, kind)
        if dumped != original:
            _atomic_text(path, dumped if dumped.endswith("\n") else dumped + "\n")
        _write_pin(root, _pin_from(mine))
        return True
    except Exception:
        return False


def _block_complete(mine: Mapping[str, Any]) -> bool:
    return all(_section_complete(mine.get(name), lanes) for name, lanes in _LANES.items())


def _section_complete(section: Any, lanes: tuple[str, ...]) -> bool:
    if not _has_model(section):
        return False
    worker = section.get("worker_llm") if isinstance(section, Mapping) else None
    if not isinstance(worker, Mapping):
        return False
    return all(_has_model(worker.get(name)) for name in lanes)


def _has_model(bag: Any) -> bool:
    return (
        isinstance(bag, Mapping)
        and bool(str(bag.get("provider") or "").strip())
        and bool(str(bag.get("model") or "").strip())
    )


def _merge_block(mine: dict[str, Any], source: Mapping[str, Any]) -> None:
    for name, lanes in _LANES.items():
        current = _as_dict(mine.get(name))
        mine[name] = current
        src = source.get(name) if isinstance(source.get(name), Mapping) else {}
        _fill_section(current, src, lanes)
        if name == "weekly":
            brief = src.get("weekly_brief_weixin")
            if brief and "weekly_brief_weixin" not in current:
                current["weekly_brief_weixin"] = brief
        if name == "monthly":
            for key in _MONTHLY_EXTRAS:
                if key not in current and key in src:
                    current[key] = _plain(src[key])
    retention = _as_dict(mine.get("retention"))
    mine["retention"] = retention
    if "allow_tool_override" not in retention:
        retention["allow_tool_override"] = False


def _fill_section(current: dict[str, Any], source: Mapping[str, Any], lanes: tuple[str, ...]) -> None:
    provider = str(source.get("provider") or _PROVIDER)
    model = str(source.get("model") or _MODEL)
    if not _has_model(current):
        current["provider"] = provider
        current["model"] = model
    if "allow_tool_override" not in current:
        current["allow_tool_override"] = False
    worker = current.get("worker_llm")
    if not isinstance(worker, dict):
        worker = {}
        current["worker_llm"] = worker
    src_worker = source.get("worker_llm") if isinstance(source.get("worker_llm"), Mapping) else {}
    for lane in lanes:
        row = worker.get(lane)
        if not isinstance(row, dict):
            row = {}
            worker[lane] = row
        if _has_model(row):
            continue
        src_row = src_worker.get(lane) if isinstance(src_worker.get(lane), Mapping) else {}
        row["provider"] = str(src_row.get("provider") or provider)
        row["model"] = str(src_row.get("model") or model)


def _ensure_enabled(plugins: dict[str, Any]) -> None:
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
        plugins["enabled"] = enabled
    if "MyMemory" not in [str(item) for item in enabled]:
        enabled.append("MyMemory")


def _ensure_memory_provider(data: dict[str, Any]) -> None:
    memory = data.get("memory")
    if not isinstance(memory, dict):
        memory = {}
        data["memory"] = memory
    if not str(memory.get("provider") or "").strip():
        memory["provider"] = "MyMemory"


def _pin_from(mine: Mapping[str, Any]) -> dict[str, Any]:
    pin: dict[str, Any] = {}
    for key in ("digest", "weekly", "monthly", "retention"):
        section = mine.get(key)
        if isinstance(section, Mapping):
            pin[key] = _plain(section)
    return pin


def _load_pin(home: Path) -> dict[str, Any] | None:
    path = pin_path(home)
    if not path.is_file():
        return None
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) and _block_complete(raw) else None


def _write_pin(home: Path, pin: Mapping[str, Any]) -> None:
    import yaml

    text = yaml.safe_dump(_plain(pin), sort_keys=False, allow_unicode=True)
    path = pin_path(home)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    _atomic_text(path, text)


def _atomic_text(path: Path, content: str) -> None:
    """Reuse digest's replace so a crash cannot leave a half-written config or pin.

    Import by file path. A top-level ``digest`` module elsewhere on sys.path is
    not this plugin's package, and a relative import fails when tests load this
    file as a script.
    """
    global _REPLACE
    if _REPLACE is None:
        import importlib.util

        source = Path(__file__).resolve().parent / "digest" / "operation_log.py"
        spec = importlib.util.spec_from_file_location("mymemory_operation_log", source)
        if spec is None or spec.loader is None:
            raise ImportError(source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _REPLACE = module._atomic_replace_text
    _REPLACE(path, content)


_REPLACE = None


def _read_config(path: Path) -> tuple[Any, Any, str, str]:
    text = path.read_text(encoding="utf-8")
    try:
        from ruamel.yaml import YAML

        engine = YAML()
        engine.preserve_quotes = True
        data = engine.load(text) or {}
        return engine, data, "ruamel", text
    except Exception:
        import yaml

        return yaml, yaml.safe_load(text) or {}, "pyyaml", text


def _dump(engine: Any, data: Any, kind: str) -> str:
    if kind == "ruamel":
        import io

        buf = io.StringIO()
        engine.dump(data, buf)
        return buf.getvalue()
    return engine.safe_dump(data, sort_keys=False, allow_unicode=True)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value
