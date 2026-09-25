"""Cross-window recall: id / entity / FTS / L1 channels plus expand and policy.

Prefetch stays in digest; this package is the on-demand finder and walker so
the prompt never dumps week files.
"""

from .normalize import entity_key

__all__ = ["entity_key"]
