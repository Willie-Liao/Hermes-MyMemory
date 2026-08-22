"""Put monthly/ and MyMemory on sys.path so pytest collection matches weekly's flat imports."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_monthly = Path(__file__).resolve().parent
_mymemory = _monthly.parent
_weekly = _mymemory / "weekly"
_digest = _mymemory / "digest"
for path in (_monthly, _mymemory, _weekly, _digest):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


def _card(
    mem_id: str,
    type_: str,
    entity: str,
    body: str,
    *,
    related: str = "",
    extra: str = "",
) -> str:
    rel = f"related: [{related}]\n" if related else ""
    return (
        "---\n"
        f"id: {mem_id}\n"
        f"type: {type_}\n"
        f"entity: {entity}\n"
        "confidence: high\n"
        "status: candidate\n"
        "sources: [session s-fake]\n"
        f"{rel}{extra}"
        "---\n"
        f"{body}\n"
    )


@pytest.fixture(autouse=True)
def sandbox_monthly_home(tmp_path, monkeypatch):
    """Point monthly tests at a fake Hermes home so they never score live dailies."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    daily = tmp_path / "memories" / "staging" / "daily"
    weekly = tmp_path / "memories" / "staging" / "weekly"
    daily.mkdir(parents=True)
    weekly.mkdir()
    target = "mem-2026-08-15-decision-55091D2900CD"
    daily.joinpath("2026-06-15.md").write_text(
        _card("mem-2026-06-15-decision-a", "decision", "GitNexus", "Decision: keep the graph index.")
        + _card(
            "mem-2026-06-15-procedure-a",
            "procedure",
            "GitNexus",
            "Obstacle: lookup drifted; Solution: normalize keys.",
        )
        + _card("mem-2026-06-15-decision-b", "decision", "Casey", "Decision: user wants short briefs."),
        encoding="utf-8",
    )
    daily.joinpath("2026-06-16.md").write_text(
        _card(
            "mem-2026-06-16-decision-c",
            "decision",
            "Casey",
            "Decision: user confirmed the lunch plan.",
            extra="supersedes: [mem-2026-06-15-decision-b]\n",
        ),
        encoding="utf-8",
    )
    daily.joinpath("2026-07-10.md").write_text(
        _card("mem-2026-07-10-decision-a", "decision", "Project", "Decision: ship the July cut.")
        + _card(
            "mem-2026-07-10-procedure-a",
            "procedure",
            "Project",
            "Obstacle: batch overflow; Solution: pack by week.",
        )
        + _card("mem-2026-07-10-decision-b", "decision", "GitNexus", "Decision: freeze entity keys."),
        encoding="utf-8",
    )
    daily.joinpath("2026-08-01.md").write_text(
        _card(
            "mem-2026-08-01-event-a",
            "event",
            "Memory Digest",
            "Beginning: August opened; Course: planned; Outcome: week 31 noted.",
            related=target,
        ),
        encoding="utf-8",
    )
    daily.joinpath("2026-08-08.md").write_text(
        _card(
            "mem-2026-08-08-event-g",
            "event",
            "GitNexus",
            "Beginning: graph query; Course: ran; Outcome: hit.",
            related=target,
        )
        + _card(
            "mem-2026-08-08-procedure-m",
            "procedure",
            "Memory Digest",
            "Obstacle: semicolon; Solution: strip it.",
        ),
        encoding="utf-8",
    )
    daily.joinpath("2026-08-12.md").write_text(
        _card(
            "mem-2026-08-12-event-9625547B667B",
            "event",
            "Memory Digest",
            "Beginning: digest bug; Course: traced; Outcome: linked.",
            related=target,
        ),
        encoding="utf-8",
    )
    daily.joinpath("2026-08-15.md").write_text(
        _card(
            target,
            "decision",
            "User",
            "Decision: user prefers the August merge order.",
        )
        + _card(
            "mem-2026-08-15-fact-skip",
            "fact",
            "Casey",
            "Casey likes mermaid diagrams.",
        ),
        encoding="utf-8",
    )
    daily.joinpath("2026-08-20.md").write_text(
        _card(
            "mem-2026-08-20-event-w34",
            "event",
            "Project",
            "Beginning: week 34 note; Course: filed; Outcome: closed.",
        ),
        encoding="utf-8",
    )
    for week in ("2026-W31", "2026-W32", "2026-W33", "2026-W34"):
        weekly.joinpath(f"{week}.md").write_text(
            "schema_version: 1\n"
            "cycle: weekly\n"
            f"week_key: {week}\n"
            "entities:\n"
            "  - key: memorydigest\n"
            "    canonical: Memory Digest\n"
            "  - key: gitnexus\n"
            "    canonical: GitNexus\n"
            "cross-day-thread: []\n"
            "intra-day-thread: []\n"
            "legend: {}\n",
            encoding="utf-8",
        )
    return tmp_path
