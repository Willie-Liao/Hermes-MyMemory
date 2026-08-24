"""Channel 4 paraphrase rerank with a lazy int8 ONNX GTE — never load PyTorch in Hermes.

FTS5 wins identifier-dense queries; this path only runs after a miss so BM25
cannot be reranked away. Weights live in ~/.cache/mymemory (or MYMEMORY_GTE_ONNX),
not git. Export once on a Mac (Optimum QInt8); the cloud process uses onnxruntime
only. Peak-RAM spike of fp32 GTE would OOM the ~1.6 GB Hermes box.

Mac export (not CI)::

    pip install 'sentence-transformers' optimum onnx onnxruntime
    python -c "from recall.embed import export_gte_int8; export_gte_int8()"

from the MyMemory plugin directory. Fail-open: missing file / ORT / encode error
returns [] so recall_memory falls through to L1.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Sequence

from .ids import (
    BlockRecord,
    classify_daily_id,
    classify_weekly_id,
    iso_week,
    one_line,
    staging_root,
)

MISS_LOG = ".fts-miss.jsonl"
GATE_RATE = 0.20
DEFAULT_MODEL = "Alibaba-NLP/gte-multilingual-base"
UPGRADE_MODEL = "BAAI/bge-m3"
FORBIDDEN_MODEL = "BAAI/bge-small-zh-v1.5"
CANDIDATE_CAP = 48
ENCODE_BATCH = 8
COSINE_FLOOR = 0.30
COSINE_GAP = 0.02
MAX_LENGTH = 512

_SESSION: Any = None
_TOKENIZER: Any = None


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


def _cache_dir() -> Path:
    env = (os.environ.get("MYMEMORY_GTE_ONNX") or "").strip()
    if env:
        path = Path(env)
        return path.parent if path.suffix == ".onnx" else path
    return Path.home() / ".cache" / "mymemory" / "gte-multilingual-base-int8"


def _onnx_file() -> Path | None:
    env = (os.environ.get("MYMEMORY_GTE_ONNX") or "").strip()
    if env:
        path = Path(env)
        if path.is_file() and path.suffix == ".onnx":
            return path
        for name in ("model_int8.onnx", "model.onnx"):
            cand = path / name if path.is_dir() else path
            if cand.is_file():
                return cand
        if path.is_dir():
            found = sorted(path.glob("*.onnx"))
            if found:
                return found[0]
        return None
    cache = _cache_dir()
    for name in ("model_int8.onnx", "model.onnx"):
        cand = cache / name
        if cand.is_file():
            return cand
    if cache.is_dir():
        found = sorted(cache.glob("*.onnx"))
        if found:
            return found[0]
    return None


def _drop_session() -> None:
    global _SESSION, _TOKENIZER
    _SESSION = None
    _TOKENIZER = None


def _ort_session():
    """Lazy InferenceSession so plugin import does not pay 300 MB RSS."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    onnx_path = _onnx_file()
    if onnx_path is None:
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    try:
        _SESSION = ort.InferenceSession(
            str(onnx_path), providers=["CPUExecutionProvider"]
        )
    except Exception:
        return None
    return _SESSION


def _tokenizer():
    global _TOKENIZER
    if _TOKENIZER is not None:
        return _TOKENIZER
    cache = _cache_dir()
    tok_path = cache / "tokenizer.json"
    if not tok_path.is_file():
        onnx = _onnx_file()
        if onnx is not None:
            tok_path = onnx.parent / "tokenizer.json"
    if not tok_path.is_file():
        return None
    try:
        from tokenizers import Tokenizer
    except ImportError:
        return None
    try:
        tok = Tokenizer.from_file(str(tok_path))
        tok.enable_truncation(max_length=MAX_LENGTH)
        tok.no_padding()
    except Exception:
        return None
    _TOKENIZER = tok
    return _TOKENIZER


def _mean_pool(hidden: Any, mask: Any) -> Any:
    import numpy as np

    weights = mask.astype("float32")[:, :, None]
    summed = (hidden * weights).sum(axis=1)
    counts = weights.sum(axis=1).clip(min=1e-9)
    return summed / counts


def _l2_normalize(rows: Any) -> Any:
    import numpy as np

    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return rows / norms


def _encode_chunk(batch: list[str]) -> list[list[float]]:
    session = _ort_session()
    tok = _tokenizer()
    if session is None or tok is None or not batch:
        return []
    try:
        import numpy as np

        encoded = tok.encode_batch(batch)
        seq = max((len(e.ids) for e in encoded), default=1)
        seq = min(max(seq, 1), MAX_LENGTH)
        rows_ids = []
        rows_mask = []
        for e in encoded:
            ids = list(e.ids)[:seq]
            mask = list(e.attention_mask)[:seq]
            pad = seq - len(ids)
            rows_ids.append(ids + [0] * pad)
            rows_mask.append(mask + [0] * pad)
        input_ids = np.array(rows_ids, dtype=np.int64)
        attention_mask = np.array(rows_mask, dtype=np.int64)
        feeds: dict[str, Any] = {}
        for inp in session.get_inputs():
            name = inp.name
            if "mask" in name.lower():
                feeds[name] = attention_mask
            elif "type" in name.lower():
                feeds[name] = np.zeros_like(input_ids)
            else:
                feeds[name] = input_ids
        outputs = session.run(None, feeds)
        hidden = outputs[0]
        if hidden.ndim == 3:
            pooled = _mean_pool(hidden, attention_mask)
        else:
            pooled = hidden
        pooled = _l2_normalize(np.asarray(pooled, dtype="float32"))
        return [[float(x) for x in row] for row in pooled]
    except Exception:
        return []


