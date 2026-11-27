#!/usr/bin/env python3
"""Fill procedure_keywords on daily cards from the cited session window only.

A card's Obstacle/Solution line is already a rewrite. Keywords have to be
words from the session slice in sources:, or recall ranks on language the
user never used. Cards with no #start-end are left unchanged.

Run on the Tencent host (state.db and the dailies are there):

  sudo -n /usr/local/lib/hermes-agent/venv/bin/python \\
    scripts/backfill_procedure_keywords.py --hermes-home /root/.hermes

Add --write to patch the markdown. Without it, the script only prints the
keywords it would store.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

MODEL = "deepseek/deepseek-v4.1-flash-1017-zdr"
BASE_URL = "https://api.inferway.ai/v1"
TOOL_NAME = "set_procedure_keywords"
_TRANSCRIPT_CAP = 12000
_TAG_RE = re.compile(r"session\s+(\S+)")
_ID_RE = re.compile(r"(?m)^id:\s*(\S+)\s*$")
_TYPE_RE = re.compile(r"(?m)^type:\s*procedure\s*$")

SYSTEM_PROMPT = (
    "Attach procedure_keywords for one existing procedure card.\n"
    "You are shown only the cited session messages. Do not use any other source.\n"
    "Call set_procedure_keywords with at least 3 items.\n"
    "canonical is one stable English task word.\n"
    "originals are the exact surfaces in the user lines for that word. "
    "Each original is at most 4 Chinese characters and 2 English words. "
    "Use an empty list when the user already wrote that English word.\n"
    "Do not copy the English canonical into originals.\n"
    "Canonicals must be distinct from each other.\n"
    "If the transcript has no user lines, copy originals from the lines that are shown."
)


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines so the Inferway call sees the host key without printing it.

    A missing file is left to the environment. Values already set are kept.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _load_digest_tools(plugin_root: Path) -> Any:
    """Load digest_tools by file so package init does not pull the digest runtime.

    digest/__init__.py imports the digest worker. This backfill only needs the
    keyword schema, the YAML writer, and the session-tag parsers.
    """
    root = str(plugin_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    path = plugin_root / "digest" / "digest_tools.py"
    spec = importlib.util.spec_from_file_location("backfill_digest_tools", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _blocks(text: str) -> list[tuple[str, str, str]]:
    """Split a daily file into preamble, frontmatter, body without rewriting scalars.

    The digest splitter quotes some colons as it reads. This walk only finds
    fences so a keyword insert cannot reformat the rest of the card.
    """
    lines = text.splitlines(keepends=True)
    parts: list[tuple[str, str, str]] = []
    idx = 0
    while idx < len(lines):
        if lines[idx].strip() != "---":
            idx += 1
            continue
        start = idx
        idx += 1
        fm_start = idx
        while idx < len(lines) and lines[idx].strip() != "---":
            idx += 1
        if idx >= len(lines):
            break
        fm_end = idx
        idx += 1
        body_start = idx
        while idx < len(lines) and lines[idx].strip() != "---":
            idx += 1
        preamble = "".join(lines[start:fm_start])
        frontmatter = "".join(lines[fm_start:fm_end])
        body = "".join(lines[body_start:idx])
        parts.append((preamble, frontmatter, body))
    return parts


def _card_id(frontmatter: str) -> str:
    match = _ID_RE.search(frontmatter)
    return match.group(1).strip() if match else ""


def _has_keywords(frontmatter: str) -> bool:
    return bool(re.search(r"(?m)^procedure_keywords:\s*$", frontmatter))


def _stored_keyword_errors(frontmatter: str, tools: Any) -> list[str]:
    """Return validator errors for a card that already has a keyword block.

    An older write can hold a phrase-length original. Those cards are rewritten.
    A block that already passes is left in place.
    """
    import yaml

    try:
        parsed = yaml.safe_load(frontmatter) or {}
    except Exception as exc:
        return [f"unreadable procedure_keywords: {exc}"]
    if not isinstance(parsed, dict):
        return ["unreadable procedure_keywords"]
    return list(tools._procedure_keyword_errors(parsed))


def _windows(frontmatter: str, tools: Any) -> list[tuple[str, int, int]]:
    """Return (session_id, start, end) for tags that name a message range.

    A bare session id has no part to read. Skipping it keeps the model off
    the rest of that chat.
    """
    found: list[tuple[str, int, int]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in _TAG_RE.finditer(frontmatter):
        token = match.group(1).strip().strip("\"'").rstrip(",])")
        tag = f"session {token}"
        span = tools.message_range_from_source_tag(tag)
        if span is None:
            continue
        sid = tools.session_id_from_source_tag(tag)
        if not sid:
            continue
        key = (sid, span[0], span[1])
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def _fetch_window(
    conn: sqlite3.Connection, session_id: str, start: int, end: int
) -> list[tuple[int, str, str]]:
    """Read user and assistant rows inside one cited id range, including compacted ones.

    Hermes sets active=0 when it compacts a turn, but the text is still the
    session slice the card cites. Requiring active=1 would skip that slice
    and leave the card without keywords. Compaction summary blobs are a
    recap, not the words this procedure was taken from, so those are dropped.
    """
    rows = conn.execute(
        """
        SELECT id, role, content
        FROM messages
        WHERE session_id = ? AND id >= ? AND id <= ?
        ORDER BY id ASC
        """,
        (session_id, start, end),
    ).fetchall()
    out: list[tuple[int, str, str]] = []
    for row in rows:
        role = str(row[1] or "")
        content = str(row[2] or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if role == "user" and content.startswith("[CONTEXT COMPACTION"):
            continue
        out.append((int(row[0]), role, content))
    return out


def _transcript(rows: list[tuple[int, str, str]]) -> str:
    """Prefer user lines so originals are copied from the person, not the rewrite.

    Assistant lines are used only when the window has no user text. The cap
    keeps a long cron slice inside one completion.
    """
    users = [row for row in rows if row[1] == "user"]
    chosen = users or list(rows)
    chunks: list[str] = []
    used = 0
    for mid, role, content in chosen:
        piece = f"[{role} #{mid}] {content}\n"
        if used + len(piece) > _TRANSCRIPT_CAP:
            break
        chunks.append(piece)
        used += len(piece)
    return "".join(chunks).strip()


def _tool_schema(tools: Any) -> dict[str, Any]:
    prop = tools._procedure_keywords_prop()
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Store procedure keywords taken from the session lines.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["procedure_keywords"],
                "properties": {"procedure_keywords": prop},
            },
        },
    }


def _post_json(url: str, payload: Mapping[str, Any], api_key: str) -> dict[str, Any]:
    """POST one chat completion. Retry transport failures; leave schema misses to the caller.

    A 429 or a dropped connection should not drop the card. A 400 is the
    request itself and is raised.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    delay = 2.0
    last_error = ""
    for _attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code in {429, 500, 502, 503, 504}:
                last_error = f"HTTP {exc.code}: {detail}"
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(last_error or "inferway request failed")


