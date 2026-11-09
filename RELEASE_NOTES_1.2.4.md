## MyMemory 1.2.4

A month that already has last month's portrait, but no supersedes pair, no longer stays pending on `user_image`.

November 2026 was that case. The tool schema left `cognition_change` optional and allowed `from` and `to`. The validator did the opposite: an empty list was a gap, and a row that carried `from` or `to` was rejected with `must omit from and to`. Either shape sent `user_image` back to the worker. The other six sections could pass and stay on disk.

- When `facts.supersedes_pairs` is empty, `normalize_synthesis_args` drops `from` and `to` and records `cognition_change[i] from/to dropped (no supersedes pair)`. The sentence and its evidence stay.
- An empty `cognition_change` is not a gap. The month file gets `cognition_empty_reason: no cognition change found yet`. A real supersedes pair is unchanged: the row must still copy that pair.
- A stored reduce resume is rechecked before the next model call. If the only stored gap was the old cognition rule, the month finishes with no further reduce call.
- The reduce prompt and the `cognition_change` tool description now say the same thing: write text plus evidence only when the understanding changed, and never send `from` or `to` unless the prompt lists a pair.
- `RELEASE_NOTES_1.1.37.md` is removed. `install.sh` deletes it on the gateway because rsync does not.
