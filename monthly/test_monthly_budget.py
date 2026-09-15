"""Compression, no verbatim bodies, evidence resolves, skipped node-count."""

from __future__ import annotations

import pytest
from monthly_slice import count_tokens, slice_corpus_tokens
from monthly_schema import (
    MonthlyPayload,
    MonthlyRange,
    MonthlyProgress,
    MonthlyDecision,
    MonthlyProcedure,
    MonthlySummaryItem,
)
from monthly_writer import dump_yaml, loads, write_month, BANNED_BODY_MARKERS, RETIRED_KEYS
from monthly_actions import expand_from_block, handle_monthly, lookup_by_entity, month_band


def _file_tokens(text: str) -> int:
    return count_tokens(text)


def test_compression_affine_bound_on_hand_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Budget uses live slice corpus vs generated file; hand payload must stay small.
    payload = MonthlyPayload(
        key="2026-06",
        weeks=("2026-W25",),
        range=MonthlyRange(start="2026-06-01", end="2026-06-30"),
        summary=(MonthlySummaryItem(text="june"),),
        core_progress=(
            MonthlyProgress(
                id="cp-1",
                title="start",
                body="credential goal stated",
                evidence=("mem-20260617-priority",),
            ),
        ),
    )
    text = dump_yaml(payload)
    cap = 0.5 * slice_corpus_tokens("2026-06") + 1200
    assert _file_tokens(text) <= cap


def test_no_verbatim_bodies_and_retired_keys():
    payload = MonthlyPayload(
        key="2026-08",
        range=MonthlyRange(start="2026-08-01", end="2026-08-31"),
        summary=(MonthlySummaryItem(text="x"),),
        key_decisions=(
            MonthlyDecision(
                id="mem-1",
                kind="preference",
                text="user must deliver pages individually",
                why_it_matters="house format",
                evidence=("mem-1",),
            ),
        ),
    )
    dumped = dump_yaml(payload)
    for marker in BANNED_BODY_MARKERS:
        assert marker not in dumped
    for key in RETIRED_KEYS:
        assert key not in dumped


def test_node_count_skipped_while_threads_empty():
    from monthly_slice import weekly_dir
    from weekly_json import load_sidecar

    empty = True
    folder = weekly_dir()
    if folder.is_dir():
        for path in folder.glob("*.md"):
            obj = load_sidecar(path)
            if obj.get("cross-day-thread"):
                empty = False
                break
    if empty:
        pytest.skip("L3 threads empty; node-count invariant is vacuous")
    pytest.fail("node-count assertion not implemented once threads exist")


def test_month_band_and_expand(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = MonthlyPayload(
        key="2026-08",
        range=MonthlyRange(start="2026-08-01", end="2026-08-31"),
        summary=(
            MonthlySummaryItem(text="Shipped weekly review retries", weeks=("2026-W33",)),
            MonthlySummaryItem(text="Qixi card from drafting to sharing", weeks=("2026-W34", "2026-W35")),
        ),
        key_decisions=(
            MonthlyDecision(
                id="mem-2026-08-05-decision-PREF",
                kind="preference",
                text="user prefers concise review summaries",
                why_it_matters="scannable",
                context="when writing weekly review summaries",
                exceptions="do not shorten explicit user quotes",
                evidence=("mem-2026-08-05-decision-PREF",),
                strength=1.2,
            ),
        ),
        key_procedures=(
            MonthlyProcedure(
                id="mem-2026-08-05-procedure-CRON",
                trigger="user asked to draft a reminder cron",
                problem="wrong cadence",
                obstacles=("scheduled cron triggered at the wrong cadence",),
                solution="treat the trigger as ad-hoc until confirmed",
                evidence=("mem-2026-08-05-procedure-CRON",),
                strength=0.9,
            ),
        ),
        core_progress=(
            MonthlyProgress(
                id="cp-1",
                title="weekly",
                body="retries",
                evidence=("mem-2026-08-12-event-9625547B667B", "mem-2026-08-09-procedure-C1DD5CAA2A26"),
            ),
        ),
    )
    write_month(payload)
    band = month_band()
    assert "### 2026-08  2026-08-01..2026-08-31" in band
    assert "- Shipped weekly review retries (2026-W33)" in band
    assert "- Qixi card from drafting to sharing (2026-W34, 2026-W35)" in band
    assert "when writing weekly review summaries" in band
    assert "mem-2026-08-05-decision-PREF" in band
    assert "user asked to draft a reminder cron" in band
    assert "wrong cadence" in band
    assert "2026-08  2026-08-01..2026-08-31:" not in band
    walked = expand_from_block("mem-2026-08-12-event-9625547B667B")
    assert walked["ok"] is True
    assert "mem-2026-08-09-procedure-C1DD5CAA2A26" in walked["sibling_ids"]


def test_lookup_by_entity_returns_weeks(tmp_path, monkeypatch):
    from monthly_schema import MonthlyEntity

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = MonthlyPayload(
        key="2026-08",
        summary=(MonthlySummaryItem(text="entity weeks"),),
        entities=(
            MonthlyEntity(
                key="gitnexus",
                canonical="GitNexus",
                months=("2026-08",),
                weeks=("2026-W32", "2026-W33"),
                month_count=2,
            ),
        ),
    )
    write_month(payload)
    hits = lookup_by_entity("gitnexus")
    assert len(hits) == 1
    assert hits[0]["month"] == "2026-08"
    assert hits[0]["entity"].weeks == ("2026-W32", "2026-W33")


def test_handle_monthly_help_update_show(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    help_text = handle_monthly("help")
    assert "update" in help_text and "show" in help_text
    assert "ui" not in help_text.casefold() or "/monthly" in help_text
    assert "close" not in help_text
    assert "bad month" in handle_monthly("update 2026-13")
    monkeypatch.setattr(
        "monthly_actions.generate_month",
        lambda key, **_: {"outcome": "ok", "month": key},
    )
    assert "updated 2026-08" in handle_monthly("update 2026-08")
    write_month(
        MonthlyPayload(
            key="2026-08",
            range=MonthlyRange(start="2026-08-01", end="2026-08-31"),
            summary=(MonthlySummaryItem(text="one story", weeks=("2026-W32",)),),
        )
    )
    shown = handle_monthly("show 2026-08")
    assert "- one story (2026-W32)" in shown
