from __future__ import annotations

from pathlib import Path

import tiktoken

from recall.ids import load_blocks


def test_layer_compression_w33(staging):
    enc = tiktoken.get_encoding("o200k_base")
    weekly = (staging / "weekly" / "2026-W33.md").read_text(encoding="utf-8")
    l3 = len(enc.encode(weekly))
    l2_text = []
    n2 = 0
    for rec in load_blocks(staging):
        if rec.day < "2026-08-10" or rec.day > "2026-08-16":
            continue
        n2 += 1
        l2_text.append(rec.path.read_text(encoding="utf-8"))
    # unique files
    files = {
        rec.path
        for rec in load_blocks(staging)
        if "2026-08-10" <= rec.day <= "2026-08-16"
    }
    l2 = len(enc.encode("".join(p.read_text(encoding="utf-8") for p in sorted(files))))
    n3 = weekly.count("mem-")  # rough node stand-in via legend ids
    legend_n = weekly.split("legend:", 1)[-1].split("cross-day-thread", 1)[0].count("mem-")
    assert legend_n <= n2
    assert l3 <= 6554 or l3 <= 0.51 * l2
