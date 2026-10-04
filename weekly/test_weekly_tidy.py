from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugins = Path(__file__).resolve().parent.parent.parent

    def _load(name: str, rel: str):
        path = plugins / rel
        spec = importlib.util.spec_from_file_location(f"test_{name}", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    weekly = _load("weekly", "MyMemory/weekly/weekly.py")
    tidy = _load("tidy", "MyMemory/weekly/weekly_tidy.py")
    return weekly, tidy


def _write_reviewed(tmp_path: Path, week: str, body: str) -> Path:
    path = tmp_path / "memories" / "staging" / "weekly" / f"{week} reviewed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_weekly_candidates_sections(tmp_path, monkeypatch):
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    _write_reviewed(
        tmp_path,
        "2026-W24",
        "# Weekly\n\n## 1. Proposed additions\n\n### Foo\n- Proposed §-entry: bar\n\n"
        "## 2. Hypotheses awaiting confirmation\n\n- Career pivot hypothesis\n\n"
        "## 3. Reinforced procedures\n\n- Hot writes queue via pending\n",
    )
    candidates = tidy.parse_weekly_candidates("2026-W24")
    assert len(candidates) == 3
    assert candidates[0]["section"] == "§1"
    assert candidates[1]["section"] == "§2"


def test_parse_weekly_candidates_section_1_1_structured(tmp_path, monkeypatch):
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    _write_reviewed(
        tmp_path,
        "2026-W24",
        "## 1. Proposed additions\n\n### 1.1 Proposed for hot memory\n"
        "- record_id: F1\n  target: MEMORY.md\n  valid_to: 2026-07-16\n"
        "  block_ids: [mem-20260616-a]\n  text: compact text\n\n"
        "### 1.2 Not proposed for hot promotion\n"
        "- block_ids: [mem-event]\n  reason: event\n",
    )
    rows = tidy.parse_weekly_candidates("2026-W24")
    proposed = [r for r in rows if r.get("tier") == "proposed"]
    assert len(proposed) == 1
    assert proposed[0]["block_id"] == "mem-20260616-a"
    assert "compact text" in (proposed[0].get("proposed_text") or "")
    assert proposed[0]["valid_to"] == "2026-07-16"


def test_parse_weekly_candidates_w26_prose_and_section_boundary(tmp_path, monkeypatch):
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    _write_reviewed(
        tmp_path,
        "2026-W26",
        "## 1. Proposed additions\n\n"
        "### 1.1 Compact entries proposed for MEMORY.md\n"
        "F1. School visit policy remains active.\n"
        "  sources: mem-20260625-school-visit-policy\n"
        "  target: MEMORY.md\n"
        "  valid_to: 2026-08-01\n"
        "  confidence: high\n\n"
        "### 1.2 Candidate blocks NOT proposed for hot promotion\n"
        "- mem-20260626-delivered — event; episodic.\n\n"
        "## 2. Hypotheses awaiting confirmation\n"
        "H1. The pilot scope still needs confirmation.\n"
        "  block_ids: [mem-20260627-pilot]\n"
        "  confidence: medium\n\n"
        "## 3. Reinforced procedures\n"
        "P1. Log every new file path when written.\n"
        "  sources: mem-20260625-new-file-rule\n"
        "  valid_to: open\n\n"
        "## 4. Conflicts\n"
        "- capacity metric must not become a procedure candidate\n",
    )

    rows = tidy.parse_weekly_candidates("2026-W26")
    by_id = {row["record_id"]: row for row in rows}

    assert by_id["F1"]["tier"] == "proposed"
    assert by_id["F1"]["block_id"] == "mem-20260625-school-visit-policy"
    assert by_id["F1"]["proposed_text"] == "School visit policy remains active."
    assert by_id["F1"]["valid_to"] == "2026-08-01"
    assert by_id["mem-20260626-delivered"]["tier"] == "not_proposed"
    assert by_id["H1"]["tier"] == "hypothesis"
    assert by_id["P1"]["tier"] == "procedure"
    assert all("capacity metric" not in row["label"] for row in rows)


def test_parse_weekly_candidates_distill_yaml_hypothesis_procedure(tmp_path, monkeypatch):
    """## Distill YAML yields hypothesis/procedure; events and conflicts are skipped."""
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    _write_reviewed(
        tmp_path,
        "2026-W28",
        "# Weekly distill 2026-W28\n\n"
        "## Distill\n\n"
        "---\n"
        "id: evt-week-kickoff\n"
        "type: event\n"
        "confidence: high\n"
        "status: candidate\n"
        "related:\n"
        '  - "[1] mem-20260706-kickoff"\n'
        "sources: [session s1]\n"
        "---\n"
        "Kickoff happened [1].\n\n"
        "---\n"
        "id: hyp-pilot-scope\n"
        "type: hypothesis\n"
        "confidence: medium\n"
        "status: candidate\n"
        "related:\n"
        '  - "[1] mem-20260706-kickoff"\n'
        "sources: [session s1]\n"
        "---\n"
        "Pilot scope still needs confirmation.\n\n"
        "---\n"
        "id: proc-log-paths\n"
        "type: procedure\n"
        "confidence: high\n"
        "status: candidate\n"
        "related:\n"
        '  - "[2] mem-20260707-log-rule"\n'
        "sources: [session s2]\n"
        "---\n"
        "Log every new file path when written.\n\n"
        "---\n"
        "id: conflict-capacity\n"
        "type: conflict\n"
        "confidence: medium\n"
        "status: candidate\n"
        "related:\n"
        '  - "[1] mem-20260706-kickoff"\n'
        "sources: [session s1]\n"
        "---\n"
        "Capacity metric should not become a procedure.\n\n"
        "## Brief\n\n"
        "### What happened\n\n"
        "Kickoff [1].\n",
    )

    rows = tidy.parse_weekly_candidates("2026-W28")
    by_id = {row["record_id"]: row for row in rows}

    assert set(by_id) == {"hyp-pilot-scope", "proc-log-paths"}
    assert by_id["hyp-pilot-scope"]["tier"] == "hypothesis"
    assert by_id["hyp-pilot-scope"]["block_id"] == "mem-20260706-kickoff"
    assert "confirmation" in (by_id["hyp-pilot-scope"].get("proposed_text") or "")
    assert by_id["proc-log-paths"]["tier"] == "procedure"
    assert by_id["proc-log-paths"]["block_id"] == "mem-20260707-log-rule"
    assert "Log every new file path" in (by_id["proc-log-paths"].get("proposed_text") or "")


def test_parse_weekly_candidates_legacy_unchanged_without_distill(tmp_path, monkeypatch):
    """Without ## Distill, legacy §1–§3 path still parses."""
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    _write_reviewed(
        tmp_path,
        "2026-W24",
        "# Weekly\n\n## 1. Proposed additions\n\n### Foo\n- Proposed §-entry: bar\n\n"
        "## 2. Hypotheses awaiting confirmation\n\n- Career pivot hypothesis\n\n"
        "## 3. Reinforced procedures\n\n- Hot writes queue via pending\n",
    )
    candidates = tidy.parse_weekly_candidates("2026-W24")
    assert len(candidates) == 3
    assert candidates[0]["section"] == "§1"
    assert candidates[1]["section"] == "§2"
    assert candidates[2]["section"] == "§3"


def test_parse_brief_cite_candidates_maps_brief_to_mem(tmp_path, monkeypatch):
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-06-29.md").write_text(
        "---\nid: mem-2026-06-29-tuition-notice-batch-delivered\ntype: event\nstatus: candidate\n---\n"
        "Tuition batch delivered.\n",
        encoding="utf-8",
    )
    _write_reviewed(
        tmp_path,
        "2026-W27",
        "# Weekly distill 2026-W27\n\n## Distill\n\n"
        "---\nid: evt-1\ntype: event\nrelated:\n"
        '  - "[1] mem-2026-06-29-tuition-notice-batch-delivered"\n'
        "---\nEvent body [1].\n\n"
        "## Brief\n\n"
        "Tuition landed [1].\n",
    )
    rows = tidy.parse_brief_cite_candidates("2026-W27")
    assert len(rows) == 1
    assert rows[0]["block_id"] == "mem-2026-06-29-tuition-notice-batch-delivered"
    assert rows[0]["cite_n"] == "1"
    assert rows[0]["record_id"] == "cite-1"
    assert rows[0]["type"] == "event"
    assert "Tuition" in rows[0]["proposed_text"]


