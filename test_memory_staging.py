from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


def _load_memory_staging():
    path = Path(__file__).with_name("memory_staging.py")
    spec = importlib.util.spec_from_file_location("memory_staging_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migrate_legacy_yaml_renames_when_md_missing(tmp_path):
    ms = _load_memory_staging()
    daily = tmp_path / "daily"
    daily.mkdir()
    yaml_path = daily / "2026-06-16.yaml"
    yaml_path.write_text("---\nid: x\ntype: fact\n---\nbody\n", encoding="utf-8")

    migrated = ms.migrate_legacy_daily_yaml(daily)

    assert migrated == [str(daily / "2026-06-16.md")]
    assert not yaml_path.exists()
    assert (daily / "2026-06-16.md").read_text(encoding="utf-8").strip()


def test_migrate_legacy_yaml_merges_when_md_exists(tmp_path):
    ms = _load_memory_staging()
    daily = tmp_path / "daily"
    daily.mkdir()
    md_path = daily / "2026-06-16.md"
    md_path.write_text("existing\n", encoding="utf-8")
    yaml_path = daily / "2026-06-16.yaml"
    yaml_path.write_text("legacy block\n", encoding="utf-8")

    ms.migrate_legacy_daily_yaml(daily)

    assert not yaml_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "existing" in text
    assert "legacy block" in text


def test_iter_daily_staging_files_only_returns_dated_md(tmp_path):
    ms = _load_memory_staging()
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / "2026-06-16.yaml").write_text("legacy\n", encoding="utf-8")
    (daily / "notes.md").write_text("skip\n", encoding="utf-8")

    paths = ms.iter_daily_staging_files(daily)

    assert paths == [daily / "2026-06-16.md"]


def test_iter_daily_pending_weekly_review_excludes_weeks_with_report(tmp_path):
    ms = _load_memory_staging()
    daily = tmp_path / "memories" / "staging" / "daily"
    weekly = tmp_path / "memories" / "staging" / "weekly"
    daily.mkdir(parents=True)
    weekly.mkdir(parents=True)
    # 2026-06-09 is ISO week 24; 2026-06-16/18 are week 25
    (daily / "2026-06-09.md").write_text("week 24\n", encoding="utf-8")
    (daily / "2026-06-16.md").write_text("week 25\n", encoding="utf-8")
    (daily / "2026-06-18.md").write_text("week 25\n", encoding="utf-8")
    (weekly / "2026-W25.md").write_text("weekly done\n", encoding="utf-8")

    paths = ms.iter_daily_pending_weekly_review(tmp_path, migrate_legacy=False)

    assert [p.name for p in paths] == ["2026-06-09.md"]


def test_week_has_report_reviewed_only(tmp_path):
    ms = _load_memory_staging()
    weekly = tmp_path / "memories" / "staging" / "weekly"
    weekly.mkdir(parents=True)
    (weekly / "2026-W25 reviewed.md").write_text("done\n", encoding="utf-8")
    assert ms.week_has_report(tmp_path, 2026, 25) is True

    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    (daily / "2026-06-16.md").write_text("week 25\n", encoding="utf-8")
    (daily / "2026-06-18.md").write_text("week 25\n", encoding="utf-8")
    paths = ms.iter_daily_pending_weekly_review(tmp_path, migrate_legacy=False)
    assert paths == []


def test_eligibility_iso_week_sunday_bumps(tmp_path):
    ms = _load_memory_staging()
    saturday = date(2026, 6, 13)  # ISO 2026-W24
    sunday = date(2026, 6, 14)  # ISO 2026-W24, last day of week
    assert ms.eligibility_iso_week(saturday) == (2026, 24)
    assert ms.eligibility_iso_week(sunday) == (2026, 25)


def test_eligibility_iso_week_year_rollover_sunday(tmp_path):
    ms = _load_memory_staging()
    # 2025-12-28 is Sunday in ISO week 52; bump lands in 2026-W01
    sunday = date(2025, 12, 28)
    assert sunday.isocalendar() == (2025, 52, 7)
    assert ms.eligibility_iso_week(sunday) == (2026, 1)


