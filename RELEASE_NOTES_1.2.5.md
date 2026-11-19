## MyMemory 1.2.5

A recalled card used to stay as strong as the day it was first seen, and only the first id in the recall text received that credit. Monthly guidance could also fill with decisions and leave procedures out. A Chinese denial such as 过时 or 不对 never counted as a correction.

- Strength now decays from the last full recall. Older uses are approximated from `first_seen`, so a card that stops being used gets weaker without storing every recall date. A missing `last_recall_at` still decays from `first_seen`, which keeps archive behavior for cards that were never stamped.
- Every recalled seed gets a full credit. A related card shown beside it gets a smaller credit that raises `recall_n` and does not move `last_recall_at`. The same stamp writes a daily card or a monthly key row.
- Regenerating a month keeps `recall_n`, `last_recall_at`, and `first_seen` on key rows that survive, and recomputes strength from those fields.
- Monthly guidance keeps room for both decisions and procedures. Each kind gets up to half the slots, and a kind with fewer than two passing rows is topped up. Ranking uses live strength, and the line the agent sees prints that live value. Ids shown in guidance are stamped after the turn, off the request thread.
- Digest treats 过时, 不对, 错了, and the matching traditional phrases as a user correction. English keywords stay word-bounded.
