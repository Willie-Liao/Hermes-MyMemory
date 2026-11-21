"""ACT-R base-level strength so recency is a power law, not a decay cron.

Exponential MemoryBank decay would drop repeatedly used June cards; Anderson &
Schooler need-probability is t^{-d}. Neighbor credit is positive-sum but capped
so one hot entity cannot inflate a 40-member clique.
"""

from __future__ import annotations

import math
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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
_MONTH_KEY_ID = re.compile(
    r"^mem-(\d{4}-\d{2})-key-(decision|procedure)-[0-9a-f]+$"
)


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
    last_recall_at: date | str | None = None,
    now: date | None = None,
) -> float:
    """Decay from the last recall, with earlier uses spread across the card's life.

    A single clock from first_seen keeps a card strong months after it stopped
    being used. Petrov's hybrid keeps the exact t^{-d} of the latest recall and
    approximates the older ones from first_seen, so no recall-date list is stored.
    Dividing by (1-d) matches the previous one-clock formula when last_recall_at
    is missing, so archive thresholds do not move for cards that were never stamped.
    """
    n = max(float(recall_n or 0.0), 0.0)
    if n <= 0:
        return 0.0
    today = now or date.today()
    born = _as_date(first_seen, today) or today
    recent_on = _as_date(last_recall_at, None) or born
    t1 = max((today - recent_on).days, 0.5)
    tn = max((today - born).days, t1)
    recent = min(n, 1.0) * (t1 ** -D)
    older_n = max(n - 1.0, 0.0)
    if older_n == 0.0 or tn == t1:
        older = older_n * (t1 ** -D)
    else:
        older = older_n * ((tn ** (1.0 - D)) - (t1 ** (1.0 - D))) / ((1.0 - D) * (tn - t1))
    total = recent + older
    if total <= 0.0:
        return 0.0
    return math.log(total / (1.0 - D))


def strength_value(
    *,
    recall_n: float,
    first_seen: date | str | None,
    importance: int = 3,
    now: date | None = None,
    last_recall_at: date | str | None = None,
    evidence: Sequence[Mapping[str, Any]] = (),
) -> float:
    """Use evidence cards so a month rewrite cannot wipe strength that still lives on those days.

    An empty evidence list keeps the caller's own counter. Python round is not
    used for importance; the caller passes floor(mean + 0.5).
    """
    if evidence:
        levels = []
        for card in evidence:
            n = float(card.get("recall_n") or 0.0)
            if n <= 0:
                continue
            levels.append(
                base_level(
                    n,
                    card.get("first_seen") or card.get("valid_from"),
                    last_recall_at=card.get("last_recall_at"),
                    now=now,
                )
            )
        b = max(levels) if levels else 0.0
    else:
        b = base_level(
            recall_n,
            first_seen,
            last_recall_at=last_recall_at,
            now=now,
        )
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
    credit: float = 1.0,
) -> dict[str, Any]:
    """Add recall credit. A full hit moves last_recall_at; a shown neighbor does not.

    Treating a sidebar mention as a recall today would reset decay for a card
    the user did not ask for. credit below 1 only raises recall_n.
    """
    today = now or date.today()
    added = float(credit)
    n = float(parsed.get("recall_n") or 0) + added
    first = parsed.get("first_seen") or parsed.get("valid_from") or today.isoformat()
    parsed["recall_n"] = n
    parsed["first_seen"] = str(first)[:10]
    if added >= 1.0:
        parsed["last_recall_at"] = today.isoformat()
    imp = int(parsed.get("importance") or 3)
    parsed["strength"] = round(
        strength_value(
            recall_n=n,
            first_seen=parsed["first_seen"],
            last_recall_at=parsed.get("last_recall_at"),
            importance=imp,
            now=today,
        ),
        4,
    )
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


def _month_file(staging: Path | None, month_key: str) -> Path:
    """Resolve the month file under the caller's staging, not the process home.

    stamp runs from tests and from prefetch with an explicit root. month_file_path
    always follows HERMES_HOME, which would write the live month during a unit test.
    """
    if staging is not None:
        return Path(staging) / "monthly" / f"{month_key}.md"
    from monthly_state import month_file_path

    return month_file_path(month_key)


