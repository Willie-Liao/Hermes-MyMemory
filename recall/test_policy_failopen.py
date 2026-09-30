from __future__ import annotations

from recall.policy import gate_candidates, parse_gate_keep, parse_scope, plan_scope
from recall.strength import neighbor_credits, strength_value


def test_policy_failopen_and_out_of_range():
    assert parse_scope("???") == "hybrid"
    assert parse_scope("Scope: Simple.") == "simple"
    scope, budget = plan_scope("x", force_raw="not a label")
    assert scope == "hybrid"
    assert budget["L3"] == 2
    assert parse_gate_keep("keep 0 99 1", 3) in ([0, 1], [0, 1])
    kept = parse_gate_keep("99 100", 3)
    assert kept == []
    cands = [{"id": "a"}, {"id": "b"}, {"id": "Jordan"}]
    all_kept = gate_candidates("q", cands, force_raw="")
    assert all_kept == cands
    dropped = gate_candidates("q", cands, force_raw="0 1")
    assert [c["id"] for c in dropped] == ["a", "b"]


def test_strength_actr_monotonic_and_neighbor_cap():
    low_n = strength_value(recall_n=1, first_seen="2026-01-01", importance=3, now=__import__("datetime").date(2026, 8, 1))
    high_n = strength_value(recall_n=8, first_seen="2026-01-01", importance=3, now=__import__("datetime").date(2026, 8, 1))
    assert high_n > low_n
    fresh = strength_value(recall_n=3, first_seen="2026-07-01", importance=3, now=__import__("datetime").date(2026, 8, 1))
    stale = strength_value(recall_n=3, first_seen="2025-01-01", importance=3, now=__import__("datetime").date(2026, 8, 1))
    assert fresh > stale
    assert strength_value(recall_n=10_000, first_seen="2026-08-01", importance=5, now=__import__("datetime").date(2026, 8, 1)) == 10.0
    assert strength_value(recall_n=0, first_seen="2026-01-01", importance=0) == 0.0
    forty = {f"n{i}": 0.5 for i in range(40)}
    credits = neighbor_credits(forty)
    assert abs(sum(credits.values()) - 1.0) < 1e-9
