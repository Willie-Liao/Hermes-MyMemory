"""7-day retention sweep — snapshots, staging daily files, .archive/, and digest-state.

Runs on plugin load and session lifecycle hooks (CLI + gateway).
Computes age when the hook fires (not 24/7 cron).

Also: ``purge_orphan_daily_blocks`` — before weekly generate, auto-remove daily
blocks whose sole ``session:`` source is missing from ``state.db``.

Purge rules:
  - approved_for_removal → delete on next sweep (no git gate)
  - active + age >= 7d → flag over_retention for weekly review (no auto-delete)
  - .digest-state.json session maps: drop YYYYMMDD_ keys age >= 7d unless
    that session id is still in state.db (live bookmark must not rewind)
  - orphan daily_block (single dead session:) → remove + queue as purged
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import shutil
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
_plugins_root_str = str(_plugins_root)
if _plugins_root_str not in sys.path:
    sys.path.insert(0, _plugins_root_str)

from memory_staging import (
    daily_staging_dir,
    hermes_local_today,
    iter_daily_staging_files,
    remove_daily_block,
)

logger = logging.getLogger("plugins.memory-retention")

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover
    import os

    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val).resolve() if val else (Path.home() / ".hermes").resolve()


RETENTION_DAYS = 7

_SESSION_ID_DATE_RE = re.compile(r"^(\d{8})_")

_sweep_lock = threading.Lock()


def _hermes_home() -> Path:
    return get_hermes_home()


def _snapshots_dir() -> Path:
    return _hermes_home() / "state-snapshots"


def _registry_file() -> Path:
    return _hermes_home() / "memories" / "staging" / "snapshot-registry.yaml"


def _retention_queue() -> Path:
    return _hermes_home() / "memories" / "staging" / "retention-queue.yaml"


def _staging_daily() -> Path:
    return daily_staging_dir(_hermes_home())


def _staging_weekly() -> Path:
    return _hermes_home() / "memories" / "staging" / "weekly"


def _archive_dir() -> Path:
    return _hermes_home() / "memories" / ".archive"


def _log_file() -> Path:
    return _hermes_home() / "logs" / "memory-retention.log"


def _log(msg: str) -> None:
    logger.info(msg)
    try:
        path = _log_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except OSError:
        pass


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _age_days(ts: datetime | None) -> float:
    if ts is None:
        return 0.0
    now = datetime.now(timezone.utc)
    return (now - ts).total_seconds() / 86400.0


def _file_age_days(path: Path) -> float:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return 0.0
    return _age_days(mtime)


def _load_yaml_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _log(f"yaml read failed {path}: {exc}")
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [row for row in data["items"] if isinstance(row, dict)]
    return []


def _save_yaml_list(path: Path, rows: list[dict[str, Any]], header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [header.rstrip(), ""] if header else []
    for row in rows:
        lines.append(yaml.safe_dump([row], allow_unicode=True, sort_keys=False).strip())
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _expand_path(raw: str) -> Path:
    return Path(raw.replace("~", str(Path.home()))).expanduser()


def _sync_snapshot_registry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {str(r.get("label", "")): r for r in rows if r.get("label")}
    snapshots = _snapshots_dir()
    if not snapshots.is_dir():
        return list(known.values())

    for child in sorted(snapshots.iterdir()):
        if not child.is_dir():
            continue
        label = child.name
        if label in known:
            continue
        created = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        known[label] = {
            "label": label,
            "path": f"~/.hermes/state-snapshots/{label}",
            "created_at": created.isoformat(),
            "reason": "discovered",
            "status": "active",
        }
        _log(f"registry sync: added snapshot {label}")

    return list(known.values())


def _purge_snapshot(row: dict[str, Any]) -> bool:
    label = str(row.get("label", ""))
    path = _expand_path(str(row.get("path", f"~/.hermes/state-snapshots/{label}")))
    if path.is_dir():
        try:
            shutil.rmtree(path)
            _log(f"purged snapshot {label}")
            return True
        except OSError as exc:
            _log(f"purge failed snapshot {label}: {exc}")
            return False
    _log(f"purged snapshot {label} (path already gone)")
    return True


def _sweep_snapshots() -> None:
    header = (
        "# State snapshot inventory for weekly review and 7-day retention.\n"
        "# Append a row on each `hermes backup --quick`. Purge triggers as soon as status = approved_for_removal.\n"
        "#\n"
        "# status: active | over_retention | approved_for_removal | purged"
    )
    registry = _registry_file()
    rows = _sync_snapshot_registry(_load_yaml_list(registry))
    changed = False

    for row in rows:
        status = str(row.get("status", "active"))
        created = _parse_ts(str(row.get("created_at", "")))
        age = _age_days(created)

        if status == "purged":
            continue

        if status == "approved_for_removal":
            if _purge_snapshot(row):
                row["status"] = "purged"
                row["purged_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
            continue

        if status == "active" and age >= RETENTION_DAYS:
            row["status"] = "over_retention"
            row["over_retention_since"] = datetime.now(timezone.utc).isoformat()
            _log(f"flagged over_retention snapshot {row.get('label')}")
            changed = True

    if changed or rows:
        _save_yaml_list(registry, rows, header=header)


def _queue_key(kind: str, path: str) -> str:
    return f"{kind}:{path}"


def _block_queue_key(rel_path: str, block_id: str) -> str:
    return f"daily_block:{rel_path}#{block_id}"


def _load_queue_map() -> dict[str, dict[str, Any]]:
    items = _load_yaml_list(_retention_queue())
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        kind = str(item.get("kind", ""))
        path = str(item.get("path", ""))
        if not path:
            continue
        if kind == "daily_block" and item.get("block_id"):
            key = _block_queue_key(path, str(item.get("block_id")))
        else:
            key = _queue_key(kind, path)
        out[key] = item
    return out


def _save_queue_map(queue: dict[str, dict[str, Any]]) -> None:
    header = (
        "# Retention queue — staging/archive past 7-day cap, plus purged orphan daily_block rows.\n"
        "# status: over_retention | approved_for_removal | purged\n"
        "# kind: daily | weekly | archive | daily_block"
    )
    rows = sorted(queue.values(), key=lambda r: (r.get("kind", ""), r.get("path", "")))
    _save_yaml_list(_retention_queue(), rows, header=header)


def _purge_path(path: Path, label: str) -> bool:
    if not path.exists():
        _log(f"purged {label} (path already gone)")
        return True
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        _log(f"purged {label}")
        return True
    except OSError as exc:
        _log(f"purge failed {label}: {exc}")
        return False


def _flag_or_purge_file(
    queue: dict[str, dict[str, Any]],
    *,
    kind: str,
    path: Path,
    rel_path: str,
    created_at: datetime,
) -> None:
    age = _age_days(created_at)
    if age < RETENTION_DAYS:
        return

    key = _queue_key(kind, rel_path)
    item = queue.get(key)
    if item is None:
        item = {
            "kind": kind,
            "path": rel_path,
            "created_at": created_at.isoformat(),
            "status": "over_retention",
            "flagged_at": datetime.now(timezone.utc).isoformat(),
        }
        queue[key] = item
        _log(f"flagged over_retention {kind} {rel_path}")
        return

    status = str(item.get("status", "over_retention"))
    if status == "purged":
        return
    if status == "approved_for_removal":
        # Never unlink daily/weekly digests from the sweep either — record only.
        if kind in {"daily", "weekly"}:
            item["status"] = "purged"
            item["purged_at"] = datetime.now(timezone.utc).isoformat()
            _log(f"purged retention record only ({kind}) {rel_path}")
        elif _purge_path(path, rel_path):
            item["status"] = "purged"
            item["purged_at"] = datetime.now(timezone.utc).isoformat()
    elif status == "active":
        item["status"] = "over_retention"


def _parse_archive_date(name: str) -> datetime | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", name)
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def _sweep_staging_and_archive() -> None:
    queue = _load_queue_map()

    daily = _staging_daily()
    for path in iter_daily_staging_files(daily):
        created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        rel = f"memories/staging/daily/{path.name}"
        _flag_or_purge_file(
            queue,
            kind="daily",
            path=path,
            rel_path=rel,
            created_at=created,
        )

    weekly = _staging_weekly()
    if weekly.is_dir():
        for path in sorted(weekly.glob("*.md")):
            if path.name.startswith("."):
                continue
            created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            rel = f"memories/staging/weekly/{path.name}"
            _flag_or_purge_file(
                queue,
                kind="weekly",
                path=path,
                rel_path=rel,
                created_at=created,
            )

    archive = _archive_dir()
    if archive.is_dir():
        for path in sorted(archive.iterdir()):
            if not path.is_dir():
                continue
            created = _parse_archive_date(path.name)
            if created is None:
                created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            rel = f"memories/.archive/{path.name}"
            _flag_or_purge_file(
                queue,
                kind="archive",
                path=path,
                rel_path=rel,
                created_at=created,
            )

    _save_queue_map(queue)


def _digest_state_path() -> Path:
    return _hermes_home() / "memories" / "staging" / ".digest-state.json"


def _session_id_date(sid: str):
    """Return date from ``YYYYMMDD_…`` session id, else None."""
    match = _SESSION_ID_DATE_RE.match(str(sid or ""))
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _sweep_digest_state() -> int:
    """Drop idle dated digest-state keys; never the bookmark of a still-open session.

    Session ids encode the *open* day (``20260811_…``). Age-only trim then
    deletes a live WeChat cursor after 7 days, so the next turn re-extracts
    old cards onto today. Skip keys (or nested ``session_id``) still in
    ``state.db``. Missing DB keeps age-only trim so tests and a broken
    store still drain dead maps.
    """
    path = _digest_state_path()
    if not path.is_file():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log(f"digest-state trim skipped (unreadable): {exc}")
        return 0
    if not isinstance(raw, dict):
        return 0

    today = hermes_local_today()
    cutoff = today - timedelta(days=RETENTION_DAYS)
    live_ids = list_live_session_ids(_hermes_home())
    removed = 0
    for _map_name, mapping in raw.items():
        if not isinstance(mapping, dict):
            continue
        dead = []
        for sid in list(mapping.keys()):
            opened = _session_id_date(sid)
            if opened is None or opened > cutoff:
                continue
            if live_ids is not None:
                nested = ""
                value = mapping.get(sid)
                if isinstance(value, dict):
                    nested = str(value.get("session_id") or "")
                if sid in live_ids or (nested and nested in live_ids):
                    continue
            dead.append(sid)
        for sid in dead:
            mapping.pop(sid, None)
            removed += 1

    if removed:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            _log(f"digest-state trim removed {removed} session key(s)")
        except OSError as exc:
            _log(f"digest-state trim save failed: {exc}")
            return 0
    return removed


def list_live_session_ids(hermes_home: Path) -> set[str] | None:
    """Read Hermes ``state.db`` session ids; ``None`` if DB missing/unreadable."""
    path = Path(hermes_home) / "state.db"
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT id FROM sessions").fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return None
    return {str(r[0]) for r in rows if r and r[0] is not None}


def _load_digest_frontmatter():
    path = Path(__file__).resolve().parent.parent / "digest" / "digest.py"
    spec = importlib.util.spec_from_file_location(
        "memory_digest_for_retention", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("memory-digest digest.py not loadable")
    mod_name = "memory_digest_for_retention"
    digest = importlib.util.module_from_spec(spec)
    if _plugins_root_str not in sys.path:
        sys.path.insert(0, _plugins_root_str)
    sys.modules[mod_name] = digest
    spec.loader.exec_module(digest)
    return digest


def _normalize_sources(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _sole_session_id(sources: list[str]) -> str | None:
    if len(sources) != 1:
        return None
    tag = sources[0]
    prefix = "session:"
    if not tag.casefold().startswith(prefix):
        return None
    sid = tag[len(prefix) :].strip()
    return sid or None


def purge_orphan_daily_blocks() -> int:
    """Remove daily blocks with a sole dead ``session:`` source; queue as purged.

    Fail closed when ``state.db`` is missing/unreadable. Returns purge count.
    """
    hermes_home = _hermes_home()
    try:
        live = list_live_session_ids(hermes_home)
    except Exception as exc:
        _log(f"orphan daily purge skipped (live sessions): {exc}")
        return 0
    if live is None:
        _log("orphan daily purge skipped (state.db unreadable)")
        return 0

    try:
        digest = _load_digest_frontmatter()
    except Exception as exc:
        _log(f"orphan daily purge skipped (digest helpers): {exc}")
        return 0

    orphans: list[tuple[str, str, str]] = []
    daily = _staging_daily()
    for path in iter_daily_staging_files(daily):
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = f"memories/staging/daily/{path.name}"
        for _line_no, raw_frontmatter, _body in digest._frontmatter_blocks(original):
            try:
                parsed = yaml.safe_load(raw_frontmatter)
            except yaml.YAMLError:
                continue
            if not isinstance(parsed, dict):
                continue
            block_id = str(parsed.get("id") or "").strip()
            if not block_id:
                continue
            sid = _sole_session_id(_normalize_sources(parsed.get("sources")))
            if sid is None or sid in live:
                continue
            orphans.append((rel, block_id, sid))

    if not orphans:
        return 0

    queue = _load_queue_map()
    purged = 0
    now = datetime.now(timezone.utc).isoformat()
    for rel, block_id, sid in orphans:
        snap = remove_daily_block(hermes_home, block_id)
        if snap is None:
            _log(f"orphan daily purge miss block_id={block_id}")
            continue
        key = _block_queue_key(rel, block_id)
        queue[key] = {
            "kind": "daily_block",
            "path": rel,
            "block_id": block_id,
            "session_id": sid,
            "status": "purged",
            "purged_at": now,
            "reason": "orphan_session_deleted",
        }
        purged += 1
        _log(f"purged orphan daily_block {block_id} session={sid}")

    if purged:
        _save_queue_map(queue)
    return purged


_QUEUE_PURGE_KINDS = frozenset({"daily", "weekly", "archive"})
# Digests stay on disk; Weekly Review only clears the retention-queue row.
# Archive dirs and state-snapshots are still removed from disk.
_QUEUE_RECORD_ONLY_KINDS = frozenset({"daily", "weekly"})

# Close-time log cleanup: months → approximate day cutoff (mtime).
LOG_PURGE_MONTH_DAYS: dict[int, int] = {
    1: 30,
    2: 60,
    3: 90,
    6: 182,  # half year
    12: 365,  # year
}


def _logs_dir() -> Path:
    return _hermes_home() / "logs"


def purge_old_logs(*, months: int) -> dict[str, Any]:
    """Delete files under ``~/.hermes/logs/`` whose mtime is older than *months*.

    Allowed months: 1, 2, 3, 6 (half year), 12 (year). Directories are kept
    (even if emptied). Skips missing logs dir. Returns ``purged_logs`` count.
    """
    try:
        months_i = int(months)
    except (TypeError, ValueError):
        return {"purged_logs": 0, "error": f"unsupported months: {months}"}
    days = LOG_PURGE_MONTH_DAYS.get(months_i)
    if days is None:
        return {"purged_logs": 0, "error": f"unsupported months: {months}"}

    root = _logs_dir()
    if not root.is_dir():
        return {"purged_logs": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    purged = 0
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_file():
            continue
        if path.name == ".DS_Store":
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError as exc:
            _log(f"purge logs skip {path}: {exc}")
            continue
        if mtime >= cutoff:
            continue
        try:
            path.unlink()
            purged += 1
            _log(f"purged log {path.relative_to(root)} (mtime={mtime.date()} >{months_i}mo)")
        except OSError as exc:
            _log(f"purge logs failed {path}: {exc}")

    return {"purged_logs": purged}


_SNAPSHOT_REGISTRY_HEADER = (
    "# State snapshot inventory for weekly review and 7-day retention.\n"
    "# Append a row on each `hermes backup --quick`. Purge triggers as soon as status = approved_for_removal.\n"
    "#\n"
    "# status: active | over_retention | approved_for_removal | purged"
)


def approve_and_purge_over_retention(
    *, queue: bool, snapshots: bool
) -> dict[str, int]:
    """Flip over_retention → approved_for_removal and purge (no full sweep).

    Only ``kind`` daily|weekly|archive queue rows and snapshot registry rows
    already marked ``over_retention`` are touched. Does not call
    ``run_retention_sweep`` (avoids flagging / digest trim side effects).

    Daily/weekly digest files are never deleted here — only their
    ``retention-queue.yaml`` rows are marked ``purged``. Snapshot cleanup
    still removes registry rows' corresponding ``state-snapshots/`` dirs.
    Archive queue rows still delete the archive path on disk.
    """
    purged_queue = 0
    purged_snapshots = 0

    if queue:
        qmap = _load_queue_map()
        changed = False
        for item in qmap.values():
            kind = str(item.get("kind") or "")
            if kind not in _QUEUE_PURGE_KINDS:
                continue
            if str(item.get("status") or "") != "over_retention":
                continue
            item["status"] = "approved_for_removal"
            changed = True

        for item in qmap.values():
            kind = str(item.get("kind") or "")
            if kind not in _QUEUE_PURGE_KINDS:
                continue
            if str(item.get("status") or "") != "approved_for_removal":
                continue
            rel = str(item.get("path") or "")
            if not rel:
                continue
            if kind in _QUEUE_RECORD_ONLY_KINDS:
                item["status"] = "purged"
                item["purged_at"] = datetime.now(timezone.utc).isoformat()
                purged_queue += 1
                changed = True
                _log(f"purged retention record only ({kind}) {rel}")
                continue
            path = _hermes_home() / rel
            if _purge_path(path, rel):
                item["status"] = "purged"
                item["purged_at"] = datetime.now(timezone.utc).isoformat()
                purged_queue += 1
                changed = True

        if changed:
            _save_queue_map(qmap)

    if snapshots:
        registry = _registry_file()
        rows = _load_yaml_list(registry)
        changed = False
        for row in rows:
            if str(row.get("status") or "") != "over_retention":
                continue
            row["status"] = "approved_for_removal"
            changed = True

        for row in rows:
            if str(row.get("status") or "") != "approved_for_removal":
                continue
            if _purge_snapshot(row):
                row["status"] = "purged"
                row["purged_at"] = datetime.now(timezone.utc).isoformat()
                purged_snapshots += 1
                changed = True

        if changed:
            _save_yaml_list(registry, rows, header=_SNAPSHOT_REGISTRY_HEADER)

    return {"purged_queue": purged_queue, "purged_snapshots": purged_snapshots}


def run_retention_sweep(reason: str = "manual") -> None:
    with _sweep_lock:
        _log(f"retention sweep start ({reason})")
        _sweep_snapshots()
        _sweep_staging_and_archive()
        _sweep_digest_state()
        _log(f"retention sweep done ({reason})")


def run_async(reason: str) -> None:
    thread = threading.Thread(
        target=run_retention_sweep,
        args=(reason,),
        name="memory-retention",
        daemon=True,
    )
    thread.start()
