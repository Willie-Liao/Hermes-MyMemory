"""Read-only gate calibration. Prints aggregates only so no message or card text leaves the host.

Run on the host with the plugin on sys.path. --overlay encodes into this process
and does not write the cache. A free-text value in the JSON is a bug: the
emitter rejects it before anything is printed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

_LABELS = frozenset(
    {
        "gate",
        "history",
        "strength",
        "cache",
        "legacy",
        "index",
        "index_events",
        "r2",
        "r2_centroid",
        "r2_centroid_nnn",
        "card",
        "card_events",
        "ship",
        "stop_procedure",
        "stop_english",
        "stop_beta",
        "stop_daily",
        "stop_encode",
        "stop_admit",
        "stop_far",
        "stop_tokens",
        "stop_sample",
        "off",
        "not_run",
        "ok",
        "decision",
        "procedure",
        "english",
        "chinese",
        "zero",
        "one_two",
        "three_plus",
    }
)
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ACK_RE = re.compile(
    r"^(?:(?:哈|嗯|好|行|谢)+|可以|收到|了解|明白|ok|okay|thanks|thank you|got it|lol)"
    r"[!！.。,，~～…\s]*$",
    re.IGNORECASE,
)
_POS_CAP = 400
_NEG_CAP = 400
_SEED = 0
_MIN_CHARS = 24
# August's labelled lines are older than 60 days. A shorter window emits the
# grid floor from an empty sample and cannot set the cosine gate.
_LOOKBACK_S = 180 * 24 * 3600
_BETAS = (0.85, 0.90, 0.95, 1.00)
_CAPS = (3, 5)
_GATES = tuple(round(x * 0.01, 2) for x in range(50, 100))
_R2_GATES = tuple(round(x * 0.01, 2) for x in range(5, 100))
_KIND_K = 6
_LEXICAL_K = 12


def _stage(label: str) -> None:
    print(label, file=sys.stderr, flush=True)


def _safe(value: Any) -> bool:
    """Reject free text so a probe bug cannot print a message, a card, or an id."""
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return value in _LABELS or _DATE_RE.fullmatch(value) is not None
    if isinstance(value, list):
        return all(_safe(item) for item in value)
    if isinstance(value, dict):
        return all(_KEY_RE.fullmatch(str(key)) and _safe(item) for key, item in value.items())
    return False


def _emit(payload: dict[str, Any]) -> None:
    if not _safe(payload):
        raise SystemExit(2)
    json.dump(payload, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


def _quantile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return float(ordered[idx])


def _cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _legacy_lookup(row: Any) -> str:
    name = row.__class__.__name__
    if name == "MonthlyDecision":
        parts = [
            " ".join(row.entity_keys),
            row.context,
            row.text,
            row.exceptions,
            row.why_it_matters,
        ]
    else:
        parts = [
            " ".join(row.entity_keys),
            row.trigger,
            row.problem,
            " ".join(row.obstacles),
            row.solution,
            row.insight,
        ]
    return " ".join(part for part in parts if str(part).strip())


def _view_of(row: Any) -> tuple[str, str]:
    """Name the extra embedded view so the probe maxes the same channel the ranker does.

    A decision's context and a procedure's solution are separate cache rows.
    Leaving them out would calibrate a gate for a score the live path no longer uses.
    """
    name = row.__class__.__name__
    if name == "MonthlyDecision":
        return "dp_context", str(row.lookup_text("context") or "").strip()
    if name == "MonthlyProcedure":
        return "dp_solution", str(row.lookup_text("solution") or "").strip()
    return "", ""


def _scale_id(
    scale_block_id, *, text: str, mem_id: str, month_key: str, kind: str = "dp"
) -> str:
    return scale_block_id(
        {
            "scale": "month",
            "kind": kind,
            "text": text.strip(),
            "mem_id": mem_id,
            "month_key": month_key,
        }
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--plugin", default="")
    parser.add_argument("--mode", default="gate")
    parser.add_argument("--overlay", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def _home() -> Path:
    raw = (os.environ.get("HERMES_HOME") or "").strip()
    if not raw:
        raise SystemExit(2)
    return Path(raw)


def _install_plugin(path: str) -> None:
    """Put the plugin and monthly/ on sys.path so the probe imports the tree it was pointed at.

    monthly_actions is not a package; the monthly tools insert that directory
    themselves, but only after the first import. Without it the probe cannot
    start.
    """
    root = Path(path).resolve() if path else Path.cwd()
    for extra in (root / "monthly", root):
        text = str(extra)
        if text not in sys.path:
            sys.path.insert(0, text)


def _load_user_messages(db: Path) -> list[dict[str, Any]]:
    from digest.digest_run import _as_unix, _open_messages_ro

    if not db.is_file():
        return []
    cutoff = time.time() - _LOOKBACK_S
    conn = _open_messages_ro(db)
    try:
        rows = conn.execute(
            """
            SELECT id, session_id, content, timestamp
            FROM messages
            WHERE active = 1 AND role = 'user'
            """
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        content = str(row["content"] or "").strip()
        if not content or content.startswith("[CONTEXT COMPACTION"):
            continue
        stamp = _as_unix(row["timestamp"])
        if stamp is None or stamp < cutoff:
            continue
        out.append(
            {
                "id": int(row["id"]),
                "session_id": str(row["session_id"] or ""),
                "content": content,
                "ts": float(stamp),
            }
        )
    return out


def _source_labels(
    store, messages: list[dict[str, Any]], *, min_chars: int = _MIN_CHARS
) -> dict[tuple[str, int], set[str]]:
    """Keep the long-line gate sample stable while the history probe can still label a short line.

    The gate ignores lines of `_MIN_CHARS` or fewer. A follow-up is often that short,
    so the history mode passes `min_chars=0` and would otherwise have no positives.
    """
    from digest.digest_tools import message_range_from_source_tag, session_id_from_source_tag

    by_session: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        by_session.setdefault(msg["session_id"], []).append(msg)
    labels: dict[tuple[str, int], set[str]] = {}
    for rec in store.records:
        raw = rec.parsed.get("sources") or []
        tags = [raw] if isinstance(raw, str) else list(raw or [])
        for tag in tags:
            sid = session_id_from_source_tag(str(tag))
            span = message_range_from_source_tag(str(tag))
            if not sid or span is None:
                continue
            start, end = span
            for msg in by_session.get(sid, ()):
                if msg["id"] < start or msg["id"] > end:
                    continue
                if len(msg["content"]) <= min_chars or _ACK_RE.fullmatch(msg["content"]):
                    continue
                labels.setdefault((sid, msg["id"]), set()).add(rec.block_id)
    return labels


def _sample(items: list[Any], cap: int) -> list[Any]:
    if len(items) <= cap:
        return list(items)
    return random.Random(_SEED).sample(items, cap)


def _encode_unique(texts: list[str]) -> dict[str, list[float]]:
    from recall.embed import _encode_texts

    unique: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if text in seen:
            continue
        seen.add(text)
        unique.append(text)
    if not unique:
        return {}
    vectors = _encode_texts(unique)
    if len(vectors) != len(unique):
        return {}
    return {text: [float(x) for x in vec] for text, vec in zip(unique, vectors) if vec}


def _dot(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def _event_count(store, ids: list[str]) -> int:
    from recall.tools import _is_rejected

    wanted = set(ids)
    seen: set[str] = set()
    for rec in store.records:
        if rec.item_type != "event" or _is_rejected(rec):
            continue
        if wanted.intersection(rec.related):
            seen.add(rec.block_id)
    return len(seen)


def _event_units(
    store,
    ids: list[str],
    cache: dict,
    cap: int,
    mean_dir: list[float] | None = None,
) -> list[tuple[str, list[float]]]:
    """Capped event ids and R2 vectors, so a leave-one-out query can drop itself.

    Averaging the probe event back in scores a sentence against itself. The id
    stays beside the vector so the offline gate can exclude that one event
    without changing the ranker's own centroid.
    """
    from recall.tools import _capped_linked_events, _unit_residual

    chosen = _capped_linked_events({"row": ids}, store, cap=cap).get("row") or []
    units: list[tuple[str, list[float]]] = []
    for rec in chosen:
        raw = (cache.get(rec.block_id) or {}).get("embedding") or []
        if not raw:
            continue
        if mean_dir is None:
            units.append((rec.block_id, [float(x) for x in raw]))
            continue
        unit = _unit_residual(raw, mean_dir)
        if unit:
            units.append((rec.block_id, unit))
    return units


def _event_vectors(
    store,
    ids: list[str],
    cache: dict,
    cap: int,
    mean_dir: list[float] | None = None,
) -> list[list[float]]:
    """Capped event vectors in R2 space when the cone axis is known.

    Raw vectors make the model mean look like a match. The ranker projects
    before the centroid, so the probe has to do the same or its gate is fiction.
    """
    return [vec for _event_id, vec in _event_units(store, ids, cache, cap, mean_dir)]


def _event_probe_queries(store, prepared: list[dict[str, Any]], blocks: dict) -> list[dict[str, Any]]:
    """Use each cited event as a query when session tags never meet a card.

    This notebook's sources are wecom keys, so the message join is empty and
    the gate collapses to the grid floor. The measured bar is the event body
    against the monthly rows that cite it, with that event left out of the centroid.
    """
    from recall.embed_cache import _compose_embedding_text
    from recall.tools import _is_rejected

    # A monthly row cites the daily card. The event points at that card through
    # related, which is the same join `_capped_linked_events` uses.
    cited: dict[str, set[str]] = {}
    for row in prepared:
        for item in row["evidence"]:
            cited.setdefault(str(item), set()).add(str(row["id"]))
    queries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in store.records:
        if rec.item_type != "event" or _is_rejected(rec):
            continue
        labels: set[str] = set()
        for item in rec.related or ():
            labels.update(cited.get(str(item), ()))
        block = blocks.get(rec.block_id)
        if not labels or not block:
            continue
        text = _compose_embedding_text(block)
        if not text or text in seen:
            continue
        seen.add(text)
        queries.append(
            {
                "text": text,
                "labels": set(labels),
                "key": ("event", rec.block_id),
                "event_id": rec.block_id,
            }
        )
    return queries


def _centroid_vec(rows: list[list[float]]) -> list[float]:
    """Mean of already-projected event vectors, renormalised.

    Max-pooling one neighbour hides the rest of the card's usage. This is the
    same reduction ``linked_event_scores`` uses.
    """
    from recall.tools import _unit_residual

    usable = [row for row in rows if row]
    if not usable:
        return []
    dim = len(usable[0])
    acc = [0.0] * dim
    count = 0
    for row in usable:
        if len(row) != dim:
            continue
        count += 1
        for index, value in enumerate(row):
            acc[index] += float(value)
    if not count:
        return []
    mean = [value / count for value in acc]
    return _unit_residual(mean, None) or []


def _bucket(count: int) -> str:
    if count <= 0:
        return "zero"
    if count <= 2:
        return "one_two"
    return "three_plus"


def _lowest_gate(
    neg_scores: list[float],
    pos_hits: list[tuple[float, bool]],
    gates: tuple[float, ...] | None = None,
) -> tuple[float, float]:
    """Lowest gate whose false-admit rate is at most 2%, and the admit rate there."""
    chosen = -1.0
    admit = 0.0
    n_neg = max(len(neg_scores), 1)
    n_pos = max(len(pos_hits), 1)
    for gate in gates or _GATES:
        false_n = sum(1 for score in neg_scores if score >= gate)
        if false_n / n_neg > 0.02:
            continue
        chosen = gate
        admit = sum(1 for score, hit in pos_hits if hit and score >= gate) / n_pos
        break
    return chosen, admit


def _far_split(rows: list[tuple[float, str]]) -> dict[str, float | int]:
    out: dict[str, float | int] = {}
    for name in ("zero", "one_two", "three_plus"):
        group = [score for score, bucket in rows if bucket == name]
        out[f"n_{name}"] = len(group)
        out[f"far_{name}"] = (
            sum(1 for score in group if score >= 0.92) / len(group) if group else 0.0
        )
    return out


def _mode_cache() -> None:
    from recall.embed_cache import _embed_cache_tz, _load_meta, load_cache, scale_block_id
    from recall.ids import BlockIndex
    from recall.tools import (
        _is_rejected,
        collect_month_scale_rows,
        collect_week_scale_rows,
    )

    home = _home()
    staging = home / "memories" / "staging"
    cache = load_cache()
    store = BlockIndex(staging)
    live = [rec for rec in store.records if not _is_rejected(rec)]
    missing_daily = sum(
        1 for rec in live if not (cache.get(rec.block_id) or {}).get("embedding")
    )
    month_rows = []
    try:
        month_rows = list(collect_month_scale_rows(staging, views=True))
    except Exception:
        month_rows = []
    week_rows = []
    try:
        week_rows = list(collect_week_scale_rows(staging))
    except Exception:
        week_rows = []
    from recall.embed_cache import scan_all_blocks

    scanned = scan_all_blocks()
    current = {key for key in scanned if str(key).startswith("emb-scale:")}
    current.update(scale_block_id(row) for row in (*month_rows, *week_rows))
    missing_month = sum(
        1
        for row in month_rows
        if not (cache.get(scale_block_id(row)) or {}).get("embedding")
    )
    view_kinds = {"dp_solution", "dp_context", "obstacle", "solution"}
    missing_views = sum(
        1
        for key, block in scanned.items()
        if str(block.get("kind") or "") in view_kinds
        and not (cache.get(key) or {}).get("embedding")
    )
    stale_month = sum(
        1 for key in cache if str(key).startswith("emb-scale:") and key not in current
    )
    meta = _load_meta()
    last = str(meta.get("last_checked_on") or "")
    if not _DATE_RE.fullmatch(last):
        last = "1970-01-01"
    from datetime import datetime

    today = datetime.now(_embed_cache_tz()).date().isoformat()
    _emit(
        {
            "mode": "cache",
            "ok": True,
            "last_checked_on": last,
            "last_checked_today": last == today,
            "missing_daily": missing_daily,
            "live_daily": len(live),
            "missing_month": missing_month,
            "missing_views": missing_views,
            "month_rows": len(month_rows),
            "stale_month": stale_month,
        }
    )


def _active_rows(staging: Path) -> list[Any]:
    from monthly_actions import _is_active_decision, _iter_payloads

    rows = []
    for payload in _iter_payloads(staging):
        superseded = {sid for row in payload.key_decisions for sid in row.supersedes}
        for row in payload.key_decisions:
            if _is_active_decision(row, superseded):
                rows.append((payload, row, "decision"))
        for row in payload.key_procedures:
            rows.append((payload, row, "procedure"))
    return rows


def _mode_gate(overlay: bool) -> None:
    import numpy as np

    from monthly_actions import _GUIDANCE_HUB_ALPHA, _guidance_sim, _lexical_sim
    from recall.embed import COSINE_FLOOR
    from recall.embed_cache import _compose_embedding_text, load_cache, scale_block_id
    from recall.ids import BlockIndex
    from recall.lexical import lexical_db_path, search_lexical
    from recall.strength import strength_value
    from recall.tools import _is_rejected

    home = _home()
    staging = home / "memories" / "staging"
    _stage("load")
    store = BlockIndex(staging)
    cache = {key: dict(value) for key, value in load_cache().items()}
    messages = _load_user_messages(home / "state.db")
    by_key = {(msg["session_id"], msg["id"]): msg for msg in messages}
    labels = _source_labels(store, messages)
    actives = _active_rows(staging)
    evidence_of: dict[str, list[str]] = {}
    prepared = []
    for payload, row, kind in actives:
        evidence = [str(item) for item in (row.evidence or ()) if str(item).strip()]
        evidence_of[row.id] = evidence
        index_text = str(row.lookup_text() or "").strip()
        view_kind, view_text = _view_of(row)
        legacy_text = _legacy_lookup(row)
        live = strength_value(
            recall_n=float(getattr(row, "recall_n", 0.0) or 0.0),
            first_seen=getattr(row, "first_seen", None) or getattr(row, "valid_from", None),
            last_recall_at=getattr(row, "last_recall_at", None),
            importance=int(getattr(row, "importance", 3) or 3),
        )
        prepared.append(
            {
                "id": row.id,
                "kind": kind,
                "month": str(payload.key),
                "index_text": index_text,
                "view_kind": view_kind,
                "view_text": view_text,
                "legacy_text": legacy_text,
                "evidence": evidence,
                "live": float(live),
                "index_id": _scale_id(
                    scale_block_id, text=index_text, mem_id=row.id, month_key=str(payload.key)
                ),
                "view_id": _scale_id(
                    scale_block_id,
                    text=view_text,
                    mem_id=row.id,
                    month_key=str(payload.key),
                    kind=view_kind or "dp",
                )
                if view_text
                else "",
                "legacy_id": _scale_id(
                    scale_block_id, text=legacy_text, mem_id=row.id, month_key=str(payload.key)
                ),
            }
        )
    evidence_ids = set().union(*evidence_of.values()) if evidence_of else set()
    monthly_pos_keys = [key for key, ids in labels.items() if ids.intersection(evidence_ids)]
    monthly_pos = []
    seen_text: set[str] = set()
    for key in monthly_pos_keys:
        msg = by_key.get(key)
        if msg is None or msg["content"] in seen_text:
            continue
        seen_text.add(msg["content"])
        row_ids = {
            row["id"]
            for row in prepared
            if labels[key].intersection(row["evidence"])
        }
        if row_ids:
            monthly_pos.append({"text": msg["content"], "labels": row_ids, "key": key})
    event_sample = False
    if not monthly_pos:
        # Session tags never meet these cards. The cited event is the query the
        # leave-one-out bar was measured on; a message-only sample emits the grid floor.
        from recall.embed_cache import scan_all_blocks

        monthly_pos = _event_probe_queries(store, prepared, scan_all_blocks())
        event_sample = bool(monthly_pos)
    positive_available = len(monthly_pos)
    monthly_pos = _sample(monthly_pos, _POS_CAP)
    positive_keys = {item["key"] for item in monthly_pos}
    negatives = [
        msg
        for msg in messages
        if (msg["session_id"], msg["id"]) not in labels
        and len(msg["content"]) > _MIN_CHARS
        and not _ACK_RE.fullmatch(msg["content"])
        and msg["content"] not in seen_text
    ]
    negatives = _sample(negatives, _NEG_CAP)

    texts = [item["text"] for item in monthly_pos] + [msg["content"] for msg in negatives]
    texts.extend(row["index_text"] for row in prepared)
    texts.extend(row["view_text"] for row in prepared if row["view_text"])
    missing_blocks = []
    if overlay:
        from recall.embed_cache import scan_all_blocks

        for block in scan_all_blocks().values():
            if (cache.get(block["id"]) or {}).get("embedding"):
                continue
            missing_blocks.append(block)
            texts.append(_compose_embedding_text(block))
    _stage("encode")
    encoded = _encode_unique(texts)
    if texts and not encoded:
        _emit({"mode": "gate", "ok": False, "error": "stop_encode", "overlay": overlay})
        return
    if overlay:
        for row in prepared:
            vec = encoded.get(row["index_text"]) or []
            if vec:
                cache[row["index_id"]] = {"embedding": vec}
            view = (encoded.get(row["view_text"]) or []) if row["view_text"] else []
            if view and row["view_id"]:
                cache[row["view_id"]] = {"embedding": view}
        for block in missing_blocks:
            vec = encoded.get(_compose_embedding_text(block)) or []
            if vec:
                cache[block["id"]] = {"embedding": vec}
    for row in prepared:
        legacy = (cache.get(row["legacy_id"]) or {}).get("embedding") or []
        index = (cache.get(row["index_id"]) or {}).get("embedding") or []
        view = (cache.get(row["view_id"]) or {}).get("embedding") or [] if row["view_id"] else []
        row["legacy_vec"] = [float(x) for x in legacy]
        row["index_vec"] = [float(x) for x in index]
        row["view_vec"] = [float(x) for x in view]
        row["event_n"] = _event_count(store, row["evidence"])
    from recall.embed_cache import mean_direction
    from recall.tools import _capped_linked_events, _unit_residual, event_bank, hub_bias

    mu = mean_direction(cache)
    for row in prepared:
        row["event_units"] = {
            cap: _event_units(store, row["evidence"], cache, cap, mu) for cap in _CAPS
        }
        row["events"] = {
            cap: [vec for _event_id, vec in units]
            for cap, units in row["event_units"].items()
        }
    month_targets = {row["id"]: row["evidence"] for row in prepared}
    month_capped = _capped_linked_events(month_targets, store, cap=5)
    month_exclude = {
        key: [rec.block_id for rec in recs] for key, recs in month_capped.items()
    }
    month_lookups = {}
    for row in prepared:
        units = []
        for raw in (row["index_vec"], row["view_vec"]):
            if raw and (unit := _unit_residual(raw, mu)):
                units.append(unit)
        if units:
            month_lookups[row["id"]] = units
    month_centroids = {
        row["id"]: centroid
        for row in prepared
        if (centroid := _centroid_vec(row["events"].get(5) or []))
    }
    month_bias = hub_bias(
        month_lookups,
        month_centroids,
        event_bank(store, cache, mu),
        k=8,
        alpha=_GUIDANCE_HUB_ALPHA,
        exclude=month_exclude,
        beta=0.95,
    )

    def score_variant(
        query: str,
        qv: list[float],
        qi: int,
        *,
        legacy: bool,
        cap: int,
        beta: float,
        nnn: bool = False,
    ):
        best_id = ""
        best_key = None
        top = 0.0
        winner = prepared[0] if prepared else None
        for ri, row in enumerate(prepared):
            text = row["legacy_text"] if legacy else row["index_text"]
            vec = row["legacy_vec"] if legacy else row["index_vec"]
            s_trig = _guidance_sim(
                query, text, qv, vec, mean_dir=None if legacy else mu
            )
            s_view = 0.0
            if not legacy and row["view_text"]:
                s_view = _guidance_sim(
                    query, row["view_text"], qv, row["view_vec"], mean_dir=mu
                )
            s_evt = 0.0 if legacy or cap == 0 else float(month_evt[cap][qi, ri])
            sim = s_trig if legacy else max(s_trig, s_view)
            if not legacy and cap:
                sim = max(sim, beta * s_evt)
            if nnn and not legacy:
                sim -= float(month_bias.get(row["id"], 0.0))
            key = (sim, s_trig, row["id"])
            if best_key is None or key > best_key:
                best_key = key
                best_id = row["id"]
                top = sim
                winner = row
        return top, winner

    _stage("score-month")
    queries = [(item["text"], item["labels"], "pos") for item in monthly_pos]
    queries += [(msg["content"], set(), "neg") for msg in negatives]
    query_events = [str(item.get("event_id") or "") for item in monthly_pos]
    query_events += [""] * len(negatives)
    qvecs = [encoded.get(text) or [] for text, _labels, _kind in queries]
    width = next((len(vec) for vec in qvecs if vec), 0)
    q_r2 = []
    for vec in qvecs:
        unit = _unit_residual(vec, mu) if vec else None
        q_r2.append(unit if unit and len(unit) == width else [0.0] * width)
    qmat_r2 = (
        np.asarray(q_r2, dtype=np.float32)
        if qvecs and width
        else np.zeros((len(qvecs), 1), dtype=np.float32)
    )
    month_evt: dict[int, Any] = {}
    for cap in _CAPS:
        columns = []
        for row in prepared:
            vecs = [vec for vec in (row["events"].get(cap) or []) if len(vec) == width]
            centroid = _centroid_vec(vecs)
            if not centroid or not len(qvecs) or not width:
                columns.append(np.zeros(len(qvecs), dtype=np.float32))
                continue
            columns.append(qmat_r2 @ np.asarray(centroid, dtype=np.float32))
        month_evt[cap] = np.stack(columns, axis=1) if columns else np.zeros((len(qvecs), 0))

    def _loo_summary(
        cap: int, beta: float, *, nnn: bool, shared_gate: float | None = None
    ) -> dict[str, float]:
        """Rank each cited event with that event left out of its own centroid.

        Including it scores the sentence against itself and the gate comes out
        too high for a new message. Live flips count how often 0.2 * live/10
        changes the winner on this wider scale.
        """
        ranked: list[tuple[list[tuple[float, str, float]], set[str]]] = []
        flips = 0
        for qi, ((text, labels, role), qv) in enumerate(zip(queries, qvecs)):
            if role != "pos" or not qv:
                continue
            drop = query_events[qi] if qi < len(query_events) else ""
            scored: list[tuple[float, str, float]] = []
            for row in prepared:
                s_trig = _guidance_sim(
                    text, row["index_text"], qv, row["index_vec"], mean_dir=mu
                )
                s_view = (
                    _guidance_sim(text, row["view_text"], qv, row["view_vec"], mean_dir=mu)
                    if row["view_text"]
                    else 0.0
                )
                sim = max(s_trig, s_view)
                if cap:
                    kept = [
                        vec
                        for event_id, vec in (row["event_units"].get(cap) or [])
                        if event_id != drop and len(vec) == width
                    ]
                    centroid = _centroid_vec(kept)
                    s_use = (
                        float(sum(a * b for a, b in zip(q_r2[qi], centroid)))
                        if centroid
                        else 0.0
                    )
                    sim = max(sim, beta * s_use)
                if nnn:
                    sim -= float(month_bias.get(row["id"], 0.0))
                scored.append((sim, str(row["id"]), float(row.get("live") or 0.0)))
            if not scored:
                continue
            by_sim = max(scored, key=lambda item: (item[0], item[1]))
            by_live = max(
                scored,
                key=lambda item: (0.8 * item[0] + 0.2 * item[2] / 10.0, item[0], item[1]),
            )
            if by_sim[1] != by_live[1]:
                flips += 1
            ranked.append((scored, labels))
        n = len(ranked)
        if not n:
            return {
                "loo_n": 0,
                "loo_top1": 0.0,
                "loo_mean_rank": 0.0,
                "loo_impostors": 0.0,
                "loo_gate": 0.0,
                "loo_admit": 0.0,
                "loo_over_gate": 0.0,
                "loo_over_shared": 0.0,
                "live_flips": 0,
            }
        top1 = 0
        rank_sum = 0
        ahead_sum = 0
        best_true: list[float | None] = []
        impostor_counts: list[list[float]] = []
        for scored, labels in ranked:
            trues = [sim for sim, card_id, _live in scored if card_id in labels]
            if not trues:
                best_true.append(None)
                impostor_counts.append([sim for sim, _card_id, _live in scored])
                rank_sum += len(scored) + 1
                ahead_sum += len(scored)
                continue
            target = max(trues)
            best_true.append(target)
            ahead = sum(
                1 for sim, card_id, _live in scored if card_id not in labels and sim > target
            )
            ahead_sum += ahead
            rank_sum += ahead + 1
            if ahead == 0:
                top1 += 1
            impostor_counts.append(
                [sim for sim, card_id, _live in scored if card_id not in labels]
            )
        chosen_gate = 0.0
        chosen_admit = 0.0
        chosen_over = 0.0
        fallback: tuple[float, float, float] | None = None
        for gate_value in _R2_GATES:
            admit_n = sum(
                1 for score in best_true if score is not None and score >= gate_value
            )
            over = sum(
                sum(1 for sim in sims if sim >= gate_value) for sims in impostor_counts
            ) / n
            admit = admit_n / n
            if over <= 2.35 and (fallback is None or admit > fallback[1]):
                fallback = (gate_value, admit, over)
            if over <= 2.35 and admit >= 0.50:
                chosen_gate = gate_value
                chosen_admit = admit
                chosen_over = over
                break
        if chosen_gate == 0.0 and fallback is not None:
            chosen_gate, chosen_admit, chosen_over = fallback
        shared = chosen_gate if shared_gate is None else shared_gate
        over_shared = (
            sum(sum(1 for sim in sims if sim >= shared) for sims in impostor_counts) / n
        )
        proc_ids = {row["id"] for row in prepared if row["kind"] == "procedure"}
        proc_ranked = [
            (scored, labels)
            for scored, labels in ranked
            if labels.intersection(proc_ids)
        ]
        proc_n = len(proc_ranked)
        proc_top1 = 0
        proc_rank_sum = 0
        proc_over = 0
        for scored, labels in proc_ranked:
            wanted = labels.intersection(proc_ids)
            trues = [sim for sim, card_id, _live in scored if card_id in wanted]
            if not trues:
                proc_rank_sum += len(scored) + 1
                proc_over += sum(1 for sim, _card_id, _live in scored if sim >= 0.31)
                continue
            target = max(trues)
            ahead = sum(
                1
                for sim, card_id, _live in scored
                if card_id not in wanted and sim > target
            )
            proc_rank_sum += ahead + 1
            if ahead == 0:
                proc_top1 += 1
            proc_over += sum(
                1
                for sim, card_id, _live in scored
                if card_id not in wanted and sim >= 0.31
            )
        return {
            "loo_n": n,
            "loo_top1": top1 / n,
            "loo_mean_rank": rank_sum / n,
            "loo_impostors": ahead_sum / n,
            "loo_gate": chosen_gate,
            "loo_admit": chosen_admit,
            "loo_over_gate": chosen_over,
            "loo_over_shared": over_shared,
            "live_flips": flips,
            "proc_n": proc_n,
            "proc_top1": (proc_top1 / proc_n) if proc_n else 0.0,
            "proc_mean_rank": (proc_rank_sum / proc_n) if proc_n else 0.0,
            "proc_over_031": (proc_over / proc_n) if proc_n else 0.0,
        }

    def run_cell(
        *,
        legacy: bool,
        cap: int,
        beta: float,
        nnn: bool = False,
        variant: str | None = None,
    ) -> dict[str, Any]:
        pos_hits: list[tuple[float, bool]] = []
        proc_hits: list[tuple[float, bool]] = []
        neg_scores: list[float] = []
        neg_buckets: list[tuple[float, str]] = []
        for qi, ((text, labels, role), qv) in enumerate(zip(queries, qvecs)):
            if not qv:
                continue
            top, winner = score_variant(
                text, qv, qi, legacy=legacy, cap=cap, beta=beta, nnn=nnn
            )
            if role == "neg":
                neg_scores.append(top)
                neg_buckets.append((top, _bucket(int(winner["event_n"]))))
                continue
            hit = winner["id"] in labels
            pos_hits.append((top, hit))
            if any(
                row["id"] in labels and row["kind"] == "procedure" for row in prepared
            ):
                proc_labels = {
                    row["id"]
                    for row in prepared
                    if row["id"] in labels and row["kind"] == "procedure"
                }
                if proc_labels:
                    proc_hits.append((top, winner["id"] in proc_labels))
        gate, admit = _lowest_gate(
            neg_scores,
            proc_hits or pos_hits,
            gates=_GATES if legacy else _R2_GATES,
        )
        n_neg = max(len(neg_scores), 1)
        n_pos = max(len(pos_hits), 1)
        n_proc = max(len(proc_hits), 1)
        cell = {
            "variant": variant
            or ("legacy" if legacy else ("index" if cap == 0 else "index_events")),
            "beta": float(beta),
            "cap": int(cap),
            "n_pos": len(pos_hits),
            "n_proc": len(proc_hits),
            "n_neg": len(neg_scores),
            "admit_at_092": sum(1 for score, hit in proc_hits if hit and score >= 0.92) / n_proc,
            "false_admit_at_092": sum(1 for score in neg_scores if score >= 0.92) / n_neg,
            "gate_far_002": gate,
            "admit_at_gate": admit,
            "pos_p50": _quantile([score for score, _hit in pos_hits], 0.50),
            "pos_p90": _quantile([score for score, _hit in pos_hits], 0.90),
            "neg_p50": _quantile(neg_scores, 0.50),
            "neg_p90": _quantile(neg_scores, 0.90),
        }
        cell.update(_far_split(neg_buckets))
        return cell

    cells = [run_cell(legacy=True, cap=0, beta=0.0), run_cell(legacy=False, cap=0, beta=0.0, variant="r2")]
    for cap in _CAPS:
        for beta in _BETAS:
            cells.append(run_cell(legacy=False, cap=cap, beta=beta))
    cells.append(run_cell(legacy=False, cap=5, beta=0.95, variant="r2_centroid"))
    cells.append(run_cell(legacy=False, cap=5, beta=0.95, nnn=True, variant="r2_centroid_nnn"))
    loo_nnn = _loo_summary(5, 0.95, nnn=True)
    loo_by_variant = {
        "r2": _loo_summary(0, 0.0, nnn=False, shared_gate=loo_nnn["loo_gate"]),
        "r2_centroid": _loo_summary(5, 0.95, nnn=False, shared_gate=loo_nnn["loo_gate"]),
        "r2_centroid_nnn": loo_nnn,
    }
    loo_nnn["loo_over_shared"] = loo_nnn["loo_over_gate"]
    for cell in cells:
        extra = loo_by_variant.get(str(cell["variant"]))
        if extra:
            cell.update(extra)

    diff = 0.0
    if monthly_pos and qvecs and qvecs[0]:
        from monthly_actions import rank_monthly_guidance

        held = {}
        for row in prepared:
            if row["index_vec"]:
                held[row["index_id"]] = {"embedding": row["index_vec"]}
            if row["view_vec"] and row["view_id"]:
                held[row["view_id"]] = {"embedding": row["view_vec"]}
        for key, value in cache.items():
            if not str(key).startswith("emb-scale:"):
                held[key] = value
        scores: list[dict[str, Any]] = []
        import monthly_actions

        previous_cap = monthly_actions._GUIDANCE_EVENT_CAP
        previous_beta = monthly_actions._GUIDANCE_EVENT_BETA
        monthly_actions._GUIDANCE_EVENT_CAP = 5
        monthly_actions._GUIDANCE_EVENT_BETA = 0.95
        try:
            rank_monthly_guidance(
                monthly_pos[0]["text"],
                staging=staging,
                store=store,
                cache=held,
                query_vec=qvecs[0],
                scores_out=scores,
                mean_dir=mu,
                limit=8,
            )
        finally:
            monthly_actions._GUIDANCE_EVENT_CAP = previous_cap
            monthly_actions._GUIDANCE_EVENT_BETA = previous_beta
        ours = {}
        for row in prepared:
            s_trig = _guidance_sim(
                monthly_pos[0]["text"],
                row["index_text"],
                qvecs[0],
                row["index_vec"],
                mean_dir=mu,
            )
            s_view = (
                _guidance_sim(
                    monthly_pos[0]["text"],
                    row["view_text"],
                    qvecs[0],
                    row["view_vec"],
                    mean_dir=mu,
                )
                if row["view_text"]
                else 0.0
            )
            s_evt = float(month_evt[5][0, prepared.index(row)]) if prepared else 0.0
            lexical = _lexical_sim(monthly_pos[0]["text"], row["index_text"])
            lexical_view = (
                _lexical_sim(monthly_pos[0]["text"], row["view_text"]) if row["view_text"] else 0.0
            )
            if lexical >= 0.92 or lexical_view >= 0.92:
                ours[row["id"]] = max(lexical, lexical_view)
            else:
                ours[row["id"]] = max(s_trig, s_view, 0.95 * s_evt) - float(
                    month_bias.get(row["id"], 0.0)
                )
        for item in scores:
            diff = max(diff, abs(float(item["sim"]) - ours.get(item["id"], 0.0)))

    _stage("score-daily")
    daily_cards = []
    for rec in store.records:
        if rec.item_type not in {"decision", "procedure"} or _is_rejected(rec):
            continue
        raw = (cache.get(rec.block_id) or {}).get("embedding") or []
        live = strength_value(
            recall_n=float(rec.parsed.get("recall_n") or 0.0),
            first_seen=rec.parsed.get("first_seen") or rec.parsed.get("valid_from"),
            last_recall_at=rec.parsed.get("last_recall_at"),
            importance=int(rec.parsed.get("importance") or 3),
        )
        units = {cap: _event_units(store, [rec.block_id], cache, cap, mu) for cap in _CAPS}
        daily_cards.append(
            {
                "id": rec.block_id,
                "kind": rec.item_type,
                "vec": [float(x) for x in raw],
                "live": float(live),
                "event_n": _event_count(store, [rec.block_id]),
                "event_units": units,
                "events": {cap: [vec for _event_id, vec in units[cap]] for cap in _CAPS},
            }
        )
    daily_pairs = []
    for key, ids in labels.items():
        msg = by_key.get(key)
        if msg is None:
            continue
        for card in daily_cards:
            if card["id"] in ids:
                daily_pairs.append(
                    {
                        "text": msg["content"],
                        "card": card["id"],
                        "kind": card["kind"],
                        "linked": card["event_n"] > 0,
                        "english": not _cjk(msg["content"]),
                    }
                )
    daily_available = len(daily_pairs)
    daily_pairs = _sample(daily_pairs, _POS_CAP)
    extra_texts = [pair["text"] for pair in daily_pairs if pair["text"] not in encoded]
    if extra_texts:
        encoded.update(_encode_unique(extra_texts))
    lexical_ok = lexical_db_path(staging).is_file()

    def daily_rates(order_fn) -> dict[str, float | int]:
        stats = {
            "n": 0,
            "top1": 0,
            "topk": 0,
            "n_english": 0,
            "top1_english": 0,
            "n_chinese": 0,
            "top1_chinese": 0,
            "n_linked": 0,
            "top1_linked": 0,
            "n_unlinked": 0,
            "top1_unlinked": 0,
            "tie_n": 0,
            "tie_first_n": 0,
            "rank_sum": 0.0,
            "ahead_sum": 0.0,
        }
        for pair in daily_pairs:
            qv = encoded.get(pair["text"]) or []
            if not qv and order_fn is not _lexical_order:
                continue
            ranked, tied = order_fn(pair, qv)
            if not ranked:
                stats["n"] += 1
                if pair["english"]:
                    stats["n_english"] += 1
                else:
                    stats["n_chinese"] += 1
                if pair["linked"]:
                    stats["n_linked"] += 1
                else:
                    stats["n_unlinked"] += 1
                continue
            stats["n"] += 1
            top1 = ranked[0]["id"] == pair["card"]
            kind_rows = [item for item in ranked if item["kind"] == pair["kind"]]
            topk = any(item["id"] == pair["card"] for item in kind_rows[:_KIND_K])
            stats["top1"] += int(top1)
            stats["topk"] += int(topk)
            if pair["english"]:
                stats["n_english"] += 1
                stats["top1_english"] += int(top1)
            else:
                stats["n_chinese"] += 1
                stats["top1_chinese"] += int(top1)
            if pair["linked"]:
                stats["n_linked"] += 1
                stats["top1_linked"] += int(top1)
            else:
                stats["n_unlinked"] += 1
                stats["top1_unlinked"] += int(top1)
            if tied:
                stats["tie_n"] += 1
                if tied == "first":
                    stats["tie_first_n"] += 1
            order_ids = [item["id"] for item in ranked]
            if pair["card"] in order_ids:
                place = order_ids.index(pair["card"])
                stats["rank_sum"] += place + 1
                stats["ahead_sum"] += place
            else:
                stats["rank_sum"] += len(order_ids) + 1
                stats["ahead_sum"] += len(order_ids)
        def rate(num: str, den: str) -> float:
            return (stats[num] / stats[den]) if stats[den] else 0.0

        return {
            "n": stats["n"],
            "top1": rate("top1", "n"),
            "topk": rate("topk", "n"),
            "n_english": stats["n_english"],
            "top1_english": rate("top1_english", "n_english"),
            "n_chinese": stats["n_chinese"],
            "top1_chinese": rate("top1_chinese", "n_chinese"),
            "n_linked": stats["n_linked"],
            "top1_linked": rate("top1_linked", "n_linked"),
            "n_unlinked": stats["n_unlinked"],
            "top1_unlinked": rate("top1_unlinked", "n_unlinked"),
            "tie_n": stats["tie_n"],
            "tie_first_n": stats["tie_first_n"],
            "mean_rank": (stats["rank_sum"] / stats["n"]) if stats["n"] else 0.0,
            "impostors_ahead": (stats["ahead_sum"] / stats["n"]) if stats["n"] else 0.0,
        }

    width = next((len(vec) for vec in encoded.values() if vec), 0)
    q_rows = []
    for pair in daily_pairs:
        raw = encoded.get(pair["text"]) or []
        unit = _unit_residual(raw, mu) if raw else None
        q_rows.append(unit if unit and len(unit) == width else [0.0] * width)
    qmat = (
        np.asarray(q_rows, dtype=np.float32)
        if q_rows and width
        else np.zeros((len(daily_pairs), 1), dtype=np.float32)
    )
    for card in daily_cards:
        units: list[list[float]] = []
        if card["kind"] == "procedure":
            for view_kind in ("obstacle", "solution"):
                raw = (
                    cache.get(
                        scale_block_id(
                            {"scale": "day", "kind": view_kind, "mem_id": card["id"]}
                        )
                    )
                    or {}
                ).get("embedding") or []
                unit = _unit_residual(list(raw), mu) if raw else None
                if unit and len(unit) == width:
                    units.append(unit)
        if not units and card["vec"]:
            unit = _unit_residual(card["vec"], mu)
            if unit and len(unit) == width:
                units.append(unit)
        card["score_units"] = units
    s_card_all = np.zeros((len(daily_pairs), len(daily_cards)), dtype=np.float32)
    if len(daily_pairs) and len(daily_cards) and width:
        for col, card in enumerate(daily_cards):
            if not card["score_units"]:
                continue
            view_mat = np.asarray(card["score_units"], dtype=np.float32)
            s_card_all[:, col] = (qmat @ view_mat.T).max(axis=1)
    s_evt_all: dict[int, Any] = {}
    for cap in _CAPS:
        columns = []
        for card in daily_cards:
            vecs = [vec for vec in (card["events"].get(cap) or []) if len(vec) == width]
            centroid = _centroid_vec(vecs)
            if not centroid or not len(daily_pairs) or not width:
                columns.append(np.zeros(len(daily_pairs), dtype=np.float32))
                continue
            columns.append(qmat @ np.asarray(centroid, dtype=np.float32))
        s_evt_all[cap] = np.stack(columns, axis=1)
    daily_targets = {card["id"]: [card["id"]] for card in daily_cards}
    daily_capped = _capped_linked_events(daily_targets, store, cap=5)
    daily_bias = hub_bias(
        {card["id"]: card["score_units"] or None for card in daily_cards},
        {card["id"]: centroid for card in daily_cards if (centroid := _centroid_vec(card["events"].get(5) or []))},
        event_bank(store, cache, mu),
        k=8,
        alpha=_GUIDANCE_HUB_ALPHA,
        exclude={key: [rec.block_id for rec in recs] for key, recs in daily_capped.items()},
        beta=0.95,
    )

    def _daily_loo(nnn: bool) -> dict[str, float]:
        """Leave each cited event out of its daily card, on the same score the fallback uses.

        Message labels do not meet these cards. The daily gate has to come from
        the event the card already cites, or it is just the grid floor.
        """
        pairs: list[tuple[list[float], set[str], str]] = []
        for rec in store.records:
            if rec.item_type != "event" or _is_rejected(rec):
                continue
            related = {str(item) for item in (rec.related or ())}
            hit_ids = {card["id"] for card in daily_cards if card["id"] in related}
            if not hit_ids:
                continue
            raw = (cache.get(rec.block_id) or {}).get("embedding") or []
            unit = _unit_residual(list(raw), mu) if raw else None
            if not unit:
                continue
            pairs.append((unit, hit_ids, rec.block_id))
        card_units = [list(card["score_units"]) for card in daily_cards]
        n = len(pairs)
        if not n:
            return {
                "loo_n": 0,
                "loo_top1": 0.0,
                "loo_mean_rank": 0.0,
                "loo_impostors": 0.0,
                "loo_gate": 0.0,
                "loo_admit": 0.0,
                "loo_over_gate": 0.0,
            }
        top1 = 0
        rank_sum = 0
        ahead_sum = 0
        best_true: list[float | None] = []
        impostor_counts: list[list[float]] = []
        for query, hit_ids, drop in pairs:
            scored: list[tuple[float, str]] = []
            for card, units in zip(daily_cards, card_units):
                dots = [
                    float(sum(a * b for a, b in zip(query, unit)))
                    for unit in units
                    if unit and len(unit) == len(query)
                ]
                s_card = max(dots) if dots else 0.0
                kept = [
                    vec
                    for event_id, vec in (card["event_units"].get(5) or [])
                    if event_id != drop and len(vec) == len(query)
                ]
                centroid = _centroid_vec(kept)
                s_use = (
                    float(sum(a * b for a, b in zip(query, centroid))) if centroid else 0.0
                )
                sim = max(s_card, 0.95 * s_use)
                if nnn:
                    sim -= float(daily_bias.get(card["id"], 0.0))
                scored.append((sim, card["id"]))
            trues = [sim for sim, card_id in scored if card_id in hit_ids]
            if not trues:
                best_true.append(None)
                impostor_counts.append([sim for sim, _card_id in scored])
                rank_sum += len(scored) + 1
                ahead_sum += len(scored)
                continue
            target = max(trues)
            best_true.append(target)
            ahead = sum(1 for sim, card_id in scored if card_id not in hit_ids and sim > target)
            ahead_sum += ahead
            rank_sum += ahead + 1
            if ahead == 0:
                top1 += 1
            impostor_counts.append([sim for sim, card_id in scored if card_id not in hit_ids])
        chosen_gate = 0.0
        chosen_admit = 0.0
        chosen_over = 0.0
        fallback: tuple[float, float, float] | None = None
        for gate_value in _R2_GATES:
            admit = sum(
                1 for score in best_true if score is not None and score >= gate_value
            ) / n
            over = sum(
                sum(1 for sim in sims if sim >= gate_value) for sims in impostor_counts
            ) / n
            if over <= 2.35 and (fallback is None or admit > fallback[1]):
                fallback = (gate_value, admit, over)
            if over <= 2.35 and admit >= 0.50:
                chosen_gate = gate_value
                chosen_admit = admit
                chosen_over = over
                break
        if chosen_gate == 0.0 and fallback is not None:
            chosen_gate, chosen_admit, chosen_over = fallback
        return {
            "loo_n": n,
            "loo_top1": top1 / n,
            "loo_mean_rank": rank_sum / n,
            "loo_impostors": ahead_sum / n,
            "loo_gate": chosen_gate,
            "loo_admit": chosen_admit,
            "loo_over_gate": chosen_over,
        }

    daily_loo = _daily_loo(True)
    for index, pair in enumerate(daily_pairs):
        pair["i"] = index
    by_id = {card["id"]: card for card in daily_cards}

    def _finish(scored: list[dict[str, Any]], pair: dict[str, Any], *, event_tie: str):
        scored.sort(key=lambda item: (-item["rank"], -item["s_card"], item["id"]))
        return scored, event_tie

    def _lexical_order(pair: dict[str, Any], qv: list[float]):
        hits = []
        if lexical_ok:
            try:
                hits = [
                    row
                    for row in search_lexical(pair["text"], k=_LEXICAL_K, staging=staging)
                    if row["id"] in by_id
                ]
            except Exception:
                hits = []
        if hits:
            best = min(float(row["bm25"]) for row in hits)
            scored = []
            for row in hits:
                card = by_id[str(row["id"])]
                rel = (float(row["bm25"]) / best) if best else 1.0
                scored.append(
                    {
                        "id": card["id"],
                        "kind": card["kind"],
                        "s_card": rel,
                        "rank": 0.8 * rel + 0.2 * card["live"] / 10.0,
                    }
                )
            return _finish(scored, pair, event_tie="")
        pool = []
        for card in daily_cards:
            if not card["vec"] or not qv:
                continue
            sim = _dot(qv, card["vec"])
            if sim >= COSINE_FLOOR:
                pool.append((sim, card))
        pool.sort(key=lambda item: -item[0])
        span = max(len(pool), 1)
        scored = []
        for index, (_sim, card) in enumerate(pool):
            rel = 1.0 - (index / span)
            scored.append(
                {
                    "id": card["id"],
                    "kind": card["kind"],
                    "s_card": rel,
                    "rank": 0.8 * rel + 0.2 * card["live"] / 10.0,
                }
            )
        return _finish(scored, pair, event_tie="")

    def _vector_order(beta: float, cap: int, use_events: bool, nnn: bool = False):
        def order(pair: dict[str, Any], qv: list[float]):
            del qv
            row_i = int(pair["i"])
            scored = []
            event_winners = []
            label_evt = 0.0
            floor = -1.0 if nnn else COSINE_FLOOR
            for col, card in enumerate(daily_cards):
                s_card = float(s_card_all[row_i, col]) if card["score_units"] else 0.0
                s_evt = float(s_evt_all[cap][row_i, col]) if use_events else 0.0
                if card["id"] == pair["card"]:
                    label_evt = s_evt
                if card["score_units"]:
                    sim = max(s_card, beta * s_evt) if use_events else s_card
                else:
                    sim = beta * s_evt if use_events else 0.0
                if nnn:
                    sim -= float(daily_bias.get(card["id"], 0.0))
                if sim < floor:
                    continue
                scored.append(
                    {
                        "id": card["id"],
                        "kind": card["kind"],
                        "s_card": s_card,
                        "rank": 0.8 * sim + 0.2 * card["live"] / 10.0,
                    }
                )
                if use_events and beta * s_evt >= s_card and beta * s_evt >= COSINE_FLOOR:
                    event_winners.append((s_evt, s_card, card["id"]))
            tie = ""
            if event_winners and label_evt > 0:
                siblings = [
                    item for item in event_winners if abs(item[0] - label_evt) <= 1e-4
                ]
                if len(siblings) >= 2:
                    siblings.sort(key=lambda item: (-item[1], item[2]))
                    tie = "first" if siblings[0][2] == pair["card"] else "other"
            return _finish(scored, pair, event_tie=tie)

        return order

    daily_cells = [
        {"variant": "legacy", "beta": 0.0, "cap": 0, **daily_rates(_lexical_order)}
    ]
    for cap in _CAPS:
        daily_cells.append(
            {
                "variant": "card",
                "beta": 0.0,
                "cap": int(cap),
                **daily_rates(_vector_order(0.0, cap, False)),
            }
        )
    for cap in _CAPS:
        for beta in _BETAS:
            daily_cells.append(
                {
                    "variant": "card_events",
                    "beta": float(beta),
                    "cap": int(cap),
                    **daily_rates(_vector_order(beta, cap, True)),
                }
            )
    daily_cells.append(
        {"variant": "r2", "beta": 0.0, "cap": 0, **daily_rates(_vector_order(0.0, 5, False))}
    )
    daily_cells.append(
        {
            "variant": "r2_centroid",
            "beta": 0.95,
            "cap": 5,
            **daily_rates(_vector_order(0.95, 5, True)),
        }
    )
    daily_cells.append(
        {
            "variant": "r2_centroid_nnn",
            "beta": 0.95,
            "cap": 5,
            **daily_rates(_vector_order(0.95, 5, True, True)),
        }
    )

    legacy_cell = cells[0]
    index_cells = [cell for cell in cells if cell["variant"] == "index_events" and cell["gate_far_002"] > 0]
    best = None
    for cell in index_cells:
        if best is None or cell["admit_at_gate"] > best["admit_at_gate"]:
            best = cell
    legacy_daily = daily_cells[0]
    daily_best = None
    for cell in daily_cells:
        if cell["variant"] != "card_events":
            continue
        if daily_best is None or cell["top1"] > daily_best["top1"]:
            daily_best = cell
    verdict = "ship"
    chosen = best or {"beta": 0.95, "cap": 5, "gate_far_002": 0.92, "admit_at_gate": 0.0}
    if best is None or chosen["admit_at_gate"] <= legacy_cell["admit_at_gate"]:
        verdict = "stop_procedure"
    beta_step = {value: index for index, value in enumerate(_BETAS)}
    if (
        verdict == "ship"
        and daily_best is not None
        and abs(beta_step.get(chosen["beta"], 0) - beta_step.get(daily_best["beta"], 0)) > 1
    ):
        verdict = "stop_beta"
    chosen_daily = next(
        (
            cell
            for cell in daily_cells
            if cell["variant"] == "card_events"
            and cell["beta"] == chosen["beta"]
            and cell["cap"] == chosen["cap"]
        ),
        daily_best or legacy_daily,
    )
    if verdict == "ship" and chosen_daily["top1"] <= legacy_daily["top1"]:
        verdict = "stop_daily"
    if verdict == "ship" and chosen_daily["top1_english"] < legacy_daily["top1_english"]:
        verdict = "stop_english"
    gate = float(chosen["gate_far_002"])
    fixtures_ok = 0.40 < gate <= 0.95
    _emit(
        {
            "mode": "gate",
            "ok": True,
            "overlay": overlay,
            "positive_available": positive_available,
            "positive_used": len(monthly_pos),
            "event_sample": event_sample,
            "negative_used": len(negatives),
            "daily_available": daily_available,
            "daily_used": len(daily_pairs),
            "rows": len(prepared),
            "overlay_cards": len(missing_blocks),
            "scores_out_max_abs_diff": float(diff),
            "lexical_db": lexical_ok,
            "monthly": cells,
            "daily": daily_cells,
            "verdict": verdict,
            "gate": gate,
            "beta": float(chosen["beta"]),
            "cap": int(chosen["cap"]),
            "fixtures_ok": fixtures_ok,
            "procedure_admit": float(chosen["admit_at_gate"]),
            "legacy_procedure_admit": float(legacy_cell["admit_at_gate"]),
            "daily_top1": float(chosen_daily["top1"]),
            "legacy_daily_top1": float(legacy_daily["top1"]),
            "daily_english_top1": float(chosen_daily["top1_english"]),
            "legacy_english_top1": float(legacy_daily["top1_english"]),
            "loo_top1": float(loo_nnn["loo_top1"]),
            "loo_mean_rank": float(loo_nnn["loo_mean_rank"]),
            "loo_impostors": float(loo_nnn["loo_impostors"]),
            "loo_gate": float(loo_nnn["loo_gate"]),
            "loo_admit": float(loo_nnn["loo_admit"]),
            "loo_over_gate": float(loo_nnn["loo_over_gate"]),
            "loo_n": int(loo_nnn["loo_n"]),
            "live_flips": int(loo_nnn["live_flips"]),
            "daily_loo_top1": float(daily_loo["loo_top1"]),
            "daily_loo_mean_rank": float(daily_loo["loo_mean_rank"]),
            "daily_loo_impostors": float(daily_loo["loo_impostors"]),
            "daily_loo_gate": float(daily_loo["loo_gate"]),
            "daily_loo_admit": float(daily_loo["loo_admit"]),
            "daily_loo_over": float(daily_loo["loo_over_gate"]),
            "daily_loo_n": int(daily_loo["loo_n"]),
        }
    )


def _entropy_bits(sims: list[float], tau: float = 0.03) -> float:
    """Match the pile's entropy so the grid stops on the same plateau the ranker does.

    A looser copy would keep adding lines after the live loop has already given up.
    """
    if not sims:
        return 0.0
    peak = max(sims)
    weights = [math.exp((score - peak) / tau) for score in sims]
    total = sum(weights) or 1.0
    entropy = 0.0
    for weight in weights:
        prob = weight / total
        if prob > 0.0:
            entropy -= prob * math.log(prob, 2)
    return entropy


def _history_decision(
    steps: list[list[tuple[str, float]]],
    labels: set[str],
    *,
    floor: float,
    margin: float,
    eps: float | None,
    gate: float,
    positive: bool,
    tau: float = 0.03,
) -> tuple[str, int]:
    """Say whether this pile would inject the labelled card, and how many steps it scored.

    Step 0 uses the monthly gate and does not keep piling once any row clears it.
    Later steps use the pile floor and margin. A winner outside the label is a miss,
    so a hub card cannot count as a saved follow-up.
    """
    if not steps or not steps[0]:
        return "miss", 0

    def _top(step: list[tuple[str, float]]) -> tuple[str, float, float]:
        ordered = sorted(step, key=lambda item: item[1], reverse=True)
        best_id, best = ordered[0]
        second = ordered[1][1] if len(ordered) > 1 else 0.0
        return best_id, best, second

    best_id, best, _second = _top(steps[0])
    if best >= gate:
        if not positive or best_id in labels:
            return "step0", 1
        return "miss", 1
    h_prev = _entropy_bits([score for _row_id, score in steps[0]], tau)
    for index, step in enumerate(steps[1:], start=1):
        if not step:
            return "miss", index
        best_id, best, second = _top(step)
        if best >= floor and best - second >= margin:
            if not positive or best_id in labels:
                return "pile", index + 1
            return "miss", index + 1
        h_now = _entropy_bits([score for _row_id, score in step], tau)
        if eps is not None and h_prev - h_now <= eps:
            return "miss", index + 1
        h_prev = h_now
    return "miss", len(steps)


def _mode_history() -> None:
    """Calibrate the short-line pile from live follow-ups without printing a message or a card.

    The grid is scored offline from the same cosine the ranker stores in `scores_out`.
    One live call checks that copy. Defaults stay in the module until a cell beats
    step 0 at a false-admit rate of 2% and the pile stays under the embedder's length.
    """
    import numpy as np

    from monthly_actions import (
        _GUIDANCE_COS_GATE,
        _GUIDANCE_EVENT_BETA,
        _GUIDANCE_EVENT_CAP,
        _GUIDANCE_GATE,
        _GUIDANCE_HUB_ALPHA,
        _PILE_MAX_TURNS,
        _PILE_SHORT_CHARS,
        _lexical_sim,
        rank_monthly_guidance,
    )
    from recall.embed import MAX_LENGTH
    from recall.embed_cache import (
        _compose_embedding_text,
        load_cache,
        mean_direction,
        scale_block_id,
        scan_all_blocks,
    )
    from recall.ids import BlockIndex
    from recall.tools import _capped_linked_events, _unit_residual, event_bank, hub_bias

    home = _home()
    staging = home / "memories" / "staging"
    _stage("load")
    store = BlockIndex(staging)
    cache = {key: dict(value) for key, value in load_cache().items()}
    missing_blocks = []
    missing_texts = []
    for block in scan_all_blocks().values():
        if (cache.get(block["id"]) or {}).get("embedding"):
            continue
        missing_blocks.append(block)
        missing_texts.append(_compose_embedding_text(block))
    if missing_texts:
        _stage("encode-cards")
        filled = _encode_unique(missing_texts)
        for block in missing_blocks:
            vec = filled.get(_compose_embedding_text(block)) or []
            if vec:
                cache[block["id"]] = {"embedding": vec}
    mu = mean_direction(cache)
    messages = _load_user_messages(home / "state.db")
    by_session: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        by_session.setdefault(msg["session_id"], []).append(msg)
    for rows in by_session.values():
        rows.sort(key=lambda item: (item["ts"], item["id"]))
    labels = _source_labels(store, messages, min_chars=0)
    actives = _active_rows(staging)
    prepared = []
    for payload, row, kind in actives:
        evidence = [str(item) for item in (row.evidence or ()) if str(item).strip()]
        index_text = str(row.lookup_text() or "").strip()
        view_kind, view_text = _view_of(row)
        index_id = _scale_id(scale_block_id, text=index_text, mem_id=row.id, month_key=str(payload.key))
        view_id = (
            _scale_id(
                scale_block_id,
                text=view_text,
                mem_id=row.id,
                month_key=str(payload.key),
                kind=view_kind,
            )
            if view_text
            else ""
        )
        raw = (cache.get(index_id) or {}).get("embedding") or []
        view_raw = (cache.get(view_id) or {}).get("embedding") or [] if view_id else []
        events = _event_vectors(store, evidence, cache, _GUIDANCE_EVENT_CAP, mu)
        prepared.append(
            {
                "id": str(row.id),
                "kind": kind,
                "index_text": index_text,
                "view_text": view_text,
                "evidence": evidence,
                "index_vec": [float(x) for x in raw],
                "view_vec": [float(x) for x in view_raw],
                "events": events,
                "centroid": _centroid_vec(events),
            }
        )
    history_targets = {row["id"]: row["evidence"] for row in prepared}
    history_capped = _capped_linked_events(history_targets, store, cap=_GUIDANCE_EVENT_CAP)
    history_cards: dict[str, list[list[float]]] = {}
    for row in prepared:
        units = []
        for raw in (row["index_vec"], row["view_vec"]):
            if raw and (unit := _unit_residual(raw, mu)):
                units.append(unit)
        if units:
            history_cards[row["id"]] = units
    history_bias = hub_bias(
        history_cards,
        {row["id"]: row["centroid"] for row in prepared if row["centroid"]},
        event_bank(store, cache, mu),
        k=8,
        alpha=_GUIDANCE_HUB_ALPHA,
        exclude={key: [rec.block_id for rec in recs] for key, recs in history_capped.items()},
        beta=_GUIDANCE_EVENT_BETA,
    )
    evidence_ids = set().union(*(row["evidence"] for row in prepared)) if prepared else set()
    index_of: dict[tuple[str, int], int] = {}
    for rows in by_session.values():
        for index, msg in enumerate(rows):
            index_of[(msg["session_id"], msg["id"])] = index

    def _history_of(msg: dict[str, Any]) -> list[tuple[float, str]]:
        rows = by_session.get(msg["session_id"]) or []
        start = index_of.get((msg["session_id"], msg["id"]), 0)
        earlier: list[tuple[float, str]] = []
        for prev in reversed(rows[:start]):
            if _ACK_RE.fullmatch(prev["content"]):
                continue
            earlier.append((float(prev["ts"]), prev["content"]))
            if len(earlier) >= 5:
                break
        return earlier

    positives = []
    seen_text: set[str] = set()
    for msg in messages:
        text = msg["content"]
        if len(text) > _PILE_SHORT_CHARS or text in seen_text or _ACK_RE.fullmatch(text):
            continue
        card_ids = labels.get((msg["session_id"], msg["id"])) or set()
        row_ids = {row["id"] for row in prepared if card_ids.intersection(row["evidence"])}
        if not row_ids:
            continue
        seen_text.add(text)
        positives.append({"text": text, "ts": float(msg["ts"]), "labels": row_ids, "history": _history_of(msg)})
    positives = _sample(positives, _POS_CAP)
    negatives = []
    for msg in messages:
        text = msg["content"]
        if len(text) > _PILE_SHORT_CHARS or text in seen_text or _ACK_RE.fullmatch(text):
            continue
        if (msg["session_id"], msg["id"]) in labels:
            continue
        seen_text.add(text)
        negatives.append({"text": text, "ts": float(msg["ts"]), "labels": set(), "history": _history_of(msg)})
    negatives = _sample(negatives, _NEG_CAP)
    if not positives or not prepared:
        _emit(
            {
                "mode": "history",
                "ok": False,
                "error": "stop_sample",
                "short_pos": len(positives),
                "short_neg": len(negatives),
                "rows": len(prepared),
            }
        )
        return

    gaps: list[float] = []
    for rows in by_session.values():
        inside = [
            msg
            for msg in rows
            if evidence_ids.intersection(labels.get((msg["session_id"], msg["id"])) or set())
        ]
        for prev, nxt in zip(inside, inside[1:]):
            gap = float(nxt["ts"]) - float(prev["ts"])
            if gap >= 0.0:
                gaps.append(gap)
    gap_p90 = _quantile(gaps, 0.90)
    gap_s = int(math.ceil(gap_p90 / 60.0 - 1e-9) * 60) if gaps else 600
    if gap_s <= 0:
        gap_s = 60

    def _prefixes(sample: dict[str, Any]) -> list[str]:
        pile = [sample["text"]]
        last_ts = float(sample["ts"])
        for ts, line in sample["history"]:
            if len(pile) >= _PILE_MAX_TURNS or last_ts - float(ts) > gap_s:
                break
            pile.append(line)
            last_ts = float(ts)
        return ["\n".join(pile[: end]) for end in range(1, len(pile) + 1)]

    samples = positives + negatives
    texts: list[str] = []
    prefixes = []
    for sample in samples:
        steps = _prefixes(sample)
        prefixes.append(steps)
        texts.extend(steps)
    token_lengths = [_embed_token_count(steps[-1]) for steps in prefixes if steps]
    _stage("encode")
    encoded = _encode_unique(texts)
    if texts and not encoded:
        _emit({"mode": "history", "ok": False, "error": "stop_encode", "short_pos": len(positives)})
        return

    def _sims(text: str) -> list[tuple[str, float]]:
        qvec = encoded.get(text) or []
        query = _unit_residual(qvec, mu) if qvec else None
        scored: list[tuple[str, float]] = []
        for row in prepared:
            lexical = _lexical_sim(text, row["index_text"])
            lexical_view = _lexical_sim(text, row["view_text"]) if row["view_text"] else 0.0
            if lexical >= _GUIDANCE_GATE or lexical_view >= _GUIDANCE_GATE:
                scored.append((row["id"], max(lexical, lexical_view)))
                continue
            index = _unit_residual(row["index_vec"], mu) if row["index_vec"] else None
            view = _unit_residual(row["view_vec"], mu) if row["view_vec"] else None
            s_trig = _dot(query or [], index or [])
            s_view = _dot(query or [], view or [])
            s_use = _dot(query or [], row["centroid"])
            sim = max(s_trig, s_view, _GUIDANCE_EVENT_BETA * s_use) - float(
                history_bias.get(row["id"], 0.0)
            )
            scored.append((row["id"], sim))
        return scored

    sim_cache: dict[str, list[tuple[str, float]]] = {}
    for text in dict.fromkeys(texts):
        sim_cache[text] = _sims(text)
    check_text = texts[0]
    scores_out: list[dict[str, Any]] = []
    rank_monthly_guidance(
        check_text,
        staging=staging,
        store=store,
        scores_out=scores_out,
        query_vec=encoded.get(check_text) or None,
        cache=cache,
        history=(),
    )
    ours = {row_id: score for row_id, score in sim_cache[check_text]}
    diffs = [abs(float(item["sim"]) - ours[item["id"]]) for item in scores_out if item["id"] in ours]
    diff = max(diffs) if diffs else 0.0

    step_lists = [[sim_cache[text] for text in steps] for steps in prefixes]
    step0_hits = 0
    neg_step0 = 0
    for sample, steps in zip(samples, step_lists):
        kind, _used = _history_decision(
            steps,
            sample["labels"],
            floor=1.1,
            margin=1.1,
            eps=0.0,
            gate=_GUIDANCE_COS_GATE,
            positive=bool(sample["labels"]),
        )
        if kind == "step0" and sample["labels"]:
            step0_hits += 1
        elif kind == "step0":
            neg_step0 += 1
    n_pos = len(positives)
    n_neg = len(negatives)
    floors = tuple(round(0.05 + i * 0.05, 2) for i in range(12))
    margins = (0.02, 0.04, 0.06, 0.10)
    taus = (0.02, 0.03, 0.05, 0.10)
    eps_grid: tuple[tuple[str, float | None], ...] = (
        ("e10", 0.10),
        ("e15", 0.15),
        ("e25", 0.25),
        ("off", None),
    )
    best: tuple[tuple, dict[str, Any]] | None = None
    lowest_far: tuple[tuple, dict[str, Any]] | None = None
    for floor in floors:
        for margin in margins:
            for tau in taus:
                for eps_label, eps in eps_grid:
                    pos_hits = 0
                    neg_hits = 0
                    for sample, steps in zip(samples, step_lists):
                        kind, _used = _history_decision(
                            steps,
                            sample["labels"],
                            floor=floor,
                            margin=margin,
                            eps=eps,
                            gate=_GUIDANCE_COS_GATE,
                            positive=bool(sample["labels"]),
                            tau=tau,
                        )
                        if kind == "miss":
                            continue
                        if sample["labels"]:
                            pos_hits += 1
                        else:
                            neg_hits += 1
                    far = (neg_hits / n_neg) if n_neg else 0.0
                    cell = {
                        "floor": floor,
                        "margin": margin,
                        "tau": tau,
                        "eps": eps_label,
                        "admit": pos_hits / n_pos,
                        "far": far,
                        "pos_hits": pos_hits,
                        "neg_hits": neg_hits,
                    }
                    far_key = (far, -pos_hits)
                    if lowest_far is None or far_key < lowest_far[0]:
                        lowest_far = (far_key, cell)
                    if far <= 0.02 and pos_hits > step0_hits:
                        pick = (pos_hits, -far)
                        if best is None or pick > best[0]:
                            best = (pick, cell)
    chosen = best[1] if best is not None else (lowest_far[1] if lowest_far else {})
    tokens_p99 = _quantile([float(n) for n in token_lengths], 0.99)
    if tokens_p99 >= MAX_LENGTH:
        verdict = "stop_tokens"
    elif best is None and (n_neg == 0 or (neg_step0 / n_neg if n_neg else 0.0) > 0.02):
        verdict = "stop_far"
    elif best is None:
        verdict = "stop_admit"
    else:
        verdict = "ok"

    _stage("latency")
    without: list[float] = []
    with_hist: list[float] = []
    timed = 0
    timed_samples = [sample for sample in negatives if sample["history"]] or negatives
    for sample in timed_samples:
        if timed >= 12:
            break
        started = time.perf_counter()
        rank_monthly_guidance(
            sample["text"],
            staging=staging,
            store=store,
            cache=cache,
            history=(),
            now=sample["ts"],
        )
        without.append(time.perf_counter() - started)
        started = time.perf_counter()
        rank_monthly_guidance(
            sample["text"],
            staging=staging,
            store=store,
            cache=cache,
            history=sample["history"],
            now=sample["ts"],
        )
        with_hist.append(time.perf_counter() - started)
        timed += 1
    added = _quantile(with_hist, 0.95) - _quantile(without, 0.95)
    eps_number = {"e10": 0.10, "e15": 0.15, "e25": 0.25, "off": 0.0}.get(str(chosen.get("eps", "off")), 0.0)
    _emit(
        {
            "mode": "history",
            "ok": True,
            "verdict": verdict,
            "short_pos": n_pos,
            "short_neg": n_neg,
            "rows": len(prepared),
            "gap_n": len(gaps),
            "gap_p50": _quantile(gaps, 0.50),
            "gap_p90": gap_p90,
            "gap_s": gap_s,
            "pile_tokens_p99": tokens_p99,
            "step0_admit": step0_hits / n_pos,
            "step0_far": (neg_step0 / n_neg) if n_neg else 0.0,
            "best_floor": float(chosen.get("floor", 0.0)),
            "best_margin": float(chosen.get("margin", 0.0)),
            "best_tau": float(chosen.get("tau", 0.03)),
            "best_eps": eps_number,
            "best_eps_off": str(chosen.get("eps", "off")) == "off",
            "best_admit": float(chosen.get("admit", 0.0)),
            "best_far": float(chosen.get("far", 0.0)),
            "best_pos_hits": int(chosen.get("pos_hits", 0)),
            "best_neg_hits": int(chosen.get("neg_hits", 0)),
            "scores_out_max_abs_diff": diff,
            "latency_n": timed,
            "latency_p95_without": _quantile(without, 0.95),
            "latency_p95_with": _quantile(with_hist, 0.95),
            "latency_p95_added": added,
            "latency_ok": added <= 1.5,
        }
    )


def _embed_token_count(text: str) -> int:
    """Count embedder tokens without the 512 cap so a truncated id list cannot hide a long pile.

    The live tokenizer stops at MAX_LENGTH. The probe needs the length before that cut.
    """
    tok = getattr(_embed_token_count, "_tok", None)
    if tok is None:
        try:
            from tokenizers import Tokenizer

            from recall.embed import _cache_dir, _onnx_file

            path = _cache_dir() / "tokenizer.json"
            if not path.is_file():
                onnx = _onnx_file()
                if onnx is not None:
                    path = onnx.parent / "tokenizer.json"
            if path.is_file():
                tok = Tokenizer.from_file(str(path))
        except Exception:
            tok = False
        setattr(_embed_token_count, "_tok", tok)
    if not tok:
        return len(text)
    return len(tok.encode(text).ids)


def _mode_strength() -> None:
    """Compare each row's stored strength with the live evidence value. Aggregates only."""
    from datetime import date

    from monthly_actions import _is_active_decision, _iter_payloads
    from recall.ids import BlockIndex
    from recall.strength import strength_value

    home = _home()
    staging = home / "memories" / "staging"
    store = BlockIndex(staging)
    today = date.today()
    deltas: list[float] = []
    lower = {"importance": 0, "unrecalled": 0, "older": 0, "other": 0}
    imp_counts = {2: 0, 3: 0, 4: 0, 5: 0, "other": 0}
    within = 0
    out_of_range = 0
    changed = 0
    credited = 0
    for payload in _iter_payloads(staging):
        superseded = {sid for row in payload.key_decisions for sid in row.supersedes}
        rows = [
            row
            for row in payload.key_decisions
            if _is_active_decision(row, superseded)
        ] + list(payload.key_procedures)
        for row in rows:
            cards = []
            for evidence_id in getattr(row, "evidence", ()) or ():
                rec = store.get(str(evidence_id))
                if rec is not None:
                    cards.append(dict(rec.parsed))
            if cards:
                importance = math.floor(
                    sum(int(card.get("importance") or 3) for card in cards) / len(cards) + 0.5
                )
            else:
                importance = 3
            live = strength_value(
                recall_n=0.0,
                first_seen=None,
                importance=importance,
                now=today,
                evidence=cards,
            )
            stored = float(getattr(row, "strength", 0.0) or 0.0)
            if not 0.0 <= live <= 10.0 or not 0.0 <= stored <= 10.0:
                out_of_range += 1
            delta = live - stored
            deltas.append(delta)
            if abs(delta) > 0.01:
                changed += 1
            if abs(live - stored) <= 0.01:
                within += 1
            if float(getattr(row, "recall_n", 0.0) or 0.0) > 0:
                credited += 1
            level = int(importance)
            if level in imp_counts and level != "other":
                imp_counts[level] += 1
            else:
                imp_counts["other"] += 1
            if delta < -0.01:
                recalled = [card for card in cards if float(card.get("recall_n") or 0) > 0]
                row_seen = str(getattr(row, "first_seen", "") or "")[:10]
                older = any(str(card.get("first_seen") or card.get("valid_from") or "")[:10] < row_seen for card in recalled if row_seen)
                if importance < 3:
                    lower["importance"] += 1
                elif not recalled:
                    lower["unrecalled"] += 1
                elif older:
                    lower["older"] += 1
                else:
                    lower["other"] += 1
    n = len(deltas)
    _emit(
        {
            "mode": "strength",
            "ok": True,
            "rows": n,
            "changed": changed,
            "within_001": within,
            "out_of_range": out_of_range,
            "credited_rows": credited,
            "change_p50": _quantile(deltas, 0.50),
            "change_p90": _quantile(deltas, 0.90),
            "change_max": max(deltas) if deltas else 0.0,
            "change_min": min(deltas) if deltas else 0.0,
            "lower_importance": lower["importance"],
            "lower_unrecalled": lower["unrecalled"],
            "lower_older": lower["older"],
            "lower_other": lower["other"],
            "imp_2": imp_counts[2],
            "imp_3": imp_counts[3],
            "imp_4": imp_counts[4],
            "imp_5": imp_counts[5],
            "imp_other": imp_counts["other"],
        }
    )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    _install_plugin(args.plugin)
    if args.mode == "cache":
        _mode_cache()
        return
    if args.mode == "gate":
        _mode_gate(bool(args.overlay))
        return
    if args.mode == "strength":
        _mode_strength()
        return
    if args.mode == "history":
        _mode_history()
        return
    _emit({"mode": "gate", "ok": False, "error": "not_run"})


if __name__ == "__main__":
    main()
