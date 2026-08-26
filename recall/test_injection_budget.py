from __future__ import annotations

from datetime import date

from recall.tools import render_bands


def test_week_band_no_truncated_brief(staging):
    text = render_bands(staging, today=date(2026, 8, 18))
    assert "## Memory / weeks" in text
    for line in text.splitlines():
        if not line.startswith("- 2026-W"):
            continue
        if "stub" in line.casefold():
            continue
        assert "(no brief)" not in line
        assert "…" not in line.split("·")[0]


def test_injection_budget_and_byte_stable(staging):
    import tiktoken

    a = render_bands(staging, today=date(2026, 8, 18))
    b = render_bands(staging, today=date(2026, 8, 18))
    assert a == b
    enc = tiktoken.get_encoding("o200k_base")
    n = len(enc.encode(a))
    assert n <= 1500, n
    assert "memorydigest" in a
    assert a.count("\n- memorydigest ") <= 1 or "\n- memorydigest (" in a


def test_band_d_month_summaries_when_month_file_exists(staging):
    monthly = staging / "monthly" / "2026-08.md"
    monthly.write_text(
        monthly.read_text(encoding="utf-8") + "summary: Shipped weekly review retries.\n",
        encoding="utf-8",
    )
    out = render_bands(staging, today=date(2026, 8, 18))
    assert "## Month summaries" in out
    assert "2026-08:" in out
    import tiktoken

    n = len(tiktoken.get_encoding("o200k_base").encode(out))
    assert n <= 1500, n
