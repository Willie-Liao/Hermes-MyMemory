"""Suggest lists up to four months and prints a conversation guide. It does not write hot files."""

from __future__ import annotations

import json

from monthly_schema import (
    MonthlyDecision,
    MonthlyDecisionPreference,
    MonthlyEntity,
    MonthlyPayload,
    MonthlyProcedure,
    MonthlyRange,
    MonthlySummaryItem,
    MonthlyUserImage,
)
from monthly_suggest import suggest_from_tokens, suggest_path
from monthly_writer import write_month


def _month() -> None:
    write_month(
        MonthlyPayload(
            key="2026-08",
            range=MonthlyRange(start="2026-08-01", end="2026-08-31"),
            summary=(MonthlySummaryItem(text="memory pipeline explanations", weeks=("2026-W32",)),),
            user_image=MonthlyUserImage(
                decision_preference=MonthlyDecisionPreference(
                    text="terse replies",
                    evidence=("mem-2026-08-01-decision-AAAA",),
                )
            ),
            key_decisions=(
                MonthlyDecision(
                    id="mem-2026-08-01-decision-AAAA",
                    kind="preference",
                    text="keep bilingual parent notes",
                    context="homeroom WeChat",
                    valid_to="open",
                    evidence=("mem-2026-08-01-decision-AAAA", "mem-2026-08-20-decision-BBBB"),
                    occurrence_n=2,
                    first_seen="2026-08-01",
                    last_seen="2026-08-20",
                    strength=3.0,
                ),
                MonthlyDecision(
                    id="mem-2026-08-15-decision-SAME",
                    kind="preference",
                    text="expand collapsed UI sections",
                    evidence=("mem-2026-08-15-decision-SAME",),
                    occurrence_n=2,
                    first_seen="2026-08-15",
                    last_seen="2026-08-15",
                    strength=2.0,
                ),
            ),
            key_procedures=(
                MonthlyProcedure(
                    id="mem-2026-08-05-procedure-CCCC",
                    problem="recall dumps the whole staging tree",
                    solution="start from recent daily events",
                    trigger="ingredient list from a prior session",
                    evidence=("mem-2026-08-05-procedure-CCCC",),
                    weeks=("2026-W32", "2026-W33"),
                    occurrence_n=2,
                    first_seen="2026-08-05",
                    last_seen="2026-08-18",
                    strength=2.5,
                ),
            ),
            entities=(
                MonthlyEntity(
                    key="hermes",
                    canonical="Hermes",
                    months=("2026-07", "2026-08"),
                    weeks=("2026-W32",),
                    month_count=2,
                ),
            ),
        )
    )


def test_suggest_rejects_fifth_month(monkeypatch):
    called: list[int] = []

    def boom(*_args, **_kwargs):
        called.append(1)
        return {"failed": True}

    monkeypatch.setattr("monthly_suggest._default_oneshot", boom)
    text = suggest_from_tokens(["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"])
    assert text == "at most 4 months"
    assert called == []


def test_suggest_saves_numbered_items(monkeypatch):
    _month()
    seen: dict[str, object] = {}

    def fake(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["max_tokens"] = kwargs.get("max_tokens")
        return {
            "failed": False,
            "tool_args": {
                "items": [
                    {
                        "bucket": "USER.md",
                        "text": "keep bilingual parent notes",
                        "evidence": ["mem-2026-08-01-decision-AAAA"],
                        "why": "repeated across the month",
                    },
                    {
                        "bucket": "skill",
                        "text": "progressive recall",
                        "evidence": ["mem-2026-08-15-decision-SAME"],
                        "why": "same-day id must be dropped",
                    },
                ]
            },
        }

    text = suggest_from_tokens(["2026-08,"], call_oneshot=fake)
    assert seen["max_tokens"] == 4096
    prompt = str(seen["prompt"])
    assert '"entities"' not in prompt
    assert "repeats across more than one day" in prompt
    assert "preference about the user" in prompt
    assert "how Hermes must behave" in prompt
    assert "situation they will hit again" in prompt
    assert "cut at 200 characters" in prompt
    assert "mem-2026-08-15-decision-SAME" not in prompt
    assert "mem-2026-08-01-decision-AAAA" in prompt
    record = json.loads(suggest_path().read_text(encoding="utf-8"))
    assert record["items"][0]["n"] == 1
    assert record["items"][0]["bucket"] == "USER.md"
    assert record["items"][0]["evidence"] == ["mem-2026-08-01-decision-AAAA"]
    assert len(record["items"]) == 1
    assert "create a skill" in text
    assert "add" in text and "replace" in text and "remove" in text
    assert "/monthly suggest apply" not in text
