## MyMemory 1.2.4.2

A long month no longer has to fit every section under one output cap. One full submit was cut off, and the retries spent the next calls rebuilding the whole month.

- The month is written as four ordered group calls: user image and last-month comparison, key decisions, key procedures, then the remaining sections. Each group has its own cap. Later groups read the JSON the earlier groups wrote.
- Every group call sends the same tool list. Only the forced tool name and the message tail change, so the prompt cache can hit.
- A stored resume still skips those groups. Sections that still lack a judgment are patched afterwards, the same way as before.
- `install.sh` deletes a leftover `.cursor` tree and week-numbered or calendar-dated test files on the gateway. rsync will not remove those if an older install already left them behind.
