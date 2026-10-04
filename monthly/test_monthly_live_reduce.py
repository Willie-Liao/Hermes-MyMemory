"""One live MiniMax-M2.5 reduce after the judgment schema is on disk.

The installer ignores this file. REAL_LLM_TEST=0 skips it so a unit run cannot
spend a model call.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


def _report(usage: dict, wrote: bool) -> str:
    lines: list[str] = []
    attempts = list(usage.get("attempts") or [])
    for row in attempts:
        lines.append(f"try {row.get('n')}  {row.get('tool')}")
        teaching = str(row.get("reason") or "").strip()
        lines.append("  teaching: " + (teaching if teaching else "(none)"))
        failed = row.get("failed") or []
        lines.append("  failed: " + (", ".join(failed) if failed else "(none)"))
        retired = row.get("retired") or []
        if retired:
            lines.append("  retired: " + ", ".join(retired))
        fixes = row.get("format_fixes") or []
        if fixes:
            lines.append("  format_fixes: " + "; ".join(fixes) + " (no model)")
        lines.append(
            "  input_tokens / output_tokens / total_tokens: "
            f"{row.get('input_tokens')} / {row.get('output_tokens')} / {row.get('total_tokens')}"
        )
        lines.append(f"  duration_ms: {row.get('duration_ms')}")
    lines.append("overall")
    lines.append(
        "  input_tokens / output_tokens / total_tokens: "
        f"{usage.get('input_tokens')} / {usage.get('output_tokens')} / {usage.get('total_tokens')}"
    )
    latency = sum(int(row.get("duration_ms") or 0) for row in attempts)
    lines.append(f"  latency_ms: {latency}")
    lines.append(f"  write_month: {'yes' if wrote else 'no'}")
    return "\n".join(lines)


def _load_env_file(path: Path) -> None:
    """Put worker keys in the process when hermes_cli's loader is not on this path.

    The oneshot reads os.environ. A copied .env that never gets parsed looks like
    a transport failure and burns the three reduce tries with zero tokens.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        row = line.strip()
        if not row or row.startswith("#") or "=" not in row:
            continue
        key, _, val = row.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def test_live_reduce_records_steps(tmp_path: Path) -> None:
    """Call generate_month for real so a section retry shows why it was sent back."""
    flag = os.environ.get("REAL_LLM_TEST", "0").strip().lower()
    if flag in {"", "0", "false", "no"}:
        pytest.skip("skipped=true tokens=0")
    home = Path(__file__).resolve().parents[3]
    for name in ("config.yaml", ".env"):
        source = home / name
        if source.is_file():
            shutil.copy(source, tmp_path / name)
    _load_env_file(tmp_path / ".env")
    from monthly_actions import generate_month

    result = generate_month("2026-06", reason="live-reduce", force_refresh=True)
    usage = result.get("usage") or {}
    wrote = result.get("outcome") == "ok"
    text = _report(usage, wrote)
    print(text)
    assert "try 1" in text
    assert "overall" in text
    assert usage.get("model") == "MiniMax-M2.5"
    assert int(usage.get("total_tokens") or 0) > 0
    assert usage.get("attempts")
