# OpenCode Cron

Scheduled tasks for agent CLIs — **opencode** now, kimi/pi next. Tasks are
small YAML files; a Python runner executes them on a Windows `schtasks`
schedule, with per-run logs, history, and lock/concurrency safety.

## Quickstart

1. `pip install -r requirements.txt`
2. Copy `config.example.yaml` to `config.local.yaml` (gitignored) and fill in
   `WORKSPACE` for this machine
3. `python runner/cron_runner/main.py sync`

## Commands

| Command | What it does |
|---|---|
| `run <task_id>` | Execute one task now; logs under `runs/<task_id>/` |
| `list` | Show all tasks: schedule, enabled, last status, next run |
| `validate` | Check every task YAML against the schema; exit 1 on any error |
| `sync` | Register/unregister Windows `schtasks` entries to match `tasks.d/` |

## OpenCode commands

- `/cron-list` — render the runner `list` table
- `/cron-run <task_id>` — execute a task and report exit code + log paths

## Example task

See [`tasks.d/example-echo.yaml`](tasks.d/example-echo.yaml) (disabled by
default; enable temporarily for acceptance):

```yaml
id: example-echo
name: Echo smoke test (safe, no-op)
enabled: false            # enable temporarily for acceptance, disable after
schedule: "*/5 * * * *"
miss_policy: coalesce
executor: script
steps:
  - name: echo
    type: script
    run: ${PYTHON} -c "print('cron ok')"
```

Use `${WORKSPACE}`, `${CRON_HOME}`, `${RUN_DIR}`, and `${PYTHON}` instead of
absolute paths in task files — real paths belong in `config.local.yaml`
(`WORKSPACE`) so they never get committed. If `WORKSPACE` is empty at run
time, the runner falls back to `CRON_HOME`.

## Docs

- [Design spec](docs/spec/2026-10-26-opencode-cron-design.md)
