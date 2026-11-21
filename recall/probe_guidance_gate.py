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
_LOOKBACK_S = 60 * 24 * 3600
_BETAS = (0.85, 0.90, 0.95, 1.00)
_CAPS = (3, 5)
_GATES = tuple(round(x * 0.01, 2) for x in range(50, 100))
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


def _scale_id(scale_block_id, *, text: str, mem_id: str, month_key: str) -> str:
    return scale_block_id(
        {
            "scale": "month",
            "kind": "dp",
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


def _max_event(query: list[float], rows: list[list[float]]) -> float:
    if not query or not rows:
        return 0.0
    best = 0.0
    width = len(query)
    for row in rows:
        if len(row) != width:
            continue
        score = _dot(query, row)
        if score > best:
            best = score
    return best


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


def _event_vectors(store, ids: list[str], cache: dict, cap: int) -> list[list[float]]:
    from recall.tools import _capped_linked_events

    chosen = _capped_linked_events({"row": ids}, store, cap=cap).get("row") or []
    vectors: list[list[float]] = []
    for rec in chosen:
        raw = (cache.get(rec.block_id) or {}).get("embedding") or []
        if raw:
            vectors.append([float(x) for x in raw])
    return vectors


def _bucket(count: int) -> str:
    if count <= 0:
        return "zero"
    if count <= 2:
        return "one_two"
    return "three_plus"


def _lowest_gate(neg_scores: list[float], pos_hits: list[tuple[float, bool]]) -> tuple[float, float]:
    """Lowest gate whose false-admit rate is at most 2%, and the admit rate there."""
    chosen = -1.0
    admit = 0.0
    n_neg = max(len(neg_scores), 1)
    n_pos = max(len(pos_hits), 1)
    for gate in _GATES:
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
        month_rows = list(collect_month_scale_rows(staging))
    except Exception:
        month_rows = []
    week_rows = []
    try:
        week_rows = list(collect_week_scale_rows(staging))
    except Exception:
        week_rows = []
    current = {scale_block_id(row) for row in (*month_rows, *week_rows)}
    missing_month = sum(
        1
        for row in month_rows
        if not (cache.get(scale_block_id(row)) or {}).get("embedding")
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

    from monthly_actions import _guidance_sim
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
        legacy_text = _legacy_lookup(row)
        prepared.append(
            {
                "id": row.id,
                "kind": kind,
                "month": str(payload.key),
                "index_text": index_text,
                "legacy_text": legacy_text,
                "evidence": evidence,
                "index_id": _scale_id(
                    scale_block_id, text=index_text, mem_id=row.id, month_key=str(payload.key)
                ),
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
        for block in missing_blocks:
            vec = encoded.get(_compose_embedding_text(block)) or []
            if vec:
                cache[block["id"]] = {"embedding": vec}
    for row in prepared:
        legacy = (cache.get(row["legacy_id"]) or {}).get("embedding") or []
        index = (cache.get(row["index_id"]) or {}).get("embedding") or []
        row["legacy_vec"] = [float(x) for x in legacy]
        row["index_vec"] = [float(x) for x in index]
        row["event_n"] = _event_count(store, row["evidence"])
        row["events"] = {
            cap: _event_vectors(store, row["evidence"], cache, cap) for cap in _CAPS
        }

    def score_variant(query: str, qv: list[float], qi: int, *, legacy: bool, cap: int, beta: float):
        best_id = ""
        best_key = None
        top = 0.0
        winner = prepared[0] if prepared else None
        for ri, row in enumerate(prepared):
            text = row["legacy_text"] if legacy else row["index_text"]
            vec = row["legacy_vec"] if legacy else row["index_vec"]
            s_trig = _guidance_sim(query, text, qv, vec)
            s_evt = 0.0 if legacy or cap == 0 else float(month_evt[cap][qi, ri])
            sim = s_trig if legacy or cap == 0 else max(s_trig, beta * s_evt)
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
    qvecs = [encoded.get(text) or [] for text, _labels, _kind in queries]
    width = next((len(vec) for vec in qvecs if vec), 0)
    qmat = (
        np.asarray(
            [vec if len(vec) == width else [0.0] * width for vec in qvecs],
            dtype=np.float32,
        )
        if qvecs and width
        else np.zeros((len(qvecs), 1), dtype=np.float32)
    )
    month_evt: dict[int, Any] = {}
    for cap in _CAPS:
        columns = []
        for row in prepared:
            vecs = [vec for vec in (row["events"].get(cap) or []) if len(vec) == width]
            if not vecs or not len(qvecs) or not width:
                columns.append(np.zeros(len(qvecs), dtype=np.float32))
                continue
            emat = np.asarray(vecs, dtype=np.float32)
            columns.append(np.max(qmat @ emat.T, axis=1))
        month_evt[cap] = np.stack(columns, axis=1) if columns else np.zeros((len(qvecs), 0))

    def run_cell(*, legacy: bool, cap: int, beta: float) -> dict[str, Any]:
        pos_hits: list[tuple[float, bool]] = []
        proc_hits: list[tuple[float, bool]] = []
        neg_scores: list[float] = []
        neg_buckets: list[tuple[float, str]] = []
        for qi, ((text, labels, role), qv) in enumerate(zip(queries, qvecs)):
            if not qv:
                continue
            top, winner = score_variant(text, qv, qi, legacy=legacy, cap=cap, beta=beta)
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
        gate, admit = _lowest_gate(neg_scores, proc_hits or pos_hits)
        n_neg = max(len(neg_scores), 1)
        n_pos = max(len(pos_hits), 1)
        n_proc = max(len(proc_hits), 1)
        cell = {
            "variant": "legacy" if legacy else ("index" if cap == 0 else "index_events"),
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

    cells = [run_cell(legacy=True, cap=0, beta=0.0), run_cell(legacy=False, cap=0, beta=0.0)]
    for cap in _CAPS:
        for beta in _BETAS:
            cells.append(run_cell(legacy=False, cap=cap, beta=beta))

    diff = 0.0
    if monthly_pos and qvecs and qvecs[0]:
        from monthly_actions import rank_monthly_guidance

        held = {}
        for row in prepared:
            if row["index_vec"]:
                held[row["index_id"]] = {"embedding": row["index_vec"]}
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
                limit=8,
            )
        finally:
            monthly_actions._GUIDANCE_EVENT_CAP = previous_cap
            monthly_actions._GUIDANCE_EVENT_BETA = previous_beta
        ours = {}
        for row in prepared:
            s_trig = _guidance_sim(
                monthly_pos[0]["text"], row["index_text"], qvecs[0], row["index_vec"]
            )
            s_evt = _max_event(qvecs[0], row["events"].get(5) or [])
            ours[row["id"]] = max(s_trig, 0.95 * s_evt)
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
        daily_cards.append(
            {
                "id": rec.block_id,
                "kind": rec.item_type,
                "vec": [float(x) for x in raw],
                "live": float(live),
                "event_n": _event_count(store, [rec.block_id]),
                "events": {
                    cap: _event_vectors(store, [rec.block_id], cache, cap) for cap in _CAPS
                },
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
        }

    width = next((len(vec) for vec in encoded.values() if vec), 0)
    q_rows = []
    for pair in daily_pairs:
        vec = encoded.get(pair["text"]) or []
        q_rows.append(vec if len(vec) == width else [0.0] * width)
    qmat = (
        np.asarray(q_rows, dtype=np.float32)
        if q_rows and width
        else np.zeros((len(daily_pairs), 1), dtype=np.float32)
    )
    c_rows = [
        card["vec"] if len(card["vec"]) == width else [0.0] * max(width, 1) for card in daily_cards
    ]
    cmat = (
        np.asarray(c_rows, dtype=np.float32)
        if c_rows and width
        else np.zeros((len(daily_cards), qmat.shape[1]), dtype=np.float32)
    )
    if len(daily_pairs) and len(daily_cards) and width:
        s_card_all = qmat @ cmat.T
    else:
        s_card_all = np.zeros((len(daily_pairs), len(daily_cards)), dtype=np.float32)
    s_evt_all: dict[int, Any] = {}
    for cap in _CAPS:
        columns = []
        for card in daily_cards:
            vecs = [vec for vec in (card["events"].get(cap) or []) if len(vec) == width]
            if not vecs or not len(daily_pairs) or not width:
                columns.append(np.zeros(len(daily_pairs), dtype=np.float32))
                continue
            emat = np.asarray(vecs, dtype=np.float32)
            columns.append(np.max(qmat @ emat.T, axis=1))
        s_evt_all[cap] = np.stack(columns, axis=1)
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

    def _vector_order(beta: float, cap: int, use_events: bool):
        def order(pair: dict[str, Any], qv: list[float]):
            del qv
            row_i = int(pair["i"])
            scored = []
            event_winners = []
            label_evt = 0.0
            for col, card in enumerate(daily_cards):
                s_card = float(s_card_all[row_i, col]) if card["vec"] else 0.0
                s_evt = float(s_evt_all[cap][row_i, col]) if use_events else 0.0
                if card["id"] == pair["card"]:
                    label_evt = s_evt
                if card["vec"]:
                    sim = max(s_card, beta * s_evt) if use_events else s_card
                else:
                    sim = beta * s_evt if use_events else 0.0
                if sim < COSINE_FLOOR:
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
    h_prev = _entropy_bits([score for _row_id, score in steps[0]])
    for index, step in enumerate(steps[1:], start=1):
        if not step:
            return "miss", index
        best_id, best, second = _top(step)
        if best >= floor and best - second >= margin:
            if not positive or best_id in labels:
                return "pile", index + 1
            return "miss", index + 1
        h_now = _entropy_bits([score for _row_id, score in step])
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
        _GUIDANCE_EVENT_BETA,
        _GUIDANCE_EVENT_CAP,
        _GUIDANCE_GATE,
        _PILE_MAX_TURNS,
        _PILE_SHORT_CHARS,
        _lexical_sim,
        rank_monthly_guidance,
    )
    from recall.embed import MAX_LENGTH
    from recall.embed_cache import load_cache, scale_block_id
    from recall.ids import BlockIndex

    home = _home()
    staging = home / "memories" / "staging"
    _stage("load")
    store = BlockIndex(staging)
    cache = {key: dict(value) for key, value in load_cache().items()}
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
        index_id = _scale_id(scale_block_id, text=index_text, mem_id=row.id, month_key=str(payload.key))
        raw = (cache.get(index_id) or {}).get("embedding") or []
        events = _event_vectors(store, evidence, cache, _GUIDANCE_EVENT_CAP)
        prepared.append(
            {
                "id": str(row.id),
                "kind": kind,
                "index_text": index_text,
                "evidence": evidence,
                "index_vec": [float(x) for x in raw],
                "events": events,
                "event_mat": np.asarray(events, dtype=np.float64) if events else None,
            }
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
        q = np.asarray(qvec, dtype=np.float64) if qvec else None
        scored: list[tuple[str, float]] = []
        for row in prepared:
            lexical = _lexical_sim(text, row["index_text"])
            if lexical >= _GUIDANCE_GATE:
                s_trig = lexical
            elif q is not None and row["index_vec"]:
                s_trig = float(np.dot(np.asarray(row["index_vec"], dtype=np.float64), q))
            else:
                s_trig = lexical
            s_evt = 0.0
            if q is not None and row["event_mat"] is not None and row["event_mat"].size:
                s_evt = float(np.max(row["event_mat"] @ q))
            scored.append((row["id"], max(s_trig, _GUIDANCE_EVENT_BETA * s_evt)))
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
            gate=_GUIDANCE_GATE,
            positive=bool(sample["labels"]),
        )
        if kind == "step0" and sample["labels"]:
            step0_hits += 1
        elif kind == "step0":
            neg_step0 += 1
    n_pos = len(positives)
    n_neg = len(negatives)
    floors = tuple(round(0.75 + i * 0.01, 2) for i in range(16))
    margins = (0.04, 0.06, 0.08, 0.10, 0.12)
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
                        gate=_GUIDANCE_GATE,
                        positive=bool(sample["labels"]),
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
