"""Prompt and parser for the Phase 2 duplicate-consolidation proposer.

Detection is the LLM's job: lexical similarity cannot see an event extension,
because the new block says something the old one did not. This module owns the
instructions; ``digest.llm_proposer`` owns the invocation.

Every example here uses placeholders (``Person P``, ``Topic X``, ``job_id=A``).
Real names, paths, job ids, and dates from staging must never appear: they steer
the worker toward those entities instead of the pattern.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping


MAX_ERRORS_IN_PROPOSER_PROMPT = 8
MAX_PROPOSER_ERROR_CHARS = 800

NORMATIVE_RULES = """## The question you are answering

Compare **same type only**. Never compare an event to a fact, procedure, or
decision (or any other mixed-type pair). Different types may `related:` each
other; that is coexistence, not a duplicate. Cross-type drop/merge is legacy
from when four extractors wrote the four types separately.

If `## Filtered candidate board` is present, compare **only those listed
candidate pairs**. `pairs[k] = [i, j]` means compare `cards[i]` with
`cards[j]`; emit operations using `cards[i].id` / `cards[j].id`.
If that section is absent, compare every same-type pair. For each compared
pair ask: does one block EXTEND the other, or RESTATE it? Consolidate only
then.

An extension is any of:
- A follow-up or addition — same episode, later stage. "asked the contact about
  Topic X" then "the contact agreed to do Topic X on date D".
- B specification — same record, more concrete. "Person P is a teacher" then
  "Person P teaches subject S at school Q".
- C evolution or progress — same episode, advanced. "component failed to load at
  T1" then "component self-recovered by T2".

A restatement is the same content reworded. It is the easier case and also
consolidates.

## Resolution

Cards are grouped into four JSON arrays (event, fact, procedure, decision).
Compare **inside each array**. `related:` may cite ids in other arrays; that
is coexistence, not a duplicate pair.