def test_parse_brief_cite_candidates_empty_when_no_brief_cites(tmp_path, monkeypatch):
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    _write_reviewed(
        tmp_path,
        "2026-W27",
        "# W\n\n## Distill\n\n---\nid: h1\ntype: hypothesis\n"
        "related: [evt-1]\nsources: [mem-2026-06-29-x]\n---\nHyp body.\n\n"
        "## Brief\n\nNo citations here.\n",
    )
    assert tidy.parse_brief_cite_candidates("2026-W27") == []


def test_filter_approval_hub_candidates_events_only(tmp_path, monkeypatch):
    """Mixed daily types → Approval Hub list keeps type:event only."""
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-07-01.md").write_text(
        "---\nid: mem-2026-07-01-kickoff\ntype: event\nstatus: candidate\n---\n"
        "Kickoff landed.\n\n"
        "---\nid: mem-2026-07-01-policy\ntype: fact\nstatus: candidate\n---\n"
        "Policy remains active.\n\n"
        "---\nid: mem-2026-07-01-log-rule\ntype: procedure\nstatus: candidate\n---\n"
        "Log every new path.\n\n"
        "---\nid: mem-2026-07-01-budget-cap\ntype: decision_constraint\nstatus: candidate\n---\n"
        "Budget cap stays.\n\n"
        "---\nid: mem-2026-07-01-pivot\ntype: hypothesis\nstatus: candidate\n---\n"
        "Career pivot still open.\n",
        encoding="utf-8",
    )
    _write_reviewed(
        tmp_path,
        "2026-W27",
        "# Weekly distill 2026-W27\n\n## Distill\n\n"
        "---\nid: evt-1\ntype: event\nrelated:\n"
        '  - "[1] mem-2026-07-01-kickoff"\n'
        '  - "[2] mem-2026-07-01-policy"\n'
        '  - "[3] mem-2026-07-01-log-rule"\n'
        '  - "[4] mem-2026-07-01-budget-cap"\n'
        '  - "[5] mem-2026-07-01-pivot"\n'
        "---\nEvent body [1][2][3][4][5].\n\n"
        "## Brief\n\n"
        "Kickoff [1]. Policy [2]. Log rule [3]. Cap [4]. Pivot [5].\n",
    )

    raw = tidy.parse_brief_cite_candidates("2026-W27")
    assert {r["block_id"]: r.get("type") for r in raw} == {
        "mem-2026-07-01-kickoff": "event",
        "mem-2026-07-01-policy": "fact",
        "mem-2026-07-01-log-rule": "procedure",
        "mem-2026-07-01-budget-cap": "decision_constraint",
        "mem-2026-07-01-pivot": "hypothesis",
    }

    hub = tidy.filter_approval_hub_candidates(raw)
    assert len(hub) == 1
    assert hub[0]["type"] == "event"
    assert hub[0]["block_id"] == "mem-2026-07-01-kickoff"
    assert hub[0]["record_id"] == "cite-1"
    assert hub[0]["tier"] == "cited"
    assert "Kickoff" in hub[0]["proposed_text"]
    # Actionable Approval Hub shape (Add memory / user / Delete stage on these keys)
    for key in ("record_id", "block_id", "proposed_text", "cite_n", "type"):
        assert hub[0].get(key)