def _stamp_month_row(
    month_key: str,
    kind: str,
    mem_id: str,
    *,
    staging: Path | None,
    now: date | None,
    credit: float,
    seen: set[str] | None = None,
    index: Any = None,
) -> dict[str, Any] | None:
    """Write the row's own counter and leave the same credit on each evidence card.

    A later rewrite can reset the row. The daily cards keep the credit, and the
    row's stored strength is recomputed from those cards after this stamp.
    A missing evidence card is skipped so one bad id cannot abort the turn.
    """
    from monthly_writer import dump_yaml, loads, payload_from_dict
    from .ids import BlockIndex

    path = _month_file(staging, month_key)
    if not path.is_file():
        return None
    try:
        payload = loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data = payload.to_dict()
    section = "key_decisions" if kind == "decision" else "key_procedures"
    target = None
    for row in data.get(section) or []:
        if isinstance(row, dict) and str(row.get("id") or "") == mem_id:
            target = row
            break
    if target is None:
        return None
    store = index if index is not None else BlockIndex(path.parent.parent)
    evidence_ids = [str(item).strip() for item in (target.get("evidence") or []) if str(item).strip()]
    for evidence_id in evidence_ids:
        stamp_recall_on_card(
            evidence_id,
            staging=staging,
            now=now,
            credit=1.0,
            index=store,
            seen=seen,
        )
    cards = []
    for evidence_id in evidence_ids:
        rec = store.get(evidence_id)
        if rec is not None:
            cards.append(dict(rec.parsed))
    apply_recall(target, now=now, credit=credit)
    if cards:
        importance = math.floor(
            sum(int(card.get("importance") or 3) for card in cards) / len(cards) + 0.5
        )
    else:
        importance = 3
    target["importance"] = importance
    target["strength"] = round(
        strength_value(
            recall_n=0.0,
            first_seen=None,
            importance=importance,
            now=now,
            evidence=cards,
        ),
        4,
    )
    try:
        body = dump_yaml(payload_from_dict(data))
        path.write_text(body, encoding="utf-8")
    except (OSError, ValueError):
        return None
    return {key: target.get(key) for key in (*OPTIONAL_FIELDS, "importance")}


def stamp_recall_on_card(
    block_id: str,
    *,
    staging: Path | None = None,
    now: date | None = None,
    credit: float = 1.0,
    index: Any = None,
    seen: set[str] | None = None,
) -> dict[str, Any] | None:
    """Persist apply_recall onto the daily card or the monthly key row.

    Chat recall used to leave staging unchanged; pytest-only apply_recall never
    flushed. Fail-open: missing card or IO error returns None and does not raise.
    index lets one turn stamp many cards without rescanning daily/*.md each time.
    seen skips a daily id already credited in this turn, so two monthly rows
    that cite the same card add 1, not 2.
    """
    from .ids import BlockIndex, _FRONTMATTER_RE

    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("RECALL_PERSIST_IN_TEST"):
        return None

    mem_id = str(block_id or "").strip()
    month_hit = _MONTH_KEY_ID.match(mem_id)
    if month_hit:
        return _stamp_month_row(
            month_hit.group(1),
            month_hit.group(2),
            mem_id,
            staging=staging,
            now=now,
            credit=credit,
            seen=seen,
            index=index,
        )
    if seen is not None and mem_id in seen:
        return None

    store = index if index is not None else BlockIndex(staging)
    rec = store.get(mem_id)
    if rec is None or not rec.path.is_file():
        return None
    parsed = dict(rec.parsed)
    apply_recall(parsed, now=now, credit=credit)
    rec.parsed.update({key: parsed.get(key) for key in OPTIONAL_FIELDS})
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
    if seen is not None:
        seen.add(mem_id)
    return {key: parsed.get(key) for key in OPTIONAL_FIELDS}


def should_drop_from_prefetch(parsed: Mapping[str, Any]) -> bool:
    """L1 forget reads live decay so a snapshot from the stamp day cannot keep a cold card in Band B."""
    if "recall_n" not in parsed and "strength" not in parsed:
        return False
    try:
        live = strength_value(
            recall_n=float(parsed.get("recall_n") or 0),
            first_seen=parsed.get("first_seen") or parsed.get("valid_from"),
            last_recall_at=parsed.get("last_recall_at"),
            importance=int(parsed.get("importance") or 3),
        )
    except (TypeError, ValueError):
        return False
    return live < THETA_INJECT


def should_archive_body(parsed: Mapping[str, Any], *, now: date | None = None) -> bool:
    """L2 forget uses live decay so a stale snapshot cannot keep an unused card.

    The stored strength is whatever it was on the last stamp. Reading it here
    would skip archive for a card whose last recall is long past.
    """
    item_type = str(parsed.get("type") or "").strip()
    if item_type == "decision":
        return False
    try:
        importance = int(parsed.get("importance") or 3)
    except (TypeError, ValueError):
        importance = 3
    if importance > 3:
        return False
    today = now or date.today()
    try:
        strength = strength_value(
            recall_n=float(parsed.get("recall_n") or 0),
            first_seen=parsed.get("first_seen") or parsed.get("valid_from"),
            last_recall_at=parsed.get("last_recall_at"),
            importance=importance,
            now=today,
        )
    except (TypeError, ValueError):
        return False
    if strength >= THETA_ARCHIVE:
        return False
    seen = _as_date(parsed.get("first_seen") or parsed.get("valid_from"), None)
    if seen is None:
        return False
    return (today - seen).days > ARCHIVE_AGE_DAYS
