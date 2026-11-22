"""recall_memory (channel ladder) and expand_memory (depth≤2 PPR).

Two tools, not three: a mode=auto finder that also expands made search_memory a
duplicate and was rewound. Fail-open to date sort when the subgraph is a clique.
"""

from __future__ import annotations

import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .edges import WEIGHT_OVERLAP, adjacency, load_edges
from .embed import (
    COSINE_FLOOR,
    _cosine,
    _encode_texts,
    _project_off,
    embed_enabled,
    log_fts_miss,
    rerank_embed,
)
from .ids import (
    BlockIndex,
    BlockRecord,
    classify_daily_id,
    classify_weekly_id,
    intervals_overlap,
    iso_week,
    one_line,
    parse_iso_datetime,
    resolve_id,
    staging_root,
)
from .l1 import search_l1
from .lexical import search_lexical
from .normalize import entity_key, load_entity_index, lookup_key
from .policy import DEFAULT_SCOPE, SCOPE_BUDGETS
from .strength import (
    apply_recall,
    neighbor_credits,
    should_drop_from_prefetch,
    stamp_recall_on_card,
    strength_value,
)

_MEM_ID_RE = re.compile(r"\b(mem-[A-Za-z0-9\-]+|w-evt-\d{4}-\d{2}-\d{2}-\d+|w\d{1,2}-e\d+-[A-Za-z0-9\-]+)\b")
PPR_ALPHA = 0.5
PPR_ITERS = 40
EXPAND_K = 8
MAX_DEPTH = 2
INDEX_TYPES = {"fact", "procedure", "decision", "event", "decision_constraint"}
TIME_WIDEN_STAGES = (0, 3, 7)
TIME_HIT_FLOOR = 3

BAND_FIDELITY_RULE = (
    "Date fidelity: real memory ids follow the format "
    "`mem-YYYY-MM-DD-<type>-<4-byte-hash>` (date + type + hash). "
    "When the user's question is about a specific date or timeframe "
    "(e.g. 'when did X happen?', 'last August'), only state the exact day "
    "when the matching `mem-YYYY-MM-DD-...` id is visible in this context. "
    "When the date is incidental (e.g. 'what did you help me with in "
    "August?'), it is fine to answer with the approximate period "
    "(month/season) you can read from the visible mem id or wrap-up text. "
    "If you cannot find the date and the question needs precision, respond "
    "in the user's language: 'I remember this was around {period}, but the "
    "exact day needs verification — should I look it up?'"
)


def _plugin_subpath(name: str) -> Path:
    """Keep monthly/weekly imports on the plugin path so recall does not depend on Hermes cwd."""
    return Path(__file__).resolve().parent.parent / name


def collect_month_scale_rows(staging: Path) -> list[dict[str, Any]]:
    """Index month stories and D/P lookup text so Channel 4 can rank a small bag before daily cards.

    Daily YAML remains the card corpus; these rows only supply locators (weeks or mem-ids).
    """
    mdir = _plugin_subpath("monthly")
    if str(mdir) not in sys.path:
        sys.path.insert(0, str(mdir))
    try:
        from monthly_actions import _iter_payloads
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    try:
        payloads = list(_iter_payloads(staging))
    except Exception:
        return []
    for payload in payloads:
        for item in payload.summary or ():
            text = str(getattr(item, "text", "") or "").strip()
            if not text:
                continue
            weeks = tuple(
                str(w).strip() for w in (getattr(item, "weeks", ()) or ()) if str(w).strip()
            )
            rows.append(
                {
                    "scale": "month",
                    "kind": "summary",
                    "text": text,
                    "weeks": weeks,
                    "month_key": str(getattr(payload, "key", "") or "").strip(),
                }
            )
        for row in list(payload.key_decisions or ()) + list(payload.key_procedures or ()):
            text = str(row.lookup_text() if hasattr(row, "lookup_text") else "").strip()
            if not text:
                continue
            evidence = tuple(
                str(x).strip() for x in (getattr(row, "evidence", ()) or ()) if str(x).strip()
            )
            rows.append(
                {
                    "scale": "month",
                    "kind": "dp",
                    "text": text,
                    "mem_id": str(getattr(row, "id", "") or "").strip(),
                    "evidence": evidence,
                    "month_key": str(getattr(payload, "key", "") or "").strip(),
                }
            )
    return rows


