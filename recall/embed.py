"""Channel 4 embed rerank — off until FTS5 miss rate on real queries exceeds 20%.

Identifier-dense queries (_EVENT_STAGE_RE, tool_choice) lose to BM25; download
multilingual-e5-small only after the miss log proves a paraphrase gap.
"""

from __future__ import annotations

import json
from pathlib import Path

from .ids import staging_root

MISS_LOG = ".fts-miss.jsonl"
GATE_RATE = 0.20
DEFAULT_MODEL = "intfloat/multilingual-e5-small"
UPGRADE_MODEL = "BAAI/bge-m3"
FORBIDDEN_MODEL = "BAAI/bge-small-zh-v1.5"


def log_fts_miss(query: str, hit: bool, staging: Path | None = None) -> None:
    """Append one live Channel-3 outcome so the 20% gate is measured, not guessed."""
    root = staging_root(staging)
    path = root / MISS_LOG
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"q": query, "hit": bool(hit)}) + "\n")
    except OSError:
        return


def miss_rate(staging: Path | None = None) -> float:
    root = staging_root(staging)
    path = root / MISS_LOG
    if not path.is_file():
        return 0.0
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return 0.0
    misses = sum(1 for r in rows if not r.get("hit"))
    return misses / len(rows)


def embed_enabled(staging: Path | None = None) -> bool:
    """Channel 4 stays off below the gate so lexical wins cannot be reranked away."""
    return miss_rate(staging) > GATE_RATE


def rerank_embed(*_a, **_k) -> list:
    """Placeholder: do not download weights until embed_enabled is true."""
    return []