def _encode_texts(texts: Sequence[str]) -> list[list[float]]:
    """ORT forward in small batches so a 1.6 GB Hermes box cannot OOM a 256-card dump."""
    batch = [str(t or "") for t in texts]
    if not batch:
        return []
    out: list[list[float]] = []
    for i in range(0, len(batch), ENCODE_BATCH):
        chunk = _encode_chunk(batch[i : i + ENCODE_BATCH])
        if len(chunk) != len(batch[i : i + ENCODE_BATCH]):
            return []
        out.extend(chunk)
    return out


def _passage_text(rec: BlockRecord) -> str:
    bits = [
        str(rec.item_type or "").strip(),
        str(rec.entity or "").strip(),
        str(rec.body or "").strip(),
    ]
    return " ".join(b for b in bits if b)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = sum(float(a[i]) * float(a[i]) for i in range(n)) ** 0.5
    nb = sum(float(b[i]) * float(b[i]) for i in range(n)) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rerank_embed(
    query: str,
    records: Sequence[BlockRecord] | None = None,
    k: int = 8,
    *_a,
    **_k,
) -> list | str:
    """Cosine-rerank live cards; drop int8 pile-up tails so the LLM never sees a 0.82 sibling of a 0.83 hit.

    COSINE_FLOOR is only a prefilter. A flat top-2 gap below COSINE_GAP keeps rank 1;
    a real peak keeps through the first cliff, still fail-open [] so L1 can run.
    """
    q = str(query or "").strip()
    live = list(records or [])
    if _k.get("live") is not None and not live:
        live = list(_k["live"] or [])
    if not q or not live:
        return []
    if classify_daily_id(q) or classify_weekly_id(q) or q.startswith("mem-"):
        return []
    try:
        ordered = sorted(live, key=lambda r: str(r.day or ""), reverse=True)
        pool = ordered[:CANDIDATE_CAP]
        passages = [_passage_text(rec) for rec in pool]
        vectors = _encode_texts([q, *passages])
        if (os.environ.get("MYMEMORY_EMBED_UNLOAD") or "").strip() == "1":
            _drop_session()
        if len(vectors) != 1 + len(pool) or not vectors[0]:
            return []
        qv = vectors[0]
        scored: list[tuple[float, BlockRecord]] = []
        for rec, vec in zip(pool, vectors[1:]):
            score = _cosine(qv, vec)
            if score >= COSINE_FLOOR:
                scored.append((score, rec))
        scored.sort(key=lambda row: row[0], reverse=True)
        cap = max(1, int(k))
        if len(scored) >= 2 and (scored[0][0] - scored[1][0]) < COSINE_GAP:
            top = scored[:1]
        else:
            top1 = scored[0][0] if scored else 0.0
            top = []
            for row in scored:
                if len(top) >= cap:
                    break
                top.append(row)
                if top1 - row[0] >= COSINE_GAP:
                    break
        if not top:
            return []
        lines = [f"## Memory / recall  channel=embed  q={q}"]
        for rank, (score, rec) in enumerate(top, start=1):
            snippet = one_line(rec.body, 100)
            lines.append(
                f"- rank={rank}  cosine={score:.2f}  {rec.block_id}  "
                f"{rec.day} {iso_week(rec.day)}"
            )
            lines.append(f"  entity: {rec.entity}")
            lines.append(f"  one-line: {snippet}")
        return "\n".join(lines)
    except Exception:
        return []


def export_gte_int8(dest: Path | None = None) -> Path:
    """Mac-only: download GTE, write QInt8 ONNX + tokenizer next to it.

    Optimum cannot export Alibaba ``new`` architecture; torch.onnx + ORT
    dynamic QInt8 is the path that actually produces a Hermes-loadable file.
    """
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from sentence_transformers import SentenceTransformer

    cache = dest or _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    st_model = SentenceTransformer(
        DEFAULT_MODEL, trust_remote_code=True, device="cpu"
    )
    save_tok = getattr(st_model, "tokenizer", None)
    if save_tok is not None:
        save_tok.save_pretrained(cache)
    else:
        raise RuntimeError("GTE tokenizer missing; cannot export ONNX")
    inner = st_model[0].auto_model
    inner.to("cpu")
    inner.eval()

    class _Hidden(torch.nn.Module):
        def __init__(self, core):
            super().__init__()
            self.core = core

        def forward(self, input_ids, attention_mask):
            out = self.core(input_ids=input_ids, attention_mask=attention_mask)
            return out.last_hidden_state

    wrapped = _Hidden(inner)
    dummy_ids = save_tok(
        "hello world for onnx export",
        return_tensors="pt",
        padding="max_length",
        truncation=True,
        max_length=MAX_LENGTH,
    )
    input_ids = dummy_ids["input_ids"].cpu()
    attention_mask = dummy_ids["attention_mask"].cpu()
    fp32 = cache / "model_fp32.onnx"
    torch.onnx.export(
        wrapped,
        (input_ids, attention_mask),
        str(fp32),
        input_names=["input_ids", "attention_mask"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch", 1: "seq"},
            "attention_mask": {0: "batch", 1: "seq"},
            "last_hidden_state": {0: "batch", 1: "seq"},
        },
        opset_version=17,
        dynamo=False,
    )
    dest_int8 = cache / "model_int8.onnx"
    quantize_dynamic(str(fp32), str(dest_int8), weight_type=QuantType.QInt8)
    if dest_int8.is_file():
        return dest_int8
    raise FileNotFoundError(f"int8 ONNX was not written under {cache}")
