"""Backfill ## Day wrap-up via MiMo for sparse weeks, then refresh weekly summary.

Uses chronicle cache + legacy distill when daily staging blocks were purged.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

MYMEMORY = Path(__file__).resolve().parents[2]
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or MYMEMORY.parent.parent).resolve()
AGENT_ROOT = HERMES_HOME / "hermes-agent"
WEEKLY_DIR = HERMES_HOME / "memories" / "staging" / "weekly"
DAILY_DIR = HERMES_HOME / "memories" / "staging" / "daily"
METRICS_OUT = HERMES_HOME / "metrics" / "weekly-wrapup-backfill-w26-w29.json"

for path_str in (str(AGENT_ROOT), str(MYMEMORY), str(MYMEMORY / "weekly"), str(MYMEMORY / "digest")):
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from hermes_cli.env_loader import load_hermes_dotenv  # noqa: E402

load_hermes_dotenv(hermes_home=str(HERMES_HOME))
os.environ["HERMES_HOME"] = str(HERMES_HOME)

import weekly_tools  # noqa: E402
import dedup_prompt  # noqa: E402
import digest_tools  # noqa: E402
from digest import format_wrapup_body, join_daily_wrapup, split_daily_wrapup  # noqa: E402
from memory_staging import read_week_status, write_week_status  # noqa: E402
from weekly_event_workers import _run_summary_worker  # noqa: E402
from weekly_json import dump_yaml, load_sidecar, loads, write_sidecars  # noqa: E402
from worker_llm import run_worker_llm_oneshot  # noqa: E402

weekly_tools.ensure_weekly_tools_registered()

TARGET_WEEKS = ("2026-W26", "2026-W27", "2026-W29")
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_RANGE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})→(20\d{2}-\d{2}-\d{2})\b")


def _week_dates(week_key: str) -> list[date]:
    year_s, _, week_s = week_key.partition("-W")
    start = date.fromisocalendar(int(year_s), int(week_s), 1)
    return [start + timedelta(days=i) for i in range(7)]


def _load_chronicle() -> dict[str, Any]:
    path = HERMES_HOME / "memories" / "staging" / ".weekly-chronicle.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_w29_distill() -> str:
    git_root = HERMES_HOME.parent
    try:
        out = subprocess.check_output(
            ["git", "-C", str(git_root), "show", "de05edc:hermes-home/memories/staging/weekly/2026-W29.md"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return out


def _assign_chronicle_lines(text: str) -> dict[date, list[str]]:
    by_day: dict[date, list[str]] = defaultdict(list)
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("-"):
            continue
        for start_s, end_s in _RANGE_RE.findall(line):
            start_d = date.fromisoformat(start_s)
            end_d = date.fromisoformat(end_s)
            cur = start_d
            while cur <= end_d:
                by_day[cur].append(line.lstrip("- ").strip())
                cur += timedelta(days=1)
        explicit = _DATE_RE.findall(line)
        if explicit:
            for token in explicit:
                by_day[date.fromisoformat(token)].append(line.lstrip("- ").strip())
        elif not _RANGE_RE.search(line):
            by_day.setdefault(None, []).append(line.lstrip("- ").strip())  # type: ignore[arg-type]
    return by_day


def _parse_distill_blocks(text: str) -> list[dict[str, Any]]:
    import yaml

    blocks: list[dict[str, Any]] = []
    if not text.strip():
        return blocks
    chunks = [c.strip() for c in re.split(r"\n---\n", text) if c.strip()]
    idx = 0
    while idx < len(chunks):
        chunk = chunks[idx]
        if not chunk.startswith("id:"):
            idx += 1
            continue
        body = chunks[idx + 1].strip() if idx + 1 < len(chunks) and not chunks[idx + 1].startswith("id:") else ""
        try:
            fm = yaml.safe_load(chunk)
        except yaml.YAMLError:
            idx += 1
            continue
        if not isinstance(fm, dict):
            idx += 1
            continue
        vf = str(fm.get("valid_from") or "")[:10]
        if vf and body:
            blocks.append({"valid_from": vf, "body": body, "id": fm.get("id")})
        idx += 2 if body else 1
    return blocks


def _sources_by_day() -> dict[date, list[str]]:
    by_day: dict[date, list[str]] = defaultdict(list)
    chronicle = _load_chronicle()
    w26 = str((chronicle.get("2026-W26") or {}).get("summary") or "")
    for day, lines in _assign_chronicle_lines(w26).items():
        if day is None:
            continue
        by_day[day].extend(lines)
    w27_note = str((chronicle.get("2026-W27") or {}).get("summary") or "").strip()
    if w27_note and "no current news" not in w27_note.casefold():
        for day in _week_dates("2026-W27"):
            by_day[day].append(w27_note)
    for block in _parse_distill_blocks(_load_w29_distill()):
        day = date.fromisoformat(str(block["valid_from"])[:10])
        body = str(block.get("body") or "").strip()
        if body:
            by_day[day].append(body)
    # W29 distill references W27 tuition delivery anchored on 2026-06-29.
    by_day[date(2026, 6, 29)].append(
        "W27 weekly brief cited E6-3 term-end delivery and tuition/grading package on 2026-06-29."
    )
    return by_day


def _llm_wrapup(day: date, context: list[str]) -> str:
    if not context:
        return ""
    prompt = (
        dedup_prompt.WRAPUP_RULES
        + f"\n\n## Source material for {day.isoformat()}\n"
        + "\n".join(f"- {line}" for line in context[:12])
    )
    capture = run_worker_llm_oneshot(
        prompt,
        plugin="memory-digest",
        purpose="digest-wrapup-backfill",
        force_tool_name="submit_day_wrapup",
        tool_schema=digest_tools.submit_day_wrapup_schema(),
        max_tokens=1024,
    )
    if capture.get("failed"):
        raise RuntimeError(str(capture.get("error") or capture.get("final_response") or "wrapup failed"))
    _name, args = digest_tools.parse_tool_args_from_result(capture)
    raw: str | list[Any] = ""
    if isinstance(args, dict):
        listed = args.get("phrases")
        if isinstance(listed, list) and listed:
            raw = listed
        else:
            raw = str(args.get("phrase") or "")
    phrase = format_wrapup_body(raw)
    if not phrase:
        raise RuntimeError("blank wrap-up phrase")
    return phrase


def _write_daily_wrapup(day: date, phrase: str) -> None:
    path = DAILY_DIR / f"{day.isoformat()}.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    fences, _old = split_daily_wrapup(existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(join_daily_wrapup(fences, phrase), encoding="utf-8")


def _summary_llm(prompt: str, *, purpose: str, force_tool_name: str) -> dict[str, Any]:
    schema = (
        weekly_tools.submit_weekly_summary_schema()
        if force_tool_name == "submit_weekly_summary"
        else weekly_tools.patch_weekly_summary_schema()
    )
    return run_worker_llm_oneshot(
        prompt,
        plugin="memory-weekly",
        purpose=purpose,
        force_tool_name=force_tool_name,
        tool_schema=schema,
        max_tokens=4096,
    )


def _finish_week(week_key: str) -> dict[str, Any]:
    from weekly_event_workers import _intra_day_from_dailies  # noqa: E402
    from weekly_event_schema import WeeklyReviewPayload  # noqa: E402

    path = WEEKLY_DIR / f"{week_key}.md"
    status = read_week_status(path) if path.is_file() else "reviewed"
    status = status or "reviewed"
    prior = load_sidecar(path) if path.is_file() else {}
    generated_at = prior.get("generated_at") if isinstance(prior, dict) else None

    week_dates = _week_dates(week_key)
    by_day_paths = {
        day: DAILY_DIR / f"{day.isoformat()}.md"
        for day in week_dates
        if (DAILY_DIR / f"{day.isoformat()}.md").is_file()
    }
    intra = _intra_day_from_dailies(week_dates, by_day_paths)
    prior_payload = (
        loads(path.read_text(encoding="utf-8"))
        if path.is_file()
        else WeeklyReviewPayload(days=(), week_key=week_key)
    )
    payload = replace(
        prior_payload,
        week_key=week_key,
        intra_day_thread=intra,
        cross_day_thread=prior_payload.cross_day_thread,
    )
    intra_n = sum(1 for row in intra if not row.empty)
    cross_n = len(payload.cross_day_thread)
    summary_items = payload.summary
    if (intra_n or cross_n) and not summary_items:
        summary_items = _run_summary_worker(
            week_key=week_key,
            intra=intra,
            cross=payload.cross_day_thread,
            call_llm_tools=_summary_llm,
            log=lambda msg: print(msg, flush=True),
        )
        payload = replace(payload, summary=summary_items)
    yaml_text = dump_yaml(payload, generated_at=generated_at)
    write_week_status(path, status, week_key_str=week_key, content=yaml_text)
    write_sidecars(path, payload)
    return {
        "week": week_key,
        "intra_nonempty": intra_n,
        "cross_threads": cross_n,
        "summary_rows": len(summary_items),
        "status": status,
    }


def main() -> int:
    sources = _sources_by_day()
    wrapup_results: list[dict[str, Any]] = []
    days_to_write: set[date] = set()
    for week_key in TARGET_WEEKS:
        days_to_write.update(_week_dates(week_key))

    for day in sorted(days_to_write):
        context = sources.get(day) or []
        if not context:
            wrapup_results.append({"day": day.isoformat(), "outcome": "skipped", "reason": "no_source"})
            continue
        t0 = time.perf_counter()
        try:
            phrase = _llm_wrapup(day, context)
            _write_daily_wrapup(day, phrase)
            wrapup_results.append(
                {
                    "day": day.isoformat(),
                    "outcome": "written",
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "preview": phrase.splitlines()[0][:100],
                }
            )
            print(f"wrapup ok {day.isoformat()}", flush=True)
        except Exception as exc:  # noqa: BLE001
            wrapup_results.append(
                {
                    "day": day.isoformat(),
                    "outcome": "failed",
                    "error": str(exc),
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                }
            )
            print(f"wrapup fail {day.isoformat()}: {exc}", flush=True)

    week_results: list[dict[str, Any]] = []
    for week_key in TARGET_WEEKS:
        t0 = time.perf_counter()
        try:
            row = _finish_week(week_key)
            row["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            week_results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001
            week_results.append({"week": week_key, "outcome": "failed", "error": str(exc)})

    METRICS_OUT.parent.mkdir(parents=True, exist_ok=True)
    METRICS_OUT.write_text(
        json.dumps({"wrapups": wrapup_results, "weeks": week_results}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {METRICS_OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
