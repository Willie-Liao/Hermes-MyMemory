"""CLI for ``hermes MyMemory digest|weekly`` — same handlers as in-chat /digest /weekly."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_plugins_root = Path(__file__).resolve().parent.parent
if str(_plugins_root) not in sys.path:
    sys.path.insert(0, str(_plugins_root))

from .digest import slash as digest_slash
from .weekly import slash as weekly_slash


def register_cli(subparser) -> None:
    """Build ``hermes MyMemory digest …`` and ``hermes MyMemory weekly …``."""
    subs = subparser.add_subparsers(dest="mymemory_command")
    digest_p = subs.add_parser(
        "digest",
        help="Force a digest run or manage bookmark / status",
    )
    digest_p.add_argument(
        "tokens",
        nargs=argparse.REMAINDER,
        help="Same tokens as the old /digest command",
    )
    weekly_p = subs.add_parser(
        "weekly",
        help="Weekly memory: ui / update / close / reopen",
    )
    weekly_p.add_argument(
        "tokens",
        nargs=argparse.REMAINDER,
        help="Same tokens as the old /weekly command",
    )


def MyMemory_command(args) -> None:
    """Dispatch ``hermes MyMemory`` to digest or weekly slash handlers.

    Confirmed history runs stay on the CLI thread so the process cannot exit
    while Phase-1/Phase-2 workers are still writing staging.
    """
    cmd = str(getattr(args, "mymemory_command", "") or "")
    tokens = list(getattr(args, "tokens", None) or [])
    if tokens and tokens[0] == "--":
        tokens = tokens[1:]
    raw = " ".join(tokens)
    if cmd == "digest":
        history_sync = bool(tokens) and tokens[0] == "history"
        print(digest_slash.handle_digest(raw, history_sync=history_sync))
        return
    if cmd == "weekly":
        print(weekly_slash.handle_weekly(raw))
        return
    print("Usage: hermes MyMemory digest|weekly [args]")
