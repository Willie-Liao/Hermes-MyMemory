"""Offline stamp of user/assistant/generated clocks from state.db (no LLM)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import load_plugin_module


def _load_digest():
    return load_plugin_module("digest.py", "memory_digest_clock_backfill_test")


def _write_db(home: Path, rows: list[tuple]) -> None:
    db = home / "state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, timestamp REAL, active INTEGER)"
    )
    conn.executemany(
        "INSERT INTO messages (id, session_id, role, content, timestamp, active) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_backfill_uses_state_db_window_and_preserves_wrapup(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "_hermes_home", lambda: tmp_path)
    tz = timezone(timedelta(hours=8))
    user_ts = datetime(2026, 8, 22, 16, 1, 12, tzinfo=tz).timestamp()
    asst_ts = datetime(2026, 8, 22, 17, 10, 44, tzinfo=tz).timestamp()
    _write_db(
        tmp_path,
        [
            (10, "sess-a", "user", "hi", user_ts, 1),
            (20, "sess-a", "assistant", "ok", asst_ts, 1),
        ],
    )
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    path = daily / "2026-08-22.md"
    path.write_text(
        "---\n"
        "id: mem-2026-08-22-fact-1\n"
        "type: fact\n"
        "entity: Topic\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session sess-a#10-20]\n"
        "---\n"
        "Factual: an observation.\n"
        "\n"
        "## Day wrap-up\n"
        "- Catalog bullet stays.\n",
        encoding="utf-8",
    )
    n = digest.backfill_daily_file_clocks(path)
    assert n == 1
    text = path.read_text(encoding="utf-8")
    assert "user_message_at:" in text
    assert "2026-08-22T16:01:12+08:00" in text
    assert "assistant_response_at:" in text
    assert "2026-08-22T17:10:44+08:00" in text
    assert "generated_at:" in text
    assert "## Day wrap-up" in text
    assert "Catalog bullet stays." in text
    assert digest.backfill_daily_file_clocks(path) == 0


def test_backfill_falls_back_to_civil_noon_when_db_misses(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "_hermes_home", lambda: tmp_path)
    _write_db(tmp_path, [])
    daily = tmp_path / "daily"
    daily.mkdir()
    path = daily / "2026-08-01.md"
    path.write_text(
        "---\n"
        "id: mem-2026-08-01-fact-1\n"
        "type: fact\n"
        "entity: Topic\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session missing#1-2]\n"
        "---\n"
        "Factual: leftover card.\n",
        encoding="utf-8",
    )
    assert digest.backfill_daily_file_clocks(path) == 1
    text = path.read_text(encoding="utf-8")
    assert "T12:00:00" in text
    assert "2026-08-01T12:00:00" in text


def test_backfill_uses_session_day_when_cited_ids_missing(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "_hermes_home", lambda: tmp_path)
    tz = timezone(timedelta(hours=8))
    user_ts = datetime(2026, 8, 15, 9, 30, 0, tzinfo=tz).timestamp()
    asst_ts = datetime(2026, 8, 15, 9, 45, 0, tzinfo=tz).timestamp()
    _write_db(
        tmp_path,
        [
            (5, "sess-b", "user", "later ids missing", user_ts, 1),
            (6, "sess-b", "assistant", "reply", asst_ts, 1),
        ],
    )
    daily = tmp_path / "daily"
    daily.mkdir()
    path = daily / "2026-08-15.md"
    path.write_text(
        "---\n"
        "id: mem-2026-08-15-fact-1\n"
        "type: fact\n"
        "entity: Topic\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session sess-b#900-910]\n"
        "---\n"
        "Factual: cited ids not in this db.\n",
        encoding="utf-8",
    )
    assert digest.backfill_daily_file_clocks(path) == 1
    text = path.read_text(encoding="utf-8")
    assert "2026-08-15T09:30:00+08:00" in text
    assert "2026-08-15T09:45:00+08:00" in text
