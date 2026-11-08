## MyMemory 1.1.32

### Digest clock and nightly

- Nightly leftover and day wrap-up move from **23:55** to **23:59:59** so a minute-only grid entry cannot close the civil day before extract runs; phase-2 grid logic treats the nightly slot at second 59.
- Recover stale `phase2_in_flight_at` on civil ticks (missing timestamp or older than the stale window) so a crashed merge cannot block every later tick.
- `/digest update`, `/weekly update`, and `/monthly update` return their outcomes on the slash reply again instead of going silent.

### Runtime packages

- `requirements.txt` is the gateway runtime list: `onnxruntime`, `tokenizers`, `numpy`, `pyyaml`, `openai`, `ruamel.yaml`, and `sentence-transformers`, with the CPU PyTorch index so the cloud host does not pull a CUDA wheel.
- The installer and the cloud push install that file into the Python environment the running gateway loads (`installs/.../environments/.../venv`), not the older 3.11 virtualenv. Phase-2 Pearson/MI needs `sentence-transformers` there. The 01:00 ONNX cache needs `onnxruntime`, `tokenizers`, and `numpy` there. No version bump.
- A gateway start after `hermes update` installs this file into the new environment when `yaml`, `onnxruntime`, `tokenizers`, `numpy`, or `sentence_transformers` cannot be imported, before digest loads.

### Install and publish hygiene

- Installer and GitHub publish rsync exclude `.claude/`, `.test-hermes-home/`, and `probe_*.py`; post-install removes retired W38 harness files, `monthly/test_monthly_eval.py`, the dated live-eval script `weekly/scripts/eval_weekly_threads_mimo.py`, and any `weekly/debug_*.py` left on the gateway.
- Cloud push excludes those harness names and deletes them on the host after sync so rsync cannot leave stale one-off tests beside the supported suite.
- `test_runtime_deps.py` covers gateway-start `ensure_runtime_deps()` without running pip during normal pytest collection.

### Worker config (since 1.1.31)

- When digest, weekly, or monthly worker lanes are missing from an existing config, restore them from `~/.hermes/.mymemory-worker-pin.yaml` on load (unchanged from 1.1.31; included in this snapshot).