def _write_daily_block(hermes_home: Path, *, day: str, mem_id: str, body: str, status: str = "candidate") -> Path:
    daily = hermes_home / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    path = daily / f"{day}.md"
    path.write_text(
        f"---\nid: {mem_id}\ntype: fact\nconfidence: high\nstatus: {status}\n"
        f"sources: [session s1]\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_patch_daily_block_status_omitted_body_preserves_body(tmp_path):
    ms = _load_memory_staging()
    mem_id = "mem-2026-06-16-preserve-body"
    path = _write_daily_block(
        tmp_path, day="2026-06-16", mem_id=mem_id, body="original staging body"
    )

    ok = ms.patch_daily_block_status(
        tmp_path,
        mem_id,
        status="rejected",
        timestamp_field="discarded_at",
        timestamp_value="2026-07-16T12:00:00",
    )

    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert "status: rejected" in text
    assert "discarded_at: '2026-07-16T12:00:00'" in text or "discarded_at: 2026-07-16T12:00:00" in text
    assert "original staging body" in text


def test_patch_daily_block_status_with_body_replaces_body(tmp_path):
    ms = _load_memory_staging()
    mem_id = "mem-2026-06-16-edit-body"
    path = _write_daily_block(
        tmp_path, day="2026-06-16", mem_id=mem_id, body="original staging body"
    )

    ok = ms.patch_daily_block_status(
        tmp_path,
        mem_id,
        status="candidate",
        timestamp_field="updated_at",
        timestamp_value="2026-07-16T12:00:00",
        body="edited staging body",
    )

    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert "status: candidate" in text
    assert "edited staging body" in text
    assert "original staging body" not in text
    assert "updated_at: '2026-07-16T12:00:00'" in text or "updated_at: 2026-07-16T12:00:00" in text


def test_patch_daily_block_status_with_body_leaves_other_blocks(tmp_path):
    ms = _load_memory_staging()
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    path = daily / "2026-06-16.md"
    path.write_text(
        "---\nid: mem-keep\ntype: fact\nstatus: candidate\nsources: [session s1]\n---\nkeep me\n\n"
        "---\nid: mem-edit\ntype: fact\nstatus: candidate\nsources: [session s1]\n---\nreplace me\n",
        encoding="utf-8",
    )

    ok = ms.patch_daily_block_status(
        tmp_path,
        "mem-edit",
        status="candidate",
        timestamp_field="updated_at",
        timestamp_value="2026-07-16T12:00:00",
        body="new body only",
    )

    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert "keep me" in text
    assert "new body only" in text
    assert "replace me" not in text


def test_patch_daily_block_status_preserves_day_wrapup(tmp_path):
    ms = _load_memory_staging()
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    path = daily / "2026-08-15.md"
    path.write_text(
        "---\nid: mem-a\ntype: fact\nconfidence: high\nstatus: candidate\n"
        "sources: [session s1]\n---\nkeep body\n\n"
        "## Day wrap-up\nXiaohongshu infographic as HTML cards\n",
        encoding="utf-8",
    )
    ok = ms.patch_daily_block_status(
        tmp_path,
        "mem-a",
        status="rejected",
        timestamp_field="discarded_at",
        timestamp_value="2026-08-15T12:00:00",
    )
    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert text.rstrip().endswith("Xiaohongshu infographic as HTML cards")
    assert "## Day wrap-up" in text
    assert "keep body" in text
    assert "status: rejected" in text


def _write_daily_block_with_valid_to(
    hermes_home: Path,
    *,
    day: str,
    mem_id: str,
    body: str,
    status: str = "candidate",
    valid_to: str = "open",
) -> Path:
    daily = hermes_home / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    path = daily / f"{day}.md"
    path.write_text(
        f"---\nid: {mem_id}\ntype: fact\nconfidence: high\nstatus: {status}\n"
        f"valid_to: {valid_to}\nsources: [session s1]\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_patch_daily_block_valid_to_sets_date(tmp_path):
    ms = _load_memory_staging()
    mem_id = "mem-2026-06-16-valid-to"
    path = _write_daily_block_with_valid_to(
        tmp_path, day="2026-06-16", mem_id=mem_id, body="span body", valid_to="open"
    )

    ok = ms.patch_daily_block_valid_to(tmp_path, mem_id, valid_to="2026-07-10")

    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert "valid_to: '2026-07-10'" in text or "valid_to: 2026-07-10" in text
    assert "updated_at:" in text


def test_patch_daily_block_valid_to_preserves_body_and_status(tmp_path):
    ms = _load_memory_staging()
    mem_id = "mem-2026-06-16-valid-to-preserve"
    path = _write_daily_block_with_valid_to(
        tmp_path,
        day="2026-06-16",
        mem_id=mem_id,
        body="keep this body",
        status="candidate",
        valid_to="open",
    )

    ok = ms.patch_daily_block_valid_to(tmp_path, mem_id, valid_to="2026-07-10")

    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert "keep this body" in text
    assert "status: candidate" in text
    assert "valid_to: '2026-07-10'" in text or "valid_to: 2026-07-10" in text


def test_patch_daily_block_valid_to_leaves_other_blocks(tmp_path):
    ms = _load_memory_staging()
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    path = daily / "2026-06-16.md"
    path.write_text(
        "---\nid: mem-keep\ntype: fact\nstatus: candidate\nvalid_to: open\n"
        "sources: [session s1]\n---\nkeep me\n\n"
        "---\nid: mem-edit\ntype: fact\nstatus: candidate\nvalid_to: open\n"
        "sources: [session s1]\n---\nedit me\n",
        encoding="utf-8",
    )

    ok = ms.patch_daily_block_valid_to(tmp_path, "mem-edit", valid_to="2026-07-10")

    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert "keep me" in text
    assert "edit me" in text
    # only mem-edit should get the new date; mem-keep stays open
    keep_section, edit_section = text.split("---\nid: mem-edit", 1)
    assert "valid_to: open" in keep_section
    assert "valid_to: '2026-07-10'" in edit_section or "valid_to: 2026-07-10" in edit_section


def test_patch_daily_block_valid_to_missing_id_returns_false(tmp_path):
    ms = _load_memory_staging()
    _write_daily_block_with_valid_to(
        tmp_path, day="2026-06-16", mem_id="mem-exists", body="body", valid_to="open"
    )

    ok = ms.patch_daily_block_valid_to(tmp_path, "mem-missing", valid_to="2026-07-10")

    assert ok is False


def test_patch_daily_block_valid_to_rejects_empty_or_invalid(tmp_path):
    ms = _load_memory_staging()
    mem_id = "mem-2026-06-16-valid-to-reject"
    path = _write_daily_block_with_valid_to(
        tmp_path, day="2026-06-16", mem_id=mem_id, body="body", valid_to="open"
    )
    before = path.read_text(encoding="utf-8")

    for bad in ("", "   ", "not-a-date", "2026/07/10", "2026-7-10"):
        raised = False
        try:
            ms.patch_daily_block_valid_to(tmp_path, mem_id, valid_to=bad)
        except ValueError:
            raised = True
        assert raised, f"expected ValueError for valid_to={bad!r}"

    assert path.read_text(encoding="utf-8") == before


def test_patch_daily_block_valid_to_accepts_open(tmp_path):
    ms = _load_memory_staging()
    mem_id = "mem-2026-06-16-valid-to-open"
    path = _write_daily_block_with_valid_to(
        tmp_path, day="2026-06-16", mem_id=mem_id, body="body", valid_to="2026-07-01"
    )

    ok = ms.patch_daily_block_valid_to(tmp_path, mem_id, valid_to="open")

    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert "valid_to: open" in text


def _write_two_blocks(tmp_path: Path, day: str = "2026-06-16") -> Path:
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    path = daily / f"{day}.md"
    path.write_text(
        "---\nid: mem-keep\ntype: fact\nstatus: candidate\n"
        "sources: [session s1]\n---\nkeep me\n\n"
        "---\nid: mem-drop\ntype: fact\nstatus: candidate\n"
        "sources: [session s1]\n---\ndrop me\n",
        encoding="utf-8",
    )
    return path


def test_remove_daily_block_keeps_file_when_others_remain(tmp_path):
    ms = _load_memory_staging()
    path = _write_two_blocks(tmp_path)

    snap = ms.remove_daily_block(tmp_path, "mem-drop")

    assert snap is not None
    assert snap["block_id"] == "mem-drop"
    assert snap["daily_date"] == "2026-06-16"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "keep me" in text
    assert "drop me" not in text


def test_remove_daily_block_unlinks_file_when_last_block(tmp_path):
    ms = _load_memory_staging()
    path = _write_two_blocks(tmp_path)
    assert ms.remove_daily_block(tmp_path, "mem-keep") is not None

    snap = ms.remove_daily_block(tmp_path, "mem-drop")

    assert snap is not None
    assert snap["before_body"] == "drop me"
    assert snap["daily_date"] == "2026-06-16"
    assert not path.exists(), "last-block delete must unlink the daily file (not leave 0-byte stub)"


def test_patch_daily_block_status_extra_fields_preserve_body(tmp_path):
    ms = _load_memory_staging()
    mem_id = "mem-2026-06-16-canteen-old"
    path = _write_daily_block(
        tmp_path, day="2026-06-16", mem_id=mem_id, body="Canteen is open."
    )

    ok = ms.patch_daily_block_status(
        tmp_path,
        mem_id,
        status="rejected",
        timestamp_field="superseded_at",
        timestamp_value="2026-08-20",
        extra_fields={
            "valid_to": "2026-08-20",
            "rejected_reason": "rejected by mem-2026-08-20-fact-bbbbbbbbbbbb",
        },
    )

    assert ok is True
    text = path.read_text(encoding="utf-8")
    assert "status: rejected" in text
    assert "valid_to: 2026-08-20" in text
    assert "rejected_reason: rejected by mem-2026-08-20-fact-bbbbbbbbbbbb" in text
    assert "Canteen is open." in text


def test_patch_daily_block_status_replace_failure_leaves_original(tmp_path, monkeypatch):
    ms = _load_memory_staging()
    mem_id = "mem-2026-06-16-atomic"
    path = _write_daily_block(
        tmp_path, day="2026-06-16", mem_id=mem_id, body="original staging body"
    )
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(ms.os, "replace", boom)
    try:
        ok = ms.patch_daily_block_status(
            tmp_path,
            mem_id,
            status="rejected",
            timestamp_field="superseded_at",
            extra_fields={"valid_to": "2026-08-20", "rejected_reason": "rejected by user's correction"},
        )
    except OSError:
        ok = False
    assert ok is False
    assert path.read_text(encoding="utf-8") == before
