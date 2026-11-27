#!/usr/bin/env python3
"""Rewrite procedure originals that are a single Chinese character.

A one-character original is only a verb or a bare noun, so recall stores a
predicate with no object. This pass replaces that original with a 2–4
character phrase already supported by the same card. Canonicals, and every
original that is already a phrase, are copied through unchanged.

Dry-run is the default. Add --write to patch the markdown.

    python3 scripts/rewrite_bare_chinese_canonicals.py --self-check
    python3 scripts/rewrite_bare_chinese_canonicals.py --hermes-home ~/.hermes
    python3 scripts/rewrite_bare_chinese_canonicals.py --hermes-home ~/.hermes --write
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

TOOL_NAME = "rewrite_original"
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_TYPE_RE = re.compile(r"(?m)^type:\s*procedure\s*$")

SYSTEM_PROMPT = (
    "Rewrite one procedure original that is a single Chinese character.\n"
    "The replacement must be a phrase of 2 to 4 Chinese characters that "
    "contains that same character.\n"
    "When the character is a verb, put it first and name the object of that "
    "predicate. When it is a noun, attach the modifier the card already uses.\n"
    "The other characters must already appear together on the card. "
    "Do not invent an object.\n"
    "When candidates are listed, return one of those strings.\n"
    "Call rewrite_original with the new original only.\n"
    "Do not change the canonical or any other original."
)


def _load_backfill(plugin_root: Path) -> Any:
    """Load the keyword backfill by file so this repair shares its markdown splice.

    Importing scripts as a package would fail: they are not a package, and a
    second YAML walker would drift from the splice the backfill already trusts.
    """
    path = plugin_root / "scripts" / "backfill_procedure_keywords.py"
    spec = importlib.util.spec_from_file_location("backfill_procedure_keywords", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bare_verb(text: str) -> str:
    """Return the single Han character when a surface is only that character.

    English words and phrases of two or more characters are already legal
    lookup keys. Rewriting them would change a surface the validator accepts.
    """
    stripped = str(text or "").strip()
    han = _HAN_RE.findall(stripped)
    if not han or re.search(r"[A-Za-z]", stripped) or len(han) != 1:
        return ""
    if stripped != han[0]:
        return ""
    return han[0]


def _pure_han(text: str) -> str:
    stripped = str(text or "").strip()
    han = _HAN_RE.findall(stripped)
    if not han or stripped != "".join(han):
        return ""
    return stripped


def _pick_phrase(phrases: list[str]) -> str:
    """Use the longest phrase when every longest candidate is the same string.

    Two different objects at the same length are a guess. Leaving the verb
    untouched is safer than attaching the wrong object.
    """
    if not phrases:
        return ""
    longest = max(len(phrase) for phrase in phrases)
    winners = {phrase for phrase in phrases if len(phrase) == longest}
    if len(winners) != 1:
        return ""
    return next(iter(winners))


def _rest_is_on_card(phrase: str, char: str, evidence: str) -> bool:
    """Reject a model phrase whose added characters are not already on the card.

    The one character we already stored is removed once. What remains must be
    a contiguous object or modifier the card wrote, otherwise the rewrite
    invented a lookup key.
    """
    index = phrase.find(char)
    if index < 0:
        return False
    rest = phrase[:index] + phrase[index + len(char) :]
    return bool(rest) and rest in evidence


def _same_row_phrases(char: str, surfaces: list[str]) -> list[str]:
    """Keep sibling originals that already attach an object to this character.

    A longer original on the same keyword is the phrase the user wrote. A
    window cut from the card body would swallow the next word.
    """
    found: list[str] = []
    seen: set[str] = set()
    for surface in surfaces:
        if _bare_verb(surface):
            continue
        phrase = _pure_han(surface)
        if not phrase or phrase in seen:
            continue
        if char in phrase and 2 <= len(phrase) <= 4:
            seen.add(phrase)
            found.append(phrase)
    return found


def _rewrite_rows(
    items: list[Any],
    body: str,
    *,
    ask: Any | None,
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """Replace one-character originals and copy every canonical through.

    A bare original with no phrase on the card stays as it is, so one miss
    does not throw away the other originals on that keyword. A replacement
    that repeats the canonical is dropped because the validator treats that
    as the same string twice.
    """
    notes: list[str] = []
    rewritten: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            return None, ["procedure_keywords item is not an object"]
        canonical = str(item.get("canonical") or "").strip()
        originals = item.get("originals")
        if isinstance(originals, str):
            originals = [originals]
        if not isinstance(originals, list):
            originals = []
        surfaces = [str(surface).strip() for surface in originals if str(surface).strip()]
        kept: list[str] = []
        seen: set[str] = set()
        for surface in surfaces:
            char = _bare_verb(surface)
            if not char:
                if surface not in seen:
                    kept.append(surface)
                    seen.add(surface)
                continue
            phrases = _same_row_phrases(char, surfaces)
            phrase = _pick_phrase(phrases)
            if not phrase and ask is not None:
                phrase = ask(char, canonical, surfaces, body, phrases, items)
            if (
                not phrase
                or char not in phrase
                or not (2 <= len(phrase) <= 4)
                or _pure_han(phrase) != phrase
                or phrase == canonical
            ):
                if surface not in seen:
                    kept.append(surface)
                    seen.add(surface)
                notes.append(f"keep {canonical}: {surface}")
                continue
            notes.append(f"{canonical}: {surface} -> {phrase}")
            if phrase not in seen:
                kept.append(phrase)
                seen.add(phrase)
        rewritten.append({"canonical": canonical, "originals": kept})
    return rewritten, notes


def _tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Store the rewritten original.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["phrase"],
                "properties": {
                    "phrase": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 4,
                    }
                },
            },
        },
    }


def _canonical_argument(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("inferway reply had no choices")
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping):
        raise RuntimeError("inferway reply had no message")
    raw = ""
    calls = message.get("tool_calls") or []
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
    parsed = dict(raw) if isinstance(raw, Mapping) else json.loads(str(raw))
    if not isinstance(parsed, dict):
        raise RuntimeError("rewrite payload was not an object")
    return str(parsed.get("phrase") or "").strip()


def _ask_factory(backfill: Any, *, api_key: str, base_url: str, model: str) -> Any:
    """Build one ask closure so the system prompt stays byte-identical.

    Card text, the character, and the candidate list vary per original and
    stay in the user message. Putting them in the system string would miss
    the cache.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    schema = _tool_schema()

    def ask(
        char: str,
        canonical: str,
        originals: list[str],
        body: str,
        phrases: list[str],
        items: list[Any],
    ) -> str:
        keyword_lines: list[str] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            keyword_lines.append(str(item.get("canonical") or ""))
            row_originals = item.get("originals")
            if isinstance(row_originals, str):
                row_originals = [row_originals]
            if isinstance(row_originals, list):
                keyword_lines.extend(str(surface) for surface in row_originals)
        evidence = "\n".join([body, *keyword_lines])
        user = (
            f"character: {char}\n"
            f"canonical: {canonical}\n"
            f"originals: {json.dumps(originals, ensure_ascii=False)}\n"
            f"candidates: {json.dumps(phrases, ensure_ascii=False)}\n"
            f"card:\n{evidence}"
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
        try:
            reply = backfill._post_json(url, payload, api_key)
            phrase = _pure_han(_canonical_argument(reply))
        except (RuntimeError, json.JSONDecodeError, TypeError):
            return ""
        if phrases and phrase not in phrases:
            return ""
        if not _rest_is_on_card(phrase, char, evidence):
            return ""
        if phrase == canonical:
            return ""
        return phrase

    return ask


def _replace_keyword_block(frontmatter: str, rendered: str) -> str:
    """Swap the keyword block in place so the rest of the card stays put.

    Reinserting after entity would move sources and make the diff the whole card.
    """
    lines = frontmatter.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if line.startswith("procedure_keywords:"):
            start = index
            break
    if start is None:
        return frontmatter
    end = start + 1
    while end < len(lines) and lines[end].startswith((" ", "\t")):
        end += 1
    newline = "\r\n" if "\r\n" in frontmatter else "\n"
    block = rendered if rendered.endswith(("\n", "\r\n")) else rendered + newline
    if newline == "\r\n":
        block = block.replace("\n", "\r\n")
    return "".join(lines[:start]) + block + "".join(lines[end:])


def _memory_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def _card_has_bare_original(items: list[Any]) -> bool:
    """Detect a one-character original without looking at the English canonical.

    The stored canonicals are English task words. The bare verb sits in
    originals, and scanning canonicals reports a card that needs no rewrite.
    """
    for item in items:
        if not isinstance(item, Mapping):
            continue
        originals = item.get("originals")
        if isinstance(originals, str):
            originals = [originals]
        if not isinstance(originals, list):
            continue
        if any(_bare_verb(str(surface)) for surface in originals):
            return True
    return False


def _self_check(backfill: Any, tools: Any) -> None:
    """Check the phrase pick and the splice without calling Inferway."""
    body = "Obstacle: 没写消息; Solution: 先写消息再发送。"
    items = [
        {"canonical": "write", "originals": ["写", "写消息"]},
        {"canonical": "send", "originals": ["发送"]},
        {"canonical": "venue", "originals": ["场馆"]},
    ]
    rewritten, notes = _rewrite_rows(items, body, ask=None)
    if rewritten is None or notes != ["write: 写 -> 写消息"]:
        raise SystemExit(f"unique phrase failed: {rewritten} {notes}")
    if rewritten[0]["canonical"] != "write" or rewritten[0]["originals"] != ["写消息"]:
        raise SystemExit("canonical changed or the bare original was kept")
    if rewritten[1]["originals"] != ["发送"] or rewritten[2]["originals"] != ["场馆"]:
        raise SystemExit("untouched originals changed")
    kept, kept_notes = _rewrite_rows(
        [{"canonical": "write", "originals": ["写"]}],
        "订场馆，也订餐厅",
        ask=None,
    )
    if kept is None or kept[0]["originals"] != ["写"] or "keep write: 写" not in kept_notes:
        raise SystemExit(f"missing phrase should leave the original: {kept} {kept_notes}")
    sample = (
        "---\n"
        "id: mem-1\n"
        "type: procedure\n"
        "procedure_keywords:\n"
        "  - canonical: write\n"
        "    originals:\n"
        "      - 写\n"
        "      - 写消息\n"
        "  - canonical: send\n"
        "    originals:\n"
        "      - 发送\n"
        "  - canonical: venue\n"
        "    originals:\n"
        "      - 场馆\n"
        "sources: [session abc#1-2]\n"
        "---\n"
        "Obstacle: 没写消息; Solution: 先写消息再发送。\n"
    )
    parts = backfill._blocks(sample)
    front = parts[0][1]
    import yaml

    parsed = yaml.safe_load(front)
    updated_rows, _notes = _rewrite_rows(
        parsed["procedure_keywords"], sample, ask=None
    )
    rendered = "\n".join(tools.format_procedure_keywords_lines(updated_rows))
    spliced = _replace_keyword_block(front, rendered)
    if "canonical: write" not in spliced or "\n      - 写\n" in spliced:
        raise SystemExit("splice kept the one-character original or dropped the canonical")
    if "sources:" not in spliced or spliced.index("procedure_keywords:") > spliced.index(
        "sources:"
    ):
        raise SystemExit("splice moved the keyword block")
    errors = tools._procedure_keyword_errors({"procedure_keywords": updated_rows})
    if errors:
        raise SystemExit(f"rewritten keywords failed validation: {errors}")
    print("self-check ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-home", type=Path, default=None)
    parser.add_argument("--plugin-root", type=Path, default=None)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash-1017-zdr")
    parser.add_argument("--base-url", default="https://api.inferway.ai/v1")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the model when the card has two equally long phrases.",
    )
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    plugin_root = (args.plugin_root or Path(__file__).resolve().parents[1]).resolve()
    backfill = _load_backfill(plugin_root)
    tools = backfill._load_digest_tools(plugin_root)
    if args.self_check:
        _self_check(backfill, tools)
        return 0

    home = (
        args.hermes_home or Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    ).resolve()
    memories = home / "memories"
    if not memories.is_dir():
        print(f"missing memories under {home}", file=sys.stderr)
        return 1
    backfill._load_dotenv(home / ".env")
    ask = None
    if not args.no_llm:
        api_key = (os.environ.get("INFERWAY_API_KEY") or "").strip()
        if api_key:
            ask = _ask_factory(
                backfill,
                api_key=api_key,
                base_url=args.base_url,
                model=args.model,
            )
        else:
            print("INFERWAY_API_KEY missing; ambiguous cards will be skipped")

    wanted = set(args.id)
    changed = 0
    import yaml

    for path in _memory_files(memories):
        text = path.read_text(encoding="utf-8")
        updates: dict[str, str] = {}
        for _preamble, frontmatter, body in backfill._blocks(text):
            if not _TYPE_RE.search(frontmatter):
                continue
            card = backfill._card_id(frontmatter)
            if not card or (wanted and card not in wanted):
                continue
            if not re.search(r"(?m)^procedure_keywords:\s*$", frontmatter):
                continue
            try:
                parsed = yaml.safe_load(frontmatter) or {}
            except Exception as exc:
                print(f"skip {card} unreadable frontmatter: {exc}")
                continue
            if not isinstance(parsed, dict):
                continue
            items = parsed.get("procedure_keywords")
            if not isinstance(items, list):
                continue
            if not _card_has_bare_original(items):
                continue
            if args.limit and changed >= args.limit:
                break
            rewritten, notes = _rewrite_rows(items, body, ask=ask)
            if rewritten is None:
                print(f"skip {card} {notes[0] if notes else 'no phrase'}")
                continue
            if not any("->" in note for note in notes):
                print(f"skip {card} {notes[0] if notes else 'no phrase'}")
                continue
            errors = tools._procedure_keyword_errors({"procedure_keywords": rewritten})
            if errors:
                print(f"skip {card} {errors[0]}")
                continue
            rendered = "\n".join(tools.format_procedure_keywords_lines(rewritten))
            updates[card] = _replace_keyword_block(frontmatter, rendered)
            changed += 1
            print(f"{card} {path.name} {'; '.join(notes)}")
        if updates and args.write:
            backup = path.parent / ".bak-bare-chinese-canonicals" / path.name
            if not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(text, encoding="utf-8")
            path.write_text(backfill._rebuild(text, updates), encoding="utf-8")
        if args.limit and changed >= args.limit:
            break
    print(f"{'wrote' if args.write else 'dry-run'} {changed} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
