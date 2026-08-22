"""Tests for Worker 1 distill YAML validator."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).with_name("weekly_distill_validate.py")
    spec = importlib.util.spec_from_file_location("memory_weekly_distill_validate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_HAPPY = """# Weekly distill 2026-W26

## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants:
  - entity: Alex Chen
    role: homeroom_teacher
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
  - "[2] mem-2026-06-29-b"
---
First [1]. Second [2].

## Brief

What happened [1].
"""


def test_happy_path_ok():
    v = _load()
    assert v.validate_weekly_distill(_HAPPY) == []


def test_missing_distill_section():
    v = _load()
    errs = v.validate_weekly_distill("# Weekly\n\n## Brief\nHi\n")
    assert any("Distill" in e for e in errs)


def test_bad_type():
    v = _load()
    md = """## Distill

---
id: x
type: fact
sources: [session x]
related: ["[1] mem-2026-06-29-a"]
---
Body [1].
"""
    errs = v.validate_weekly_distill(md)
    assert any("type" in e and "fact" in e for e in errs)


def test_missing_sources():
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
related: ["[1] mem-2026-06-29-a"]
---
Body [1].
"""
    errs = v.validate_weekly_distill(md)
    assert any("sources" in e for e in errs)


def test_citation_gap():
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
  - "[3] mem-2026-06-29-b"
---
A [1]. B [3].
"""
    errs = v.validate_weekly_distill(md)
    assert any("contiguous" in e for e in errs)


def test_body_related_multiset_mismatch_no_longer_fail_closed():
    """Body↔related cite multisets are owned by normalize_event_citations."""
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
  - "[2] mem-2026-06-29-b"
---
Only cites first [1].
"""
    assert v.validate_weekly_distill(md) == []


def test_non_event_must_link_event_id():
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
---
Body [1].

---
id: hyp-a
type: hypothesis
entity: Example
valid_from: 2026-06-25
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
confidence: medium
status: candidate
---
Orphan hypothesis.
"""
    errs = v.validate_weekly_distill(md)
    assert any("hypothesis" in e.casefold() or "type" in e.casefold() for e in errs)


def test_conflict_missing_status_no_longer_fail_closed():
    """Conflict field inventory is owned by tool schema + render."""
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
---
Body [1].

---
id: cfl-a
type: conflict
sources: [session x]
related: [evt-a]
confidence: medium
---
Tension without status.
"""
    errs = v.validate_weekly_distill(md)
    assert any("conflict" in e for e in errs)


def test_event_missing_entity_no_longer_fail_closed():
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
predicate: example_delivered
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
---
Body [1].
"""
    assert v.validate_weekly_distill(md) == []


def test_event_missing_predicate_no_longer_fail_closed():
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
---
Body [1].
"""
    assert v.validate_weekly_distill(md) == []


def test_event_empty_participants_no_longer_fail_closed():
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants: []
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
---
Body [1].
"""
    assert v.validate_weekly_distill(md) == []


def test_event_sparse_participant_without_role_ok():
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: medium
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
---
Body [1].
"""
    assert v.validate_weekly_distill(md) == []


def test_hypothesis_missing_entity_no_longer_fail_closed():
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
entity: Example
predicate: example_delivered
participants:
  - entity: Example
valid_from: 2026-06-25
valid_to: 2026-06-26
confidence: high
status: candidate
sources: [session x]
related:
  - "[1] mem-2026-06-29-a"
---
Body [1].

---
id: hyp-a
type: hypothesis
valid_from: 2026-06-25
sources: [session x]
related: [evt-a]
confidence: medium
status: candidate
---
Missing entity.
"""
    errs = v.validate_weekly_distill(md)
    assert any("hypothesis" in e for e in errs)


def test_bare_mem_related_without_cite_prefix_ok():
    """Tool path emits bare mem-…; normalize adds [N] later."""
    v = _load()
    md = """## Distill

---
id: evt-a
type: event
sources: [session x]
related:
  - mem-2026-06-29-a
---
Body without markers.
"""
    assert v.validate_weekly_distill(md) == []
