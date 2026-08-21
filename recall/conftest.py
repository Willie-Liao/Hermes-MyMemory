from __future__ import annotations

import sys
from pathlib import Path

import pytest

_MYMEMORY = Path(__file__).resolve().parent.parent
if str(_MYMEMORY) not in sys.path:
    sys.path.insert(0, str(_MYMEMORY))

STAGING = _MYMEMORY.parent.parent / "memories" / "staging"


@pytest.fixture(scope="session")
def staging() -> Path:
    return STAGING
