from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    plugins = Path(__file__).resolve().parent

    def _mod(name: str, rel: str):
        path = plugins / rel
        spec = importlib.util.spec_from_file_location(f"test_{name}", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    return _mod("review_html", "memory_review_html.py")


def _write_reviewed(tmp_path: Path, week: str, body: str) -> Path:
    path = tmp_path / "memories" / "staging" / "weekly" / f"{week} reviewed.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_render_contains_marker_and_payload_no_localhost(tmp_path, monkeypatch):
    html = _load(tmp_path, monkeypatch)
    payload = {
        "schema_version": 1,
        "mode": "weekly",
        "id": "2026-W24",
        "generated_at": "2026-07-11T00:00:00+00:00",
        "status": "open",
        "view": {
            "story": ["Scope note"],
            "needs": [],
            "remember": [
                {
                    "record_id": "mem-20260616-a",
                    "block_id": "mem-20260616-a",
                    "label": "body a",
                    "section": "§1",
                    "text": "body a",
                    "type": "fact",
                }
            ],
        },
        "decisions": [],
    }
    page = html.render_review_html(payload)
    assert html.MARKER in page
    assert 'id="hermes-payload"' in page
    assert "localhost" not in page.casefold()
    assert "127.0.0.1" not in page
    assert "Gate Locked" not in page
    assert "Confirm and send" in page
    assert "MEMORY" in page
    parsed = html.parse_review_html(page)
    assert parsed["mode"] == "weekly"
    assert parsed["id"] == "2026-W24"
    assert parsed["status"] == "open"


def test_parse_rejects_missing_marker():
    plugins = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "test_review_html_bare", plugins / "memory_review_html.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.parse_review_html("<html><body>nope</body></html>")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "marker" in str(exc).casefold()


def test_confirm_payload_roundtrip_and_apply_weekly(tmp_path, monkeypatch):
    review = _load(tmp_path, monkeypatch)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    daily_file = daily / "2026-06-16.md"
    daily_file.write_text(
        "---\nid: mem-20260616-b\ntype: fact\nconfidence: high\nstatus: candidate\n"
        "sources: [session s1]\n---\nbody b\n",
        encoding="utf-8",
    )
    _write_reviewed(
        tmp_path,
        "2026-W24",
        "## 1. Proposed additions\n\n- mem-20260616-b: promote me\n",
    )

    path = review.write_weekly_html("2026-W24")
    assert path is not None and path.exists()
    page = path.read_text(encoding="utf-8")
    payload = review.parse_review_html(page)
    payload["status"] = "confirmed"
    payload["decisions"] = [
        {
            "record_id": "mem-20260616-b",
            "label": "promote me",
            "action": "promote",
            "block_id": "mem-20260616-b",
            "hot_target": "MEMORY.md",
            "source": "§1",
            "proposed_text": "promote me (edited)",
        }
    ]
    confirmed = review.render_review_html(payload)
    assert '"status": "confirmed"' in confirmed or '"status":"confirmed"' in confirmed

    out = review.apply_review_html_text(confirmed)
    assert out["outcome"] == "applied"
    assert "status: approved" in daily_file.read_text(encoding="utf-8")
    decisions = tmp_path / "memories" / "staging" / ".tidy-decisions-2026-W24.json"
    assert not decisions.exists()


def test_unconfirmed_does_not_apply(tmp_path, monkeypatch):
    review = _load(tmp_path, monkeypatch)
    _write_reviewed(
        tmp_path,
        "2026-W24",
        "## 1. Proposed additions\n\n- mem-20260616-x: skip\n",
    )
    path = review.write_weekly_html("2026-W24")
    assert path is not None
    out = review.apply_review_html(path)
    assert out["outcome"] == "not_confirmed"


def test_daily_approve_reject_apply(tmp_path, monkeypatch):
    review = _load(tmp_path, monkeypatch)
    daily = tmp_path / "memories" / "staging" / "daily"
    daily.mkdir(parents=True)
    daily_file = daily / "2026-07-10.md"
    daily_file.write_text(
        "---\nid: mem-20260710-a\ntype: fact\nconfidence: high\nstatus: candidate\n"
        "sources: [session s1]\n---\nkeep me\n\n"
        "---\nid: mem-20260710-b\ntype: fact\nconfidence: medium\nstatus: candidate\n"
        "sources: [session s1]\n---\ndrop me\n",
        encoding="utf-8",
    )
    path = review.write_daily_html("2026-07-10")
    assert path is not None
    payload = review.parse_review_html(path.read_text(encoding="utf-8"))
    payload["status"] = "confirmed"
    payload["decisions"] = [
        {
            "record_id": "mem-20260710-a",
            "block_id": "mem-20260710-a",
            "action": "approve",
            "label": "keep me",
            "body": "keep me (edited)",
        },
        {
            "record_id": "mem-20260710-b",
            "block_id": "mem-20260710-b",
            "action": "reject",
            "label": "drop me",
        },
    ]
    out = review.apply_review_html_text(review.render_review_html(payload))
    assert out["outcome"] == "applied"
    text = daily_file.read_text(encoding="utf-8")
    assert "status: approved" in text
    assert "status: rejected" in text
    assert "keep me (edited)" in text


def test_confirm_js_mentions_autosend_paths(tmp_path, monkeypatch):
    review = _load(tmp_path, monkeypatch)
    page = review.render_review_html(
        {
            "schema_version": 1,
            "mode": "daily",
            "id": "2026-07-10",
            "generated_at": "2026-07-11T00:00:00+00:00",
            "status": "open",
            "view": {"blocks": []},
            "decisions": [],
        }
    )
    # Auto-send spike: WeCom bridge first, then Web Share, download last-resort only.
    assert "navigator.share" in page
    assert "ww." in page or "WeixinJSBridge" in page or "wx." in page
    assert "Confirm and send" in page
