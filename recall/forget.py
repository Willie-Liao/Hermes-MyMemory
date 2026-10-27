"""L2 body archive that keeps id/entity/edges findable. Never delete a node.

Hard-delete would break neighbor coupling the same way dangling weekly cites do.
"""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path
from typing import Any

from .ids import BlockRecord, iso_week, staging_root
from .strength import should_archive_body

ONE_LINE_KEY = "one_line"


def archive_record(rec: BlockRecord, *, now: date | None = None) -> dict[str, Any] | None:
    """If L2 gates pass, return the jsonl object to gzip; caller keeps the stub card."""
    if not should_archive_body(rec.parsed, now=now):
        return None
    return {
        "id": rec.block_id,
        "entity": rec.entity,
        "type": rec.item_type,
        "day": rec.day,
        "week": iso_week(rec.day),
        "body": rec.body,
        "path": str(rec.path),
    }


def write_archive(
    rec: BlockRecord,
    *,
    staging: Path | None = None,
    now: date | None = None,
) -> Path | None:
    """Append body to archive/{week}.jsonl.gz; does not unlink the daily file."""
    payload = archive_record(rec, now=now)
    if payload is None:
        return None
    root = staging_root(staging)
    week = iso_week(rec.day) or "unknown"
    dest_dir = root / "archive"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{week}.jsonl.gz"
    with gzip.open(path, "ab") as fh:
        fh.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def stub_body(rec: BlockRecord) -> str:
    """Indexed remnant after L2: id/entity/one-line stay, prose lives in archive."""
    line = " ".join((rec.body or "").split())[:120]
    return f"(archived) {line}"
