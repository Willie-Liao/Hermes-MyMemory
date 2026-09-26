#!/usr/bin/env python3
"""Stamp user_message_at / assistant_response_at / generated_at on daily staging files.

Reads Hermes state.db message timestamps for cited session#start-end windows.
When the row is missing, uses civil noon on that file's date. No LLM.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def _plugin_home() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_digest():
    plugin = Path(__file__).resolve().parent
    sys.path[:0] = [str(plugin.parent), str(plugin)]
    spec = importlib.util.spec_from_file_location(
        "memory_digest_clock_backfill", plugin / "digest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    home = Path(os.environ.get("HERMES_HOME") or _plugin_home())
    os.environ.setdefault("HERMES_HOME", str(home))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--daily-dir",
        type=Path,
        default=home / "memories" / "staging" / "daily",
        help="Directory of YYYY-MM-DD.md daily staging files",
    )
    args = parser.parse_args(argv)
    digest = _load_digest()
    stats = digest.backfill_daily_dir_clocks(args.daily_dir)
    print(
        f"stamped clocks files={stats['files']} cards={stats['cards']} "
        f"dir={args.daily_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
