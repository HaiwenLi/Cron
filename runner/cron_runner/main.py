# runner/cron_runner/main.py
# sys.path bootstrap: the schtasks Action (Task 8/9 contract) invokes this file
# script-style (<python> "<CRON_HOME>/runner/cron_runner/main.py" ...), which
# puts runner/cron_runner/ -- not runner/ -- on sys.path; without this the
# cron_runner imports below crash at import time.
import sys as _sys
from pathlib import Path as _Path
_SYS_PATH_BOOTSTRAP = str(_Path(__file__).resolve().parents[1])
if _SYS_PATH_BOOTSTRAP not in _sys.path:
    _sys.path.insert(0, _SYS_PATH_BOOTSTRAP)
import argparse, json, os, sys
from datetime import datetime
from pathlib import Path

from cron_runner.config import load_config
from cron_runner.due import next_run
from cron_runner.history import new_run_dir, write_meta, latest_run, cleanup
from cron_runner.locks import (acquire_task_lock, register_active,
                               active_weight, unregister)
from cron_runner.executors import get_builder
from cron_runner.executors.opencode import parse_output
from cron_runner.tasks import load_tasks, validate_task

def _find(tasks, task_id):
    for t in tasks:
        if t.id == task_id:
            return t
    names = ", ".join(t.id for t in tasks) or "(none)"
    print(f"unknown task {task_id!r}; known: {names}")
    return None

def cmd_run(task_id: str, cron_home: Path) -> int:
    cfg = load_config(cron_home)
    tasks = load_tasks(cron_home)
    task = _find(tasks, task_id)
    if task is None:
        return 2
    errs = validate_task(task, cfg)
    if errs:
        print(f"task {task_id} invalid:\n  - " + "\n  - ".join(errs))
        return 2
    if not task.enabled:
        print(f"task {task_id} is disabled; nothing to do")
        return 0
    run_dir = new_run_dir(cron_home / "runs", task.id)
    started = datetime.now().isoformat(timespec="seconds")
    lock = acquire_task_lock(cron_home / f"state/locks/{task.id}.lock",
                             task.id, task.timeout)
    if not lock.acquired:
        write_meta(run_dir, {"task_id": task.id, "status": "skipped_lock",
                             "exit_code": None, "duration_sec": 0,
                             "executor": task.executor, "model": None,
                             "permission": task.permission, "missed": False,
                             "started_at": started})
        print(f"skip: task {task.id} already running ({lock.reason})")
        return 0
    if active_weight(cron_home / "state/active") + task.weight > cfg.max_concurrent:
        write_meta(run_dir, {"task_id": task.id, "status": "skipped_busy",
                             "exit_code": None, "duration_sec": 0,
                             "executor": task.executor, "model": None,
                             "permission": task.permission, "missed": False,
                             "started_at": started})
        print(f"skip: concurrency cap {cfg.max_concurrent} reached")
        return 0
    variables = dict(cfg.variables)
    variables.setdefault("WORKSPACE", "")
    variables["RUN_DIR"] = str(run_dir)
    if not variables.get("WORKSPACE"):
        print("warn: WORKSPACE empty, falling back to CRON_HOME")
        variables["WORKSPACE"] = variables["CRON_HOME"]
    token = register_active(cron_home / "state/active", task.id, task.weight)
    try:
        try:
            builder = get_builder(task.executor)
            spec = builder(task, task.steps[0], variables)
            env = {**os.environ, **variables}
            result = execute_wrapper(spec, run_dir, task, env, variables)
        except Exception as e:
            result = {"exit_code": None, "duration_sec": 0.0,
                      "status": "failed"}
            with (run_dir / "stderr.log").open("a", encoding="utf-8",
                                               errors="replace") as f:
                f.write(f"executor error: {e!r}\n")
    finally:
        unregister(token)
    write_meta(run_dir, {"task_id": task.id, "status": result["status"],
                         "exit_code": result["exit_code"],
                         "duration_sec": result["duration_sec"],
                         "executor": task.executor,
                         "model": result.get("model"),
                         "permission": task.permission, "missed": False,
                         "started_at": started})
    cleanup(cron_home / "runs", cfg.retention_days)
    return 0 if result["status"] == "success" else 1

def execute_wrapper(spec, run_dir, task, env, variables):
    from cron_runner.executors.base import execute
    result = execute(spec, run_dir, task.timeout, env=env)
    if task.executor == "opencode":
        raw = (run_dir / "stdout.log").read_text(encoding="utf-8",
                                                 errors="replace")
        parsed = parse_output(raw)
        result["model"] = parsed.get("model")
        (run_dir / "summary.txt").write_text(parsed.get("text", ""),
                                             encoding="utf-8")
    return result

def cmd_list(cron_home: Path, fmt: str = "table") -> None:
    cfg = load_config(cron_home)
    tasks = load_tasks(cron_home)
    now = datetime.now()
    rows = []
    for t in tasks:
        last = latest_run(cron_home / "runs", t.id) or {}
        # 坏 schedule 不许炸掉整个 list(croniter 抛 CroniterBadCronError);
        # 宽 catch:不 import croniter 内部名。失败行照常显示,next_run 记 "-"。
        try:
            nxt = next_run(t.schedule, now).strftime("%Y-%m-%d %H:%M") \
                if t.enabled else "-"
        except Exception:
            nxt = "-"
        rows.append({
            "id": t.id, "schedule": t.schedule, "enabled": t.enabled,
            "last_status": last.get("status", "-"),
            "last_time": last.get("finished_at", "-"),
            "next_run": nxt,
        })
    if fmt == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for r in rows:
        print(f"{r['id']:<20} {r['schedule']:<14} "
              f"{'on' if r['enabled'] else 'OFF':<4} "
              f"{r['last_status']:<13} {r['last_time']:<20} {r['next_run']}")

def cmd_validate(cron_home: Path) -> int:
    cfg = load_config(cron_home)
    bad = 0
    for t in load_tasks(cron_home):
        errs = validate_task(t, cfg)
        if errs:
            bad += 1
            print(f"✗ {t.id}:\n  - " + "\n  - ".join(errs))
        else:
            print(f"✓ {t.id}")
    return 1 if bad else 0

def _force_utf8_stdio():
    # 偏差记录(见 task-8-report):GBK 控制台/管道(本机 Windows 默认)无法编码
    # cmd_validate 的 ✓/✗ → UnicodeEncodeError 崩溃,违背退出码契约。
    # 入口处重配置 stdio 为 UTF-8:输出字节与编排顺序完全不变。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass

def main(argv=None):
    ap = argparse.ArgumentParser(prog="cron_runner")
    _force_utf8_stdio()

    ap.add_argument("--home", default=None,
                    help="cron repo root (default: parent of this package)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run"); p_run.add_argument("task_id")
    p_list = sub.add_parser("list")
    p_list.add_argument("--format", default="table", choices=["table", "json"])
    sub.add_parser("validate"); sub.add_parser("sync")
    args = ap.parse_args(argv)
    home = Path(args.home) if args.home else Path(__file__).resolve().parents[2]
    if args.cmd == "run":
        sys.exit(cmd_run(args.task_id, home))
    if args.cmd == "list":
        cmd_list(home, args.format); sys.exit(0)
    if args.cmd == "validate":
        sys.exit(cmd_validate(home))
    if args.cmd == "sync":
        # 函数内 import:免去非 sync 调用的启动开销
        from cron_runner.scheduler_win import cmd_sync
        sys.exit(cmd_sync(home))

if __name__ == "__main__":
    main()
