"""Public monthly ops for the weekly bridge and recall lookups."""

from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Sequence

_monthly = Path(__file__).resolve().parent
_mymemory = _monthly.parent
for path in (_monthly, _mymemory):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from monthly_notes import map_batch  # noqa: E402
from monthly_slice import (  # noqa: E402
    calendar_range,
    carry_card,
    mechanical_facts,
    pack_batches,
    previous_month_key,
    week_slices,
)
from monthly_schema import MAP_BATCH_TOKENS, REDUCE_MAX_TOKENS  # noqa: E402
from monthly_state import (  # noqa: E402
    atomic_json_write,
    month_file_path,
    monthly_config,
    monthly_staging_dir,
)
from monthly_synth import synthesize_month  # noqa: E402
from monthly_writer import load_month, write_month, loads  # noqa: E402
from worker_llm import format_token_line, sum_usage_after, usage_ledger_offset  # noqa: E402

_MONTH_KEY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_GUIDANCE_GATE = 0.92
_GUIDANCE_EVENT_BETA = 0.95
_GUIDANCE_EVENT_CAP = 5
# Leave-one-out on cited events: weight 1 subtracts 0.49 from a true score of
# 0.46, so the wanted card drops out of the top. 0.25 keeps top-1 level with
# the centroid and puts fewer impostors over the gate than R2 alone.
_GUIDANCE_HUB_ALPHA = 0.25
# Lowest gate at that weight whose impostors stay within 2.35 and at least half
# of the cited events still clear it. Monthly and daily differ by 0.08, so the
# daily fallback has its own constant.
_GUIDANCE_COS_GATE = 0.31
_GUIDANCE_COS_GATE_DAILY = 0.39
_ACK_RE = re.compile(
    r"^(?:(?:哈|嗯|好|行|谢)+|可以|收到|了解|明白|ok|okay|thanks|thank you|got it|lol)"
    r"[!！.。,，~～…\s]*$",
    re.IGNORECASE,
)
_PILE_SHORT_CHARS = 24
# Same bar as the cosine gate. 0.85 sat above every R2 score, so a short
# follow-up could never recover its card. History mode had no labelled
# short lines (sources are wecom keys), so the margin and tau stay.
_PILE_FLOOR = 0.31
_PILE_MARGIN = 0.10
_PILE_EPS_BITS = 0.15
_PILE_GAP_S = 600
_PILE_MAX_TURNS = 6
_PILE_TAU = 0.03
_GUIDANCE_KIND_SHARE = 6
_GUIDANCE_KIND_FLOOR = 2
_GUIDANCE_TIE_EPS = 0.01
_BAND_DECISIONS = 4
_BAND_PROCEDURES = 3
_BAND_SUMMARY = 8
_HELP = (
    "/monthly update [YYYY-MM]  refresh that month (default: current)\n"
    "/monthly show [YYYY-MM]    print story bullets and D/P guidance\n"
    "/monthly suggest YYYY-MM [YYYY-MM ...]  up to four months, then talk: "
    "create a skill, add, replace, or remove"
)


