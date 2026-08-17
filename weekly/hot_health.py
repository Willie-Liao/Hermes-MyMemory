"""LLM-backed health suggestions for MEMORY.md, USER.md, and HERMES.md hot entries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - standalone plugin/test fallback

    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        value = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(value).resolve() if value else (Path.home() / ".hermes").resolve()


ALLOWED_KINDS = {"outdated", "move_to_user", "merge", "rephrase", "purge"}
HOT_FILES = ("MEMORY.md", "USER.md", "HERMES.md")
_VALID_TO_RE = re.compile(r"\bvalid_to\s*:\s*(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_SECTION_SPLIT_RE = re.compile(r"(?:\n§\n|^§\s*)", re.MULTILINE)
_HEADING_SPLIT_RE = re.compile(r"(?=^## )", re.MULTILINE)
_MAX_WEEKLY_CONTEXT_CHARS = 12000


def _today() -> date:
    return date.today()


def _health_path() -> Path:
    return get_hermes_home() / "memories" / "staging" / ".hot-health.json"


def _hot_file_path(filename: str) -> Path:
    if filename == "HERMES.md":
        # Cloud: ~/.hermes/HERMES.md. Mac AGENT: sibling of hermes-home/.
        home = get_hermes_home()
        inside = home / "HERMES.md"
        beside = home.parent / "HERMES.md"
        if inside.is_file():
            return inside
        if beside.is_file():
            return beside
        return inside
    return get_hermes_home() / "memories" / filename


def _read_hot_file(filename: str) -> str:
    path = _hot_file_path(filename)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _split_entries(content: str, filename: str | None = None) -> list[str]:
    if not content.strip():
        return []
    if filename == "HERMES.md" and re.search(r"^## ", content, re.MULTILINE):
        return [
            part.strip()
            for part in _HEADING_SPLIT_RE.split(content)
            if part.strip()
        ]
    return [
        part.replace("§", "").strip()
        for part in _SECTION_SPLIT_RE.split(content)
        if part.replace("§", "").strip()
    ]


def _join_entries(entries: list[str], filename: str | None = None) -> str:
    cleaned = [entry.strip() for entry in entries if entry and entry.strip()]
    if filename == "HERMES.md":
        return "\n\n".join(cleaned)
    return "\n§\n".join(cleaned)


def write_hot_entries(filename: str, entries: list[str]) -> None:
    path = _hot_file_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_join_entries(entries, filename), encoding="utf-8")


def _hot_source_fingerprint() -> str:
    """SHA-256 of hot file names + bytes (mtime ignored)."""
    parts: list[bytes] = []
    for name in HOT_FILES:
        path = _hot_file_path(name)
        try:
            data = path.read_bytes()
            parts.append(f"{name}\n".encode("utf-8") + data)
        except OSError:
            parts.append(f"{name}:missing".encode("utf-8"))
    return hashlib.sha256(b"\n".join(parts)).hexdigest()


def _empty_suggestions() -> dict[str, Any]:
    return {filename: [] for filename in HOT_FILES}


def _annotations_only(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out = _empty_suggestions()
    for filename in HOT_FILES:
        value = payload.get(filename)
        if isinstance(value, list):
            out[filename] = value
    return out


def _latest_weekly_context() -> str:
    weekly_dir = get_hermes_home() / "memories" / "staging" / "weekly"
    if not weekly_dir.is_dir():
        return ""
    paths = sorted(weekly_dir.glob("*.md"))
    if not paths:
        return ""
    try:
        return paths[-1].read_text(encoding="utf-8")[:_MAX_WEEKLY_CONTEXT_CHARS]
    except OSError:
        return ""


def _build_prompt(
    memory_content: str,
    user_content: str,
    hermes_content: str,
    weekly_context: str,
    *,
    reason: str,
) -> str:
    memory_entries = "\n\n".join(
        f"[{index}] {entry}"
        for index, entry in enumerate(_split_entries(memory_content, "MEMORY.md"))
    )
    user_entries = "\n\n".join(
        f"[{index}] {entry}"
        for index, entry in enumerate(_split_entries(user_content, "USER.md"))
    )
    hermes_entries = "\n\n".join(
        f"[{index}] {entry}"
        for index, entry in enumerate(_split_entries(hermes_content, "HERMES.md"))
    )
    return (
        "You are the hot-memory health worker. Read the indexed entries below and "
        "return ONLY one JSON object with keys \"MEMORY.md\", \"USER.md\", and "
        "\"HERMES.md\". Each value must be a list of suggestions shaped as: "
        "{\"index\": integer, \"kinds\": [kind], \"reason\": string, \"actions\": "
        "[string], optional \"peer_groups\": [[integer]] (preferred) or optional "
        "\"peers\": [integer]}. Allowed kinds are outdated, "
        "move_to_user, merge, rephrase, purge. MEMORY contains timed facts; flag "
        "stale facts and durable identity/preferences that belong in USER. USER "
        "holds durable identity/preferences. HERMES.md is project session rules "
        "(usually ## heading sections); flag stale or contradictory rules, overlap "
        "with MEMORY/USER, tighten phrasing, or purge obsolete sections; use "
        "move_to_user when durable identity content wrongly lives in HERMES. "
        "Always inspect all three for overlap, merge, tightening, or purge "
        "opportunities even when under budget. Prefer one peer_groups entry with "
        "a single peer for merge. Use multiple peers in a group (or multiple "
        "groups) only when entries are too scattered / overlapping and merging "
        "clearly improves clarity; do not aggressively bundle unrelated entries. "
        "Do not suggest edits that are not "
        "grounded in the supplied text. Indices are zero-based; use the number in "
        "brackets before each entry.\n\n"
        f"Reason: {reason}\n\n"
        f"MEMORY.md:\n{memory_entries}\n\n"
        f"USER.md:\n{user_entries}\n\n"
        f"HERMES.md:\n{hermes_entries}\n\n"
        f"LATEST WEEKLY CONTEXT (may be empty):\n{weekly_context}\n"
    )


def _call_llm(prompt: str, *, purpose: str = "hot_health") -> str:
    """Use the same gateway-backed, tool-free agent stack as weekly.py."""
    mymemory = Path(__file__).resolve().parent.parent
    if str(mymemory) not in sys.path:
        sys.path.insert(0, str(mymemory))
    plugins_root = mymemory.parent
    plugins_root_str = str(plugins_root)
    if plugins_root_str not in sys.path:
        sys.path.insert(0, plugins_root_str)
    from worker_llm import run_worker_llm

    return run_worker_llm(
        prompt,
        plugin="memory-weekly",
        purpose=purpose,
        platform="cli",
        max_iterations=10,
    )


def _parse_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("hot health response must be a JSON object")
    return parsed


def _is_int_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalize_peer_groups(
    raw_groups: Any,
    *,
    index: int,
    entry_count: int,
) -> list[list[int]]:
    if not isinstance(raw_groups, list):
        return []
    seen_global: set[int] = set()
    groups: list[list[int]] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, list):
            continue
        group: list[int] = []
        for peer in raw_group:
            if (
                not _is_int_index(peer)
                or peer == index
                or peer < 0
                or peer >= entry_count
                or peer in seen_global
            ):
                continue
            seen_global.add(peer)
            group.append(peer)
        if group:
            groups.append(group)
    return groups


def _normalize_suggestions(
    raw: dict[str, Any], entry_counts: dict[str, int]
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = _empty_suggestions()
    for filename in out:
        suggestions = raw.get(filename)
        if not isinstance(suggestions, list):
            continue
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            index = suggestion.get("index")
            if (
                not _is_int_index(index)
                or index < 0
                or index >= entry_counts[filename]
            ):
                continue
            raw_kinds = suggestion.get("kinds")
            if not isinstance(raw_kinds, list):
                continue
            kinds = list(
                dict.fromkeys(kind for kind in raw_kinds if kind in ALLOWED_KINDS)
            )
            if not kinds:
                continue
            item: dict[str, Any] = {
                "index": index,
                "kinds": kinds,
                "reason": str(suggestion.get("reason") or "").strip(),
                "actions": [
                    str(action)
                    for action in suggestion.get("actions", [])
                    if isinstance(action, str) and action.strip()
                ],
            }
            peer_groups = _normalize_peer_groups(
                suggestion.get("peer_groups"),
                index=index,
                entry_count=entry_counts[filename],
            )
            if not peer_groups:
                peers = suggestion.get("peers")
                if isinstance(peers, list):
                    legacy = [
                        peer
                        for peer in peers
                        if _is_int_index(peer)
                        and peer != index
                        and 0 <= peer < entry_counts[filename]
                    ]
                    legacy = list(dict.fromkeys(legacy))
                    if legacy:
                        peer_groups = [legacy]
            if peer_groups:
                item["peer_groups"] = peer_groups
                item["peers"] = [
                    peer for group in peer_groups for peer in group
                ]
            out[filename].append(item)
    return out


def _apply_expiry_floor(
    suggestions: dict[str, list[dict[str, Any]]],
    memory_entries: list[str],
    *,
    today: date,
) -> None:
    by_index = {item["index"]: item for item in suggestions["MEMORY.md"]}
    for index, entry in enumerate(memory_entries):
        match = _VALID_TO_RE.search(entry)
        if match is None:
            continue
        try:
            valid_to = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if valid_to >= today:
            continue
        item = by_index.get(index)
        if item is None:
            item = {
                "index": index,
                "kinds": ["outdated"],
                "reason": f"valid_to {valid_to.isoformat()} is past",
                "actions": ["purge", "extend"],
            }
            suggestions["MEMORY.md"].append(item)
            by_index[index] = item
            continue
        if "outdated" not in item["kinds"]:
            item["kinds"].append("outdated")
        for action in ("purge", "extend"):
            if action not in item["actions"]:
                item["actions"].append(action)


def _persist_suggestions(suggestions: dict[str, Any]) -> None:
    path = _health_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(suggestions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_hot_health() -> dict[str, Any]:
    """Load the last valid persisted suggestion set (includes optional source_hash)."""
    try:
        parsed = json.loads(_health_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_suggestions()
    if not isinstance(parsed, dict):
        return _empty_suggestions()
    out = _annotations_only(parsed)
    source_hash = parsed.get("source_hash")
    if isinstance(source_hash, str) and source_hash:
        out["source_hash"] = source_hash
    return out


def hot_source_changed() -> bool:
    """True when hot file bytes differ from the last persisted source_hash."""
    current = _hot_source_fingerprint()
    loaded = load_hot_health()
    previous = loaded.get("source_hash")
    if not isinstance(previous, str) or not previous:
        return True
    return previous != current


def run_hot_health(*, reason: str = "bridge") -> dict[str, Any]:
    """Generate, validate, floor, and persist hot-memory health suggestions.

    Skips the LLM when MEMORY+USER+HERMES content hash matches stored source_hash.
    """
    fingerprint = _hot_source_fingerprint()
    existing = load_hot_health()
    if existing.get("source_hash") == fingerprint:
        return existing

    contents = {filename: _read_hot_file(filename) for filename in HOT_FILES}
    entries = {
        filename: _split_entries(content, filename)
        for filename, content in contents.items()
    }
    prompt = _build_prompt(
        contents["MEMORY.md"],
        contents["USER.md"],
        contents["HERMES.md"],
        _latest_weekly_context(),
        reason=reason,
    )
    parsed = _parse_response(_call_llm(prompt))
    suggestions = _normalize_suggestions(
        parsed, {filename: len(items) for filename, items in entries.items()}
    )
    _apply_expiry_floor(suggestions, entries["MEMORY.md"], today=_today())
    suggestions["source_hash"] = fingerprint
    _persist_suggestions(suggestions)
    return suggestions
