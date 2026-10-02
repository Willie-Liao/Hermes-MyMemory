"""Shared daily staging path conventions for memory plugins.

Daily digest files are markdown (``.md``) with YAML frontmatter blocks inside.
Legacy ``YYYY-MM-DD.yaml`` files are migrated to ``.md`` on plugin sweeps.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

DAILY_STAGING_SUFFIX = ".md"
LEGACY_DAILY_STAGING_SUFFIX = ".yaml"
WEEKLY_REVIEWED_INFIX = " reviewed"  # legacy only; no longer written
WEEK_STATUS_PENDING = "pending"
WEEK_STATUS_REVIEWED = "reviewed"
WEEK_STATUS_ENUM = (WEEK_STATUS_PENDING, WEEK_STATUS_REVIEWED)
WEEK_KEY_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{2})(?: reviewed)?$")
_WEEK_DOC_FM_RE = re.compile(
    r"\A---\s*\n(?P<body>.*?)\n---\s*\n(?P<rest>.*)\Z",
    re.DOTALL,
)
BLOCK_ID_RE = re.compile(r"\b(mem-\d{8}-[a-z0-9-]+)\b", re.IGNORECASE)


def _session_env(name: str, default: str = "") -> str:
    """Read a gateway session contextvar, falling back to the process env."""
    try:
        from gateway.session_context import get_session_env

        value = get_session_env(name, default)
    except Exception:
        value = os.getenv(name, default)
    return (value or default).strip()


def session_arg_override(raw_args: str) -> str | None:
    """Return the value of a ``--session <id>`` flag in ``raw_args`` if present."""
    try:
        tokens = shlex.split(raw_args or "")
    except ValueError:
        tokens = (raw_args or "").split()
    for idx, token in enumerate(tokens):
        if token == "--session" and idx + 1 < len(tokens):
            return tokens[idx + 1]
        if token.startswith("--session="):
            return token.split("=", 1)[1]
    return None


def resolve_session(raw_args: str = "") -> tuple[str, str] | None:
    """Resolve the active ``(session_key, session_id)`` for a slash command.

    Order: explicit ``--session <id>`` flag, then the gateway session
    contextvars. Returns ``None`` when no session can be determined.
    """
    override = session_arg_override(raw_args)
    if override:
        return override, override

    session_id = _session_env("HERMES_SESSION_ID")
    session_key = _session_env("HERMES_SESSION_KEY") or session_id
    if not session_id and not session_key:
        return None
    session_id = session_id or session_key
    session_key = session_key or session_id
    return session_key, session_id


def hermes_local_now() -> datetime:
    """Wall-clock now in the user's configured Hermes timezone (``config.yaml``)."""
    try:
        from hermes_time import now

        return now()
    except Exception:
        return datetime.now().astimezone()


def hermes_local_today() -> date:
    return hermes_local_now().date()


def hermes_local_today_str() -> str:
    return hermes_local_now().strftime("%Y-%m-%d")


def eligibility_iso_week(today: date | None = None) -> tuple[int, int]:
    """ISO week used for past-vs-current weekly cutoff.

    On Sunday (``weekday()==6``), advance one week so the closing ISO week is
    eligible for generation and presentation (same queue as Monday morning).
    """
    base = today or hermes_local_today()
    if base.weekday() == 6:
        base = base + timedelta(days=1)
    iso = base.isocalendar()
    return iso.year, iso.week


def daily_staging_dir(hermes_home: Path) -> Path:
    return hermes_home / "memories" / "staging" / "daily"


def daily_staging_name(date_str: str) -> str:
    return f"{date_str}{DAILY_STAGING_SUFFIX}"


def daily_staging_path(hermes_home: Path, date_str: str) -> Path:
    return daily_staging_dir(hermes_home) / daily_staging_name(date_str)


def weekly_staging_dir(hermes_home: Path) -> Path:
    return hermes_home / "memories" / "staging" / "weekly"


