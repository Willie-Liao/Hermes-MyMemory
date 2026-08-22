from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

_mymemory = Path(__file__).resolve().parent.parent
if str(_mymemory) not in sys.path:
    sys.path.insert(0, str(_mymemory))

_plugins_root = Path(__file__).resolve().parent.parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from memory_staging import hermes_local_now, hermes_local_today, hermes_local_today_str


from conftest import load_plugin_module


def _load_digest():
    return load_plugin_module("digest.py", "memory_digest_tier_test")


def _daily_dir(home: Path) -> Path:
    d = home / "memories" / "staging" / "daily"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_daily(home: Path, date_str: str, content: str) -> None:
    (_daily_dir(home) / f"{date_str}.md").write_text(content + "\n", encoding="utf-8")


def _today() -> str:
    return hermes_local_today_str()


def _days_ago(n: int) -> str:
    return (hermes_local_now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _block(
    *,
    block_id: str,
    body: str,
    entity: str = "Casey",
    involves: list[str] | None = None,
    related: list[str] | None = None,
    sources: str = "session abc-123",
    item_type: str = "fact",
) -> str:
    lines = ["---", f"id: {block_id}", f"type: {item_type}", f"entity: {entity}"]
    if involves:
        lines.append(f"involves: [{', '.join(involves)}]")
    if related:
        lines.append(f"related: [{', '.join(related)}]")
    lines += ["confidence: high", "status: candidate", f"sources: [{sources}]", "---", body]
    return "\n".join(lines)


def test_daily_files_last_seven(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "hermes_local_today", lambda: date(2026, 6, 23))
    _write_daily(tmp_path, "2026-06-22", _block(block_id="mem-t1", body="tier1"))
    _write_daily(tmp_path, "2026-06-18", _block(block_id="mem-t2", body="tier2 five days"))
    _write_daily(tmp_path, "2026-06-10", _block(block_id="mem-old", body="too old"))

    names = [p.name for p in digest._daily_files_for_tier()]
    assert "2026-06-22.md" in names
    assert "2026-06-18.md" in names


def test_recall_inject_line_includes_event_related_ids(tmp_path, monkeypatch):
    """Acceptance: manifest lists event id; related cited fact is standalone-or-related path."""
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "_hot_memory_text", lambda: "")
    fact = _block(
        block_id="mem-2026-07-25-ilink",
        body="ILink push path.",
        entity="论文文献综述",
        item_type="fact",
    )
    event = _block(
        block_id="mem-2026-07-25-event",
        body="User requested lit review chapter; agent drafted and pushed.",
        entity="论文文献综述",
        item_type="event",
        related=["mem-2026-07-25-ilink", "mem-2026-07-25-boyle"],
        sources="session evt-1",
    )
    _write_daily(tmp_path, _today(), fact + "\n\n" + event)
    text = digest.build_recall_injection_context(session_id="s-rel")
    assert "Memory / recent" in text or "Memory / entity" in text
    assert "论文文献综述" in text or "文献综述" in text
    assert "mem-2026-07-25-missing" not in text


def test_standalone_block_ids_excludes_event_and_related(tmp_path, monkeypatch):
    digest = _load_digest()
    monkeypatch.setattr(digest, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(digest, "hermes_local_today", lambda: date(2026, 6, 23))
    day = "2026-06-23"
    content = "\n\n".join(
        [
            _block(
                block_id="mem-2026-06-23-event-1",
                body="Beginning: e; Course: c; Outcome: o",
                item_type="event",
                related=["mem-2026-06-23-fact-cited"],
            ),
            _block(
                block_id="mem-2026-06-23-fact-cited",
                body="Cited fact.",
                item_type="fact",
            ),
            _block(
                block_id="mem-2026-06-23-fact-orphan-a",
                body="Orphan A.",
                item_type="fact",
            ),
            _block(
                block_id="mem-2026-06-23-fact-orphan-b",
                body="Orphan B.",
                item_type="fact",
            ),
        ]
    )
    _write_daily(tmp_path, day, content)
    entries = digest._collect_staging_entries(
        digest._daily_files_for_tier(1),
        tier=1,
    )
    standalone = digest._standalone_block_ids(entries)
    assert standalone == {
        "mem-2026-06-23-fact-orphan-a",
        "mem-2026-06-23-fact-orphan-b",
    }


def test_index_anchor_set_folds_aliases_and_drops_noise():
    digest = _load_digest()
    surfaces = ["MemoryDigest", "memory-digest", "MemoryDigest"]
    folded = digest._fold_index_anchors(surfaces)
    assert len(folded) == 1
    assert list(folded.values())[0] == "MemoryDigest"

    event = {
        "entity": "Weekly UI",
        "participants": [
            {"entity": "User", "role": "requester"},
            {"entity": "Assistant", "role": "executor"},
        ],
    }
    assert digest._index_anchor_set(event) == {"Weekly UI"}
    assert "User" not in digest._index_anchor_set(event)

    pathish = {"entity": "MEMORY.md", "involves": ["/root/Me/Personal/dating/"]}
    assert digest._index_anchor_set(pathish) == set()


def test_index_anchor_set_includes_narration_involves():
    digest = _load_digest()
    narration = {
        "type": "fact",
        "entity": "Riley",
        "involves": [
            {"entity": "Morgan"},
            {"entity": "Riley-mom"},
        ],
    }
    assert digest._index_anchor_set(narration) == {"Riley", "Morgan", "Riley-mom"}

    plain = {"type": "fact", "entity": "Riley", "involves": []}
    assert digest._index_anchor_set(plain) == {"Riley"}