def parse_month_key(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not _MONTH_KEY_RE.match(text):
        return None
    return text


_JUDGMENT_SECTIONS = (
    "user_image",
    "core_progress",
    "key_decisions",
    "key_procedures",
    "problems_and_risks",
    "next_month_focus",
    "comparison_with_last_month",
)


def reduce_resume_path(month_key: str) -> Path:
    """Keep a failed reduce beside the month so the next day patches only its gaps.

    The map cache is content-hashed notes. Mixing the synthesis into it would
    drop the gap list the clock needs.
    """
    return monthly_staging_dir() / ".reduce" / f"{month_key}.json"


def _positive_int(bag: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(bag.get(key))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _load_resume(month_key: str) -> dict[str, Any]:
    path = reduce_resume_path(month_key)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_resume(month_key: str, payload: dict[str, Any]) -> None:
    path = reduce_resume_path(month_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, payload)


def _delete_resume(month_key: str) -> None:
    reduce_resume_path(month_key).unlink(missing_ok=True)


def generate_month(
    month_key: str | None,
    *,
    reason: str = "bridge",
    call_oneshot=None,
    force_refresh: bool = False,
    gaps_only: bool = False,
) -> dict[str, Any]:
    """Run map-reduce for one calendar month and keep every section that passed.

    An incomplete reduce used to drop the whole month, including sections that
    had already passed. A later retry then paid for those sections again.
    gaps_only patches the stored gaps and does not rerun the map.
    """
    del reason
    key = parse_month_key(month_key)
    if key is None:
        return {"outcome": "bad_month", "month": month_key or ""}
    bag = monthly_config()
    map_tokens = _positive_int(bag, "map_batch_tokens", MAP_BATCH_TOKENS)
    reduce_tokens = _positive_int(bag, "reduce_max_tokens", REDUCE_MAX_TOKENS)
    stored = _load_resume(key) if gaps_only else {}
    if gaps_only and not stored:
        return {
            "outcome": "ok",
            "month": key,
            "path": "",
            "map_calls": 0,
            "pending_sections": [],
            "usage": {"valid": True, "attempts": [], "gap_sections": []},
        }
    if gaps_only and not stored.get("merged"):
        gaps_only = False
        stored = {}
    if gaps_only:
        notes = [{"items": stored.get("notes") or [], "cache_hit": True}]
    else:
        slices = week_slices(key, types=frozenset({"decision", "procedure"}))
        batches = pack_batches(slices, max_tokens=map_tokens)
        notes = [
            map_batch(key, batch, call_oneshot=call_oneshot, force_refresh=force_refresh)
            for batch in batches
        ]
    facts = mechanical_facts(key)
    start, _end = calendar_range(key)
    carry = carry_card(previous_month_key(start))
    payload, usage = synthesize_month(
        key,
        notes,
        call_oneshot=call_oneshot,
        carry=carry,
        facts=facts,
        reduce_max_tokens=reduce_tokens,
        resume=stored if gaps_only else None,
    )
    gap_sections = [
        section
        for section in (usage.get("gap_sections") or [])
        if section in _JUDGMENT_SECTIONS
    ]
    passed = [section for section in _JUDGMENT_SECTIONS if section not in gap_sections]
    if usage.get("comparison_exempt"):
        passed = [section for section in passed if section != "comparison_with_last_month"]
    map_calls = 0 if gaps_only else sum(0 if n.get("cache_hit") else 1 for n in notes)
    resume_body = {
        "merged": usage.get("merged") or {},
        "gaps": list(usage.get("gaps") or []),
        "notes": list(usage.get("notes") or []),
    }
    if not usage.get("valid") and not passed:
        _save_resume(key, resume_body)
        return {
            "outcome": "incomplete",
            "month": key,
            "path": "",
            "map_calls": map_calls,
            "pending_sections": gap_sections,
            "usage": usage,
            "payload": payload.to_dict(),
        }
    previous = None
    try:
        previous = load_month(key)
    except (FileNotFoundError, OSError, ValueError):
        previous = None
    carried = {}
    if not usage.get("valid") and previous is not None:
        for section in gap_sections:
            carried[section] = getattr(previous, section)
    payload = replace(
        payload,
        generator=replace(payload.generator, batch_tokens=map_tokens),
        pending_sections=tuple(gap_sections) if not usage.get("valid") else (),
        **carried,
    )
    if usage.get("valid"):
        _delete_resume(key)
        outcome = "ok"
    else:
        _save_resume(key, resume_body)
        outcome = "partial"
    from recall.ids import BlockIndex
    from recall.strength import strength_value

    evidence_store = BlockIndex(monthly_staging_dir().parent)

    def _from_evidence(row):
        cards = []
        for evidence_id in getattr(row, "evidence", ()) or ():
            rec = evidence_store.get(str(evidence_id))
            if rec is not None:
                cards.append(dict(rec.parsed))
        if cards:
            importance = math.floor(
                sum(int(card.get("importance") or 3) for card in cards) / len(cards) + 0.5
            )
        else:
            importance = 3
        return replace(
            row,
            importance=importance,
            strength=strength_value(
                recall_n=0.0,
                first_seen=None,
                importance=importance,
                evidence=cards,
            ),
        )

    prev_d = {row.id: row for row in previous.key_decisions} if previous is not None else {}
    prev_p = {row.id: row for row in previous.key_procedures} if previous is not None else {}

    def _kept(row, prev_rows):
        filled = _from_evidence(row)
        old = prev_rows.get(row.id)
        if old is None:
            return filled
        return replace(
            filled,
            recall_n=old.recall_n,
            last_recall_at=old.last_recall_at,
            first_seen=old.first_seen or filled.first_seen,
        )

    payload = replace(
        payload,
        key_decisions=tuple(_kept(row, prev_d) for row in payload.key_decisions),
        key_procedures=tuple(_kept(row, prev_p) for row in payload.key_procedures),
    )
    path = write_month(payload)
    return {
        "outcome": outcome,
        "month": key,
        "path": str(path),
        "map_calls": map_calls,
        "pending_sections": list(payload.pending_sections),
        "usage": usage,
        "payload": payload.to_dict(),
    }


def load_monthly_yaml(month_key: str | None = None) -> dict[str, Any]:
    """Return parsed YAML-as-JSON; missing file stays 404-equivalent."""
    key = parse_month_key(month_key)
    if key is None:
        return {"outcome": "bad_month", "month": month_key or ""}
    try:
        payload = load_month(key)
    except FileNotFoundError:
        return {"outcome": "missing", "month": key}
    return {"outcome": "ok", "month": key, "payload": payload.to_dict()}


def _month_folder(staging: Path | None) -> Path:
    if staging is not None:
        return Path(staging) / "monthly"
    return month_file_path("x").parent


def _iter_payloads(staging: Path | None = None):
    folder = _month_folder(staging)
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("????-??.md"), reverse=True):
        try:
            yield loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, FileNotFoundError):
            continue