def weekly_staging_name(year: int, week: int) -> str:
    return f"{year}-W{week:02d}{DAILY_STAGING_SUFFIX}"


def weekly_staging_path(hermes_home: Path, year: int, week: int) -> Path:
    return weekly_staging_dir(hermes_home) / weekly_staging_name(year, week)


def weekly_reviewed_name(year: int, week: int) -> str:
    return f"{year}-W{week:02d}{WEEKLY_REVIEWED_INFIX}{DAILY_STAGING_SUFFIX}"


def weekly_reviewed_path(hermes_home: Path, year: int, week: int) -> Path:
    return weekly_staging_dir(hermes_home) / weekly_reviewed_name(year, week)


def parse_week_key(value: str) -> tuple[int, int] | None:
    """Parse ``YYYY-Www`` or ``YYYY-Www reviewed`` into ``(year, week)``."""
    match = WEEK_KEY_RE.match(value.strip())
    if not match:
        return None
    year = int(match.group("year"))
    week = int(match.group("week"))
    if week < 1 or week > 53:
        return None
    return year, week


def week_key(year: int, week: int) -> str:
    return f"{year}-W{week:02d}"


def week_file_path(hermes_home: Path, year: int, week: int) -> Path:
    """Canonical single path for a week (atomic file)."""
    return weekly_staging_path(hermes_home, year, week)


def _split_week_doc_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Return (doc-level FM dict or None, body without doc FM)."""
    match = _WEEK_DOC_FM_RE.match(text or "")
    if not match:
        return None, text or ""
    raw = match.group("body")
    rest = match.group("rest")
    try:
        data = yaml.safe_load(raw) or {}
    except Exception:
        return None, text or ""
    if not isinstance(data, dict):
        return None, text or ""
    # Document FM uses week_status or month_status; Distill blocks use per-block status.
    if "week_status" in data or "week" in data or "month_status" in data or "month" in data:
        return dict(data), rest
    return None, text or ""


def read_week_status(path: Path) -> str | None:
    """Read ``week_status`` from document frontmatter; None if missing/unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, _rest = _split_week_doc_frontmatter(text)
    if not fm:
        return None
    status = str(fm.get("week_status") or "").strip().casefold()
    if status in WEEK_STATUS_ENUM:
        return status
    return None


def write_cycle_status(
    path: Path,
    status: str,
    *,
    key_str: str = "",
    content: str | None = None,
    cycle: str = "week",
) -> str:
    """Write week_status or month_status through one helper so the two envelopes cannot drift."""
    status_n = status.strip().casefold()
    if status_n not in WEEK_STATUS_ENUM:
        raise ValueError(f"invalid {cycle}_status: {status!r}")
    if content is None:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            content = ""
    _fm, rest = _split_week_doc_frontmatter(content)
    id_key = "month" if cycle == "month" else "week"
    status_key = "month_status" if cycle == "month" else "week_status"
    key = key_str.strip()
    if not key and _fm and _fm.get(id_key):
        key = str(_fm.get(id_key)).strip()
    if not key:
        key = path.stem
    header = (
        "---\n"
        f"{id_key}: {key}\n"
        f"{status_key}: {status_n}\n"
        "---\n"
    )
    body = rest.lstrip("\n") if rest else (content or "")
    if _fm is None:
        body = content or ""
    text = header + (body if body.startswith("#") or body.startswith("---") else body)
    if not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def write_week_status(
    path: Path,
    status: str,
    *,
    week_key_str: str = "",
    content: str | None = None,
) -> str:
    """Write/replace document-level week_status; return full file text written."""
    return write_cycle_status(
        path,
        status,
        key_str=week_key_str,
        content=content,
        cycle="week",
    )


