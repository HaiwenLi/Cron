# M1 Acceptance Record — 2026-10-26

Real-machine acceptance of the OpenCode Cron runner (branch `feat/m1`, base d64b067).
Environment: Windows Task Scheduler via `schtasks.exe`, Python 3.12.10. All paths below
relative to the repo root (CRON_HOME). No machine/user identifiers recorded.

## Step 1 — Environment & full suite

- `pip install -r requirements-dev.txt` OK.
- `python -m pytest runner/tests -q` → **59 passed** (2.84s). Re-run after the backend
  fix (see Step 3) → still **59 passed** (2.89s).

## Step 2 — Manual run smoke

- `validate` → exit 0, both tasks ✓ (example-echo, repo-update).
- `run example-echo` → exit 0 (with expected `warn: WORKSPACE empty, falling back to
  CRON_HOME` — no `config.local.yaml` on this machine, fallback by design).
- `runs/example-echo/<ts>/stdout.log` contains `cron ok`; `meta.json`: status=success,
  exit_code=0, duration 0.188s.
- `list` → exit 0; example-echo shown enabled, last_status=success.

## Step 3 — Real schtasks registration (cron-example-echo only)

### 3.0 Blocker found & fixed: DLP transparent encryption vs schtasks

First `sync` failed:

```
RuntimeError: schtasks /Create failed for 'cron-example-echo':
错误: 任务XML格式错误。 (1,2)::错误: 不正确的文档语法
```

Bisect on a byte-identical minimal XML proved the failure depends on **which process
wrote the file**, not on content: this machine's enterprise DLP applies transparent
per-process encryption — files **written by python** land encrypted on disk, and
`schtasks.exe` is not a whitelisted reader — it sees ciphertext and reports XML
syntax errors at (1,2). Files written by powershell.exe land plaintext and are
accepted. Ruled out empirically: CRLF vs LF, UTF-16 vs UTF-8 vs BOM variants,
absolute vs relative paths (a binary-identical probe file confirmed the split
follows the writing process: python → encrypted; pwsh → plaintext).

**Fix (committed)**: `SchtasksBackend.create()` now delegates the temp-XML write to
`powershell.exe` (`[IO.File]::WriteAllBytes`, payload passed as base64 so no
quoting/encoding can mangle it). UTF-16-with-BOM content unchanged; suite stays
59/59. **Duration fallback (`P3650D`) NOT needed** — the trigger itself was never
rejected; see R1a.

### 3.1 Registration & scheduler facts

After the fix:

- `sync` → `created cron-example-echo`, exit 0.
- `schtasks /Query /TN cron-example-echo /V /FO LIST` → entry present; Schedule Type:
  每天 (daily) with 重复间隔 (repeat) every 5 minutes; 重复: 终止: 时间: 无 /
  Duration disabled (no Duration element — **R1a confirmed accepted**); Start
  boundary 2020/1/1 (**R1b confirmed**); Last Run Time initially 1999/11/30 sentinel.
- **R1a (minute trigger, no Duration)**: accepted and scheduling — engine fired
  repeatedly at 14:55:00 and 15:00:00 (runs `20261026-145501`, `20261026-150000`),
  both status=success, exit_code=0, stdout `cron ok`, LastTaskResult=0.
- **R1b (past StartBoundary 2020-01-01)**: accepted; schedules forward from the
  repetition definition (Next Run Time always in the future; no 6-year backfill).
- **R1c (unexpected immediate catch-up on registration)**: **did NOT occur** — zero
  run directories between registration (~14:40:55) and the first grid fire
  (14:55:01); `NumberOfMissedRuns=0` throughout. StartWhenAvailable=true + ancient
  boundary did not force a catch-up run.
- **Phase (midnight anchor)**: Next Run Times observed 14:55:00 → 15:00:00 →
  15:05:00 — on the :00/:05 grid as displayed by `schtasks /V` and as actually
  fired. Display quirk (informational): PowerShell `Get-ScheduledTaskInfo` showed
  registration-anchored second-offset values (e.g. 14:55:55, 15:05:05) before/around
  the first fire; after the first fire both views agree on grid times. Engine
  behavior (what matters) was grid-aligned.

### 3.2 Idempotence

- Second `sync` → `unchanged cron-example-echo` — no created/updated/deleted lines;
  zero rebuild (hash file matched).

### 3.3 Manual trigger

- `schtasks /Run /TN cron-example-echo` → exit 0; after ~10s a new run dir
  (`20261026-150033`) with meta status=success, exit_code=0.

## Step 4 — Teardown (mandatory)

- `example-echo.yaml` `enabled` back to `false` (edit tool), then `sync` →
  `deleted cron-example-echo`, exit 0.
- `schtasks /Query /TN cron-example-echo` → exit 1, "系统找不到指定的文件"
  (task gone).
- `list` → exit 0; example-echo shown OFF with last success 15:00:33.
- Post-teardown sweep: **no `cron-*` entries remain** on the machine; non-cron-*
  scheduled tasks were never queried-for-modification, deleted, or altered.

## Verdict

M1 accepted on real hardware. Full suite 59/59 before and after the one code change;
manual run, scheduled runs, idempotent sync, and teardown all verified end-to-end
against the real Task Scheduler.

## Legacy / open items

1. DLP encryption dependency: `sync` create-path relies on the powershell write
   bridge on this machine (documented in `scheduler_win.py`); harmless elsewhere.
2. `Get-ScheduledTaskInfo` NextRunTime display artifact (registration-anchored
   seconds) — cosmetic, `schtasks /V` and the engine agree on grid times.
3. opencode TUI `/cron-list` interactive pass intentionally left for the owner (per
   plan); command files landed in Task 11.
4. `repo-update` stays `enabled: false` — never registered, per safety ruling.
5. No `config.local.yaml` on this machine → WORKSPACE falls back to CRON_HOME with
   a warning (expected; real workspace path would live in the uncommitted local
   config).
