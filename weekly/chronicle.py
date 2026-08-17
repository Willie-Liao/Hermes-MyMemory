"""Short Chronicle summaries for weekly review markdown.

Mirrors the hot_health sidecar + LLM call pattern, but stays a separate module
so hot_health remains hot-memory-only (suggestions over MEMORY.md / USER.md).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - standalone plugin/test fallback

    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        value = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(value).resolve() if value else (Path.home() / ".hermes").resolve()


def _chronicle_path() -> Path:
    return get_hermes_home() / "memories" / "staging" / ".weekly-chronicle.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _md_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_sidecar() -> dict[str, Any]:
    try:
        parsed = json.loads(_chronicle_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_sidecar(data: dict[str, Any]) -> None:
    path = _chronicle_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _resolve_week_key(week_key: str | None) -> str | None:
    if week_key:
        return week_key
    today = datetime.now().date()
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _resolve_weekly_md(week_key: str) -> Path | None:
    """Canonical ``YYYY-Www.md``, else legacy ``… reviewed.md`` for migrate leftovers."""
    staging = get_hermes_home() / "memories" / "staging" / "weekly"
    draft = staging / f"{week_key}.md"
    if draft.is_file():
        return draft
    reviewed = staging / f"{week_key} reviewed.md"
    return reviewed if reviewed.is_file() else None


def _build_prompt(week_key: str, md_text: str) -> str:
    return (
        "Read the weekly review markdown and list what the user actually did "
        "this week. Return ONLY one JSON object "
        '{"items": ["...", "..."]} with 3-6 short strings. '
        "Focus on actions, decisions, and shipped work — a quick 'what I did' "
        "scan, not a news broadcast. Do not invent facts. Do not use section "
        "headings or a §1–§8 outline. Avoid figures and dense stats "
        "(no N=, r=, CFI/RMSEA, commit SHAs, long literature citations). "
        "No 'Good evening' framing; no long paragraphs; no markdown fences.\n\n"
        f"Week: {week_key}\n\n"
        f"WEEKLY REVIEW MARKDOWN:\n{md_text}\n"
    )


def _parse_chronicle_items(raw: str) -> list[str]:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("chronicle response must be JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("chronicle response must be a JSON object")
    items = parsed.get("items")
    if not isinstance(items, list):
        raise ValueError('chronicle JSON must include an "items" array')
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not (1 <= len(cleaned) <= 6):
        raise ValueError("chronicle items must contain 1-6 non-empty strings")
    return cleaned


def _summary_from_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _call_llm(prompt: str) -> str:
    """Same gateway-backed, tool-free agent stack as hot_health / weekly."""
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
        purpose="chronicle",
        platform="cli",
        max_iterations=10,
    )


def _parse_week_key(value: str) -> bool:
    parts = value.split("-W")
    if len(parts) != 2:
        return False
    year, week = parts
    return year.isdigit() and week.isdigit() and 1 <= int(week) <= 53


def _extract_brief_section(md_text: str) -> str:
    """Prefer Worker 2 ``## Brief`` body when present (no second sidecar format)."""
    try:
        from .weekly_cite import extract_brief
    except ImportError:  # pragma: no cover - direct pytest collection path
        cite_path = Path(__file__).with_name("weekly_cite.py")
        spec = importlib.util.spec_from_file_location(
            "memory_weekly_cite_chronicle", cite_path
        )
        if spec is None or spec.loader is None:
            return ""
        cite = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cite)
        extract_brief = cite.extract_brief
    return str(extract_brief(md_text) or "").strip()


def get_or_refresh_chronicle(
    week_key: str | None = None, *, force: bool = False
) -> dict[str, Any]:
    """Return cached summary or regenerate when md_hash misses.

    Prefer non-empty ``## Brief`` from the weekly MD (Worker 2) over the
    news-anchor LLM. Sidecar: ``memories/staging/.weekly-chronicle.json``.
    """
    resolved = _resolve_week_key(week_key)
    if not resolved or not _parse_week_key(resolved):
        return {
            "outcome": "bad_week",
            "week": week_key or "",
            "cached": False,
            "summary": "",
            "md_hash": "",
        }

    path = _resolve_weekly_md(resolved)
    if path is None:
        return {
            "outcome": "no_md",
            "week": resolved,
            "cached": False,
            "summary": "",
            "md_hash": "",
        }

    try:
        md_text = path.read_text(encoding="utf-8")
    except OSError:
        return {
            "outcome": "no_md",
            "week": resolved,
            "cached": False,
            "summary": "",
            "md_hash": "",
        }

    digest = _md_hash(md_text)
    sidecar = _load_sidecar()
    entry = sidecar.get(resolved)
    if (
        not force
        and isinstance(entry, dict)
        and entry.get("md_hash") == digest
        and str(entry.get("summary") or "").strip()
    ):
        return {
            "outcome": "ok",
            "week": resolved,
            "cached": True,
            "summary": str(entry["summary"]).strip(),
            "md_hash": digest,
            "generated_at": entry.get("generated_at"),
        }

    brief = _extract_brief_section(md_text)
    if brief:
        summary = brief
    else:
        try:
            summary = _summary_from_items(
                _parse_chronicle_items(_call_llm(_build_prompt(resolved, md_text)))
            )
        except Exception:  # noqa: BLE001 - soft-fail to last good summary
            if isinstance(entry, dict) and str(entry.get("summary") or "").strip():
                return {
                    "outcome": "llm_failed",
                    "week": resolved,
                    "cached": True,
                    "summary": str(entry["summary"]).strip(),
                    "md_hash": str(entry.get("md_hash") or ""),
                    "generated_at": entry.get("generated_at"),
                }
            return {
                "outcome": "llm_failed",
                "week": resolved,
                "cached": False,
                "summary": "",
                "md_hash": digest,
            }

        if not summary:
            if isinstance(entry, dict) and str(entry.get("summary") or "").strip():
                return {
                    "outcome": "llm_failed",
                    "week": resolved,
                    "cached": True,
                    "summary": str(entry["summary"]).strip(),
                    "md_hash": str(entry.get("md_hash") or ""),
                    "generated_at": entry.get("generated_at"),
                }
            return {
                "outcome": "llm_failed",
                "week": resolved,
                "cached": False,
                "summary": "",
                "md_hash": digest,
            }

    generated_at = _now_iso()
    sidecar[resolved] = {
        "md_hash": digest,
        "summary": summary,
        "generated_at": generated_at,
    }
    _write_sidecar(sidecar)
    return {
        "outcome": "ok",
        "week": resolved,
        "cached": False,
        "summary": summary,
        "md_hash": digest,
        "generated_at": generated_at,
    }