def resolve_weekly_path(hermes_home: Path, week_key_str: str) -> Path | None:
    """Return canonical week path when present (after migrate, else legacy)."""
    parsed = parse_week_key(week_key_str)
    if parsed is None:
        return None
    year, week = parsed
    draft = weekly_staging_path(hermes_home, year, week)
    if draft.exists():
        return draft
    reviewed = weekly_reviewed_path(hermes_home, year, week)
    if reviewed.exists():
        return reviewed
    return None


def week_has_report(hermes_home: Path, year: int, week: int) -> bool:
    """True when the canonical (or legacy reviewed) weekly file exists."""
    return week_file_path(hermes_home, year, week).exists() or weekly_reviewed_path(
        hermes_home, year, week
    ).exists()


def week_is_reviewed(hermes_home: Path, year: int, week: int) -> bool:
    """True when week is closed (week_status reviewed or legacy-only reviewed file)."""
    path = week_file_path(hermes_home, year, week)
    if path.exists():
        status = read_week_status(path)
        if status == WEEK_STATUS_REVIEWED:
            return True
        if status == WEEK_STATUS_PENDING:
            return False
        # Missing header on canonical draft: treat as open/pending.
        return False
    return weekly_reviewed_path(hermes_home, year, week).exists()


def week_blocks_backlog_regenerate(hermes_home: Path, year: int, week: int) -> bool:
    """True when backlog must not recreate this week (closed or legacy reviewed file)."""
    if week_is_reviewed(hermes_home, year, week):
        return True
    return weekly_reviewed_path(hermes_home, year, week).exists()


def mark_week_reviewed(hermes_home: Path, week_key_str: str) -> Path | None:
    """Set ``week_status: reviewed`` in place on the canonical week file.

    Never creates ``YYYY-Www reviewed.md``. Migrates legacy reviewed sibling
    into the canonical path when needed.
    """
    parsed = parse_week_key(week_key_str)
    if parsed is None:
        return None
    year, week = parsed
    key = week_key(year, week)
    canonical = week_file_path(hermes_home, year, week)
    legacy = weekly_reviewed_path(hermes_home, year, week)

    if not canonical.exists() and legacy.exists():
        text = legacy.read_text(encoding="utf-8")
        write_week_status(canonical, WEEK_STATUS_REVIEWED, week_key_str=key, content=text)
        try:
            legacy.unlink()
        except OSError:
            pass
        return canonical

    if not canonical.exists():
        return None

    write_week_status(
        canonical,
        WEEK_STATUS_REVIEWED,
        week_key_str=key,
        content=canonical.read_text(encoding="utf-8"),
    )
    if legacy.exists():
        try:
            legacy.unlink()
        except OSError:
            pass
    return canonical


def unmark_week_reviewed(hermes_home: Path, week_key_str: str) -> Path | None:
    """Set ``week_status: pending`` in place (reopen)."""
    parsed = parse_week_key(week_key_str)
    if parsed is None:
        return None
    year, week = parsed
    key = week_key(year, week)
    migrate_week_files(hermes_home, year, week)
    canonical = week_file_path(hermes_home, year, week)
    if not canonical.exists():
        return None
    write_week_status(
        canonical,
        WEEK_STATUS_PENDING,
        week_key_str=key,
        content=canonical.read_text(encoding="utf-8"),
    )
    return canonical


def migrate_week_files(hermes_home: Path, year: int, week: int) -> Path | None:
    """Ensure at most one file: canonical ``YYYY-Www.md`` with week_status."""
    key = week_key(year, week)
    canonical = week_file_path(hermes_home, year, week)
    legacy = weekly_reviewed_path(hermes_home, year, week)

    if canonical.exists() and legacy.exists():
        # Prefer legacy reviewed content as closed.
        text = legacy.read_text(encoding="utf-8")
        write_week_status(canonical, WEEK_STATUS_REVIEWED, week_key_str=key, content=text)
        try:
            legacy.unlink()
        except OSError:
            pass
        return canonical

    if legacy.exists() and not canonical.exists():
        text = legacy.read_text(encoding="utf-8")
        write_week_status(canonical, WEEK_STATUS_REVIEWED, week_key_str=key, content=text)
        try:
            legacy.unlink()
        except OSError:
            pass
        return canonical

    if canonical.exists():
        status = read_week_status(canonical)
        if status is None:
            write_week_status(
                canonical,
                WEEK_STATUS_PENDING,
                week_key_str=key,
                content=canonical.read_text(encoding="utf-8"),
            )
        return canonical
    return None


