"""Put monthly/ and MyMemory on sys.path so pytest collection matches weekly's flat imports."""

from __future__ import annotations

import sys
from pathlib import Path

_monthly = Path(__file__).resolve().parent
_mymemory = _monthly.parent
_weekly = _mymemory / "weekly"
_digest = _mymemory / "digest"
for path in (_monthly, _mymemory, _weekly, _digest):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)
