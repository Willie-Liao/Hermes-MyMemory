"""Chat display helpers for weekly Brief bodies."""

from __future__ import annotations

import re

# Legacy theme titles (older ### theme Briefs) + four-part section titles.
BRIEF_THEME_TITLES: tuple[str, ...] = (
    "Events",
    "Hypothesis",
    "Conflict",
    "Procedure",
)

_ANY_ATX_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
_BARE_THEME_RE = re.compile(
    r"^(Events|Hypothesis|Conflict|Procedure)\s*$",
    re.IGNORECASE,
)


def format_brief_for_chat(brief: str) -> str:
    """Strip theme heading hashes for chat display; keep body/cites intact."""
    theme_cf = {t.casefold(): t for t in BRIEF_THEME_TITLES}
    out: list[str] = []
    for line in (brief or "").splitlines():
        stripped = line.strip()
        m_atx = _ANY_ATX_RE.match(stripped)
        if m_atx:
            rest = m_atx.group(2).strip()
            canonical = theme_cf.get(rest.casefold())
            if canonical is not None:
                out.append(canonical)
                continue
        m_bare = _BARE_THEME_RE.match(stripped)
        if m_bare:
            canonical = theme_cf.get(m_bare.group(1).casefold())
            if canonical is not None:
                out.append(canonical)
                continue
        out.append(line)
    return "\n".join(out)
