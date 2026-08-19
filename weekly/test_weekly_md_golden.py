"""Freeze weekly markdown bytes so later JSON/YAML work cannot silently rewrite Distill+Brief.

Without this snapshot, Step 3 could claim losslessness while the renderer drifted.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

from test_weekly_four_part import _golden_payload, _load_schema

FIXTURES = Path(__file__).with_name("fixtures")
STAGING_W33 = (
    Path(__file__).resolve().parents[3]
    / "memories"
    / "staging"
    / "weekly"
    / "2026-W33.md"
)
GOLDEN_W33 = FIXTURES / "golden_2026-W33.md"


def test_w33_staging_matches_frozen_snapshot_bytes():
    assert GOLDEN_W33.is_file(), "copy 2026-W33.md into fixtures/golden_2026-W33.md first"
    assert STAGING_W33.is_file()
    assert GOLDEN_W33.read_bytes() == STAGING_W33.read_bytes()


def test_fixed_payload_brief_matches_golden_bytes():
    schema = _load_schema()
    payload = schema.assign_typed_citations(_golden_payload(schema))
    rendered = schema.render_weekly_review_brief("2026-W31", payload)
    golden = (FIXTURES / "weekly_four_part_w31.md").read_text(encoding="utf-8")
    assert rendered == golden


def test_golden_fails_if_brief_renderer_output_changes():
    schema = _load_schema()
    payload = schema.assign_typed_citations(_golden_payload(schema))
    rendered = schema.render_weekly_review_brief("2026-W31", payload)
    golden = (FIXTURES / "weekly_four_part_w31.md").read_text(encoding="utf-8")
    mutated = rendered.replace("Weekly Brief — 2026-W31", "Weekly Brief — DRIFT", 1)
    assert mutated != golden


def test_fixed_payload_yaml_matches_json_keys():
    """Weekly file is yaml.safe_dump of JSON, not Distill+Brief."""
    schema = _load_schema()
    wj = _load_weekly_json()
    payload = schema.WeeklyReviewPayload(
        days=(),
        legend={1: "mem-2026-07-27-event-aaaa"},
        week_key="2026-W31",
        entities=(
            schema.WeeklyEntity(
                key="digest",
                canonical="digest",
                aliases=(),
                first_seen=None,
                last_seen=None,
                week_blocks=("mem-2026-07-27-event-aaaa",),
            ),
        ),
    )
    dumped = wj.dump_yaml(payload, generated_at="2026-08-19T00:00:00+08:00")
    obj = yaml.safe_load(dumped)
    json_obj = json.loads(wj.dumps(payload, generated_at="2026-08-19T00:00:00+08:00"))
    assert list(obj.keys()) == list(json_obj.keys())
    assert "## Distill" not in dumped
    assert "Weekly Brief" not in dumped
    golden = (FIXTURES / "weekly_yaml_golden_w31.yaml").read_text(encoding="utf-8")
    assert dumped == golden


def test_yaml_golden_fails_if_serializer_drifts():
    schema = _load_schema()
    wj = _load_weekly_json()
    payload = schema.WeeklyReviewPayload(
        days=(),
        legend={1: "mem-2026-07-27-event-aaaa"},
        week_key="2026-W31",
        entities=(
            schema.WeeklyEntity(
                key="digest",
                canonical="digest",
                aliases=(),
                week_blocks=("mem-2026-07-27-event-aaaa",),
            ),
        ),
    )
    dumped = wj.dump_yaml(payload, generated_at="2026-08-19T00:00:00+08:00")
    golden = (FIXTURES / "weekly_yaml_golden_w31.yaml").read_text(encoding="utf-8")
    assert dumped.replace("digest", "DRIFT", 1) != golden


def _load_weekly_json():
    path = Path(__file__).with_name("weekly_json.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_json_golden", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