1. Skip every pair whose `type` fields differ — emit nothing for that pair.
2. Same type plus extension or restatement — emit `merge`. The survivor is the
   block with the higher `importance`. For merge, fill **exactly one** nested
   object matching survivor `type` with rewritten meaning slots (non-empty):
   - event → `event: {beginning, course, outcome}` (each slot one concise sentence)
   - procedure → `procedure: {obstacle, solution}`
   - decision → `decision: {kind, subject, ruling}` (ruling is the user's predicate; subject=user; assembled Preference/Decision: subject + predicate ruling is one clause)
   - fact → `fact: {kind, content}`
   Code renders the body prefixes; do **not** send free-form `body` and do
   **not** concatenate. For same-type **fact** pairs that share a cast/story
   (or whose unioned involves would have length ≥ 2), nest **must** use
   `fact.kind = "Narration"` and absorbed Factual ids go only in `absorbed_ids`
   (dropped). Unrelated plains with different job_id / purpose stay separate —
   emit nothing.
3. Same-type `drop` only when a card is a pure duplicate you will not fold into
   a survivor (loser's sources/related/body go with it). Never `drop` an event.
4. On a tie, break by `importance`, then by number of `sources`, then by the
   earlier `valid_from`.
5. No extension and no restatement — emit nothing for that pair. There is no
   grey-zone outcome: a pair either consolidates or is left alone.

## Output contract

Call a Phase-2 tool. Do **not** write a JSON array in assistant text.
`submit_operations` takes `{ "operations": [ ... ] }`. Empty `operations[]`
keeps every card. Each item is one of:

- {"operation": "update", "id": "<existing-id>", "changes": {...}}
- {"operation": "merge", "survivor_id": "<id>", "absorbed_ids": ["<id>"], "reason": "...", "<type-nest>": {...}}
- {"operation": "drop", "id": "<id>", "reason": "..."}
- {"operation": "supersede", "helper_id": "<new-id>", "target_id": "<old-id>", "correction": "...", "confidence": "explicit"}

Rules for the array:
- Never emit `create`. New cards are already persisted; an empty array keeps them.
- `reason` is mandatory on `merge` and `drop`, and must name the pattern (A, B,
  C, or restatement) and why that block wins.
- For `merge`, fill exactly one nest matching survivor type; every field inside
  that nest must be nonempty. Never emit empty strings in nest fields.
- You may merge two EXISTING blocks with each other, not only new against
  existing. Internal duplication in the file is yours to fix.
- Read a block's type from its `type` field. Never infer it from its id.
- Merge/drop/supersede ids must share one type. Mixed-type ops are invalid.

## Past-day contradiction (retrieval-grounded update)

Do not merge complementary facts or historical evolution. A later card that
*contradicts* an older same-type/same-entity card retrieved in the same recall
set may emit a metadata-only `update` on the older id:

- `valid_to`: the later card's date
- `status`: rejected
- `rejected_reason`: `rejected by <later-mem-id>`

If the user explicitly says a freshly recalled older card is dated/wrong and
exactly one eligible older target exists:

- `rejected_reason`: `rejected by user's correction`

Never change the old body. Never merge/drop/supersede across days. Leave
complementary or evolving facts alone.
"""

FEW_SHOT_BANK = """## Worked examples

### A follow-up (same type, so merge)

Existing (decision, importance 4): `Decision: user instructed to translate
design note N and append the Phase 2 proposer section.`
New (decision, importance 4): `Decision: user instructed that all five sections
of design note N must be resynchronized after the architecture pivot.`
Why A: same note, same decision thread; the second block is the next stage.
Merged meaning goes in the decision nest (code renders the Decision: body).
Call submit_operations with operations: [the object below].

{"operation":"merge","survivor_id":"<id-of-richer-or-higher-importance>","absorbed_ids":["<other>"],
 "reason":"A follow-up: same design-note decision advanced from translate+append to full-section resync",
 "decision":{"kind":"Decision","subject":"user","ruling":"translate design note N, append Phase 2, and resynchronize all five sections after the architecture pivot"}}

### B specification (same type, so merge)

Existing (procedure, importance 4): Obstacle/Solution about clearing a stale lock.
New (procedure, importance 4): same lock recovery with clearer steps.
Why B: same recovery procedure; merge meaning into procedure nest.

{"operation":"merge","survivor_id":"<id-with-file-list>","absorbed_ids":["<shorter>"],
 "reason":"B specification: same lock recovery; survivor has clearer clear-then-restart steps",
 "procedure":{"obstacle":"gateway stuck because .dispatcher.lock is stale and restart alone fails","solution":"clear .dispatcher.lock then restart gateway"}}

### C evolution (same type, so merge)

Existing (event, importance 4) and New (event, importance 5) about the same stall.
Why C: same stall episode; later investigation and outcome.

{"operation":"merge","survivor_id":"<importance-5-event>","absorbed_ids":["<importance-4-event>"],
 "reason":"C evolution: same stall episode; survivor has later investigation and clearer outcome, importance 5>4",
 "event":{"beginning":"stall investigated after unfinished report","course":"ImportError recovered then batch B re-run failed three retries","outcome":"daily file not written"}}

### Restatement (same type, so merge; the easiest case)

Two decisions with near-identical meaning, importance 4 versus 1. Fill the
decision nest with the kept ruling; union `sources` and keep higher importance.

{"operation":"merge","survivor_id":"<importance-4>","absorbed_ids":["<importance-1>"],
 "reason":"Restatement: identical decision text; keep higher importance",
 "decision":{"kind":"Decision","subject":"user","ruling":"keep the existing decision ruling unchanged in meaning"}}

### Fact Factual restatement (same type, stay Factual)

Two Factual facts restating one observation (no multi-cast story).

{"operation":"merge","survivor_id":"<importance-4-fact>","absorbed_ids":["<importance-1-fact>"],
 "reason":"Restatement: identical outdoor-dates fact; keep higher importance",
 "fact":{"kind":"Factual","content":"Person P prefers outdoor dates"}}

### Fact cast merge into Narration (same type; drop Factual)

Existing (fact, importance 4, Narration or cast): Person P lives in a school dorm
so dates default outside. involves: [{entity: Partner Q, role: partner}]
New (fact, importance 4, Factual): Person P dislikes cilantro and green onion.
Why B/A: same cast/story about Person P; absorb Factual into Narration.
Nest MUST use kind=Narration; Factual loser only in absorbed_ids.

{"operation":"merge","survivor_id":"<richer-or-higher-importance-fact>","absorbed_ids":["<other-fact>"],
 "reason":"B specification / A follow-up: same Person-P cast story; absorb Factual into Narration",
 "fact":{"kind":"Narration","content":"Person P lives in a school dorm so dates default outside; dislikes cilantro and green onion"}}

### Negatives (emit neither merge nor drop)

1. Two facts sharing boilerplate ("user set a one-shot reminder job_id=A") but
   differing by job_id, date, and purpose. Different instances, not an extension.
2. An `event` plus a `fact`/`procedure`/`decision` it cites in `related`.
   Different types — skip the pair (do not invent a drop).
"""


def _render_block(block: Mapping[str, Any]) -> str:
    """Render a block in full.

    No field whitelist: the proposer needs full cards to decide merge/drop.
    default=str because YAML parses valid_from/valid_to into date objects.
    """
    return json.dumps(dict(block), ensure_ascii=False, sort_keys=True, default=str)


BLOCK_TYPE_ORDER = ("event", "fact", "procedure", "decision")


def _group_blocks_by_type(
    blocks: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Bucket cards so the proposer compares same-type neighbors, not a mixed list."""
    grouped = {key: [] for key in BLOCK_TYPE_ORDER}
    for block in blocks:
        raw = dict(block)
        kind = str(raw.get("type") or "").strip().lower()
        if kind not in grouped:
            continue
        grouped[kind].append(raw)
    return grouped


def render_typed_board(prefix: str, blocks: Iterable[Mapping[str, Any]]) -> str:
    """Dump one JSON array per type so mixed file order cannot hide same-type pairs.

    Empty types are ``[]``. Each card is still one object (full fields, sort_keys).
    """
    grouped = _group_blocks_by_type(blocks)
    parts: list[str] = []
    for kind in BLOCK_TYPE_ORDER:
        payload = [
            json.loads(_render_block(card)) for card in grouped[kind]
        ]
        heading = f"### {prefix} {kind}s"
        parts.append(heading + "\n" + json.dumps(payload, ensure_ascii=False, default=str))
    return "\n\n".join(parts) + "\n"


WRAPUP_RULES = """## Day wrap-up

Call submit_day_wrapup with { "phrases": ["...", "..."] }.

This trailer is a same-day catalog, not a rewrite of the YAML cards.
Events stay separate on disk so detail is not lost. The wrap-up may
connect them.

Cover every event on the checklist. If two or more events are clearly
the same sitting — same task, same artifact, later turns only refine
it — write them as one markdown bullet that names the through-line.
Unrelated events stay one bullet each. Do not invent a connection.
Do not drop an unrelated event because another cluster was large.

Not a paragraph. Not YAML. Not a comma-spliced list of every turn.

Shape (connected same-day sitting — preferred when true):
- "The user spent the day iterating a Xiaohongshu infographic for the memory plugin, from layout through final page delivery."

Shape (unrelated events — keep separate):
- "The user asked for commonly spoken dialects listed in a table."
- "The user requested a WeChat reminder draft to 林主任."

Start with "The user …" when it is the user's action. One sentence per
bullet. At most 200 characters. No second sentence inside a bullet
(code keeps only the first).

If there are no events, one short bullet for the day's remaining cards.
"""


def _wrapup_event_checklist(blocks: Iterable[Mapping[str, Any]]) -> str:
    """List every event predicate so wrap-up cannot silently drop a sitting.

    Related same-day events may still share one bullet in phrases[]; the
    checklist is coverage, not a one-phrase-per-line quota.
    """
    lines: list[str] = []
    for block in blocks:
        if str(block.get("type") or "").strip().lower() != "event":
            continue
        label = (
            str(block.get("predicate") or "").strip()
            or str(block.get("entity") or "").strip()
            or str(block.get("id") or "").strip()
        )
        if label:
            lines.append(f"- {label}")
    if not lines:
        return ""
    return (
        "## Events — cover each; related same-day events may share one bullet\n\n"
        + "\n".join(lines)
        + "\n"
    )


def build_wrapup_prompt(blocks: Iterable[Mapping[str, Any]]) -> str:
    """Reuse typed JSON arrays so wrap-up sees the same board as Phase 2, not YAML."""
    cards = list(blocks)
    checklist = _wrapup_event_checklist(cards)
    parts = [WRAPUP_RULES]
    if checklist:
        parts.append(checklist)
    parts.append(render_typed_board("Today", cards))
    return "\n".join(parts)


def _render_blocks(label: str, blocks: Iterable[Mapping[str, Any]]) -> str:
    """Compatibility alias — typed board headings replace the mixed list."""
    prefix = "Existing" if "existing" in label.casefold() else "New"
    return render_typed_board(prefix, blocks)


_OP_INDEX_RE = re.compile(r"operation\[(\d+)\]")
_ID_TOKEN_RE = re.compile(r"\b(?:mem-[A-Za-z0-9_-]+|tmp-[A-Za-z0-9_-]+|[ENFD]\d+)\b")


def _ids_from_operation(op: Mapping[str, Any] | Any) -> set[str]:
    """Collect mem-ids referenced by one operation dict / Operation-like."""
    found: set[str] = set()
    if not isinstance(op, Mapping):
        # Operation dataclass-like
        data = {
            "survivor_id": getattr(op, "survivor_id", None),
            "absorbed_ids": getattr(op, "absorbed_ids", None),
            "id": getattr(op, "id", None),
            "helper_id": getattr(op, "helper_id", None),
            "target_id": getattr(op, "target_id", None),
            "block": getattr(op, "block", None),
        }
    else:
        data = dict(op)
    for key in ("survivor_id", "id", "helper_id", "target_id"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            found.add(val.strip())
    absorbed = data.get("absorbed_ids")
    if isinstance(absorbed, list):
        for item in absorbed:
            if isinstance(item, str) and item.strip():
                found.add(item.strip())
    block = data.get("block")
    if isinstance(block, Mapping):
        bid = block.get("id")
        if isinstance(bid, str) and bid.strip():
            found.add(bid.strip())
    return found


def foul_touched_block_ids(
    errors: Iterable[str],
    previous_ops: Iterable[Any],
    existing_blocks: Iterable[Mapping[str, Any]],
    new_blocks: Iterable[Mapping[str, Any]],
) -> set[str]:
    """Foul-touched seed ids plus outbound one-hop related/supersedes.

    Seed = ids mentioned in errors ∪ ids on failing ops (by operation[i] or
    any id token in the error that matches a board id). One hop expands only
    from seed blocks' related/supersedes into ids still on the board.
    """
    board: dict[str, Mapping[str, Any]] = {}
    for block in list(existing_blocks) + list(new_blocks):
        bid = str(block.get("id") or "").strip()
        if bid:
            board[bid] = block
    board_ids = set(board)
    ops = list(previous_ops)
    seed: set[str] = set()
    error_list = [str(e) for e in errors if str(e).strip()]

    indexed: set[int] = set()
    for err in error_list:
        for match in _OP_INDEX_RE.finditer(err):
            indexed.add(int(match.group(1)))
        for token in _ID_TOKEN_RE.findall(err):
            if token in board_ids:
                seed.add(token)

    if indexed:
        for idx in indexed:
            if 0 <= idx < len(ops):
                seed |= _ids_from_operation(ops[idx])
    elif error_list and not seed:
        # Errors name no operation[i] and no board ids — no foul set.
        return set()
    elif error_list and seed:
        # Ids in errors only — still expand ops that mention those ids.
        for op in ops:
            op_ids = _ids_from_operation(op)
            if op_ids & seed:
                seed |= op_ids

    seed &= board_ids
    if not seed:
        return set()

    closure = set(seed)
    for sid in list(seed):
        block = board.get(sid) or {}
        for key in ("related", "supersedes"):
            refs = block.get(key)
            if not isinstance(refs, list):
                continue
            for ref in refs:
                rid = str(ref).strip()
                if rid in board_ids:
                    closure.add(rid)
    return closure


def pending_new_ids(
    new_blocks: Iterable[Mapping[str, Any]],
    closure_ids: Iterable[str],
) -> list[str]:
    """New-block ids not in the foul-touched closure (checklist only)."""
    closed = {str(x).strip() for x in closure_ids}
    out: list[str] = []
    for block in new_blocks:
        bid = str(block.get("id") or "").strip()
        if bid and bid not in closed:
            out.append(bid)
    return out


def build_proposer_prompt(
    existing_blocks: Iterable[Mapping[str, Any]],
    new_blocks: Iterable[Mapping[str, Any]],
    *,
    errors: Iterable[str] = (),
    attempt: int = 1,
    previous_operations: Iterable[Any] | None = None,
    pending_account_ids: Iterable[str] | None = None,
    already_persisted_new: bool = False,
    candidate_pairs: Iterable[tuple[str, str]] | None = None,
) -> str:
    """Assemble the proposer prompt: rules, examples, live blocks, then retries.

    Gated first-attempt boards must not pay for empty type arrays or a second
    mem-id pair list; unpaired cards stay on disk unprompted. ``candidate_pairs
    is None`` keeps the full typed-board fail-open wording. Static rules stay
    first for caching.

    Attempt >= 2 with ``previous_operations``: caller should pass already
    filtered existing/new (foul-touched closure only). Pending new ids appear
    under ``## Still must account for`` without bodies.
    """
    sections = [
        "You are the update operator for a daily memory staging file. You decide "
        "which blocks are duplicates of each other and how to consolidate them.",
        NORMATIVE_RULES,
        FEW_SHOT_BANK,
    ]
    if already_persisted_new:
        sections.append(
            "## Persistence note\n\n"
            "New blocks from this session are **already written** to the daily "
            "file. Emit only merge / update / drop / supersede. Never create."
        )
    if candidate_pairs is None:
        sections.extend(
            [
                "## Blocks in play",
                render_typed_board("Existing", existing_blocks),
                render_typed_board("New", new_blocks),
            ]
        )
    else:
        board_cards = list(existing_blocks) + list(new_blocks)
        by_id = {
            str(block.get("id") or "").strip(): dict(block)
            for block in board_cards
            if str(block.get("id") or "").strip()
        }
        pair_ids: list[tuple[str, str]] = []
        involved: set[str] = set()
        for pair in candidate_pairs:
            left, right = pair[0], pair[1]
            a = str(left).strip()
            b = str(right).strip()
            if a and b and a in by_id and b in by_id:
                pair_ids.append((a, b))
                involved.add(a)
                involved.add(b)
        grouped = _group_blocks_by_type(
            block
            for block in board_cards
            if str(block.get("id") or "").strip() in involved
        )
        compact: dict[str, Any] = {}
        for kind in BLOCK_TYPE_ORDER:
            cards = grouped.get(kind) or []
            if not cards:
                continue
            index_of = {
                str(card.get("id") or "").strip(): i
                for i, card in enumerate(cards)
            }
            pairs_idx: list[list[int]] = []
            for a, b in pair_ids:
                ia = index_of.get(a)
                ib = index_of.get(b)
                if ia is None or ib is None or ia == ib:
                    continue
                pairs_idx.append([ia, ib])
            if not pairs_idx:
                continue
            compact[kind] = {
                "cards": [json.loads(_render_block(card)) for card in cards],
                "pairs": pairs_idx,
            }
        sections.append(
            "## Filtered candidate board\n\n"
            "Survivor cards only, keyed by type. Empty types are omitted. "
            "`pairs[k] = [i, j]` means compare `cards[i]` with `cards[j]`. "
            "Emit merge/update/drop/supersede using `cards[i].id` / "
            "`cards[j].id`, not the indexes.\n\n"
            + json.dumps(
                compact,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        )
    if previous_operations is not None and attempt >= 2:
        try:
            prev_payload = [
                dict(op) if isinstance(op, Mapping) else (
                    op.__dict__ if hasattr(op, "__dict__") else {"repr": str(op)}
                )
                for op in previous_operations
            ]
            sections.append(
                "## Previous operations\n\n"
                + json.dumps(prev_payload, ensure_ascii=False, indent=2, default=str)
            )
        except Exception:
            sections.append("## Previous operations\n\n(unavailable)")
    error_list = [str(error) for error in errors if str(error).strip()]
    if error_list:
        joined = "\n".join(
            f"- {error}" for error in error_list[:MAX_ERRORS_IN_PROPOSER_PROMPT]
        )[:MAX_PROPOSER_ERROR_CHARS]
        if attempt >= 2 and previous_operations is not None:
            sections.append(
                f"## Validation errors (attempt {attempt})\n\n"
                f"{joined}\n\n"
                "Fix foul-touched ops via patch_operations. Unrelated board "
                "cards were omitted on purpose. Do not dump JSON in assistant text."
            )
        else:
            sections.append(
                f"## Your previous proposal was rejected (attempt {attempt})\n\n"
                f"{joined}\n\nFix exactly these via patch_operations (or "
                "submit_operations with a full operations[]). Do not dump JSON "
                "in assistant text."
            )
    if pending_account_ids:
        ids = [str(i).strip() for i in pending_account_ids if str(i).strip()]
        if ids:
            lines = "\n".join(f"- {i}" for i in ids)
            sections.append(
                "## Still must account for (ids only — no bodies)\n\n"
                f"{lines}\n\n"
                "Those ids are already on disk. Omit them unless you "
                "merge/update/drop/supersede them. Never create."
            )
    sections.append(
        "You MUST call submit_operations (full operations[]) or "
        "patch_operations (sparse fix). Put ops in the tool arguments, "
        "not in assistant text. Empty operations[] keeps all cards."
    )
    return "\n\n".join(sections)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_proposal(raw: str) -> list[dict[str, Any]]:
    """Extract the operation array from a model reply.

    Raises ``ValueError`` so ``prepare_operations`` records the failure as a
    proposal error and retries with feedback.
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("proposer returned an empty reply")
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("proposer reply contains no JSON array")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"proposer reply is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("proposer reply is not a JSON array")
    operations: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            raise ValueError(f"proposal[{index}] is not an object")
        operations.append(dict(item))
    return operations
