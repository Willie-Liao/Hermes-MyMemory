from __future__ import annotations

from datetime import date

from recall.tools import render_bands

_BUDGET = 4000


def test_week_band_no_truncated_brief(staging):
    text = render_bands(staging, today=date(2026, 8, 18))
    assert "## Memory / weeks" in text
    assert "Nevt" not in text
    week_headers = [
        line for line in text.splitlines() if line.startswith("### 2026-W")
    ]
    assert 1 <= len(week_headers) <= 4
    assert "### 2026-W32  2026-08-03..2026-08-09  f=2026-W32.md" in text
    assert "### 2026-W33  2026-08-10..2026-08-16  f=2026-W33.md" in text
    assert "First W32 bullet for injection. (Monday)" in text
    assert "Second W32 bullet also injected. (Tuesday)" in text
    assert "Memory Digest semicolon fix. (Wednesday)" in text
    assert "Second W33 summary stays in prefetch. (Thursday)" in text
    w32 = text.split("### 2026-W32", 1)[1].split("### 2026-W33", 1)[0]
    assert "entities: gitnexus, Memory Digest" in w32
    w33 = text.split("### 2026-W33", 1)[1]
    month = w33.find("## Month")
    w33_body = w33 if month < 0 else w33[:month]
    assert "entities:" not in w33_body
    for line in text.splitlines():
        if line.startswith("- ") and "…" in line:
            raise AssertionError(line)


def test_injection_budget_and_byte_stable(staging):
    import tiktoken

    a = render_bands(staging, today=date(2026, 8, 18))
    b = render_bands(staging, today=date(2026, 8, 18))
    assert a == b
    enc = tiktoken.get_encoding("o200k_base")
    n = len(enc.encode(a))
    assert n <= _BUDGET, n
    assert "memorydigest" in a
    assert a.count("\n- memorydigest ") <= 1 or "\n- memorydigest (" in a


def test_band_d_month_summaries_when_month_file_exists(staging):
    monthly = staging / "monthly" / "2026-08.md"
    monthly.write_text(
        monthly.read_text(encoding="utf-8")
        + "summary:\n"
        + "  - text: Shipped weekly review retries.\n"
        + "    weeks: [2026-W33]\n"
        + "  - text: Qixi card from drafting to sharing\n"
        + "    weeks: [2026-W34, 2026-W35]\n",
        encoding="utf-8",
    )
    out = render_bands(staging, today=date(2026, 8, 18))
    assert "## Month summaries" in out
    assert "### 2026-08  2026-08-01..2026-08-31" in out
    assert "- Shipped weekly review retries. (2026-W33)" in out
    assert "- Qixi card from drafting to sharing (2026-W34, 2026-W35)" in out
    assert "2026-08  2026-08-01..2026-08-31:" not in out
    import tiktoken

    n = len(tiktoken.get_encoding("o200k_base").encode(out))
    assert n <= _BUDGET, n
