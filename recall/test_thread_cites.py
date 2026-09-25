from __future__ import annotations

import sys
from pathlib import Path

_MYMEMORY = Path(__file__).resolve().parent.parent
if str(_MYMEMORY / "weekly") not in sys.path:
    sys.path.insert(0, str(_MYMEMORY / "weekly"))

from weekly_json import load_sidecar
from recall.ids import resolve_id


def test_thread_cites_are_events(staging):
    weekly = staging / "weekly"
    for path in sorted(weekly.glob("*.md")):
        payload = load_sidecar(path)
        legend = payload.get("legend") or {}
        event_ids: list[str] = []
        if isinstance(legend, dict):
            event_ids.extend(str(mem or "") for mem in legend.values())
        threads = payload.get("cross-day-thread") or []
        if isinstance(threads, list):
            for thread in threads:
                if not isinstance(thread, dict):
                    continue
                for step in thread.get("steps") or []:
                    if isinstance(step, dict):
                        event_ids.append(str(step.get("event_id") or ""))
        for mid in event_ids:
            if not mid or "-event-" not in mid:
                continue
            rec = resolve_id(mid, staging=staging)
            assert rec is not None, mid
            intra = payload.get("intra-day-thread") or []
            for row in intra:
                if not isinstance(row, dict):
                    continue
                text = str(row.get("text") or "")
                if text.strip():
                    day = str(row.get("date") or "")
                    daily = staging / "daily" / f"{day}.md"
                    if daily.is_file():
                        assert text.splitlines()[0].lstrip("- ").strip()[:40] in daily.read_text(
                            encoding="utf-8"
                        ) or text[:20] in daily.read_text(encoding="utf-8")
