from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from recall.l1 import search_l1


def test_l1_excludes_tools_and_finds_event_stage_regex(tmp_path: Path):
    """Search a fake state.db so L1 tests never open the live conversation store."""
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp REAL, active INTEGER)"
    )
    con.execute(
        "CREATE VIRTUAL TABLE messages_fts USING fts5(content, content=messages, content_rowid=id)"
    )
    ts = datetime(2026, 8, 12, tzinfo=timezone.utc).timestamp()
    rows = [
        (1, "user", "please compile _EVENT_STAGE_RE for wrap-up", ts, 1),
        (2, "assistant", "I will keep _EVENT_STAGE_RE in the worker", ts, 1),
        (3, "tool", "_EVENT_STAGE_RE dump from a tool payload", ts, 1),
    ]
    con.executemany(
        "INSERT INTO messages (id, role, content, timestamp, active) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    con.execute(
        "INSERT INTO messages_fts(rowid, content) SELECT id, content FROM messages"
    )
    con.commit()
    con.close()

    hits = search_l1("_EVENT_STAGE_RE", valid_from="2026-08-12", k=8, home=tmp_path)
    assert hits
    assert all(h["role"] != "tool" for h in hits)
    blob = "\n".join(str(h.get("content") or "") for h in hits[:5])
    assert "_EVENT_STAGE_RE" in blob