def _is_active_decision(row, superseded: set[str]) -> bool:
    if row.id in superseded:
        return False
    valid = str(row.valid_to or "").strip().casefold()
    return valid in {"", "open"}


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9]+", (text or "").casefold()) if tok}


def _lexical_sim(query: str, candidate: str) -> float:
    qt, ct = _tokens(query), _tokens(candidate)
    if not qt or not ct:
        return 0.0
    return len(qt & ct) / len(qt | ct)


def _guidance_sim(
    query: str,
    candidate: str,
    query_vec: Sequence[float] | None,
    cand_vec: Sequence[float] | None,
    *,
    mean_dir: Sequence[float] | None = None,
) -> float:
    """Prefer an exact lexical match, then R2 cosine, and never a partial word score.

    The cosine gate sits far below 0.92. Returning leftover Jaccard when a
    vector is missing would let word overlap clear that gate with no embedding.
    """
    lexical = _lexical_sim(query, candidate)
    if lexical >= _GUIDANCE_GATE:
        return lexical
    if not query_vec or not cand_vec:
        return 0.0
    try:
        from recall.embed import _cosine

        return float(_cosine(query_vec, cand_vec, mean_dir=mean_dir))
    except Exception:
        return 0.0


def rank_monthly_guidance(
    query: str,
    *,
    staging: Path | None = None,
    limit: int = 8,
    store=None,
    scores_out: list[dict[str, Any]] | None = None,
    query_vec: Sequence[float] | None = None,
    cache: dict[str, Any] | None = None,
    history: Sequence[tuple[float, str]] = (),
    now: float | None = None,
    mean_dir: Sequence[float] | None = None,
) -> list[dict[str, Any]]:
    """Rank active monthly D/P in R2 space so the model cone is not treated as a match.

    Decisions that merely pass the gate must not fill the block and hide a procedure
    for this message. Each kind keeps up to half the slots, and a kind with fewer
    than two passing rows is topped up from below the gate so it is not absent.
    A procedure also carries its solution vector and a decision its context vector.
    The card score is the best of the index, that view, and the usage centroid, so
    a fix can admit a card whose trigger does not resemble the line. Usage is the
    centroid of capped events, not the single closest one, and a hub penalty is
    subtracted once per card. An exact lexical match skips that penalty.
    A missing vector scores 0 so leftover word overlap cannot clear the cosine gate.
    A short line that misses is re-scored with earlier lines piled on. The daily
    fallback still sees only the latest line.
    """
    q = str(query or "").strip()
    if not q:
        return []
    pending: list[tuple[str, Any, Any, str, str]] = []
    for payload in _iter_payloads(staging):
        superseded = {sid for row in payload.key_decisions for sid in row.supersedes}
        for row in payload.key_decisions:
            if not _is_active_decision(row, superseded):
                continue
            pending.append(
                ("decision", payload, row, row.lookup_text(), row.lookup_text("context"))
            )
        for row in payload.key_procedures:
            pending.append(
                ("procedure", payload, row, row.lookup_text(), row.lookup_text("solution"))
            )
    try:
        from recall.embed_cache import load_cache, scale_block_id
    except Exception:
        load_cache = None
        scale_block_id = None
    if cache is None:
        resolved_cache: dict[str, Any] = {}
        if load_cache is not None:
            try:
                resolved_cache = load_cache()
            except Exception:
                resolved_cache = {}
    else:
        resolved_cache = cache
    if mean_dir is None:
        try:
            from recall.embed_cache import mean_direction

            resolved_mean = mean_direction(resolved_cache)
        except Exception:
            resolved_mean = None
    else:
        resolved_mean = list(mean_dir)
    vectors: list[list[float] | None] = []
    view_vectors: list[list[float] | None] = []
    need_query = False

    def _cached_vec(kind_name: str, text: str, row, payload) -> list[float] | None:
        nonlocal need_query
        if scale_block_id is None or not str(text or "").strip():
            return None
        if _lexical_sim(q, text) >= _GUIDANCE_GATE:
            return None
        bid = scale_block_id(
            {
                "scale": "month",
                "kind": kind_name,
                "text": text.strip(),
                "mem_id": str(getattr(row, "id", "") or "").strip(),
                "month_key": str(getattr(payload, "key", "") or "").strip(),
            }
        )
        raw = (resolved_cache.get(bid) or {}).get("embedding") or []
        if not raw:
            return None
        need_query = True
        return list(raw)

    for kind, payload, row, text, view_text in pending:
        vectors.append(_cached_vec("dp", text, row, payload))
        view_kind = "dp_context" if kind == "decision" else "dp_solution"
        view_vectors.append(_cached_vec(view_kind, view_text, row, payload))
    targets: dict[str, list[str]] = {}
    if store is not None:
        from recall.tools import _capped_linked_events

        for _kind, _payload, row, _text, _view in pending:
            targets[str(row.id)] = [
                str(item) for item in (getattr(row, "evidence", ()) or ()) if str(item).strip()
            ]
        capped = _capped_linked_events(targets, store, cap=_GUIDANCE_EVENT_CAP)
        for recs in capped.values():
            for rec in recs:
                raw = (resolved_cache.get(rec.block_id) or {}).get("embedding") or []
                if raw:
                    need_query = True
                    break
            if need_query:
                break
    supplied_query = query_vec is not None
    resolved_query: list[float] | None = list(query_vec) if supplied_query else None
    if not supplied_query and need_query:
        try:
            from recall.embed import _encode_texts

            encoded = _encode_texts([q])
            if encoded and encoded[0]:
                resolved_query = list(encoded[0])
        except Exception:
            resolved_query = None
    evt_scores: dict[str, float] = {}
    centroids: dict[str, list[float]] = {}
    if store is not None and resolved_query:
        from recall.tools import linked_event_scores

        evt_scores = linked_event_scores(
            resolved_query,
            targets,
            store,
            cap=_GUIDANCE_EVENT_CAP,
            cache=resolved_cache,
            mean_dir=resolved_mean,
            centroids_out=centroids,
        )
    from recall.tools import _unit_residual, event_bank, hub_bias

    lookups: dict[str, list[list[float]]] = {}
    for (_kind, _payload, row, _text, _view), cand_vec, view_vec in zip(
        pending, vectors, view_vectors
    ):
        units: list[list[float]] = []
        for cand in (cand_vec, view_vec):
            if not cand:
                continue
            unit = _unit_residual(cand, resolved_mean)
            if unit is not None:
                units.append(unit)
        if units:
            lookups[str(row.id)] = units
    if store is not None:
        bias_cards: dict[str, list[float] | None] = {
            key: lookups.get(key) for key in targets
        }
        exclude = {
            key: [rec.block_id for rec in recs]
            for key, recs in _capped_linked_events(
                targets, store, cap=_GUIDANCE_EVENT_CAP
            ).items()
        }
    else:
        bias_cards = dict(lookups)
        exclude = {}
    biases = hub_bias(
        bias_cards,
        centroids,
        event_bank(store, resolved_cache, resolved_mean),
        alpha=_GUIDANCE_HUB_ALPHA,
        exclude=exclude,
        beta=_GUIDANCE_EVENT_BETA,
    )
    from recall.strength import strength_value

    try:
        from recall.ids import BlockIndex

        evidence_index = store if store is not None else BlockIndex(_month_folder(staging).parent)
    except Exception:
        evidence_index = None

    def _cards_for(row) -> tuple[list[dict[str, Any]], int]:
        cards: list[dict[str, Any]] = []
        if evidence_index is not None:
            for evidence_id in getattr(row, "evidence", ()) or ():
                rec = evidence_index.get(str(evidence_id))
                if rec is not None:
                    cards.append(dict(rec.parsed))
        if cards:
            importance = math.floor(
                sum(int(card.get("importance") or 3) for card in cards) / len(cards) + 0.5
            )
        else:
            importance = 3
        return cards, importance

    passed: list[dict[str, Any]] = []
    below: dict[str, list[dict[str, Any]]] = {"decision": [], "procedure": []}
    all_sims: list[float] = []
    def _channels(
        qtext: str,
        qvec: list[float] | None,
        cand_vec: list[float] | None,
        text: str,
        view_vec: list[float] | None,
        view_text: str,
        row_id: str,
        use_scores: dict[str, float],
    ) -> tuple[float, float, float, float, float, float]:
        """Return sim, trigger, view, usage, bias, and the pre-penalty usage term.

        An exact lexical match on the index or the view skips the hub penalty.
        Subtracting it there would throw away a verbatim hit because the card is popular.
        The view is the solution for a procedure and the context for a decision.
        """
        lexical = _lexical_sim(qtext, text)
        lexical_view = _lexical_sim(qtext, view_text) if str(view_text or "").strip() else 0.0
        s_use = float(use_scores.get(row_id, 0.0) or 0.0)
        bias = float(biases.get(row_id, 0.0) or 0.0)
        lifted = _GUIDANCE_EVENT_BETA * s_use
        if lexical >= _GUIDANCE_GATE or lexical_view >= _GUIDANCE_GATE:
            return max(lexical, lexical_view), lexical, lexical_view, s_use, bias, lifted
        s_trig = _guidance_sim(qtext, text, qvec, cand_vec, mean_dir=resolved_mean)
        s_view = (
            _guidance_sim(qtext, view_text, qvec, view_vec, mean_dir=resolved_mean)
            if view_vec or str(view_text or "").strip()
            else 0.0
        )
        sim = max(s_trig, s_view, lifted) - bias
        return sim, s_trig, s_view, s_use, bias, lifted

    def _via(kind: str, s_trig: float, s_view: float, lifted: float, sim: float) -> str:
        """Name the channel that admitted the card so a rescue is visible in the hit."""
        if s_view > s_trig and s_view >= lifted and sim != s_trig:
            return "solution" if kind == "procedure" else "context"
        if lifted > s_trig and lifted > s_view and sim != max(s_trig, s_view):
            return "event"
        return "index"

    for (kind, payload, row, text, view_text), cand_vec, view_vec in zip(
        pending, vectors, view_vectors
    ):
        sim, s_trig, s_view, s_use, bias, lifted = _channels(
            q,
            resolved_query,
            cand_vec,
            text,
            view_vec,
            view_text,
            str(row.id),
            evt_scores,
        )
        all_sims.append(sim)
        if scores_out is not None:
            scores_out.append(
                {
                    "id": str(row.id),
                    "kind": kind,
                    "s_trig": s_trig,
                    "s_view": s_view,
                    "s_use": s_use,
                    "bias": bias,
                    "sim": sim,
                }
            )
        cards, importance = _cards_for(row)
        live = strength_value(
            recall_n=0.0,
            first_seen=None,
            importance=importance,
            evidence=cards,
        )
        year_s, _, mon_s = str(payload.key).partition("-")
        try:
            year_n, mon_n = int(year_s), int(mon_s)
        except ValueError:
            year_n, mon_n = 0, 0
        hit = {
            "kind": kind,
            "month": payload.key,
            "sim": sim,
            "s_trig": s_trig,
            "s_view": s_view,
            "s_use": s_use,
            "bias": bias,
            "via": _via(kind, s_trig, s_view, lifted, sim),
            "rank": 0.8 * sim + 0.2 * (live / 10.0),
            "overlap": _lexical_sim(q, text),
            "row": row,
            "live": live,
            "importance": importance,
            "topup": False,
            "spill": False,
            "_year": year_n,
            "_mon": mon_n,
        }
        if sim >= _GUIDANCE_COS_GATE:
            passed.append(hit)
        else:
            below[kind].append(hit)

    def _rescore(qtext: str, qvec: list[float] | None) -> list[tuple]:
        evt: dict[str, float] = {}
        if store is not None and qvec:
            from recall.tools import linked_event_scores

            evt = linked_event_scores(
                qvec,
                targets,
                store,
                cap=_GUIDANCE_EVENT_CAP,
                cache=resolved_cache,
                mean_dir=resolved_mean,
            )
        scored: list[tuple] = []
        for (kind, payload, row, text, view_text), cand_vec, view_vec in zip(
            pending, vectors, view_vectors
        ):
            sim, s_trig, s_view, s_use, bias, lifted = _channels(
                qtext, qvec, cand_vec, text, view_vec, view_text, str(row.id), evt
            )
            scored.append(
                (sim, s_trig, s_view, s_use, bias, lifted, kind, payload, row, text)
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def _as_hit(parts: tuple, *, turns: int, piled: str) -> dict[str, Any]:
        sim, s_trig, s_view, s_use, bias, lifted, kind, payload, row, text = parts
        cards, importance = _cards_for(row)
        live = strength_value(
            recall_n=0.0,
            first_seen=None,
            importance=importance,
            evidence=cards,
        )
        return {
            "kind": kind,
            "month": payload.key,
            "sim": sim,
            "s_trig": s_trig,
            "s_view": s_view,
            "s_use": s_use,
            "bias": bias,
            "via": _via(kind, s_trig, s_view, lifted, sim),
            "rank": 0.8 * sim + 0.2 * (live / 10.0),
            "overlap": _lexical_sim(piled, text),
            "row": row,
            "live": live,
            "importance": importance,
            "topup": False,
            "turns": turns,
        }

    def _entropy(sims: list[float]) -> float:
        if not sims:
            return 0.0
        peak = max(sims)
        weights = [math.exp((score - peak) / _PILE_TAU) for score in sims]
        total = sum(weights) or 1.0
        entropy = 0.0
        for weight in weights:
            prob = weight / total
            if prob > 0.0:
                entropy -= prob * math.log(prob, 2)
        return entropy

    if not passed and history and len(q) <= _PILE_SHORT_CHARS:
        pile = [q]
        last_ts = float(now if now is not None else time.time())
        h_prev = _entropy(all_sims)
        try:
            from recall.embed import _encode_texts
        except Exception:
            _encode_texts = None
        for ts, line in history:
            if len(pile) >= _PILE_MAX_TURNS or last_ts - float(ts) > _PILE_GAP_S:
                break
            pile.append(str(line))
            last_ts = float(ts)
            if _encode_texts is None:
                break
            try:
                encoded = _encode_texts(["\n".join(pile)])
            except Exception:
                break
            if not encoded or not encoded[0]:
                break
            scored = _rescore("\n".join(pile), list(encoded[0]))
            if not scored:
                break
            best = scored[0][0]
            second = scored[1][0] if len(scored) > 1 else 0.0
            if best >= _PILE_FLOOR and best - second >= _PILE_MARGIN:
                return [_as_hit(scored[0], turns=len(pile), piled="\n".join(pile))]
            h_now = _entropy([item[0] for item in scored])
            if h_prev - h_now <= _PILE_EPS_BITS:
                break
            h_prev = h_now
    if not passed:
        return []

    def _tie(hit: dict[str, Any]) -> tuple:
        bucket = -math.floor((hit["rank"] / _GUIDANCE_TIE_EPS) + 1e-9)
        return (
            bucket,
            -hit["s_trig"],
            -hit["overlap"],
            -len(getattr(hit["row"], "evidence", ()) or ()),
            -hit["_year"],
            -hit["_mon"],
            hit["row"].id,
        )

    prefs = sorted((h for h in passed if h["kind"] == "decision"), key=_tie)
    procs = sorted((h for h in passed if h["kind"] == "procedure"), key=_tie)
    for kind in below:
        below[kind].sort(key=_tie)
    limit_n = max(1, int(limit))
    share = max(_GUIDANCE_KIND_FLOOR, min(_GUIDANCE_KIND_SHARE, limit_n // 2))
    chosen = {
        "decision": prefs[: min(share, len(prefs))],
        "procedure": procs[: min(share, len(procs))],
    }
    rest = prefs[len(chosen["decision"]) :] + procs[len(chosen["procedure"]) :]
    rest.sort(key=_tie)
    room = limit_n - (len(chosen["decision"]) + len(chosen["procedure"]))
    for hit in rest[: max(room, 0)]:
        hit["spill"] = True
        chosen[hit["kind"]].append(hit)

    def _top_up(kind: str, other: str) -> None:
        need = _GUIDANCE_KIND_FLOOR - len(chosen[kind])
        if need <= 0:
            return
        extras = below[kind][:need]
        spills = [h for h in chosen[other] if h.get("spill")]
        for hit in extras:
            hit["topup"] = True
            if spills:
                dropped = spills.pop()
                chosen[other].remove(dropped)
            elif (
                len(chosen["decision"]) + len(chosen["procedure"]) >= limit_n
                and len(chosen[other]) > _GUIDANCE_KIND_FLOOR
            ):
                chosen[other].pop()
            chosen[kind].append(hit)

    _top_up("procedure", "decision")
    _top_up("decision", "procedure")
    chosen["decision"].sort(key=_tie)
    chosen["procedure"].sort(key=_tie)
    ordered = chosen["decision"] + chosen["procedure"]
    for hit in ordered:
        hit.pop("spill", None)
        hit.pop("_year", None)
        hit.pop("_mon", None)
    return ordered[:limit_n]


def format_guidance_hits(hits: list[dict[str, Any]]) -> str:
    """Name the view that admitted the card so a solution or context hit is not read as the trigger.

    turns=N is on the header only when a short follow-up needed earlier lines.
    A single-line hit must keep the header the prefetch tests already match.
    Live strength is printed so the agent sees decay, not the value saved on the last stamp.
    """
    if not hits:
        return ""
    turns = max((int(hit.get("turns") or 1) for hit in hits), default=1)
    header = "## Memory / recall  channel=monthly_guidance"
    if turns >= 2:
        header += f"  turns={turns}"
    lines = [header]
    for hit in hits:
        row = hit["row"]
        live = float(hit.get("live") or 0.0)
        importance = int(hit.get("importance") or 3)
        via = str(hit.get("via") or "index")
        if hit["kind"] == "decision":
            extra = f" except {row.exceptions}" if row.exceptions else ""
            lines.append(
                f"- preference {row.id}  via={via}  context={row.context or '-'}  "
                f"{row.text}{extra}  strength={live:.2f} importance={importance}"
            )
        else:
            obs = "; ".join(row.obstacles) if row.obstacles else ""
            lines.append(
                f"- procedure {row.id}  via={via}  trigger={row.trigger or '-'}  "
                f"obstacles={obs or '-'}  solution={row.solution}  strength={live:.2f} importance={importance}"
            )
    return "\n".join(lines)


def _extra_evidence(row) -> str:
    """Append same-rule mates on the row line so Band D does not open a second indented block."""
    mates = [item for item in row.evidence if item and item != row.id]
    if not mates:
        return ""
    return " evidence: " + ", ".join(mates)


def _paint_payload(payload) -> list[str]:
    """One short line per key row so prefetch cannot glue a month of obstacles onto one mem-id."""
    start = str(payload.range.start or "").strip()[:10]
    end = str(payload.range.end or "").strip()[:10]
    lines = [f"### {payload.key}  {start}..{end}".rstrip()]
    for row in payload.summary[:_BAND_SUMMARY]:
        weeks = ", ".join(row.weeks) if row.weeks else ""
        suffix = f" ({weeks})" if weeks else ""
        lines.append(f"- {row.text}{suffix}")
    superseded = {sid for row in payload.key_decisions for sid in row.supersedes}
    decisions = [row for row in payload.key_decisions if _is_active_decision(row, superseded)]
    for row in decisions[:_BAND_DECISIONS]:
        lines.append(f"- preference {row.id} — {row.text}{_extra_evidence(row)}")
    for row in payload.key_procedures[:_BAND_PROCEDURES]:
        lines.append(f"- procedure {row.id} — {row.problem} — {row.solution}{_extra_evidence(row)}")
    return lines


def month_band(limit: int = 8, staging: Path | None = None) -> str:
    """Paint month stories like weekly Band C, then bounded active D/P for first-turn guidance."""
    lines: list[str] = []
    for payload in list(_iter_payloads(staging))[:limit]:
        painted = _paint_payload(payload)
        if len(painted) == 1 and not payload.summary and not payload.key_decisions and not payload.key_procedures:
            continue
        lines.extend(painted)
    if not lines:
        return ""
    return "## Month summaries\n" + "\n".join(lines)


def _slash_usage_line(result: dict, offset: int, started: float) -> str:
    """Attach the same token line digest and weekly print after a model run."""
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    if usage is None:
        usage = sum_usage_after(offset)
    seconds = result.get("seconds")
    if seconds is None:
        seconds = time.monotonic() - started
    return format_token_line(usage, float(seconds))


def handle_monthly(raw_args: str) -> str:
    """Slash/CLI dispatcher for update, show, and suggest — monthly has no UI lifecycle.

    Suggest prints a list and a conversation guide. Later edits happen in chat, so this
    dispatcher does not grow apply, drop, or confirm commands. Update always reruns the
    whole month and names any section that is still pending, so a second run does not
    look like it discarded the sections that passed.
    """
    tokens = str(raw_args or "").strip().split()
    if not tokens or tokens[0] in {"help", "-h", "--help"}:
        return _HELP
    cmd = tokens[0]
    rest = tokens[1] if len(tokens) > 1 else ""
    if cmd == "suggest":
        from monthly_suggest import suggest_from_tokens

        offset = usage_ledger_offset()
        started = time.monotonic()
        text = suggest_from_tokens(tokens[1:])
        return text + "\n" + _slash_usage_line({}, offset, started)
    if cmd == "update":
        key = parse_month_key(rest) if rest else date.today().strftime("%Y-%m")
        if rest and key is None:
            return f"bad month {rest!r}; expected YYYY-MM"
        offset = usage_ledger_offset()
        started = time.monotonic()
        result = generate_month(key, reason="slash", force_refresh=True)
        if result.get("outcome") == "bad_month":
            return f"bad month {rest!r}; expected YYYY-MM"
        pending = [str(item) for item in (result.get("pending_sections") or [])]
        extra = f"; pending: {', '.join(pending)}" if pending else ""
        return (
            f"updated {result.get('month')} ({result.get('outcome')}{extra})\n"
            + _slash_usage_line(result, offset, started)
        )
    if cmd == "show":
        key = parse_month_key(rest) if rest else date.today().strftime("%Y-%m")
        if rest and key is None:
            return f"bad month {rest!r}; expected YYYY-MM"
        try:
            payload = load_month(key)
        except FileNotFoundError:
            return f"missing {key}"
        pending = ""
        if payload.pending_sections:
            pending = "pending: " + ", ".join(payload.pending_sections) + "\n"
        return pending + "## Month summaries\n" + "\n".join(_paint_payload(payload))
    return _HELP


def lookup_by_id(mem_id: str) -> dict[str, Any] | None:
    """Find a stored decision, procedure, or progress item by id for expand_memory."""
    folder = month_file_path("x").parent
    if not folder.is_dir():
        return None
    needle = str(mem_id or "")
    for path in sorted(folder.glob("????-??.md")):
        try:
            payload = load_month(path.stem)
        except (OSError, ValueError, FileNotFoundError):
            continue
        for row in payload.key_decisions:
            if row.id == needle:
                return {"month": payload.key, "kind": "decision", "item": row}
        for row in payload.key_procedures:
            if row.id == needle:
                return {"month": payload.key, "kind": "procedure", "item": row}
        for row in payload.core_progress:
            if row.id == needle or needle in row.evidence:
                return {"month": payload.key, "kind": "progress", "item": row}
    return None


def lookup_by_entity(entity_key: str) -> list[dict[str, Any]]:
    """Return month rows for a canonical key so Channel 2 can hop across months."""
    folder = month_file_path("x").parent
    if not folder.is_dir():
        return []
    key = str(entity_key or "")
    hits: list[dict[str, Any]] = []
    for path in sorted(folder.glob("????-??.md")):
        try:
            payload = load_month(path.stem)
        except (OSError, ValueError, FileNotFoundError):
            continue
        for ent in payload.entities:
            if ent.key == key:
                hits.append({"month": payload.key, "entity": ent, "payload": payload})
                break
    return hits


def expand_from_block(mem_id: str) -> dict[str, Any]:
    """Depth-2 walk: daily id → core_progress → sibling week procedure ids."""
    hit = lookup_by_id(mem_id)
    if hit is None:
        folder = month_file_path("x").parent
        if folder.is_dir():
            for path in sorted(folder.glob("????-??.md")):
                try:
                    payload = load_month(path.stem)
                except (OSError, ValueError, FileNotFoundError):
                    continue
                for row in payload.core_progress:
                    if mem_id in row.evidence:
                        hit = {"month": payload.key, "kind": "progress", "item": row, "payload": payload}
                        break
                if hit:
                    break
    if hit is None:
        return {"ok": False, "error": "not_found"}
    item = hit["item"]
    sibling_ids: list[str] = []
    if getattr(item, "evidence", None):
        sibling_ids.extend(list(item.evidence))
    return {"ok": True, "progress": hit, "sibling_ids": sibling_ids}