def test_list_tidy_candidates_events_only(tmp_path, monkeypatch):
    """Bridge list_tidy_candidates is the Approval Hub feed — events only."""
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    actions_path = Path(__file__).with_name("weekly_actions.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_actions_hub_test", actions_path
    )
    assert spec is not None and spec.loader is not None
    actions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actions)

    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-07-02.md").write_text(
        "---\nid: mem-2026-07-02-shipped\ntype: event\nstatus: candidate\n---\n"
        "Shipped weekly UI.\n\n"
        "---\nid: mem-2026-07-02-fact\ntype: fact\nstatus: candidate\n---\n"
        "Fact stays out of hub.\n",
        encoding="utf-8",
    )
    _write_reviewed(
        tmp_path,
        "2026-W27",
        "# Weekly\n\n## Distill\n\n"
        "---\nid: evt-1\ntype: event\nrelated:\n"
        '  - "[1] mem-2026-07-02-shipped"\n'
        '  - "[2] mem-2026-07-02-fact"\n'
        "---\nBody [1][2].\n\n"
        "## Brief\n\nShipped [1]. Fact [2].\n",
    )

    listed = actions.list_tidy_candidates("2026-W27")
    assert listed["outcome"] == "listed"
    assert [c["block_id"] for c in listed["candidates"]] == ["mem-2026-07-02-shipped"]
    assert listed["candidates"][0]["type"] == "event"
    assert listed["candidates"][0]["cite_n"] == "1"
    assert all(c.get("type") == "event" for c in listed["candidates"])
    assert not any(
        c.get("type") in {"fact", "procedure", "decision_constraint", "hypothesis"}
        for c in listed["candidates"]
    )


