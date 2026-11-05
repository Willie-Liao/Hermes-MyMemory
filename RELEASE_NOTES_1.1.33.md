## MyMemory 1.1.33

### Prefetch and monthly guidance

- **Monthly guidance** ranks D/P from embed-cache vectors when lexical overlap is below the 0.92 gate, encoding the query once instead of one ONNX call per candidate.
- **Embed rerank** scores the current pool from `embed-cache.json` before falling back to live encoding, so a global top-k miss does not re-encode cards that are already cached.
- **`load_edges`** refreshes `edges.jsonl` when daily/weekly cards or `entity_index.json` are newer than the file, avoiding a full edge rebuild on every prefetch turn.

### Gateway runtime

- Detect missing runtime packages with **`importlib.util.find_spec`** only (no torch import at plugin load).
- After installing `requirements.txt` into a new Hermes environment, **request a gateway restart** so the next process can register prefetch within the load timeout.

### Install and publish hygiene

- Rsync excludes and post-install **`purge_retired_mymemory_harnesses`**: W38 debug tests, `test_monthly_eval.py`, `eval_weekly_threads_mimo.py`, `probe_*.py`, and local embed-scale probe JSON.
- Cloud push and GitHub publish use the same retired-harness list.

### Requirements

- **Linux/cloud**: `torch==2.15.0+cpu` from the PyTorch CPU index.
- **macOS**: `torch==2.15.0` from PyPI for local dev installs.
