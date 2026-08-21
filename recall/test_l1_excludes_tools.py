from __future__ import annotations

from recall.l1 import search_l1


def test_l1_excludes_tools_and_finds_event_stage_regex():
    hits = search_l1("_EVENT_STAGE_RE", valid_from="2026-08-12", k=8)
    assert hits
    assert all(h["role"] != "tool" for h in hits)
    blob = "\n".join(str(h.get("content") or "") for h in hits[:5])
    assert "_EVENT_STAGE_RE" in blob