def _arguments(payload: Mapping[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("inferway reply had no choices")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping):
        raise RuntimeError("inferway reply had no message")
    calls = message.get("tool_calls") or []
    raw = ""
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            fn = call.get("function")
            if isinstance(fn, Mapping) and str(fn.get("name") or "") == TOOL_NAME:
                raw = fn.get("arguments") or ""
                break
    if not raw:
        raw = str(message.get("content") or "")
    if isinstance(raw, Mapping):
        parsed = dict(raw)
    else:
        parsed = json.loads(str(raw))
    if not isinstance(parsed, dict):
        raise RuntimeError("procedure_keywords payload was not an object")
    return parsed


def _ask(
    tools: Any,
    transcript: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> list[dict[str, Any]]:
    """Ask once, then once more with the validator text if the list is short or colliding.

    The system string stays byte-identical so the provider can cache it.
    The session text and any retry note stay in the user message.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    schema = _tool_schema(tools)
    user = "Session messages:\n" + transcript
    errors: list[str] = []
    last = ""
    for _attempt in range(2):
        if errors:
            user = (
                "Session messages:\n"
                + transcript
                + "\n\nThe previous keywords were rejected:\n"
                + "\n".join(f"- {item}" for item in errors)
                + "\nCall the tool again with a corrected list."
            )
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 800,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "tools": [schema],
            "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
        }
        reply = _post_json(url, payload, api_key)
        try:
            args = _arguments(reply)
        except (RuntimeError, json.JSONDecodeError) as exc:
            errors = [str(exc)]
            last = str(exc)
            continue
        items = args.get("procedure_keywords")
        errors = list(tools._procedure_keyword_errors({"procedure_keywords": items}))
        if not errors and isinstance(items, list):
            return list(items)
        last = "; ".join(errors)
    raise RuntimeError(last or "procedure_keywords rejected")


def _strip_keywords(frontmatter: str) -> str:
    """Drop an existing procedure_keywords block so a retry does not leave two lists."""
    lines = frontmatter.splitlines(keepends=True)
    kept: list[str] = []
    skipping = False
    for line in lines:
        if not skipping and line.startswith("procedure_keywords:"):
            skipping = True
            continue
        if skipping:
            if line.startswith((" ", "\t")):
                continue
            skipping = False
        kept.append(line)
    return "".join(kept)


def _insert_keywords(frontmatter: str, rendered: str) -> str:
    """Place the keyword block after entity_aliases, else entity, else type.

    That is the daily key order. Putting it at the bottom would survive, but
    the next digest render would move it and the diff would be the whole card.
    """
    lines = _strip_keywords(frontmatter).splitlines(keepends=True)
    anchor = 0
    for index, line in enumerate(lines):
        bare = line.lstrip("\ufeff")
        if bare.startswith("entity_aliases:"):
            anchor = index
            break
        if bare.startswith("entity:") or bare.startswith("type:"):
            anchor = index
    while anchor + 1 < len(lines) and lines[anchor + 1].startswith((" ", "\t")):
        anchor += 1
    newline = "\r\n" if frontmatter.endswith("\r\n") or "\r\n" in frontmatter else "\n"
    block = rendered
    if not block.endswith("\n") and not block.endswith("\r\n"):
        block += newline
    elif newline == "\r\n":
        block = block.replace("\n", "\r\n")
    return "".join(lines[: anchor + 1]) + block + "".join(lines[anchor + 1 :])


def _rebuild(text: str, updates: dict[str, str]) -> str:
    """Splices new frontmatter by card id and leaves every other byte in place."""
    if not updates:
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    idx = 0
    while idx < len(lines):
        if lines[idx].strip() != "---":
            out.append(lines[idx])
            idx += 1
            continue
        out.append(lines[idx])
        idx += 1
        fm: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            fm.append(lines[idx])
            idx += 1
        frontmatter = "".join(fm)
        card = _card_id(frontmatter)
        if card in updates:
            frontmatter = updates[card]
            if frontmatter and not frontmatter.endswith(("\n", "\r\n")):
                frontmatter += "\n"
        out.append(frontmatter)
        if idx < len(lines):
            out.append(lines[idx])
            idx += 1
    return "".join(out)


def _canonicals(items: list[dict[str, Any]]) -> str:
    names = [str(item.get("canonical") or "").strip() for item in items]
    return ", ".join(name for name in names if name)


def _self_check(tools: Any) -> None:
    """Check the splice and the range parser without calling Inferway or touching disk."""
    sample = (
        "---\n"
        "id: mem-1\n"
        "type: procedure\n"
        "entity: Venue\n"
        "sources: [session abc#10-12]\n"
        "---\n"
        "Obstacle: a; Solution: b\n"
    )
    windows = _windows(
        "sources: [session abc#10-12, session abc#10-12, session bare]\n",
        tools,
    )
    bracketed = _windows("sources: [session cron_x#4554-4707]\n", tools)
    if windows != [("abc", 10, 12)] or bracketed != [("cron_x", 4554, 4707)]:
        raise SystemExit(f"window parse failed: {windows} {bracketed}")
    rendered = "\n".join(
        tools.format_procedure_keywords_lines(
            [
                {"canonical": "venue", "originals": ["场馆"]},
                {"canonical": "book", "originals": []},
                {"canonical": "remind", "originals": []},
            ]
        )
    )
    front = sample.split("---\n", 2)[1]
    updated = _insert_keywords(front, rendered)
    if "procedure_keywords:" not in updated or updated.index("procedure_keywords:") > updated.index(
        "sources:"
    ):
        raise SystemExit("keyword block was not inserted before sources")
    rebuilt = _rebuild(sample, {"mem-1": updated})
    if rebuilt.count("procedure_keywords:") != 1 or "Obstacle:" not in rebuilt:
        raise SystemExit("rebuild changed the body or duplicated keywords")
    errors = tools._procedure_keyword_errors(
        {
            "procedure_keywords": [
                {"canonical": "venue", "originals": ["场馆"]},
                {"canonical": "book", "originals": []},
                {"canonical": "remind", "originals": []},
            ]
        }
    )
    if errors:
        raise SystemExit(f"validator rejected a good list: {errors}")
    print("self-check ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", type=Path, default=None)
    parser.add_argument("--plugin-root", type=Path, default=None)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    home = (args.hermes_home or Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")).resolve()
    plugin_root = (
        args.plugin_root
        or (home / "plugins" / "MyMemory")
    )
    if not (plugin_root / "digest" / "digest_tools.py").is_file():
        plugin_root = Path(__file__).resolve().parents[1]
    tools = _load_digest_tools(plugin_root)
    if args.self_check:
        _self_check(tools)
        return 0

    _load_dotenv(home / ".env")
    api_key = (os.environ.get("INFERWAY_API_KEY") or "").strip()
    if not api_key:
        print("INFERWAY_API_KEY is missing", file=sys.stderr)
        return 1
    daily = home / "memories" / "staging" / "daily"
    db_path = home / "state.db"
    if not daily.is_dir() or not db_path.is_file():
        print(f"missing daily notes or state.db under {home}", file=sys.stderr)
        return 1

    wanted = set(args.id)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    called = 0
    try:
        for path in sorted(daily.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            updates: dict[str, str] = {}
            for _preamble, frontmatter, _body in _blocks(text):
                if not _TYPE_RE.search(frontmatter):
                    continue
                card = _card_id(frontmatter)
                if not card or (wanted and card not in wanted):
                    continue
                if _has_keywords(frontmatter):
                    stored_errors = _stored_keyword_errors(frontmatter, tools)
                    if not stored_errors:
                        print(f"skip {card} already has procedure_keywords")
                        continue
                    print(f"rewrite {card} {stored_errors[0]}")
                windows = _windows(frontmatter, tools)
                if not windows:
                    print(f"skip {card} no session message range")
                    continue
                if args.limit and called >= args.limit:
                    break
                rows: list[tuple[int, str, str]] = []
                for sid, start, end in windows:
                    rows.extend(_fetch_window(conn, sid, start, end))
                transcript = _transcript(rows)
                if not transcript:
                    print(f"skip {card} session range has no user or assistant text")
                    continue
                try:
                    items = _ask(
                        tools,
                        transcript,
                        api_key=api_key,
                        base_url=args.base_url,
                        model=args.model,
                    )
                except RuntimeError as exc:
                    print(f"skip {card} {exc}")
                    continue
                rendered = "\n".join(tools.format_procedure_keywords_lines(items))
                updates[card] = _insert_keywords(frontmatter, rendered)
                called += 1
                span = ",".join(f"{sid}#{start}-{end}" for sid, start, end in windows)
                print(f"{card} {span} {_canonicals(items)}")
            if args.limit and called >= args.limit and not updates:
                break
            if updates and args.write:
                backup = daily / ".bak-procedure-keywords" / path.name
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_text(text, encoding="utf-8")
                path.write_text(_rebuild(text, updates), encoding="utf-8")
            if args.limit and called >= args.limit:
                break
    finally:
        conn.close()
    print(f"{'wrote' if args.write else 'dry-run'} {called} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
