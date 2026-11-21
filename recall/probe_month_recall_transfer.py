"""Move monthly recall credit onto evidence cards once. Dry run unless --apply.

Prints counts only. The marker stops a second --apply from adding the same
credit again. A missing evidence card is skipped so one bad id cannot abort.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import date
from pathlib import Path


def _install(path: str) -> None:
    root = Path(path).resolve() if path else Path.cwd()
    for extra in (root / "monthly", root):
        text = str(extra)
        if text not in sys.path:
            sys.path.insert(0, text)


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


def _importance(cards: list[dict]) -> int:
    if not cards:
        return 3
    return math.floor(sum(int(card.get("importance") or 3) for card in cards) / len(cards) + 0.5)


def _later(left: str, right: str) -> str:
    a = str(left or "")[:10]
    b = str(right or "")[:10]
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--plugin", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--mode", default="")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    _install(args.plugin)
    home = Path(os.environ.get("HERMES_HOME") or "").resolve()
    if not home.parts:
        raise SystemExit(2)
    staging = home / "memories" / "staging"
    marker = staging / "monthly" / ".recall-transfer-done"
    if args.apply and marker.exists():
        _emit({"ok": True, "apply": True, "wrote": False, "marker": True})
        return

    from monthly_actions import _is_active_decision, _iter_payloads
    from monthly_schema import MonthlyDecision, MonthlyProcedure
    from monthly_writer import dump_yaml, loads
    from recall.ids import BlockIndex, _FRONTMATTER_RE
    from recall.strength import _upsert_optional_frontmatter, strength_value
    from dataclasses import replace
    import yaml

    store = BlockIndex(staging)
    today = date.today()
    credits: dict[str, list[tuple[float, str]]] = {}
    credited_rows = 0
    credit_sum = 0.0
    missing = 0
    payloads = []
    for path in sorted((staging / "monthly").glob("????-??.md")):
        try:
            payloads.append((path, loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    for path, payload in payloads:
        superseded = {sid for row in payload.key_decisions for sid in row.supersedes}
        rows = [
            row for row in payload.key_decisions if _is_active_decision(row, superseded)
        ] + list(payload.key_procedures)
        for row in rows:
            added = float(getattr(row, "recall_n", 0.0) or 0.0)
            if added <= 0:
                continue
            credited_rows += 1
            credit_sum += added
            for evidence_id in getattr(row, "evidence", ()) or ():
                evidence_id = str(evidence_id).strip()
                if not evidence_id:
                    continue
                if store.get(evidence_id) is None:
                    missing += 1
                    continue
                credits.setdefault(evidence_id, []).append(
                    (added, str(getattr(row, "last_recall_at", "") or ""))
                )
    updated: dict[str, dict] = {}
    for evidence_id, parts in credits.items():
        rec = store.get(evidence_id)
        parsed = dict(rec.parsed)
        parsed["recall_n"] = float(parsed.get("recall_n") or 0.0) + sum(part[0] for part in parts)
        last = str(parsed.get("last_recall_at") or "")
        for _added, row_last in parts:
            last = _later(last, row_last)
        if last:
            parsed["last_recall_at"] = last
        if not parsed.get("first_seen"):
            parsed["first_seen"] = str(parsed.get("valid_from") or "")[:10]
        parsed["strength"] = round(
            strength_value(
                recall_n=float(parsed["recall_n"]),
                first_seen=parsed.get("first_seen"),
                last_recall_at=parsed.get("last_recall_at"),
                importance=int(parsed.get("importance") or 3),
                now=today,
            ),
            4,
        )
        updated[evidence_id] = parsed
        rec.parsed.update(parsed)

    def cards_for(row) -> list[dict]:
        found = []
        for evidence_id in getattr(row, "evidence", ()) or ():
            evidence_id = str(evidence_id).strip()
            if evidence_id in updated:
                found.append(updated[evidence_id])
                continue
            rec = store.get(evidence_id)
            if rec is not None:
                found.append(dict(rec.parsed))
        return found

    def fill(row):
        found = cards_for(row)
        importance = _importance(found)
        strength = strength_value(
            recall_n=0.0,
            first_seen=None,
            importance=importance,
            now=today,
            evidence=found,
        )
        return replace(row, importance=importance, strength=strength)

    if not args.apply:
        _emit(
            {
                "ok": True,
                "apply": False,
                "wrote": False,
                "credited_rows": credited_rows,
                "credit_sum": credit_sum,
                "cards_touched": len(updated),
                "missing_evidence": missing,
            }
        )
        return

    by_file: dict[Path, list[tuple[str, dict]]] = {}
    for evidence_id, parsed in updated.items():
        rec = store.get(evidence_id)
        by_file.setdefault(rec.path, []).append((evidence_id, parsed))
    for path, items in by_file.items():
        text = path.read_text(encoding="utf-8")
        pieces: list[str] = []
        last = 0
        pending = {item_id: parsed for item_id, parsed in items}
        for match in _FRONTMATTER_RE.finditer(text):
            pieces.append(text[last : match.start()])
            try:
                fm_obj = yaml.safe_load(match.group(1))
            except yaml.YAMLError:
                fm_obj = None
            block_id = str(fm_obj.get("id") or "").strip() if isinstance(fm_obj, dict) else ""
            if block_id in pending:
                new_fm = _upsert_optional_frontmatter(match.group(1), pending[block_id])
                pieces.append(f"---\n{new_fm}\n---\n{match.group(2)}")
            else:
                pieces.append(match.group(0))
            last = match.end()
        pieces.append(text[last:])
        path.write_text("".join(pieces), encoding="utf-8")
    for path, payload in payloads:
        payload = replace(
            payload,
            key_decisions=tuple(
                fill(row) if isinstance(row, MonthlyDecision) else row
                for row in payload.key_decisions
            ),
            key_procedures=tuple(
                fill(row) if isinstance(row, MonthlyProcedure) else row
                for row in payload.key_procedures
            ),
        )
        path.write_text(dump_yaml(payload), encoding="utf-8")
    fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, b"ok\n")
    os.close(fd)
    _emit(
        {
            "ok": True,
            "apply": True,
            "wrote": True,
            "credited_rows": credited_rows,
            "credit_sum": credit_sum,
            "cards_touched": len(updated),
            "missing_evidence": missing,
        }
    )


if __name__ == "__main__":
    main()
