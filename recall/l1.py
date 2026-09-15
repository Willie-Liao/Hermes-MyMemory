"""L1 transcript channel: exact words from state.db when digest cards missed them.

Must stay read-only and exclude role=tool — tool payloads swamp BM25 with file
contents that were never conversation. Date window comes from valid_from because
session_id spans many days.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ids import hermes_home, parse_iso_datetime


def _ro_connect(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def search_l1(
    query: str,
    *,
    valid_from: str | date | None = None,
    k: int = 8,
    home: Path | None = None,
    window_days: int = 3,
    time_from: str | None = None,
    time_to: str | None = None,
) -> list[dict[str, Any]]:
    """FTS5 over messages_fts joined to messages; never opens the DB writable.

    Explicit time_from/time_to override the valid_from ±window_days fallback so
    approximate-time recall does not reopen a three-day default around a miss.
    """
    q = str(query or "").strip()
    if not q:
        return []
    db = hermes_home(home) / "state.db"
    if not db.is_file():
        return []
    start_ts = end_ts = None
    lo = parse_iso_datetime(time_from, end_of_day=False) if time_from else None
    hi = parse_iso_datetime(time_to, end_of_day=True) if time_to else None
    if lo is not None and hi is not None:
        if hi < lo:
            lo, hi = hi, lo
        start_ts = lo.timestamp()
        end_ts = (hi + timedelta(microseconds=1)).timestamp()
    else:
        day: date | None
        if isinstance(valid_from, date):
            day = valid_from
        elif valid_from:
            try:
                day = date.fromisoformat(str(valid_from)[:10])
            except ValueError:
                day = None
        else:
            day = None
        if day is not None:
            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - timedelta(
                days=window_days
            )
            end = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(
                days=window_days + 1
            )
            start_ts = start.timestamp()
            end_ts = end.timestamp()
    con = _ro_connect(db)
    try:
        con.execute("PRAGMA query_only=ON")
        sql = (
            "SELECT m.id, m.role, m.timestamp, m.content "
            "FROM messages_fts f JOIN messages m ON m.id = f.rowid "
            "WHERE messages_fts MATCH ? AND m.role != 'tool'"
        )
        params: list[Any] = [q]
        if start_ts is not None and end_ts is not None:
            sql += " AND m.timestamp >= ? AND m.timestamp < ?"
            params.extend([start_ts, end_ts])
        sql += " LIMIT ?"
        params.append(int(k) * 4)
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    hits: list[dict[str, Any]] = []
    for mid, role, ts, content in rows:
        if str(role) == "tool":
            continue
        hits.append(
            {
                "id": mid,
                "role": role,
                "timestamp": ts,
                "content": content or "",
                "channel": "l1",
            }
        )
        if len(hits) >= k:
            break
    return hits
