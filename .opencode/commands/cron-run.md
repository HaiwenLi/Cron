---
description: Trigger a cron task immediately by id
subtask: false
---
Run the cron task `$1` right now:

1. Execute: `python runner/cron_runner/main.py run $1` (from repo root)
2. Report the exit code and the run directory under `runs/$1/`.
3. If exit code is 0 and output mentions skipped_lock/skipped_busy,
   explain why in one line.
Do not modify task definitions.