def test_list_tidy_candidates_includes_four_part_cite_map_events(tmp_path, monkeypatch):
    """Four-part Cite map event quotes appear on Approval Hub with cite_n."""
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    actions_path = Path(__file__).with_name("weekly_actions.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_actions_hub_cite_map_test", actions_path
    )
    assert spec is not None and spec.loader is not None
    actions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actions)

    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    # 2026-W27 = Mon 2026-06-29 .. Sun 2026-07-05
    (daily_dir / "2026-06-30.md").write_text(
        "---\nid: mem-20260630-title-locked\ntype: fact\nstatus: candidate\n---\n"
        "Title locked as fact evidence.\n\n"
        "---\nid: mem-20260630-ship-event\ntype: event\nstatus: candidate\n---\n"
        "Shipped the listing.\n\n",
        encoding="utf-8",
    )
    _write_reviewed(
        tmp_path,
        "2026-W27",
        "Weekly Brief — 2026-W27\n\n"
        "Monday — 2026-06-30 · Events [1]\n"
        "Title work landed.\n\n"
        "Conflict\n- None.\n\n"
        "Hypothesis\n- None.\n\n"
        "Possible overdue report\n- None.\n\n"
        "Cite map\n"
        "- [1] event mem-20260630-title-locked\n"
        "- [2] event mem-20260630-ship-event\n",
    )

    listed = actions.list_tidy_candidates("2026-W27")
    assert listed["outcome"] == "listed"
    by_id = {c["block_id"]: c for c in listed["candidates"]}
    assert "mem-20260630-title-locked" in by_id
    assert by_id["mem-20260630-title-locked"]["cite_n"] == "1"
    assert by_id["mem-20260630-title-locked"]["type"] == "event"
    assert by_id["mem-20260630-ship-event"]["cite_n"] == "2"
    assert by_id["mem-20260630-ship-event"]["type"] == "event"


def test_list_tidy_candidates_includes_uncited_week_events(tmp_path, monkeypatch):
    """Approval Hub lists every week daily event, even when Brief omits it."""
    weekly, tidy = _load_modules(tmp_path, monkeypatch)
    actions_path = Path(__file__).with_name("weekly_actions.py")
    spec = importlib.util.spec_from_file_location(
        "memory_weekly_actions_hub_uncited_test", actions_path
    )
    assert spec is not None and spec.loader is not None
    actions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actions)

    daily_dir = tmp_path / "memories" / "staging" / "daily"
    daily_dir.mkdir(parents=True)
    # 2026-W27 = Mon 2026-06-29 .. Sun 2026-07-05
    (daily_dir / "2026-06-30.md").write_text(
        "---\nid: mem-2026-06-30-cited\ntype: event\nstatus: candidate\n---\n"
        "Cited kickoff.\n\n",
        encoding="utf-8",
    )
    (daily_dir / "2026-07-01.md").write_text(
        "---\nid: mem-2026-07-01-uncited\ntype: event\nstatus: candidate\n---\n"
        "Uncited ship event.\n\n"
        "---\nid: mem-2026-07-01-fact\ntype: fact\nstatus: candidate\n---\n"
        "Fact stays out.\n",
        encoding="utf-8",
    )
    _write_reviewed(
        tmp_path,
        "2026-W27",
        "# Weekly\n\n## Distill\n\n"
        "---\nid: evt-1\ntype: event\nrelated:\n"
        '  - "[1] mem-2026-06-30-cited"\n'
        "---\nBody [1].\n\n"
        "## Brief\n\nKickoff [1].\n",
    )

    listed = actions.list_tidy_candidates("2026-W27")
    assert listed["outcome"] == "listed"
    ids = [c["block_id"] for c in listed["candidates"]]
    assert ids == ["mem-2026-06-30-cited", "mem-2026-07-01-uncited"]
    by_id = {c["block_id"]: c for c in listed["candidates"]}
    assert by_id["mem-2026-06-30-cited"]["cite_n"] == "1"
    assert "cite_n" not in by_id["mem-2026-07-01-uncited"] or not by_id[
        "mem-2026-07-01-uncited"
    ].get("cite_n")
    assert all(c.get("type") == "event" for c in listed["candidates"])
