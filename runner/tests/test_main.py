import json
import os
from pathlib import Path
from cron_runner import main as m

ECHO_TASK = """id: echo
name: echo
schedule: "*/5 * * * *"
executor: script
steps:
  - name: s
    type: script
    run: echo cron-ok
"""

def _setup(cron_home, task_text=ECHO_TASK):
    (cron_home / "tasks.d/echo.yaml").write_text(task_text, encoding="utf-8")
    (cron_home / "config.yaml").write_text(
        "variables:\n  WORKSPACE: '%s'\n" % str(cron_home), encoding="utf-8")

def test_run_success_end_to_end(cron_home, capsys):
    _setup(cron_home)
    code = m.cmd_run("echo", cron_home)
    assert code == 0
    runs = sorted((cron_home / "runs/echo").iterdir())
    assert len(runs) == 1
    assert "cron-ok" in (runs[0] / "stdout.log").read_text(encoding="utf-8")
    meta = json.loads((runs[0] / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success" and meta["executor"] == "script"
    assert list((cron_home / "state/active").glob("*.json")) == []

def test_run_unknown_task(cron_home):
    assert m.cmd_run("nope", cron_home) == 2

def test_run_locked_task_records_skip(cron_home, monkeypatch):
    from cron_runner import locks
    _setup(cron_home)
    lock = cron_home / "state/locks/echo.lock"
    lock.write_text(json.dumps({"pid": 999999, "boot_id": locks.boot_id(),
                                "task_id": "echo",
                                "started_at": 10**10}), encoding="utf-8")
    monkeypatch.setattr(locks, "pid_alive", lambda pid: True)
    code = m.cmd_run("echo", cron_home)
    assert code == 0
    meta = json.loads(
        (sorted((cron_home / "runs/echo").iterdir())[-1] / "meta.json")
        .read_text(encoding="utf-8"))
    assert meta["status"] == "skipped_lock"

def test_list_shows_task(cron_home, capsys):
    _setup(cron_home)
    m.cmd_list(cron_home, fmt="table")
    out = capsys.readouterr().out
    assert "echo" in out and "*/5" in out

def test_list_survives_invalid_schedule(cron_home, capsys):
    _setup(cron_home)
    (cron_home / "tasks.d/bad.yaml").write_text(
        "id: bad\nname: B\nschedule: 'not cron'\nexecutor: script\n"
        "steps:\n  - name: s\n    type: script\n    run: echo x\n",
        encoding="utf-8")
    m.cmd_list(cron_home, fmt="table")  # must not raise
    out = capsys.readouterr().out
    assert "echo" in out and "bad" in out

def test_validate_rejects_unimplemented_executor(cron_home, capsys):
    _setup(cron_home, ECHO_TASK.replace("executor: script", "executor: kimi"))
    assert m.cmd_validate(cron_home) == 1
    out = capsys.readouterr().out
    assert "not implemented" in out and "kimi" in out

def test_run_rejects_unimplemented_executor(cron_home, capsys):
    _setup(cron_home, ECHO_TASK.replace("executor: script", "executor: kimi"))
    assert m.cmd_run("echo", cron_home) == 2
    assert "not implemented" in capsys.readouterr().out

def test_run_records_executor_crash(cron_home, monkeypatch):
    _setup(cron_home)
    def boom(*a, **k):
        raise RuntimeError("builder exploded")
    monkeypatch.setattr(m, "execute_wrapper", boom)
    code = m.cmd_run("echo", cron_home)
    assert code == 1
    meta = json.loads((sorted((cron_home / "runs/echo").iterdir())[-1]
                       / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed" and meta["exit_code"] is None
    assert "builder exploded" in (sorted((cron_home / "runs/echo").iterdir())[-1]
                                  / "stderr.log").read_text(encoding="utf-8")

def test_run_empty_workspace_falls_back(cron_home, capsys):
    _setup(cron_home)
    (cron_home / "config.yaml").write_text(
        "variables:\n  WORKSPACE: ''\n", encoding="utf-8")
    code = m.cmd_run("echo", cron_home)
    assert code == 0
    assert "falling back" in capsys.readouterr().out

import subprocess, sys as _sys
from pathlib import Path as _Path

def test_script_style_invocation_works(cron_home):
    _setup(cron_home)
    repo_root = _Path(__file__).resolve().parents[2]
    r = subprocess.run(
        [_sys.executable, str(repo_root / "runner/cron_runner/main.py"),
         "--home", str(cron_home), "list"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": ""})
    assert r.returncode == 0, r.stderr
    assert "echo" in r.stdout
