"""FTS5 BM25 over (entity, body) so identifier queries hit without a Python ranker.

Default unicode61 misses 'semicolon' against 'semicolons'; porter unicode61 is
the measured difference between zero hits and the 08-12 seed.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from .ids import BlockIndex, BlockRecord, staging_root

TOKENIZER = "porter unicode61"
TOKENIZER_PLAIN = "unicode61"


def lexical_db_path(staging: Path | None = None) -> Path:
    return staging_root(staging) / ".lexical.db"


def _create_table(con: sqlite3.Connection, tokenizer: str) -> None:
    con.execute("DROP TABLE IF EXISTS blocks")
    con.execute(
        f"CREATE VIRTUAL TABLE blocks USING fts5("
        f"entity, body, id UNINDEXED, day UNINDEXED, tokenize='{tokenizer}')"
    )


def rebuild_lexical(
    staging: Path | None = None,
    *,
    tokenizer: str = TOKENIZER,
    records: list[BlockRecord] | None = None,
    db_path: Path | None = None,
) -> float:
    """Rebuild the FTS5 table; returns wall-ms so the <100ms bound stays testable.

    Skip ``status: rejected`` cards unless the caller passed an explicit record
    list, so a closed contradiction cannot win BM25 after a daily-file patch.
    """
    root = staging_root(staging)
    path = db_path or lexical_db_path(root)
    if records is None:
        rows = [
            rec
            for rec in BlockIndex(root).records
            if str(rec.parsed.get("status") or "").strip() != "rejected"
        ]
    else:
        rows = records
    t0 = time.perf_counter()
    con = sqlite3.connect(str(path))
    try:
        _create_table(con, tokenizer)
        con.executemany(
            "INSERT INTO blocks(entity, body, id, day) VALUES (?, ?, ?, ?)",
            [
                (rec.entity, rec.body, rec.block_id, rec.day)
                for rec in rows
            ],
        )
        con.commit()
    finally:
        con.close()
    return (time.perf_counter() - t0) * 1000.0


_STOP = {
    "the", "and", "what", "else", "did", "we", "already", "about", "how",
    "this", "that", "with", "from", "were", "was", "for", "you", "our",
}


def _match_variants(query: str) -> list[str]:
    """Prefer exact MATCH, then token OR — sentence AND is why P3 returned miss.

    FTS5 AND on 'Did we already fix the semicolon digest bug' needs every
    stopword in one card; OR of content tokens still ranks the 08-12 seed.
    """
    q = str(query or "").strip()
    if not q:
        return []
    variants = [q]
    tokens = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", q)
    keep = [t for t in tokens if t.casefold() not in _STOP and len(t) > 2]
    if keep:
        or_q = " OR ".join(keep)
        if or_q not in variants:
            variants.append(or_q)
    return variants


def search_lexical(
    query: str,
    *,
    k: int = 8,
    staging: Path | None = None,
    tokenizer: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, object]]:
    """Rank by FTS5 bm25() (lower is better); recency breaks ties."""
    q = str(query or "").strip()
    if not q:
        return []
    root = staging_root(staging)
    path = db_path or lexical_db_path(root)
    if tokenizer and tokenizer != TOKENIZER:
        tmp = path.with_name(path.name + f".{tokenizer}")
        rebuild_lexical(root, tokenizer=tokenizer, db_path=tmp)
        path = tmp
    elif not path.is_file():
        rebuild_lexical(root, db_path=path)
    con = sqlite3.connect(str(path))
    rows: list = []
    try:
        sql = (
            "SELECT id, day, entity, bm25(blocks) AS rank "
            "FROM blocks WHERE blocks MATCH ? "
            "ORDER BY rank ASC, day DESC LIMIT ?"
        )
        for variant in _match_variants(q):
            try:
                rows = con.execute(sql, (variant, int(k))).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                break
    finally:
        con.close()
    out: list[dict[str, object]] = []
    for i, (block_id, day, entity, rank) in enumerate(rows, start=1):
        out.append(
            {
                "id": block_id,
                "day": day,
                "entity": entity,
                "bm25": float(rank) if rank is not None else 0.0,
                "rank": i,
                "channel": "fts5",
            }
        )
    return out
