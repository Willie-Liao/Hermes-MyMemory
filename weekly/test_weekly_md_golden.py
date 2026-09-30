"""Pin dump_yaml key order so generate cannot drift from dumps without a failing fixture.

Distill byte freezes of staging/weekly/*.md are retired: live weeks are YAML of JSON.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

from test_weekly_four_part import _load_schema

FIXTURES = Path(__file__).with_name("fixtures")


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
    """Catch silent dump_yaml edits that would still pass key-equality."""
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
    """Load weekly_json by path so golden tests do not depend on package install."""
    path = Path(__file__).with_name("weekly_json.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_json_golden", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
