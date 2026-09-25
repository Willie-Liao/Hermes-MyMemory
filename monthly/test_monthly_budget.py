"""Compression, no verbatim bodies, evidence resolves, skipped node-count."""

from __future__ import annotations

import pytest
from monthly_slice import count_tokens, slice_corpus_tokens
from monthly_schema import MonthlyPayload, MonthlyRange, MonthlyProgress, MonthlyDecision
from monthly_writer import dump_yaml, loads, write_month, BANNED_BODY_MARKERS, RETIRED_KEYS
from monthly_actions import expand_from_block, lookup_by_entity, month_band


def _file_tokens(text: str) -> int:
    return count_tokens(text)


def test_compression_affine_bound_on_hand_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Budget uses live slice corpus vs generated file; hand payload must stay small.
    payload = MonthlyPayload(
        key="2026-06",
        weeks=("2026-W25",),
        range=MonthlyRange(start="2026-06-01", end="2026-06-30"),
        summary="june",
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
        summary="x",
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
        summary="Shipped weekly review retries.",
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
    assert "2026-08:" in band
    assert count_tokens(band) < 80
    walked = expand_from_block("mem-2026-08-12-event-9625547B667B")
    assert walked["ok"] is True
    assert "mem-2026-08-09-procedure-C1DD5CAA2A26" in walked["sibling_ids"]


def test_lookup_by_entity_returns_weeks(tmp_path, monkeypatch):
    from monthly_schema import MonthlyEntity

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = MonthlyPayload(
        key="2026-08",
        summary="entity weeks",
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
