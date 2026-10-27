## MyMemory 1.1.29

### Digest slash

- `/digest` for a civil day runs every user chat that spoke that day, not only the session the gateway bound. A WeChat routing key with no digest row is mapped to the open transcript id.
- `--all`, `--batch`, and a past date still start when that routing key has no transcript id.
- A later session on the same slash day can append. `--all` and a past date still replace the board.

### Token totals

- Digest usage sums keep the ledger row that starts at a newline file-size offset, so a one-call `/digest` reply no longer prints zero tokens.
- A past-date slash adds the day wrap-up bill and its elapsed time onto the same reply. `--all` and `--batch` do not run that wrap-up.

### Weekly update (refresh)

- `/weekly update` returns the write status and token totals. The Chronicle brief stays in the week file instead of being pasted into chat.
- The weekly token line keeps the finished ledger row at a newline offset, so a one-call update no longer prints zero tokens.
- `/weekly update`, `close`, and `reopen` omit the week file path. Weixin was sending that `.md` path as a document attachment.
