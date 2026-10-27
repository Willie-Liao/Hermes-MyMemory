"""ACT-R base-level strength so recency is a power law, not a decay cron.

Exponential MemoryBank decay would drop repeatedly used June cards; Anderson &
Schooler need-probability is t^{-d}. Neighbor credit is positive-sum but capped
so one hot entity cannot inflate a 40-member clique.
"""

from __future__ import annotations

import math
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

D = 0.5
W0 = 1.0
W1 = 0.5
ALPHA_NEIGHBOR = 0.25
NEIGHBOR_CREDIT_CAP = 1.0
STRENGTH_MIN = 0.0
STRENGTH_MAX = 10.0
THETA_INJECT = 1.0
THETA_ARCHIVE = 0.5
ARCHIVE_AGE_DAYS = 180

OPTIONAL_FIELDS = ("strength", "recall_n", "last_recall_at", "first_seen")


def _as_date(value: Any, default: date | None = None) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return default


def base_level(
    recall_n: float,
    first_seen: date | str | None,
    *,
    now: date | None = None,
) -> float:
    """Two-scalar ACT-R B_i; t_j clamped ≥ 0.5 so same-day recall is defined."""
    n = max(float(recall_n or 0.0), 0.0)
    if n <= 0:
        return 0.0
    today = now or date.today()
    seen = _as_date(first_seen, today) or today
    t = max((today - seen).days, 0.5)
    return math.log(n / (1.0 - D)) - D * math.log(t)


def strength_value(
    *,
    recall_n: float,
    first_seen: date | str | None,
    importance: int = 3,
    now: date | None = None,
) -> float:
    """Clamp stored strength so missing fields on old blocks still validate."""
    b = base_level(recall_n, first_seen, now=now)
    raw = W0 * b + W1 * (int(importance) - 3)
    return max(STRENGTH_MIN, min(STRENGTH_MAX, raw))


def neighbor_credits(
    neighbors: Mapping[str, float] | Iterable[tuple[str, float]],
) -> dict[str, float]:
    """Depth-1 UTR credit: n_k += α c_ik, total ≤ 1.0, proportional if over cap."""
    if isinstance(neighbors, Mapping):
        items = [(k, float(v)) for k, v in neighbors.items()]
    else:
        items = [(k, float(v)) for k, v in neighbors]
    raw = [(k, ALPHA_NEIGHBOR * w) for k, w in items if w > 0]
    total = sum(c for _, c in raw)
    if total <= 0:
        return {}
    scale = 1.0 if total <= NEIGHBOR_CREDIT_CAP else NEIGHBOR_CREDIT_CAP / total
    return {k: c * scale for k, c in raw}


def apply_recall(
    parsed: dict[str, Any],
    *,
    now: date | None = None,
    neighbor_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Increment recall_n on the seed; return neighbor id → added n (not written here)."""
    today = now or date.today()
    n = float(parsed.get("recall_n") or 0) + 1.0
    first = parsed.get("first_seen") or parsed.get("valid_from") or today.isoformat()
    parsed["recall_n"] = n
    parsed["last_recall_at"] = today.isoformat()
    parsed["first_seen"] = str(first)[:10]
    imp = int(parsed.get("importance") or 3)
    parsed["strength"] = round(strength_value(recall_n=n, first_seen=first, importance=imp, now=today), 4)
    return neighbor_credits(neighbor_weights or {})


def _fmt_field(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _upsert_optional_frontmatter(fm: str, parsed: Mapping[str, Any]) -> str:
    """Keep the existing card body and fence; only add or replace ACT-R keys."""
    keep = []
    skip = {f"{k}:" for k in OPTIONAL_FIELDS}
    for line in fm.splitlines():
        if any(line.startswith(prefix) for prefix in skip):
            continue
        keep.append(line)
    for key in OPTIONAL_FIELDS:
        if key not in parsed:
            continue
        keep.append(f"{key}: {_fmt_field(parsed[key])}")
    return "\n".join(keep)


def stamp_recall_on_card(
    block_id: str,
    *,
    staging: Path | None = None,
    now: date | None = None,
) -> dict[str, Any] | None:
    """Persist apply_recall onto the daily YAML card so L1/L2 have fields to read.

    Chat recall used to leave staging unchanged; pytest-only apply_recall never
    flushed. Fail-open: missing card or IO error returns None and does not raise.
    """
    from .ids import BlockIndex, _FRONTMATTER_RE

    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("RECALL_PERSIST_IN_TEST"):
        return None

    rec = BlockIndex(staging).get(str(block_id or "").strip())
    if rec is None or not rec.path.is_file():
        return None
    parsed = dict(rec.parsed)
    apply_recall(parsed, now=now)
    try:
        text = rec.path.read_text(encoding="utf-8")
    except OSError:
        return None
    parts: list[str] = []
    last = 0
    wrote = False
    for match in _FRONTMATTER_RE.finditer(text):
        parts.append(text[last : match.start()])
        try:
            fm_obj = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            fm_obj = None
        if isinstance(fm_obj, dict) and str(fm_obj.get("id") or "").strip() == rec.block_id:
            new_fm = _upsert_optional_frontmatter(match.group(1), parsed)
            parts.append(f"---\n{new_fm}\n---\n{match.group(2)}")
            wrote = True
        else:
            parts.append(match.group(0))
        last = match.end()
    parts.append(text[last:])
    if not wrote:
        return None
    try:
        rec.path.write_text("".join(parts), encoding="utf-8")
    except OSError:
        return None
    return {key: parsed.get(key) for key in OPTIONAL_FIELDS}


def should_drop_from_prefetch(parsed: Mapping[str, Any]) -> bool:
    """L1 forget: leave the card on disk, omit it from Band B."""
    if "strength" not in parsed:
        return False
    try:
        return float(parsed.get("strength") or 0) < THETA_INJECT
    except (TypeError, ValueError):
        return False


def should_archive_body(parsed: Mapping[str, Any], *, now: date | None = None) -> bool:
    """L2 forget: move body only; never archive decisions."""
    item_type = str(parsed.get("type") or "").strip()
    if item_type == "decision":
        return False
    try:
        importance = int(parsed.get("importance") or 3)
    except (TypeError, ValueError):
        importance = 3
    if importance > 3:
        return False
    try:
        strength = float(parsed.get("strength") or 0)
    except (TypeError, ValueError):
        return False
    if strength >= THETA_ARCHIVE:
        return False
    today = now or date.today()
    seen = _as_date(parsed.get("first_seen") or parsed.get("valid_from"), None)
    if seen is None:
        return False
    return (today - seen).days > ARCHIVE_AGE_DAYS
