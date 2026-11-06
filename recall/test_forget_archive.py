from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

from recall.forget import archive_record, write_archive
from recall.ids import BlockRecord
from recall.strength import should_archive_body, should_drop_from_prefetch


def test_forget_never_deletes_and_skips_decisions(tmp_path: Path):
    daily = tmp_path / "daily"
    daily.mkdir()
    rec = BlockRecord(
        block_id="mem-old-fact",
        path=daily / "2025-01-01.md",
        parsed={
            "id": "mem-old-fact",
            "type": "fact",
            "importance": 2,
            "strength": 0.1,
            "first_seen": "2025-01-01",
        },
        body="secret body",
        day="2025-01-01",
        item_type="fact",
        entity="x",
        related=[],
    )
    rec.path.write_text("id: mem-old-fact\nsecret body\n", encoding="utf-8")
    assert should_drop_from_prefetch(rec.parsed)
    assert should_archive_body(rec.parsed, now=date(2026, 8, 19))
    path = write_archive(rec, staging=tmp_path, now=date(2026, 8, 19))
    assert path is not None
    assert rec.path.is_file()
    decision = dict(rec.parsed)
    decision["type"] = "decision"
    rec2 = BlockRecord(
        block_id="mem-old-dec",
        path=rec.path,
        parsed=decision,
        body="d",
        day="2025-01-01",
        item_type="decision",
        entity="x",
    )
    assert archive_record(rec2, now=date(2026, 8, 19)) is None
    src = Path(__file__).parent
    blob = "\n".join(
        p.read_text(encoding="utf-8")
        for p in src.glob("*.py")
        if p.name != "test_forget_archive.py"
    )
    assert "os.remove" not in blob
    assert "shutil.rmtree" not in blob
    with gzip.open(path, "rb") as fh:
        blob = fh.read().decode("utf-8")
    assert "secret body" in blob
    assert "mem-old-fact" in blob
