#!/usr/bin/env python3
"""One-shot test script to measure per-stage timing of MyMemory.prefetch()"""
import sys
import time

sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

from pathlib import Path

# Set up a minimal Hermes home for the test
_test_hermes_home = Path("/root/.hermes")
_query = "remind me about the project meeting"

print(f"Query: {_query}")
print("=" * 60)

# Simulate what prefetch() does, stage by stage
# digest and weekly are submodules; digests/digest.py, weekly/weekly.py
from digest.digest import on_pre_llm_call as digest_on_pre_llm_call, get_hermes_home
from weekly.weekly import on_pre_llm_call as weekly_on_pre_llm_call
from recall.tools import _MEM_ID_RE, recall_memory

sid = "test-session-001"
first = True

# Stage 1: digest.on_pre_llm_call
t0 = time.perf_counter()
digest_hit = digest_on_pre_llm_call(
    user_message=_query,
    is_first_turn=first,
    session_id=sid,
    platform="test",
)
t_digest = time.perf_counter() - t0
print(f"  digest.on_pre_llm_call : {t_digest:.4f}s ({t_digest*1000:.2f}ms)")

# Stage 2: weekly.on_pre_llm_call
t0 = time.perf_counter()
weekly_hit = weekly_on_pre_llm_call(
    user_message=_query,
    is_first_turn=first,
    session_id=sid,
    platform="test",
)
t_weekly = time.perf_counter() - t0
print(f"  weekly.on_pre_llm_call : {t_weekly:.4f}s ({t_weekly*1000:.2f}ms)")

# Assemble parts (as prefetch does)
parts = []
for hit in (digest_hit, weekly_hit):
    if isinstance(hit, dict):
        text = str(hit.get("context") or "").strip()
        if text:
            parts.append(text)

q = str(_query or "").strip()

# Stage 3: recall_memory
t0 = time.perf_counter()
guided_result = None
if q and not _MEM_ID_RE.search(q):
    try:
        root = Path(get_hermes_home()) / "memories" / "staging"
        guided_result = recall_memory(q, staging=root, mode="guidance")
        head = (guided_result or "").split("\n", 1)[0]
        if guided_result.strip() and "channel=miss" not in head:
            parts.append(guided_result.strip())
    except Exception as e:
        guided_result = None
t_recall = time.perf_counter() - t0
print(f"  recall_memory          : {t_recall:.4f}s ({t_recall*1000:.2f}ms)")

# Total
total = t_digest + t_weekly + t_recall
print("-" * 60)
print(f"  TOTAL (3 stages)      : {total:.4f}s ({total*1000:.2f}ms)")
print(f"  Output length         : {len(parts)} part(s), {sum(len(p) for p in parts)} chars")
if guided_result:
    print(f"  recall_memory result  : {guided_result[:120]!r}...")