def migrate_all_weekly_files(hermes_home: Path) -> list[str]:
    """Migrate every week under staging/weekly; return list of week keys touched."""
    root = weekly_staging_dir(hermes_home)
    if not root.is_dir():
        return []
    touched: list[str] = []
    seen: set[tuple[int, int]] = set()
    for path in sorted(root.glob("*.md")):
        parsed = parse_week_key(path.stem.replace(" reviewed", ""))
        if parsed is None:
            # stem may be "2026-W32 reviewed"
            parsed = parse_week_key(path.stem)
        if parsed is None:
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        year, week = parsed
        migrate_week_files(hermes_home, year, week)
        touched.append(week_key(year, week))
    return touched


def tidy_decisions_path(hermes_home: Path, week_key_str: str) -> Path:
    return hermes_home / "memories" / "staging" / f".tidy-decisions-{week_key_str}.json"


def _load_digest_helpers() -> Any:
    """Load sibling digest.py so staging patches follow the live MyMemory pack."""
    pack = Path(__file__).resolve().parent
    digest_path = pack / "digest" / "digest.py"
    spec = importlib.util.spec_from_file_location("memory_digest_staging_patch", digest_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("MyMemory digest.py not loadable")
    mod_name = "memory_digest_staging_patch"
    digest = importlib.util.module_from_spec(spec)
    pack_str = str(pack)
    if pack_str not in sys.path:
        sys.path.insert(0, pack_str)
    sys.modules[mod_name] = digest
    spec.loader.exec_module(digest)
    return digest


def patch_daily_block_status(
    hermes_home: Path,
    block_id: str,
    *,
    status: str,
    timestamp_field: str,
    timestamp_value: str | None = None,
    body: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> bool:
    """Patch one staging block's status across daily files. Returns True if found.

    When ``body`` is set, replace that block's body in the same file walk.
    When omitted (None), existing body is preserved — tidy/gate/reopen callers
    keep identical behavior. Optional ``extra_fields`` merge into frontmatter in
    the same rewrite so contradiction metadata cannot desync from status.
    A sibling tempfile plus ``os.replace`` keeps a failed write from truncating
    the live daily file.
    """
    block_id = block_id.strip()
    if not block_id:
        return False

    digest = _load_digest_helpers()
    daily_dir = daily_staging_dir(hermes_home)
    files = iter_daily_staging_files(daily_dir)
    ts = timestamp_value or hermes_local_now().isoformat()

    for path in files:
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            continue

        _fences, wrap_phrase = digest.split_daily_wrapup(original)
        del _fences
        rendered_blocks: list[str] = []
        replaced = False
        for _line_no, raw_frontmatter, block_body in digest._frontmatter_blocks(original):
            try:
                block_parsed = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(block_parsed, dict):
                continue
            if str(block_parsed.get("id", "")).strip() != block_id:
                rendered_blocks.append(digest._render_digest_block(block_parsed, block_body))
                continue
            block_parsed["status"] = status
            block_parsed[timestamp_field] = ts
            if extra_fields:
                for key, value in extra_fields.items():
                    block_parsed[key] = value
            out_body = block_body if body is None else body
            rendered_blocks.append(digest._render_digest_block(block_parsed, out_body))
            replaced = True

        if replaced:
            payload = digest.join_daily_wrapup(
                "\n\n".join(rendered_blocks).rstrip() + "\n",
                wrap_phrase,
            )
            tmp = path.with_name(path.name + ".tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return False
            return True

    return False


_VALID_TO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def patch_daily_block_valid_to(
    hermes_home: Path,
    block_id: str,
    *,
    valid_to: str,
    timestamp_field: str = "updated_at",
) -> bool:
    """Set frontmatter valid_to for one daily block; preserve body and other keys.

    Returns True if found and written. valid_to must be YYYY-MM-DD or 'open'.
    """
    block_id = block_id.strip()
    if not block_id:
        return False

    valid_to = (valid_to or "").strip()
    if valid_to != "open" and not _VALID_TO_RE.match(valid_to):
        raise ValueError(f"valid_to must be YYYY-MM-DD or 'open', got {valid_to!r}")

    digest = _load_digest_helpers()
    daily_dir = daily_staging_dir(hermes_home)
    files = iter_daily_staging_files(daily_dir)
    ts = hermes_local_now().isoformat()

    for path in files:
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            continue

        _fences, wrap_phrase = digest.split_daily_wrapup(original)
        del _fences
        rendered_blocks: list[str] = []
        replaced = False
        for _line_no, raw_frontmatter, block_body in digest._frontmatter_blocks(original):
            try:
                block_parsed = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(block_parsed, dict):
                continue
            if str(block_parsed.get("id", "")).strip() != block_id:
                rendered_blocks.append(digest._render_digest_block(block_parsed, block_body))
                continue
            block_parsed["valid_to"] = valid_to
            block_parsed[timestamp_field] = ts
            rendered_blocks.append(digest._render_digest_block(block_parsed, block_body))
            replaced = True

        if replaced:
            path.write_text(
                digest.join_daily_wrapup(
                    "\n\n".join(rendered_blocks).rstrip() + "\n",
                    wrap_phrase,
                ),
                encoding="utf-8",
            )
            return True

    return False


def remove_daily_block(hermes_home: Path, block_id: str) -> dict[str, Any] | None:
    """Hard-remove one staging block from its daily file.

    Returns a snapshot for undo (daily_date, before_status, before_body,
    before_rendered) or None if the block was not found.
    """
    block_id = block_id.strip()
    if not block_id:
        return None

    digest = _load_digest_helpers()
    daily_dir = daily_staging_dir(hermes_home)
    for path in iter_daily_staging_files(daily_dir):
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            continue

        _fences, wrap_phrase = digest.split_daily_wrapup(original)
        del _fences
        kept: list[str] = []
        snapshot: dict[str, Any] | None = None
        block_index = 0
        for _line_no, raw_frontmatter, block_body in digest._frontmatter_blocks(original):
            try:
                block_parsed = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(block_parsed, dict):
                continue
            if str(block_parsed.get("id", "")).strip() != block_id:
                kept.append(digest._render_digest_block(block_parsed, block_body))
                block_index += 1
                continue
            snapshot = {
                "block_id": block_id,
                "daily_date": path.stem,
                "before_status": str(block_parsed.get("status") or "candidate").strip()
                or "candidate",
                "before_body": block_body,
                "before_rendered": digest._render_digest_block(block_parsed, block_body),
                "block_index": block_index,
            }

        if snapshot is None:
            continue
        if kept:
            path.write_text(
                digest.join_daily_wrapup(
                    "\n\n".join(kept).rstrip() + "\n",
                    wrap_phrase,
                ),
                encoding="utf-8",
            )
        else:
            # Unlink empty daily — do not leave a 0-byte stub that fakes sources.
            path.unlink(missing_ok=True)
        return snapshot

    return None


def restore_daily_block(
    hermes_home: Path,
    *,
    daily_date: str,
    before_rendered: str,
    block_index: int | None = None,
) -> bool:
    """Restore a hard-deleted block to ``YYYY-MM-DD.md`` at its original index.

    ``block_index`` is the 0-based position among valid frontmatter blocks at
    delete time. Missing/invalid index falls back to append (legacy ledger).
    """
    stem = str(daily_date or "").strip()
    rendered = str(before_rendered or "").strip()
    if not stem or not rendered or not _is_daily_date_stem(stem):
        return False
    daily_dir = daily_staging_dir(hermes_home)
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / daily_staging_name(stem)
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return False

    digest = _load_digest_helpers()
    _fences, wrap_phrase = digest.split_daily_wrapup(existing)
    del _fences
    blocks: list[str] = []
    for _line_no, raw_frontmatter, block_body in digest._frontmatter_blocks(existing):
        try:
            block_parsed = yaml.safe_load(raw_frontmatter)
        except yaml.YAMLError:
            continue
        if not isinstance(block_parsed, dict):
            continue
        blocks.append(digest._render_digest_block(block_parsed, block_body))

    idx: int | None
    if block_index is None:
        idx = None
    else:
        try:
            idx = int(block_index)
        except (TypeError, ValueError):
            idx = None
        if idx is not None and idx < 0:
            idx = None

    if idx is None:
        blocks.append(rendered)
    else:
        if idx > len(blocks):
            idx = len(blocks)
        blocks.insert(idx, rendered)

    if not blocks:
        path.write_text(
            digest.join_daily_wrapup(rendered.rstrip() + "\n", wrap_phrase),
            encoding="utf-8",
        )
        return True
    path.write_text(
        digest.join_daily_wrapup("\n\n".join(blocks).rstrip() + "\n", wrap_phrase),
        encoding="utf-8",
    )
    return True


def _is_daily_date_stem(stem: str) -> bool:
    try:
        date.fromisoformat(stem)
        return True
    except ValueError:
        return False


def migrate_legacy_daily_yaml(daily_dir: Path) -> list[str]:
    """Rename or merge legacy ``YYYY-MM-DD.yaml`` into ``YYYY-MM-DD.md``."""
    if not daily_dir.is_dir():
        return []

    migrated: list[str] = []
    for yaml_path in sorted(daily_dir.glob(f"*{LEGACY_DAILY_STAGING_SUFFIX}")):
        if yaml_path.name.startswith("."):
            continue
        stem = yaml_path.stem
        if not _is_daily_date_stem(stem):
            continue

        md_path = daily_dir / daily_staging_name(stem)
        yaml_text = yaml_path.read_text(encoding="utf-8").strip()

        if md_path.exists():
            if yaml_text:
                md_text = md_path.read_text(encoding="utf-8")
                prefix = "\n\n" if md_text.strip() else ""
                md_path.write_text(md_text.rstrip() + prefix + yaml_text + "\n", encoding="utf-8")
            yaml_path.unlink()
        else:
            md_path.write_text((yaml_text + "\n") if yaml_text else "", encoding="utf-8")
            yaml_path.unlink()

        migrated.append(str(md_path))

    return migrated


def iter_daily_staging_files(daily_dir: Path, *, migrate_legacy: bool = True) -> list[Path]:
    """Return dated daily staging ``.md`` paths (newest sort order preserved)."""
    if migrate_legacy:
        migrate_legacy_daily_yaml(daily_dir)
    if not daily_dir.is_dir():
        return []

    paths: list[Path] = []
    for path in sorted(daily_dir.glob(f"*{DAILY_STAGING_SUFFIX}")):
        if path.name.startswith("."):
            continue
        if not _is_daily_date_stem(path.stem):
            continue
        paths.append(path)
    return paths


def iter_daily_pending_weekly_review(
    hermes_home: Path, *, migrate_legacy: bool = True
) -> list[Path]:
    """Return daily staging files whose ISO week has no weekly review file yet."""
    daily_dir = daily_staging_dir(hermes_home)
    pending: list[Path] = []
    for path in iter_daily_staging_files(daily_dir, migrate_legacy=migrate_legacy):
        item_date = date.fromisoformat(path.stem)
        iso = item_date.isocalendar()
        if week_has_report(hermes_home, iso.year, iso.week):
            continue
        pending.append(path)
    return pending
