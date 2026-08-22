"""Tests for post-event-worker mem-id / F-P-D agreement validator."""

from __future__ import annotations

from pathlib import Path

import weekly_event_validate as v


def _write_daily(tmp: Path, name: str, content: str) -> Path:
    daily = tmp / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    path = daily / name
    path.write_text(content, encoding="utf-8")
    return path


def test_pass_when_mem_ids_resolve_and_overlap(tmp_path: Path):
    path = _write_daily(
        tmp_path,
        "2026-06-30.md",
        "---\n"
        "id: mem-2026-06-30-kickoff\n"
        "type: fact\n"
        "entity: X\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s1]\n"
        "---\n"
        "Project kickoff moved to the event-first digest flow.\n",
    )
    blocks = [
        {
            "frontmatter": {
                "id": "evt-a",
                "type": "event",
                "related": ['[1] mem-2026-06-30-kickoff'],
            },
            "body": "Project kickoff moved to event-first digest.",
        }
    ]
    assert v.validate_event_blocks_against_dailies(blocks, [path]) == []


def test_missing_mem_id_fails(tmp_path: Path):
    path = _write_daily(
        tmp_path,
        "2026-06-30.md",
        "---\nid: mem-other\ntype: fact\nentity: X\nconfidence: high\n"
        "status: candidate\nsources: [s]\n---\nOther.\n",
    )
    blocks = [
        {
            "frontmatter": {
                "id": "evt-a",
                "type": "event",
                "related": ["[1] mem-2026-06-30-missing"],
            },
            "body": "Something happened.",
        }
    ]
    errs = v.validate_event_blocks_against_dailies(blocks, [path])
    assert any("not found" in e for e in errs)


def test_wrong_type_fails(tmp_path: Path):
    path = _write_daily(
        tmp_path,
        "2026-06-30.md",
        "---\nid: mem-2026-06-30-e\ntype: event\nentity: X\n"
        "confidence: high\nstatus: candidate\nsources: [s]\n"
        "predicate: x\nparticipants: [{entity: X}]\n"
        "valid_from: 2026-06-30\nvalid_to: 2026-06-30\n"
        "related: [mem-x]\n---\nAn event body.\n",
    )
    blocks = [
        {
            "frontmatter": {
                "id": "evt-a",
                "type": "event",
                "related": ["[1] mem-2026-06-30-e"],
            },
            "body": "An event body referenced wrongly.",
        }
    ]
    errs = v.validate_event_blocks_against_dailies(blocks, [path])
    assert any("not found" in e or "type" in e for e in errs)


def test_disagreement_fails(tmp_path: Path):
    path = _write_daily(
        tmp_path,
        "2026-06-30.md",
        "---\nid: mem-2026-06-30-a\ntype: procedure\nentity: X\n"
        "confidence: high\nstatus: candidate\nsources: [s]\n---\n"
        "Always run validation before writing daily files.\n",
    )
    blocks = [
        {
            "frontmatter": {
                "id": "evt-a",
                "type": "event",
                "related": ["[1] mem-2026-06-30-a"],
            },
            "body": "Quantum bananas launched orbital tea ceremony.",
        }
    ]
    errs = v.validate_event_blocks_against_dailies(blocks, [path])
    assert any("does not agree" in e for e in errs)


def test_missing_related_mem_fails(tmp_path: Path):
    path = _write_daily(tmp_path, "2026-06-30.md", "")
    blocks = [
        {
            "frontmatter": {"id": "evt-a", "type": "event", "related": []},
            "body": "No cites.",
        }
    ]
    errs = v.validate_event_blocks_against_dailies(blocks, [path])
    assert any("missing mem id" in e for e in errs)
