"""Update slash commands return before the model call so the next chat turn is not queued."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import slash_jobs  # noqa: E402


def test_background_update_returns_before_the_work_finishes():
    started = threading.Event()
    release = threading.Event()
    seen: list[str] = []

    def work() -> str:
        started.set()
        release.wait(2)
        seen.append("done")
        return "finished"

    text = slash_jobs.run_or_background("digest update", work, sync=False)
    assert text == "digest update started in the background. You can keep chatting."
    assert started.wait(2)
    assert seen == []
    again = slash_jobs.run_or_background("digest update", work, sync=False)
    assert again == "digest update is already running."
    release.set()
    slash_jobs.join_slash_job("digest update", timeout=2)
    assert seen == ["done"]


def test_pytest_runs_the_update_inline(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "slash")
    assert slash_jobs.run_or_background("weekly update", lambda: "wrote") == "wrote"
