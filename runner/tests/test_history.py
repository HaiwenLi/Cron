import json
from datetime import datetime
from cron_runner.history import new_run_dir, write_meta, latest_run, cleanup

def test_new_run_dir_unique_same_second(cron_home):
    now = datetime(2026, 10, 26, 12, 0, 0)
    a = new_run_dir(cron_home / "runs", "t", now)
    b = new_run_dir(cron_home / "runs", "t", now)
    assert a.name == "20261026-120000" and b.name == "20261026-120000-2"
    assert a != b and a.exists() and b.exists()

def test_write_and_latest(cron_home):
    rd = new_run_dir(cron_home / "runs", "t", datetime(2026, 10, 26, 12, 0, 0))
    write_meta(rd, {"task_id": "t", "status": "success", "exit_code": 0})
    assert latest_run(cron_home / "runs", "t")["status"] == "success"

def test_latest_none(cron_home):
    assert latest_run(cron_home / "runs", "nope") is None

def test_cleanup_semantics(cron_home):
    runs = cron_home / "runs"
    old = new_run_dir(runs, "t", datetime(2020, 1, 1, 0, 0, 0))
    new = new_run_dir(runs, "t", datetime(2026, 10, 26, 0, 0, 0))
    only_old = new_run_dir(runs, "dead", datetime(2020, 1, 1, 0, 0, 0))
    import os
    os.utime(old, (datetime(2020, 1, 1).timestamp(),) * 2)
    os.utime(new, (datetime(2026, 10, 26).timestamp(),) * 2)
    os.utime(only_old, (datetime(2020, 1, 1).timestamp(),) * 2)
    cleanup(runs, retention_days=14, now=datetime(2026, 10, 26))
    assert not old.exists()          # 过期且非最新 → 删
    assert new.exists()              # 最新 → 留
    assert only_old.exists()         # dead 的唯一运行=最新 → 留(至少保 1)
