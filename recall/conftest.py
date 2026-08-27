"""Shared helpers for memory-recall pytest modules.

The ``staging`` fixture is a temporary fake notebook so recall tests never
read live ``hermes-home/memories/staging`` (those files change and contain
private names).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_MYMEMORY = Path(__file__).resolve().parent.parent
if str(_MYMEMORY) not in sys.path:
    sys.path.insert(0, str(_MYMEMORY))

SEED = "mem-2026-08-12-event-9625547B667B"
HOP1 = "mem-2026-08-09-procedure-C1DD5CAA2A26"
HOP2 = "mem-2026-08-08-procedure-2B93F6F55D12"
OVERLAP = "mem-2026-08-13-event-48AB7607830B"


def _block(
    *,
    mem_id: str,
    type_: str,
    entity: str,
    body: str,
    related: str | None = None,
    extra: str = "",
) -> str:
    related_line = f"related: [{related}]\n" if related else ""
    return (
        "---\n"
        f"id: {mem_id}\n"
        f"type: {type_}\n"
        f"entity: {entity}\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s-fake]\n"
        f"{related_line}"
        f"{extra}"
        "---\n"
        f"{body}\n"
    )


def write_fake_staging(root: Path) -> Path:
    """Write a tiny publishable notebook that covers recall channel/id tests."""
    daily = root / "daily"
    weekly = root / "weekly"
    monthly = root / "monthly"
    daily.mkdir(parents=True)
    weekly.mkdir()
    monthly.mkdir()

    (daily / "2026-06-16.md").write_text(
        _block(
            mem_id="mem-20260616-1607-cognitive-directionality",
            type_="fact",
            entity="Casey",
            body="Casey prefers visual outlines for architecture talks.",
        ),
        encoding="utf-8",
    )
    (daily / "2026-06-17.md").write_text(
        _block(
            mem_id="mem-20260617-career-pivot",
            type_="event",
            entity="Alex",
            body="Beginning: Alex mentioned a career pivot; Course: logged; Outcome: noted.",
            extra="predicate: career_pivot\n",
        ),
        encoding="utf-8",
    )
    (daily / "2026-07-11.md").write_text(
        _block(
            mem_id="mem-2026-07-11-w28-backlog-scan-delivered",
            type_="event",
            entity="Project",
            body="Beginning: backlog scanned; Course: delivered; Outcome: closed.",
            extra="predicate: backlog_scan\n",
        ),
        encoding="utf-8",
    )
    (daily / "2026-08-08.md").write_text(
        _block(
            mem_id=HOP2,
            type_="procedure",
            entity="Memory Digest",
            body="Obstacle: extra commas in digest output; Solution: keep Type-A Phase-1.",
        ),
        encoding="utf-8",
    )
    (daily / "2026-08-09.md").write_text(
        _block(
            mem_id=HOP1,
            type_="procedure",
            entity="Memory Digest",
            body="Obstacle: merge slots drifted; Solution: keep Type-A Phase-1.",
        ),
        encoding="utf-8",
    )
    (daily / "2026-08-12.md").write_text(
        _block(
            mem_id=SEED,
            type_="event",
            entity="Memory Digest",
            body=(
                "Beginning: semicolon digest bug returned; "
                "Course: traced the stretch; Outcome: cluster linked."
            ),
            related=f"{HOP1}, {HOP2}",
            extra="predicate: digest_bug\n",
        ),
        encoding="utf-8",
    )
    (daily / "2026-08-13.md").write_text(
        _block(
            mem_id=OVERLAP,
            type_="event",
            entity="Memory Digest",
            body="Beginning: overlap check; Course: compared weeks; Outcome: kept distinct.",
            extra="predicate: overlap_check\n",
        )
        + "\n"
        + _block(
            mem_id="mem-2026-08-13-fact-jordan-alias",
            type_="fact",
            entity="Jordan",
            body="Jordan uses Vitest on backend tests.",
        )
        + "\n"
        + _block(
            mem_id="mem-2026-08-13-fact-lin-zhuren",
            type_="fact",
            entity="林主任",
            body="林主任 is the school coordinator in synthetic fixtures.",
        ),
        encoding="utf-8",
    )
    (daily / "2026-08-16.md").write_text(
        _block(
            mem_id="mem-2026-08-17-decision-A4E9C8CBE86B",
            type_="decision",
            entity="User",
            body="Decision: user prefers concise review summaries.",
            extra="kind: Decision\nsubject: user\nruling: prefers concise summaries\n",
        )
        + "\n"
        + _block(
            mem_id="mem-2026-08-17-fact-4E29203F0247",
            type_="fact",
            entity="Project",
            body="Filename may disagree with the id date; resolve by scan.",
        ),
        encoding="utf-8",
    )

    weekly_w32 = (
        "---\n"
        "week: 2026-W32\n"
        "week_status: pending\n"
        "---\n"
        "schema_version: 2\n"
        "week_key: 2026-W32\n"
        "range:\n"
        "  start: '2026-08-03'\n"
        "  end: '2026-08-09'\n"
        "entities:\n"
        "  - {key: gitnexus, canonical: gitnexus}\n"
        "  - {key: memorydigest, canonical: Memory Digest}\n"
        "summary:\n"
        "  - text: First W32 bullet for injection.\n"
        "    weekdays: [Monday]\n"
        "  - text: Second W32 bullet also injected.\n"
        "    weekdays: [Tuesday]\n"
    )
    (weekly / "2026-W32.md").write_text(weekly_w32, encoding="utf-8")
    weekly_w33 = (
        "---\n"
        "week: 2026-W33\n"
        "week_status: pending\n"
        "---\n"
        "schema_version: 2\n"
        "week_key: 2026-W33\n"
        "range:\n"
        "  start: '2026-08-10'\n"
        "  end: '2026-08-16'\n"
        "entities:\n"
        "  - {key: memorydigest, canonical: Memory Digest}\n"
        "summary:\n"
        "  - text: Memory Digest semicolon fix.\n"
        "    weekdays: [Wednesday]\n"
        "  - text: Second W33 summary stays in prefetch.\n"
        "    weekdays: [Thursday]\n"
    )
    (weekly / "2026-W33.md").write_text(weekly_w33, encoding="utf-8")

    monthly_md = (
        "---\n"
        "month: 2026-08\n"
        "month_status: pending\n"
        "---\n"
        "schema_version: 1\n"
        "cycle: monthly\n"
        "month_key: 2026-08\n"
        "range:\n"
        "  start: '2026-08-01'\n"
        "  end: '2026-08-31'\n"
        "evidence:\n"
        f"  - {SEED}\n"
        "  - st-kimi\n"
        "  - st-mimo\n"
        "weeks: [2026-W32, 2026-W33]\n"
    )
    (monthly / "2026-08.md").write_text(monthly_md, encoding="utf-8")
    return root


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    return write_fake_staging(tmp_path)


@pytest.fixture(autouse=True)
def _no_live_gte_weights(monkeypatch, tmp_path_factory):
    """Unit tests must not load ~/.cache GTE; Channel 4 is stubbed or fail-open."""
    missing = tmp_path_factory.mktemp("no-gte") / "missing.onnx"
    monkeypatch.setenv("MYMEMORY_GTE_ONNX", str(missing))
    from recall.embed import _drop_session

    _drop_session()
    yield
    _drop_session()
