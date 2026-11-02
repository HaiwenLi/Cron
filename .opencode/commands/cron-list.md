---
description: List scheduled cron tasks (status table)
subtask: false
---
Current scheduled tasks (from the cron runner):

!`python runner/cron_runner/main.py list`

Render this as a markdown table. If any task shows a non-success
last_status, point it out and suggest checking
`runs/<task-id>/` for logs.