def collect_week_scale_rows(
    staging: Path, week_keys: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Index weekly.md summary bullets so a month/week cosine hit can map to one civil day."""
    wdir = _plugin_subpath("weekly")
    if str(wdir) not in sys.path:
        sys.path.insert(0, str(wdir))
    try:
        from weekly_json import load_sidecar
    except Exception:
        return []
    weekly = Path(staging) / "weekly"
    if not weekly.is_dir():
        return []
    allow = {str(k).strip() for k in (week_keys or ()) if str(k).strip()}
    rows: list[dict[str, Any]] = []
    for path in sorted(weekly.glob("*.md")):
        if allow and path.stem not in allow:
            continue
        try:
            payload = load_sidecar(path)
        except Exception:
            continue
        start = ""
        rng = payload.get("range") or {}
        if isinstance(rng, dict):
            start = str(rng.get("start") or "")[:10]
        if not start:
            year_s, _, week_s = path.stem.partition("-W")
            try:
                start = date.fromisocalendar(int(year_s), int(week_s), 1).isoformat()
            except ValueError:
                start = ""
        for item in payload.get("summary") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            weekdays = tuple(
                str(name).strip()
                for name in (item.get("weekdays") or [])
                if str(name).strip()
            )
            rows.append(
                {
                    "scale": "week",
                    "kind": "summary",
                    "text": text,
                    "week_key": path.stem,
                    "start": start,
                    "weekdays": weekdays,
                }
            )
    return rows


def rank_plain_passages(
    query: str, rows: Sequence[Mapping[str, Any]]
) -> list[tuple[float, dict[str, Any]]]:
    """Cosine-rank sidecar sentences without the daily [entity] (type) prefix that flattens cards.

    Prefer vectors written at the 01:00 embed-cache tick; encode leftovers on the query path
    so a cache miss cannot skip Channel 4.
    """
    from .embed_cache import load_cache, scale_block_id

    q = str(query or "").strip()
    live = [dict(row) for row in rows if str(row.get("text") or "").strip()]
    if not q or not live:
        return []
    try:
        qv_list = _encode_texts([q])
    except Exception:
        return []
    if not qv_list or not qv_list[0]:
        return []
    qv = qv_list[0]
    cache = load_cache()
    scored: list[tuple[float, dict[str, Any]]] = []
    missing: list[dict[str, Any]] = []
    for row in live:
        bid = scale_block_id(row)
        vec = (cache.get(bid) or {}).get("embedding") or []
        if vec:
            score = _cosine(qv, vec)
            if score >= COSINE_FLOOR:
                scored.append((score, row))
            continue
        missing.append(row)
    if missing:
        try:
            vecs = _encode_texts([str(row["text"]) for row in missing])
        except Exception:
            vecs = []
        if len(vecs) == len(missing):
            for row, vec in zip(missing, vecs):
                score = _cosine(qv, vec)
                if score >= COSINE_FLOOR:
                    scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _weekday_names() -> tuple[str, ...]:
    """Reuse weekly Monday=0 labels so locate cannot invent a calendar of its own."""
    wdir = _plugin_subpath("weekly")
    if str(wdir) not in sys.path:
        sys.path.insert(0, str(wdir))
    try:
        from weekly_event_schema import WEEKDAY_NAMES

        return WEEKDAY_NAMES
    except Exception:
        return (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )


def _civil_day_from_week_row(row: Mapping[str, Any], query: str) -> str | None:
    """Map a Band C weekday suffix onto the week's printed Monday start as one ISO date."""
    start_s = str(row.get("start") or "")[:10]
    try:
        start = date.fromisoformat(start_s)
    except ValueError:
        return None
    names = [str(n).strip() for n in (row.get("weekdays") or ()) if str(n).strip()]
    if not names:
        return None
    qcf = str(query or "").casefold()
    pick = names[0]
    for name in names:
        if name.casefold() in qcf:
            pick = name
            break
    try:
        offset = _weekday_names().index(pick)
    except ValueError:
        return None
    return (start + timedelta(days=offset)).isoformat()


def _locator_from_week_hit(query: str, row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Refuse a whole-week dump: no weekday on the bullet means this scale missed."""
    day = _civil_day_from_week_row(row, query)
    if not day:
        return None
    return ("day", day)


def _locator_from_scale_hit(
    query: str, row: Mapping[str, Any], staging: Path
) -> tuple[str, str] | None:
    """Turn a month/week cosine row into Channel 1 id or one civil day for recall_memory re-entry."""
    kind = str(row.get("kind") or "")
    if kind == "dp":
        for mid in (str(row.get("mem_id") or ""), *list(row.get("evidence") or ())):
            mid = str(mid).strip()
            if mid:
                return ("id", mid)
        return None
    weeks = tuple(str(w).strip() for w in (row.get("weeks") or ()) if str(w).strip())
    week_rows = collect_week_scale_rows(staging, weeks if weeks else None)
    ranked = rank_plain_passages(query, week_rows)
    if ranked:
        return _locator_from_week_hit(query, ranked[0][1])
    return _locator_from_week_hit(query, row)


def _scale_embed_locate(query: str, staging: Path) -> tuple[str, str] | None:
    """Month bag then week bag; first cosine hit above the floor yields a locate pair."""
    month_hits = rank_plain_passages(query, collect_month_scale_rows(staging))
    if month_hits:
        loc = _locator_from_scale_hit(query, month_hits[0][1], staging)
        if loc:
            return loc
    week_hits = rank_plain_passages(query, collect_week_scale_rows(staging))
    if week_hits:
        return _locator_from_week_hit(query, week_hits[0][1])
    return None


def _week(rec: BlockRecord) -> str:
    return iso_week(rec.day)


def format_id_block(rec: BlockRecord) -> str:
    """Paged-in daily YAML — Channel 1 returns the card, not a summary.

    Exact-id lookup is the audit door: include status/valid_to/rejected_reason
    so a rejected card remains inspectable after default recall hides it.
    """
    rel = rec.parsed.get("related")
    lines = [
        f"## Memory / block  channel=id  file=daily/{rec.path.name}",
        f"id: {rec.block_id}",
        f"type: {rec.item_type}",
        f"entity: {rec.entity}",
        f"related: {rel}",
        f"valid_from: {rec.parsed.get('valid_from') or rec.day}",
        f"valid_to: {rec.parsed.get('valid_to') or ''}",
        f"status: {rec.parsed.get('status') or ''}",
        f"importance: {rec.parsed.get('importance', '')}",
    ]
    reason = str(rec.parsed.get("rejected_reason") or "").strip()
    if reason:
        lines.append(f"rejected_reason: {reason}")
    lines.extend(["---", rec.body.strip()])
    return "\n".join(lines)


def _parse_recall_bounds(
    time_from: str | None, time_to: str | None
) -> tuple[datetime, datetime] | None:
    """Require both ISO bounds; otherwise keep the existing no-time ladder.

    A half-specified or garbage window must not silently drop entity hits.
    """
    if not str(time_from or "").strip() or not str(time_to or "").strip():
        return None
    lo = parse_iso_datetime(time_from, end_of_day=False)
    hi = parse_iso_datetime(time_to, end_of_day=True)
    if lo is None or hi is None:
        return None
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _is_rejected(rec: BlockRecord) -> bool:
    return str(rec.parsed.get("status") or "").strip() == "rejected"


def _capped_linked_events(
    targets: Mapping[str, Sequence[str]],
    store: BlockIndex | None,
    *,
    cap: int,
) -> dict[str, list[BlockRecord]]:
    """Keep only the newest linked events so an old episode cannot outvote the recent situation.

    One event often names several cards. The cap is applied per target before
    any cosine, and a rejected event never enters that list.
    """
    chosen: dict[str, list[BlockRecord]] = {str(key): [] for key in targets}
    if store is None or cap <= 0 or not targets:
        return chosen
    by_cite: dict[str, list[BlockRecord]] = {}
    for rec in store.records:
        if rec.item_type != "event" or _is_rejected(rec):
            continue
        for rel in rec.related:
            by_cite.setdefault(str(rel), []).append(rec)
    limit = int(cap)
    for key, ids in targets.items():
        seen: dict[str, BlockRecord] = {}
        for cid in ids:
            for rec in by_cite.get(str(cid), ()):
                seen[rec.block_id] = rec
        ordered = sorted(
            seen.values(),
            key=lambda rec: (rec.day or "", rec.block_id),
            reverse=True,
        )
        chosen[str(key)] = ordered[:limit]
    return chosen


def _unit_residual(
    vec: Sequence[float], mean_dir: Sequence[float] | None
) -> list[float] | None:
    """Project off the cone and renormalise so a centroid is not pulled by vector length.

    A residual that is exactly the cone has no direction left, so it is dropped
    instead of becoming a zero vector in the average.
    """
    projected = _project_off(vec, mean_dir)
    norm = math.sqrt(sum(value * value for value in projected))
    if norm == 0.0:
        return None
    return [value / norm for value in projected]


def linked_event_scores(
    query_vec: Sequence[float] | None,
    targets: Mapping[str, Sequence[str]],
    store: BlockIndex | None,
    *,
    cap: int,
    cache: Mapping[str, Any] | None,
    mean_dir: Sequence[float] | None = None,
    centroids_out: dict[str, list[float]] | None = None,
) -> dict[str, float]:
    """Score each target by the mean of its capped events so one episode cannot define usage.

    Closest-event max let a single neighbour outvote the rest of the card's
    history. The centroid is that usage vector. A target with no vectors stays 0.
    """
    scores = {str(key): 0.0 for key in targets}
    if store is None or not query_vec or not targets:
        return scores
    chosen = _capped_linked_events(targets, store, cap=cap)
    cache = cache or {}
    vectors: dict[str, list[float]] = {}
    width = len(list(query_vec))
    for recs in chosen.values():
        for rec in recs:
            if rec.block_id in vectors:
                continue
            raw = (cache.get(rec.block_id) or {}).get("embedding") or []
            if raw and len(raw) == width:
                vectors[rec.block_id] = [float(x) for x in raw]
    prepared = {
        eid: unit
        for eid, raw in vectors.items()
        if (unit := _unit_residual(raw, mean_dir)) is not None
    }
    query = _unit_residual(query_vec, mean_dir)
    if query is None or not prepared:
        return scores
    for key, recs in chosen.items():
        rows = [prepared[rec.block_id] for rec in recs if rec.block_id in prepared]
        if not rows:
            continue
        dim = len(rows[0])
        mean = [0.0] * dim
        for row in rows:
            for index, value in enumerate(row):
                mean[index] += value
        scale = 1.0 / len(rows)
        mean = [value * scale for value in mean]
        centroid = _unit_residual(mean, None)
        if centroid is None:
            continue
        if centroids_out is not None:
            centroids_out[str(key)] = centroid
        scores[str(key)] = sum(left * right for left, right in zip(query, centroid))
    return scores


def hub_bias(
    cards: Mapping[str, Sequence[float] | None],
    centroids: Mapping[str, Sequence[float]],
    bank: Sequence[tuple[str, Sequence[float]]],
    *,
    k: int = 8,
    alpha: float = 1.0,
    exclude: Mapping[str, Sequence[str]] | None = None,
    beta: float = 0.95,
) -> dict[str, float]:
    """Penalise a card that sits near many unrelated events so popularity is not relevance.

    The penalty does not depend on the query, so the pile loop can reuse it.
    A card's own capped events are left out; otherwise every used card would
    subtract the episodes that define it. An empty bank leaves the bias at 0.
    ``beta`` matches the usage-channel discount in monthly guidance.
    """
    owned = exclude or {}
    limit = max(int(k), 0)
    out: dict[str, float] = {}
    for card_id, lookup in cards.items():
        key = str(card_id)
        skip = {str(item) for item in owned.get(key, ())}
        centroid = centroids.get(key)
        vals: list[float] = []
        for event_id, vec in bank:
            if str(event_id) in skip:
                continue
            trig = _cosine(vec, lookup) if lookup else 0.0
            use = _cosine(vec, centroid) if centroid else 0.0
            vals.append(max(trig, beta * use))
        if limit <= 0 or not vals:
            out[key] = 0.0
            continue
        top = sorted(vals, reverse=True)[:limit]
        out[key] = float(alpha) * (sum(top) / len(top))
    return out


def event_bank(
    store: BlockIndex | None,
    cache: Mapping[str, Any] | None,
    mean_dir: Sequence[float] | None = None,
) -> list[tuple[str, list[float]]]:
    """R2 vectors of non-rejected daily events, the reference set for hubness.

    Monthly rows live in the same cache. Counting them would penalise a card
    for resembling other guidance, which is a different question than popularity
    across situations.
    """
    if store is None:
        return []
    rows = cache or {}
    bank: list[tuple[str, list[float]]] = []
    for rec in store.records:
        if rec.item_type != "event" or _is_rejected(rec):
            continue
        raw = (rows.get(rec.block_id) or {}).get("embedding") or []
        if not raw:
            continue
        unit = _unit_residual(raw, mean_dir)
        if unit is None:
            continue
        bank.append((rec.block_id, unit))
    return bank


def _time_hits(store: BlockIndex, lo: datetime, hi: datetime) -> list[BlockRecord]:
    hits = [
        rec
        for rec in store.records
        if not _is_rejected(rec)
        and intervals_overlap(rec.occurred_start, rec.occurred_end, lo, hi)
    ]
    hits.sort(
        key=lambda rec: (
            rec.occurred_start or datetime.min.replace(tzinfo=timezone.utc),
            rec.block_id,
        )
    )
    return hits


def _widen_bounds(lo: datetime, hi: datetime, days: int) -> tuple[datetime, datetime]:
    delta = timedelta(days=days)
    return lo - delta, hi + delta


def _select_time_window(
    store: BlockIndex, lo: datetime, hi: datetime
) -> tuple[list[BlockRecord], int, datetime, datetime]:
    """Progressive 0/3/7-day widen; stop at three hits or the seven-day cap."""
    chosen: list[BlockRecord] = []
    widen = 0
    applied_lo, applied_hi = lo, hi
    for days in TIME_WIDEN_STAGES:
        applied_lo, applied_hi = _widen_bounds(lo, hi, days)
        chosen = _time_hits(store, applied_lo, applied_hi)
        widen = days
        if len(chosen) >= TIME_HIT_FLOOR or days == TIME_WIDEN_STAGES[-1]:
            break
    return chosen, widen, applied_lo, applied_hi


def _rank_or_union(
    semantic: Sequence[BlockRecord],
    timed: Sequence[BlockRecord],
) -> list[BlockRecord]:
    """semantic∩time, then semantic-only, then time-only events, then other time-only."""
    sem_ids = {rec.block_id for rec in semantic}
    time_ids = {rec.block_id for rec in timed}
    seen: set[str] = set()
    out: list[BlockRecord] = []

    def _take(recs: Sequence[BlockRecord]) -> None:
        for rec in recs:
            if rec.block_id in seen:
                continue
            seen.add(rec.block_id)
            out.append(rec)

    _take([rec for rec in semantic if rec.block_id in time_ids])
    _take([rec for rec in semantic if rec.block_id not in time_ids])
    _take(
        [
            rec
            for rec in timed
            if rec.block_id not in sem_ids and rec.item_type == "event"
        ]
    )
    _take([rec for rec in timed if rec.block_id not in sem_ids])
    return out


def _format_time_or(
    query: str,
    recs: Sequence[BlockRecord],
    *,
    lo: datetime,
    hi: datetime,
    widen_days: int,
    cap: int,
) -> str:
    """Surface the applied window so the agent can tell a widened hit from an exact day."""
    lines = [
        (
            f"## Memory / recall  channel=time_or  q={query}  "
            f"time_from={lo.isoformat()}  time_to={hi.isoformat()}  "
            f"widen_days={widen_days}"
        )
    ]
    for rec in recs[:cap]:
        lines.append(
            f"- {rec.block_id}  {rec.day} {_week(rec)}  {rec.item_type}  {one_line(rec.body, 80)}"
        )
    return "\n".join(lines)


def recall_memory(
    query: str,
    k: int = 8,
    *,
    staging: Path | None = None,
    scope: str = DEFAULT_SCOPE,
    valid_from: str | None = None,
    index: BlockIndex | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    mode: str = "normal",
    _skip_scale_embed: bool = False,
    history: Sequence[tuple[float, str]] = (),
) -> str:
    """Channel ladder: id → entity_key → fts5 → month/week/daily embed locate → l1.

    mode=guidance ranks monthly D/P first and falls back to daily decision/procedure
    only, so a task-shaped prefetch cannot inject events. Scale embed is skipped on
    re-entry so a locate hop cannot loop on the same unbounded query.
    """
    q = str(query or "").strip()
    root = staging_root(staging)
    store = index or BlockIndex(root)
    budget = SCOPE_BUDGETS.get(scope, SCOPE_BUDGETS[DEFAULT_SCOPE])
    # Finder k is the tool cap. Hybrid L2=4 is a prefetch budget; clamping here
    # hid the 08-12 cluster at ranks 53–55 of memorydigest.
    cap = max(12, int(k))

    id_hits = _MEM_ID_RE.findall(q)
    if id_hits and (
        classify_daily_id(id_hits[0])
        or classify_weekly_id(id_hits[0])
        or q == id_hits[0]
    ):
        rec = resolve_id(id_hits[0], staging=root, index=store)
        if rec:
            return format_id_block(rec)

    if str(mode or "normal").strip().casefold() == "guidance":
        return _guidance_recall(q, k=cap, staging=root, store=store, history=history)

    bounds = _parse_recall_bounds(time_from, time_to)
    timed: list[BlockRecord] = []
    widen_days = 0
    applied_lo: datetime | None = None
    applied_hi: datetime | None = None
    if bounds:
        timed, widen_days, applied_lo, applied_hi = _select_time_window(
            store, bounds[0], bounds[1]
        )

    idx = load_entity_index(root)
    key = lookup_key(q, idx) or entity_key(q)
    node = idx.get(key) if key else None
    fts = search_lexical(q, k=cap, staging=root)
    if not fts and node:
        surface = str(node.get("canonical") or key or "")
        if surface and surface != q:
            fts = search_lexical(surface, k=cap, staging=root)
    log_fts_miss(q, bool(fts), staging=root)

    member_ids = set(str(x) for x in (node.get("mem_ids") or []) if x) if node else set()
    fts_only: list[BlockRecord] = []
    seen: set[str] = set()
    for row in fts:
        rec = store.get(str(row["id"]))
        if rec is None or rec.block_id in seen:
            continue
        if _is_rejected(rec):
            continue
        if member_ids and rec.block_id in member_ids:
            continue
        fts_only.append(rec)
        seen.add(rec.block_id)
    semantic: list[BlockRecord] = []
    if node and member_ids:
        recs = [store.get(mid) for mid in node["mem_ids"]]
        recs = [r for r in recs if r is not None and not _is_rejected(r)]
        recs.sort(key=lambda r: r.day or "", reverse=True)
        semantic = fts_only + recs
        if not bounds:
            lines = [f"## Memory / recall  channel=entity_key  key={key}"]
            for rec in semantic[:cap]:
                lines.append(
                    f"- {rec.block_id}  {rec.day} {_week(rec)}  {rec.item_type}  {one_line(rec.body, 80)}"
                )
            return "\n".join(lines)
    elif fts:
        for row in fts:
            rec = store.get(str(row["id"]))
            if rec is None or _is_rejected(rec):
                continue
            if rec.block_id in {r.block_id for r in semantic}:
                continue
            semantic.append(rec)
        if not bounds:
            lines = [f"## Memory / recall  channel=fts5  q={q}"]
            for row in fts:
                rec = store.get(str(row["id"]))
                if rec is not None and _is_rejected(rec):
                    continue
                snippet = one_line(rec.body, 100) if rec else ""
                lines.append(
                    f"- rank={row['rank']}  bm25={row['bm25']:.2f}  {row['id']}  {row['day']} {iso_week(str(row['day']))}"
                )
                if rec:
                    lines.append(f"  entity: {rec.entity}")
                    lines.append(f"  one-line: {snippet}")
            return "\n".join(lines)

    if not semantic and embed_enabled(root):
        if not bounds and not _skip_scale_embed:
            loc = _scale_embed_locate(q, root)
            if loc and loc[0] == "id":
                return recall_memory(
                    loc[1],
                    k=k,
                    staging=root,
                    scope=scope,
                    valid_from=valid_from,
                    index=store,
                    mode=mode,
                    _skip_scale_embed=True,
                )
            if loc and loc[0] == "day":
                return recall_memory(
                    q,
                    k=k,
                    staging=root,
                    scope=scope,
                    valid_from=valid_from,
                    index=store,
                    time_from=loc[1],
                    time_to=loc[1],
                    mode=mode,
                    _skip_scale_embed=True,
                )
        live = [rec for rec in store.records if not _is_rejected(rec)]
        reranked = rerank_embed(q, live, k=cap)
        if reranked:
            ids = _MEM_ID_RE.findall(str(reranked))
            if ids and not bounds and not _skip_scale_embed:
                hit_id = ids[0]
                rec = store.get(hit_id) or resolve_id(hit_id, staging=root, index=store)
                if rec:
                    return recall_memory(
                        rec.block_id,
                        k=k,
                        staging=root,
                        scope=scope,
                        valid_from=valid_from,
                        index=store,
                        mode=mode,
                        _skip_scale_embed=True,
                    )
            if not bounds:
                return str(reranked)
            seen_sem = {r.block_id for r in semantic}
            for mid in ids:
                rec = store.get(mid)
                if rec is not None and rec.block_id not in seen_sem:
                    semantic.append(rec)
                    seen_sem.add(rec.block_id)

    if bounds and applied_lo is not None and applied_hi is not None:
        ranked = _rank_or_union(semantic, timed)
        if ranked:
            return _format_time_or(
                q,
                ranked,
                lo=applied_lo,
                hi=applied_hi,
                widen_days=widen_days,
                cap=cap,
            )

    if budget.get("L1"):
        l1 = search_l1(
            q,
            valid_from=valid_from,
            k=min(5, int(budget["L1"])),
            time_from=applied_lo.isoformat() if applied_lo is not None else None,
            time_to=applied_hi.isoformat() if applied_hi is not None else None,
        )
        if l1:
            lines = [f"## Memory / recall  channel=l1  q={q}"]
            for hit in l1:
                if str(hit.get("role")) == "tool":
                    continue
                snippet = one_line(str(hit.get("content") or ""), 160)
                lines.append(f"- role={hit.get('role')}  {snippet}")
            return "\n".join(lines)
    return f"## Memory / recall  channel=miss  q={q}\n"


def _daily_dp_from_vectors(
    query: str,
    *,
    k: int,
    store: BlockIndex,
    query_vec: Sequence[float],
    cache: Mapping[str, Any],
    mean_dir: Sequence[float] | None = None,
) -> str:
    """On a monthly miss, score daily D/P in the same R2 space as the month path.

    A separate raw-cosine floor let a daily card through after the month path
    had already rejected that similarity. The centroid and the hub penalty are
    the ones monthly guidance just used. The event itself is never injected.
    """
    import sys

    mdir = Path(__file__).resolve().parent.parent / "monthly"
    if str(mdir) not in sys.path:
        sys.path.insert(0, str(mdir))
    from monthly_actions import (
        _GUIDANCE_COS_GATE_DAILY,
        _GUIDANCE_EVENT_BETA,
        _GUIDANCE_HUB_ALPHA,
        _GUIDANCE_EVENT_CAP,
        _GUIDANCE_KIND_FLOOR,
        _GUIDANCE_KIND_SHARE,
    )

    allowed = {"decision", "procedure"}
    live = [
        rec
        for rec in store.records
        if rec.item_type in allowed and not _is_rejected(rec)
    ]
    targets = {rec.block_id: [rec.block_id] for rec in live}
    centroids: dict[str, list[float]] = {}
    evt_scores = linked_event_scores(
        query_vec,
        targets,
        store,
        cap=_GUIDANCE_EVENT_CAP,
        cache=cache,
        mean_dir=mean_dir,
        centroids_out=centroids,
    )
    width = len(list(query_vec))
    lookups: dict[str, list[float]] = {}
    for rec in live:
        raw = (cache.get(rec.block_id) or {}).get("embedding") or []
        if raw and len(raw) == width:
            unit = _unit_residual(raw, mean_dir)
            if unit is not None:
                lookups[rec.block_id] = unit
    exclude = {
        key: [rec.block_id for rec in recs]
        for key, recs in _capped_linked_events(
            targets, store, cap=_GUIDANCE_EVENT_CAP
        ).items()
    }
    biases = hub_bias(
        {rec.block_id: lookups.get(rec.block_id) for rec in live},
        centroids,
        event_bank(store, cache, mean_dir),
        alpha=_GUIDANCE_HUB_ALPHA,
        exclude=exclude,
        beta=_GUIDANCE_EVENT_BETA,
    )
    query_unit = _unit_residual(query_vec, mean_dir)
    scored: list[dict[str, Any]] = []
    for rec in live:
        card = lookups.get(rec.block_id)
        s_use = float(evt_scores.get(rec.block_id) or 0.0)
        s_card = float(_cosine(query_unit, card)) if query_unit and card else 0.0
        combined = max(s_card, _GUIDANCE_EVENT_BETA * s_use) - float(
            biases.get(rec.block_id) or 0.0
        )
        parsed = rec.parsed
        strength = strength_value(
            recall_n=float(parsed.get("recall_n") or 0.0),
            first_seen=parsed.get("first_seen") or parsed.get("valid_from"),
            last_recall_at=parsed.get("last_recall_at"),
            importance=int(parsed.get("importance") or 3),
        )
        scored.append(
            {
                "rec": rec,
                "s": combined,
                "s_card": s_card,
                "rank": 0.8 * combined + 0.2 * (strength / 10.0),
                "above": combined >= _GUIDANCE_COS_GATE_DAILY,
            }
        )
    above = [item for item in scored if item["above"]]
    below = [item for item in scored if not item["above"]]
    if not above:
        return f"## Memory / recall  channel=miss  q={query}\n"

    def _order(item: dict[str, Any]) -> tuple:
        return (-item["rank"], -item["s_card"], item["rec"].block_id)

    above.sort(key=_order)
    below.sort(key=_order)
    by_kind: dict[str, list[dict[str, Any]]] = {"decision": [], "procedure": []}
    for item in above:
        by_kind[item["rec"].item_type].append(item)
    limit_n = max(1, int(k))
    share = max(_GUIDANCE_KIND_FLOOR, min(_GUIDANCE_KIND_SHARE, limit_n // 2))
    chosen: dict[str, list[dict[str, Any]]] = {}
    for kind, rows in by_kind.items():
        chosen[kind] = rows[: min(share, len(rows))]
    rest: list[dict[str, Any]] = []
    for kind, rows in by_kind.items():
        rest.extend(rows[len(chosen[kind]) :])
    rest.sort(key=_order)
    room = limit_n - sum(len(rows) for rows in chosen.values())
    for item in rest[: max(room, 0)]:
        item["spill"] = True
        chosen[item["rec"].item_type].append(item)
    below_by_kind: dict[str, list[dict[str, Any]]] = {"decision": [], "procedure": []}
    for item in below:
        below_by_kind[item["rec"].item_type].append(item)

    def _top_up(kind: str, other: str) -> None:
        need = _GUIDANCE_KIND_FLOOR - len(chosen[kind])
        if need <= 0:
            return
        spills = [item for item in chosen[other] if item.get("spill")]
        for item in below_by_kind[kind]:
            if need <= 0:
                break
            if spills:
                chosen[other].remove(spills.pop())
            elif (
                sum(len(rows) for rows in chosen.values()) >= limit_n
                and len(chosen[other]) > _GUIDANCE_KIND_FLOOR
            ):
                chosen[other].pop()
            item["topup"] = True
            chosen[kind].append(item)
            need -= 1

    _top_up("procedure", "decision")
    _top_up("decision", "procedure")
    body = [item for rows in (chosen["decision"], chosen["procedure"]) for item in rows if not item.get("topup")]
    extra = [item for rows in (chosen["decision"], chosen["procedure"]) for item in rows if item.get("topup")]
    body.sort(key=_order)
    lines = [f"## Memory / recall  channel=daily_dp  q={query}"]
    for item in (body + extra)[:limit_n]:
        rec = item["rec"]
        lines.append(
            f"- {rec.block_id}  {rec.day} {_week(rec)}  {rec.item_type}  {one_line(rec.body, 80)}"
        )
    return "\n".join(lines)


def _guidance_recall(
    query: str,
    *,
    k: int,
    staging: Path,
    store: BlockIndex,
    history: Sequence[tuple[float, str]] = (),
) -> str:
    """Monthly D/P first; daily decision/procedure only on a monthly miss — never L1 or events.

    Encode the query once and reuse it for the month rows and, on a miss, the
    daily cards. A second encode, or a keyword hit that skips the event view,
    would hide a card whose past situation fits the line. An acknowledgment
    returns before that encode so "哈哈" cannot pull a card. history is the
    earlier user lines for a short follow-up; the daily fallback still uses
    the latest line's vector when the pile also misses.
    The daily fallback uses the same per-kind share as monthly guidance so a
    long run of one kind cannot fill the block. Live strength is part of the
    rank so an old card loses to a recent one with the same search score.
    """
    import sys

    mdir = Path(__file__).resolve().parent.parent / "monthly"
    if str(mdir) not in sys.path:
        sys.path.insert(0, str(mdir))
    from monthly_actions import (
        _ACK_RE,
        _GUIDANCE_KIND_FLOOR,
        _GUIDANCE_KIND_SHARE,
        format_guidance_hits,
        rank_monthly_guidance,
    )

    if query and _ACK_RE.fullmatch(query):
        return f"## Memory / recall  channel=miss  q={query}\n"
    query_vec = None
    cache = None
    mean_dir = None
    if embed_enabled(staging) and query:
        try:
            from recall.embed_cache import load_cache, mean_direction

            cache = load_cache() or {}
            mean_dir = mean_direction(cache)
        except Exception:
            cache = {}
            mean_dir = None
        try:
            from recall.embed import _encode_texts

            encoded = _encode_texts([query])
            if encoded and encoded[0]:
                query_vec = list(encoded[0])
        except Exception:
            query_vec = None
    hits = rank_monthly_guidance(
        query,
        staging=staging,
        limit=k,
        store=store,
        query_vec=query_vec,
        cache=cache,
        history=history,
        mean_dir=mean_dir,
    )
    if hits:
        return format_guidance_hits(hits)
    if query_vec is not None:
        return _daily_dp_from_vectors(
            query,
            k=k,
            store=store,
            query_vec=query_vec,
            cache=cache or {},
            mean_dir=mean_dir,
        )
    allowed = {"decision", "procedure"}
    fts = search_lexical(query, k=k, staging=staging)
    scored: list[tuple[BlockRecord, float]] = []
    seen: set[str] = set()
    bm25s: list[float] = []
    fts_rows: list[tuple[BlockRecord, float]] = []
    for row in fts:
        rec = store.get(str(row["id"]))
        if rec is None or rec.block_id in seen or _is_rejected(rec):
            continue
        if rec.item_type not in allowed:
            continue
        seen.add(rec.block_id)
        try:
            bm25 = float(row.get("bm25") or 0.0)
        except (TypeError, ValueError):
            bm25 = 0.0
        fts_rows.append((rec, bm25))
        bm25s.append(bm25)
    if fts_rows:
        best = min(bm25s) if bm25s else 0.0
        for rec, bm25 in fts_rows:
            relevance = (bm25 / best) if best else 1.0
            scored.append((rec, relevance))
    elif embed_enabled(staging):
        live = [
            rec
            for rec in store.records
            if rec.item_type in allowed and not _is_rejected(rec)
        ]
        reranked = rerank_embed(query, live, k=k)
        embed_recs: list[BlockRecord] = []
        for mid in _MEM_ID_RE.findall(str(reranked or "")):
            rec = store.get(mid)
            if rec is None or rec.block_id in seen or rec.item_type not in allowed:
                continue
            seen.add(rec.block_id)
            embed_recs.append(rec)
        span = max(len(embed_recs), 1)
        for i, rec in enumerate(embed_recs):
            scored.append((rec, 1.0 - (i / span)))
    if not scored:
        return f"## Memory / recall  channel=miss  q={query}\n"

    ranked: list[tuple[BlockRecord, float, bool]] = []
    for index, (rec, relevance) in enumerate(scored):
        parsed = rec.parsed
        live = strength_value(
            recall_n=float(parsed.get("recall_n") or 0.0),
            first_seen=parsed.get("first_seen") or parsed.get("valid_from"),
            last_recall_at=parsed.get("last_recall_at"),
            importance=int(parsed.get("importance") or 3),
        )
        rank = 0.8 * relevance + 0.2 * (live / 10.0)
        ranked.append((rec, rank, False))
    ranked.sort(key=lambda item: (-item[1], item[0].block_id))
    by_kind: dict[str, list[tuple[BlockRecord, float, bool]]] = {
        "decision": [],
        "procedure": [],
    }
    for item in ranked:
        if item[0].item_type in by_kind:
            by_kind[item[0].item_type].append(item)
    limit_n = max(1, int(k))
    share = max(_GUIDANCE_KIND_FLOOR, min(_GUIDANCE_KIND_SHARE, limit_n // 2))
    chosen: dict[str, list[tuple[BlockRecord, float, bool]]] = {}
    for kind, rows in by_kind.items():
        head = rows[: min(share, len(rows))]
        chosen[kind] = [(rec, rank, False) for rec, rank, _flag in head]
    rest: list[tuple[BlockRecord, float, bool]] = []
    for kind, rows in by_kind.items():
        rest.extend(rows[len(chosen[kind]) :])
    rest.sort(key=lambda item: (-item[1], item[0].block_id))
    room = limit_n - sum(len(rows) for rows in chosen.values())
    for rec, rank, _flag in rest[: max(room, 0)]:
        chosen[rec.item_type].append((rec, rank, True))

    def _top_up(kind: str, other: str) -> None:
        need = _GUIDANCE_KIND_FLOOR - len(chosen[kind])
        if need <= 0 or not embed_enabled(staging):
            return
        have = {rec.block_id for rows in chosen.values() for rec, _r, _s in rows}
        while need > 0:
            pool = [
                rec
                for rec in store.records
                if rec.item_type == kind and rec.block_id not in have and not _is_rejected(rec)
            ]
            reranked = rerank_embed(query, pool, k=need)
            added = False
            spills = [item for item in chosen[other] if item[2]]
            for mid in _MEM_ID_RE.findall(str(reranked or "")):
                if need <= 0:
                    break
                rec = store.get(mid)
                if rec is None or rec.block_id in have or rec.item_type != kind:
                    continue
                have.add(rec.block_id)
                if spills:
                    chosen[other].remove(spills.pop())
                elif (
                    sum(len(rows) for rows in chosen.values()) >= limit_n
                    and len(chosen[other]) > _GUIDANCE_KIND_FLOOR
                ):
                    chosen[other].pop()
                chosen[kind].append((rec, -1.0, False))
                need -= 1
                added = True
            if not added:
                break

    _top_up("procedure", "decision")
    _top_up("decision", "procedure")
    pooled = chosen["decision"] + chosen["procedure"]
    body = [item for item in pooled if item[1] >= 0]
    extra = [item for item in pooled if item[1] < 0]
    body.sort(key=lambda item: (-item[1], item[0].block_id))
    ordered = (body + extra)[:limit_n]
    lines = [f"## Memory / recall  channel=daily_dp  q={query}"]
    for rec, _rank, _spill in ordered:
        lines.append(
            f"- {rec.block_id}  {rec.day} {_week(rec)}  {rec.item_type}  {one_line(rec.body, 80)}"
        )
    return "\n".join(lines)


def _neighborhood(
    seed: str,
    adj: Mapping[str, Mapping[str, tuple[float, str, str]]],
    depth: int,
) -> set[str]:
    seen = {seed}
    frontier = {seed}
    for _ in range(max(depth, 0)):
        nxt: set[str] = set()
        for node in frontier:
            for nb in adj.get(node, {}):
                if nb not in seen:
                    nxt.add(nb)
            for src, dests in adj.items():
                if node in dests and src not in seen:
                    nxt.add(src)
        if not nxt:
            break
        seen.update(nxt)
        frontier = nxt
    return seen


def _ppr(
    seeds: Sequence[str],
    nodes: Sequence[str],
    adj: Mapping[str, Mapping[str, tuple[float, str, str]]],
    alpha: float = PPR_ALPHA,
) -> dict[str, float]:
    n = len(nodes)
    if n == 0:
        return {}
    index = {u: i for i, u in enumerate(nodes)}
    s = [0.0] * n
    live = [x for x in seeds if x in index]
    if not live:
        live = [nodes[0]]
    mass = 1.0 / len(live)
    for u in live:
        s[index[u]] = mass
    # row-normalized P[i][j] = c_ij / sum_k c_ik
    trans = [[0.0] * n for _ in range(n)]
    for i, u in enumerate(nodes):
        nbrs = adj.get(u) or {}
        total = sum(w for w, _t, _s in nbrs.values())
        if total <= 0:
            trans[i][i] = 1.0
            continue
        for v, (w, _t, _s) in nbrs.items():
            if v in index:
                trans[i][index[v]] += w / total
    pi = s[:]
    for _ in range(PPR_ITERS):
        nxt = [alpha * s[j] for j in range(n)]
        for i in range(n):
            if pi[i] == 0:
                continue
            for j in range(n):
                nxt[j] += (1.0 - alpha) * pi[i] * trans[i][j]
        pi = nxt
    return {nodes[i]: pi[i] for i in range(n)}


def expand_memory(
    id_or_key: str,
    depth: int = 2,
    min_weight: float = 0.5,
    *,
    staging: Path | None = None,
    index: BlockIndex | None = None,
) -> str:
    """Inline a direct related neighbor's body so that card can be read without another recall.

    A two-hop or entity-overlap line stays an id. The opened card's own body is not repeated here.
    """
    depth = min(int(depth or 0), MAX_DEPTH)
    root = staging_root(staging)
    store = index or BlockIndex(root)
    seed_id = str(id_or_key or "").strip()
    rec = resolve_id(seed_id, staging=root, index=store)
    if rec is None:
        idx = load_entity_index(root)
        key = lookup_key(seed_id, idx) or entity_key(seed_id)
        node = idx.get(key) if key else None
        if node and node.get("mem_ids"):
            seed_id = str(node["mem_ids"][-1])
            rec = store.get(seed_id)
    if rec is None:
        return f"## Memory / expand  seed={id_or_key}  miss\n"
    seed_id = rec.block_id
    edges = load_edges(root)
    adj = adjacency(edges, min_weight=min_weight)
    nodes = sorted(_neighborhood(seed_id, adj, depth))
    weights = []
    for u in nodes:
        for _v, (w, _t, _s) in (adj.get(u) or {}).items():
            if _v in nodes:
                weights.append(w)
    fail_open = len(nodes) < 3 or (weights and len(set(round(w, 6) for w in weights)) == 1)
    extra_seeds = [seed_id]
    if fail_open:
        ranked = sorted(nodes, key=lambda i: store.get(i).day if store.get(i) else "", reverse=True)
        rank_tag = "date"
        pi_map = {i: 0.0 for i in ranked}
    else:
        pi_map = _ppr(extra_seeds, nodes, adj)
        ranked = sorted(nodes, key=lambda i: (-pi_map.get(i, 0.0), i))
        rank_tag = "ppr"
    lines = [
        f"## Memory / expand  seed={seed_id}  depth={depth}  rank={rank_tag}  alpha={PPR_ALPHA}"
    ]
    for item in ranked[:EXPAND_K]:
        hit = store.get(item)
        if (
            hit is not None
            and str(hit.parsed.get("status") or "").strip() == "rejected"
            and item != seed_id
        ):
            continue
        rel = (adj.get(seed_id) or {}).get(item) or (adj.get(item) or {}).get(seed_id)
        why = "seed" if item == seed_id else ""
        if rel and not why:
            why = f"{rel[1]} {rel[0]}  src={rel[2]}"
        day = hit.day if hit else ""
        week = _week(hit) if hit else ""
        lines.append(
            f"- π={pi_map.get(item, 0):.2f}  {item}  {day} {week}  {why}".rstrip()
        )
        if hit is None or item == seed_id:
            continue
        forward = (adj.get(seed_id) or {}).get(item)
        backward = (adj.get(item) or {}).get(seed_id)
        if not (
            (forward and forward[1] == "related")
            or (backward and backward[1] == "related")
        ):
            continue
        lines.append(f"  type: {hit.item_type}")
        body = (hit.body or "").strip()
        if body:
            lines.append(f"  {body}")
    return "\n".join(lines)


def _wrapup_for_path(path: Path) -> str:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    marker = "## Day wrap-up"
    if marker not in text:
        return "(no wrap-up)"
    rest = text.split(marker, 1)[1].strip()
    return rest or "(no wrap-up)"


def render_bands(
    staging: Path | None = None,
    *,
    today: date | None = None,
    hot_text: str = "",
) -> str:
    """Keep each day's full wrap-up; a 120-character stub dropped later bullets.

    Band C is four weeks of summary bullets plus ranges; entity names only on the older three
    so the newest week does not duplicate Band B. Band D is four month summaries with ranges.
    The day header still ends with ``blk ::`` so an empty staging tree returns no injection.
    """
    root = staging_root(staging)
    store = BlockIndex(root)
    day0 = today or date.today()
    band_a_days = [(day0 - timedelta(days=i)) for i in range(0, 7)]
    daily = root / "daily"
    a_lines = ["## Memory / recent days (wrap-up)"]
    day_files: list[Path] = []
    for d in band_a_days:
        path = daily / f"{d.isoformat()}.md"
        if not path.is_file():
            continue
        day_files.append(path)
        nblk = sum(1 for r in store.records if r.path == path)
        wrap = _wrapup_for_path(path)
        a_lines.append(f"- {d.isoformat()} {iso_week(d)} {nblk}blk ::")
        if wrap == "(no wrap-up)":
            a_lines.append("(no wrap-up)")
        else:
            for bullet in wrap.splitlines():
                line = bullet.strip()
                if line:
                    a_lines.append(line if line.startswith("-") else f"- {line}")

    idx = load_entity_index(root)
    window = {d.isoformat() for d in band_a_days}
    b_lines = ["## Memory / entity index (normalized, last 7 days)"]
    rows = []
    for key, node in idx.items():
        days = [x for x in (node.get("days") or []) if x in window]
        if not days:
            continue
        nblk = sum(1 for mid in node.get("mem_ids") or [] if (store.get(mid) and store.get(mid).day in window))
        surfaces = [node.get("canonical") or key] + list(node.get("aliases") or [])
        surf = "|".join(dict.fromkeys(str(s) for s in surfaces if s))
        compact_days = ",".join(d[5:] if len(d) >= 10 else d for d in sorted(days))
        hot = ""
        if hot_text and any(str(s).casefold() in hot_text.casefold() for s in surfaces if s):
            hot = " [also in hot memory]"
        line = f"- {key} ({surf}) {nblk}blk d={compact_days}{hot}"
        mems = [
            mid
            for mid in (node.get("mem_ids") or [])
            if store.get(mid)
            and store.get(mid).day in window
            and store.get(mid).item_type in INDEX_TYPES
        ][:1]
        if nblk <= 3 and mems:
            line += "  " + " ".join(mems)
        rows.append((-nblk, key, line))
    rows.sort()
    b_lines.extend(r[2] for r in rows[:12])
    b_lines.append("")
    b_lines.append("## Memory / cross-window edges (out-of-day related)")
    edge_n = 0
    for edge in load_edges(root):
        if edge.get("type") != "related":
            continue
        frm = store.get(str(edge.get("from")))
        to = store.get(str(edge.get("to")))
        if not frm or not to or frm.day == to.day:
            continue
        if frm.day not in window and to.day not in window:
            continue
        b_lines.append(f"- {frm.block_id} -related-> {to.block_id}")
        edge_n += 1
        if edge_n >= 8:
            break

    c_lines = ["## Memory / weeks"]
    weekly = root / "weekly"
    if weekly.is_dir():
        import sys

        wdir = Path(__file__).resolve().parent.parent / "weekly"
        if str(wdir) not in sys.path:
            sys.path.insert(0, str(wdir))
        try:
            from weekly_json import load_sidecar
        except Exception:
            load_sidecar = None
        week_paths = sorted(weekly.glob("*.md"))[-4:]
        newest_stem = week_paths[-1].stem if week_paths else ""
        for path in week_paths:
            payload: dict[str, Any] = {}
            if load_sidecar:
                try:
                    payload = load_sidecar(path)
                except Exception:
                    payload = {}
            bullets: list[str] = []
            for row in payload.get("summary") or []:
                if not isinstance(row, dict):
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                days = [
                    str(name).strip()
                    for name in (row.get("weekdays") or [])
                    if str(name).strip()
                ]
                suffix = f" ({', '.join(days)})" if days else ""
                bullets.append(f"- {text}{suffix}")
            if not bullets:
                continue
            week_key = path.stem
            start = ""
            end = ""
            rng = payload.get("range") or {}
            if isinstance(rng, dict):
                start = str(rng.get("start") or "")[:10]
                end = str(rng.get("end") or "")[:10]
            if not start or not end:
                year_s, _, week_s = week_key.partition("-W")
                try:
                    lo = date.fromisocalendar(int(year_s), int(week_s), 1)
                    start, end = lo.isoformat(), (lo + timedelta(days=6)).isoformat()
                except ValueError:
                    start, end = start, end
            c_lines.append(f"### {week_key}  {start}..{end}  f={path.name}")
            c_lines.extend(bullets)
            if week_key != newest_stem:
                names: list[str] = []
                for ent in payload.get("entities") or []:
                    if not isinstance(ent, dict):
                        continue
                    name = str(ent.get("canonical") or ent.get("key") or "").strip()
                    if name:
                        names.append(name)
                if names:
                    c_lines.append("entities: " + ", ".join(names))

    d_text = ""
    try:
        import sys

        mdir = Path(__file__).resolve().parent.parent / "monthly"
        if str(mdir) not in sys.path:
            sys.path.insert(0, str(mdir))
        from monthly_actions import month_band

        d_text = month_band(limit=4, staging=root)
    except Exception:
        d_text = ""

    parts = ["\n".join(a_lines), "\n".join(b_lines), "\n".join(c_lines)]
    if d_text.strip():
        parts.append(d_text.strip())
    text = "\n\n".join(parts).rstrip() + "\n"
    if "blk ::" not in text:
        return ""
    return text + BAND_FIDELITY_RULE


TOOL_SCHEMAS = [
    {
        "name": "recall_memory",
        "description": (
            "Find memory cards by id, entity, or lexical match. Task-shaped turns "
            "should use monthly preference/procedure guidance first; daily YAML "
            "holds card bodies. For a Band C summary weekday, pass time_from and "
            "time_to as that one civil ISO day (Monday = the week's printed start), "
            "not the week span; a Band C entities: canonical name may be query. "
            "After an embed hit the host locates the daily card (mem-id or one "
            "civil day) through this same tool. "
            "When a Band D month range matches, pass that block's printed ISO "
            "start and end. The host always runs expand_memory "
            "on the first seed id after recall (depth 2). Do not call search_memory."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 8},
                "time_from": {
                    "type": "string",
                    "description": (
                        "Optional ISO date or datetime. Set together with time_to: "
                        "one civil day from a Band C weekday, a Band D month range, "
                        "or a user-mentioned time — not the full Band C week span."
                    ),
                },
                "time_to": {
                    "type": "string",
                    "description": (
                        "Optional ISO date or datetime. Set together with time_from: "
                        "one civil day from a Band C weekday, a Band D month range, "
                        "or a user-mentioned time — not the full Band C week span."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "expand_memory",
        "description": (
            "Neighborhood walk. Chat/eval must not use this as the first step: "
            "the host rewrites a lone call into recall_memory then expand."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id_or_key": {"type": "string"},
                "depth": {"type": "integer", "default": 2},
                "min_weight": {"type": "number", "default": 0.5},
            },
            "required": ["id_or_key"],
        },
    },
]


def _first_seed_id(recall_text: str) -> str | None:
    """Skip expand on a miss so ghost ids do not PPR an empty graph."""
    if "channel=miss" in (recall_text or ""):
        return None
    found = _MEM_ID_RE.findall(recall_text or "")
    return found[0] if found else None


def _script_queries(query: str) -> list[str]:
    """Split a mixed CJK+English turn so one FTS string cannot bury the other entity."""
    q = str(query or "").strip()
    if not q:
        return []
    out = [q]
    cjk = "".join(
        ch if ("\u4e00" <= ch <= "\u9fff" or ch.isspace() or ch in "？?，,。、") else " "
        for ch in q
    )
    lat = "".join(ch if ch.isascii() else " " for ch in q)
    for part in (re.sub(r"\s+", " ", cjk).strip(), re.sub(r"\s+", " ", lat).strip()):
        if part and part not in out and len(part) >= 2:
            out.append(part)
    return out


def _ordered_ids(text: str) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for mid in _MEM_ID_RE.findall(text or ""):
        if mid in seen:
            continue
        seen.add(mid)
        ids.append(mid)
    return ids


def _recall_then_expand(
    query: str,
    *,
    k: int = 8,
    staging: Path | None = None,
    min_weight: float = 0.5,
    time_from: str | None = None,
    time_to: str | None = None,
) -> str:
    """Recall first, then expand listed seeds so hops do not depend on rank-1 or a second LLM tool.

    Entity channel pins the newest member first (June plugin), so expanding only that seed
    misses the August semicolon hops still listed further down. A channel=id chunk expands
    only the opened card: its related: ids already have bodies on that expand.
    """
    chunks = []
    expand_seeds: list[str] = []
    seen_seeds: set[str] = set()
    for q in _script_queries(query):
        chunk = recall_memory(
            q,
            k=k,
            staging=staging,
            time_from=time_from,
            time_to=time_to,
        )
        chunks.append(chunk)
        if "channel=id" in chunk:
            opened = re.search(r"(?m)^id: (\S+)", chunk)
            picked = [opened.group(1)] if opened else []
        else:
            picked = _ordered_ids(chunk)
        for seed in picked:
            if seed in seen_seeds:
                continue
            seen_seeds.add(seed)
            expand_seeds.append(seed)
    found = "\n\n".join(chunks)
    q0 = str(query or "").strip()
    asked = _MEM_ID_RE.findall(q0)
    # Explicit id that did not resolve (ghost): keep the dump, do not PPR unrelated FTS hits.
    if asked and q0 == asked[0] and "channel=id" not in found:
        return found
    if all("channel=miss" in c for c in chunks):
        return found
    ids = expand_seeds
    if not ids:
        return found
    root = staging_root(staging)
    store = BlockIndex(root)
    for seed in ids:
        stamp_recall_on_card(seed, staging=root, credit=1.0, index=store)
    walked = []
    walked_seeds: list[str] = []
    for seed in ids[:8]:
        blob = expand_memory(seed, depth=MAX_DEPTH, min_weight=min_weight, staging=staging)
        if "  miss" in blob.split("\n", 1)[0]:
            continue
        walked.append(blob)
        walked_seeds.append(seed)
    if walked:
        adj = adjacency(load_edges(root), min_weight=min_weight)
        credited = set(ids)
        for seed, blob in zip(walked_seeds, walked):
            weights: dict[str, float] = {}
            for mid in _ordered_ids(blob):
                if mid in credited:
                    continue
                rel = (adj.get(seed) or {}).get(mid) or (adj.get(mid) or {}).get(seed)
                if not rel:
                    continue
                weights[mid] = float(rel[0])
            for mid, added in neighbor_credits(weights).items():
                stamp_recall_on_card(mid, staging=root, credit=added, index=store)
                credited.add(mid)
    if not walked:
        return found
    return found.rstrip() + "\n\n" + "\n\n".join(walked)


def handle_tool(
    name: str,
    args: Mapping[str, Any] | None = None,
    *,
    staging: Path | None = None,
) -> str:
    """Chat/eval door: expand_memory is never the first step.

    A lone expand_memory call is rewritten to recall(id_or_key) then expand so
    the model cannot PPR-walk before a find.
    """
    args = args or {}
    if name == "recall_memory":
        raw_from = args.get("time_from")
        raw_to = args.get("time_to")
        return _recall_then_expand(
            str(args.get("query") or ""),
            k=int(args.get("k") or 8),
            staging=staging,
            time_from=str(raw_from) if raw_from else None,
            time_to=str(raw_to) if raw_to else None,
        )
    if name == "expand_memory":
        q = str(args.get("id_or_key") or args.get("id") or args.get("query") or "")
        return _recall_then_expand(q, staging=staging)
    raise NotImplementedError(name)
