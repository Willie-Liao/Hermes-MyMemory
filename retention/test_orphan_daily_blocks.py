"""Orphan daily blocks: sole dead session: source → purge + queue."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


def _load_retention():
    plugins = Path(__file__).resolve().parents[1]
    if str(plugins) not in sys.path:
        sys.path.insert(0, str(plugins))
    path = Path(__file__).with_name("retention.py")
    spec = importlib.util.spec_from_file_location("memory_retention_orphan", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_live_db(home: Path, ids: list[str]) -> None:
    con = sqlite3.connect(home / "state.db")
    con.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, "
        "started_at REAL NOT NULL)"
    )
    for sid in ids:
        con.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, 'test', 0.0)",
            (sid,),
        )
    con.commit()
    con.close()


def _write_daily(home: Path, name: str, body: str) -> Path:
    daily = home / "memories" / "staging" / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    path = daily / name
    path.write_text(body, encoding="utf-8")
    return path


def test_list_live_session_ids_reads_state_db(tmp_path):
    ret = _load_retention()
    _seed_live_db(tmp_path, ["live-a", "live-b"])
    assert ret.list_live_session_ids(tmp_path) == {"live-a", "live-b"}


def test_list_live_session_ids_missing_db_returns_none(tmp_path):
    ret = _load_retention()
    assert ret.list_live_session_ids(tmp_path) is None


def test_purge_orphan_single_dead_session(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    _seed_live_db(tmp_path, ["live-a"])
    _write_daily(
        tmp_path,
        "2026-07-10.md",
        "---\n"
        "id: mem-orphan-1\n"
        "type: fact\n"
        "entity: X\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [\"session:dead-x\"]\n"
        "---\n"
        "orphan body\n\n"
        "---\n"
        "id: mem-keep-multi\n"
        "type: fact\n"
        "entity: Y\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [\"session:dead-x\", zotero:ABC]\n"
        "---\n"
        "multi source keep\n\n"
        "---\n"
        "id: mem-keep-live\n"
        "type: fact\n"
        "entity: Z\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session live-a]\n"
        "---\n"
        "live keep\n",
    )

    n = ret.purge_orphan_daily_blocks()
    assert n == 1
    text = (tmp_path / "memories" / "staging" / "daily" / "2026-07-10.md").read_text(
        encoding="utf-8"
    )
    assert "mem-orphan-1" not in text
    assert "mem-keep-multi" in text
    assert "mem-keep-live" in text

    queue = ret._load_queue_map()
    key = ret._block_queue_key(
        "memories/staging/daily/2026-07-10.md", "mem-orphan-1"
    )
    assert key in queue
    assert queue[key]["status"] == "purged"
    assert queue[key]["session_id"] == "dead-x"


def test_purge_orphan_fail_closed_no_db(tmp_path, monkeypatch):
    ret = _load_retention()
    monkeypatch.setattr(ret, "get_hermes_home", lambda: tmp_path)
    _write_daily(
        tmp_path,
        "2026-07-10.md",
        "---\n"
        "id: mem-orphan-1\n"
        "type: fact\n"
        "entity: X\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [\"session:dead-x\"]\n"
        "---\n"
        "orphan body\n",
    )
    assert ret.purge_orphan_daily_blocks() == 0
    text = (tmp_path / "memories" / "staging" / "daily" / "2026-07-10.md").read_text(
        encoding="utf-8"
    )
    assert "mem-orphan-1" in text
