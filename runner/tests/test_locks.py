import json
from pathlib import Path
from cron_runner import locks
from cron_runner.locks import (acquire_task_lock, register_active,
                               active_weight, unregister)

class FakeClock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t

T0 = 1779000000  # 固定基准秒

def _always_alive(pid): return True
def _always_dead(pid): return False

def test_fresh_acquire(cron_home, monkeypatch):
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    r = acquire_task_lock(cron_home / "state/locks/t.lock", "t", 600,
                          now_provider=FakeClock(T0))
    assert r.acquired and r.reason == ""

def test_deny_when_alive(cron_home, monkeypatch):
    p = cron_home / "state/locks/t.lock"
    p.write_text(json.dumps({"pid": 123, "boot_id": 1, "task_id": "t",
                             "started_at": T0}), encoding="utf-8")
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    r = acquire_task_lock(p, "t", 600, pid_checker=_always_alive,
                          now_provider=FakeClock(T0 + 5))
    assert not r.acquired and r.reason == "alive"

def test_steal_dead_pid(cron_home, monkeypatch):
    p = cron_home / "state/locks/t.lock"
    p.write_text(json.dumps({"pid": 123, "boot_id": 1, "task_id": "t",
                             "started_at": T0}), encoding="utf-8")
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    r = acquire_task_lock(p, "t", 600, pid_checker=_always_dead,
                          now_provider=FakeClock(T0 + 5))
    assert r.acquired and r.reason == "stale-dead-pid"

def test_steal_on_boot_change(cron_home, monkeypatch):
    p = cron_home / "state/locks/t.lock"
    p.write_text(json.dumps({"pid": 123, "boot_id": 1, "task_id": "t",
                             "started_at": T0}), encoding="utf-8")
    monkeypatch.setattr(locks, "boot_id", lambda: 999)
    r = acquire_task_lock(p, "t", 600, pid_checker=_always_alive,
                          now_provider=FakeClock(T0 + 5))
    assert r.acquired and r.reason == "stale-boot"

def test_steal_on_age(cron_home, monkeypatch):
    p = cron_home / "state/locks/t.lock"
    p.write_text(json.dumps({"pid": 123, "boot_id": 1, "task_id": "t",
                             "started_at": T0}), encoding="utf-8")
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    r = acquire_task_lock(p, "t", timeout_sec=600, pid_checker=_always_alive,
                          now_provider=FakeClock(T0 + 600 + 600 + 1))
    assert r.acquired and r.reason == "stale-age"

def test_corrupt_lock_stolen(cron_home, monkeypatch):
    p = cron_home / "state/locks/t.lock"
    p.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    r = acquire_task_lock(p, "t", 600, now_provider=FakeClock(T0))
    assert r.acquired and r.reason == "corrupt"

def test_registry_admission_and_prune(cron_home, monkeypatch):
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    d = cron_home / "state/active"
    register_active(d, "a", 1)                      # 本进程,pid 存活
    dead = register_active(d, "b", 1, pid=424242)   # 死 pid
    stale_boot = register_active(d, "c", 1, pid=None, boot_provider=lambda: 1)
    # 手工把 c 的 boot_id 改成旧值
    data = json.loads(stale_boot.read_text(encoding="utf-8"))
    data["boot_id"] = 0
    stale_boot.write_text(json.dumps(data), encoding="utf-8")
    w = active_weight(d, pid_checker=lambda pid: pid != 424242,
                      boot_provider=lambda: 1)
    assert w == 1  # a 存活;b 死;c boot 不符 → 都被清
    unregister(dead)  # 幂等
    unregister(dead)

def test_steal_then_second_acquire_denied(cron_home, monkeypatch):
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    p = cron_home / "state/locks/t.lock"
    p.write_text(json.dumps({"pid": 123, "boot_id": 1, "task_id": "t",
                             "started_at": T0}), encoding="utf-8")
    first = acquire_task_lock(p, "t", 600, pid_checker=_always_dead,
                              now_provider=FakeClock(T0 + 5))
    assert first.acquired and first.reason == "stale-dead-pid"
    # 新主人是本进程(pid 存活、boot 相符、锁龄新鲜):第二次获取必须被拒
    second = acquire_task_lock(p, "t", 600, now_provider=FakeClock(T0 + 6))
    assert not second.acquired and second.reason == "alive"

def test_empty_lock_file_stolen_then_owned(cron_home, monkeypatch):
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    p = cron_home / "state/locks/t.lock"
    p.write_text("", encoding="utf-8")  # 模拟写入中途的创建者(空文件)
    first = acquire_task_lock(p, "t", 600, now_provider=FakeClock(T0))
    assert first.acquired and first.reason == "corrupt"
    second = acquire_task_lock(p, "t", 600, now_provider=FakeClock(T0 + 1))
    assert not second.acquired and second.reason == "alive"

def test_malformed_typed_fields_classified_corrupt(cron_home, monkeypatch):
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    p = cron_home / "state/locks/t.lock"
    p.write_text(json.dumps({"pid": "x", "boot_id": 1, "task_id": "t",
                             "started_at": "not-a-number"}), encoding="utf-8")
    r = acquire_task_lock(p, "t", 600, pid_checker=_always_alive,
                          now_provider=FakeClock(T0))
    assert r.acquired and r.reason == "corrupt"  # 不抛异常,类型异常判 corrupt

def test_exhausted_attempts_denied(cron_home, monkeypatch):
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    p = cron_home / "state/locks/t.lock"
    p.write_text(json.dumps({"pid": 123, "boot_id": 1, "task_id": "t",
                             "started_at": T0}), encoding="utf-8")
    r = acquire_task_lock(p, "t", 600, pid_checker=_always_dead,
                          now_provider=FakeClock(T0 + 5), _max_attempts=1)
    assert not r.acquired  # 分类+unlink 后重试次数耗尽 → 视为竞争,拒绝

def test_register_active_token_atomic_and_valid(cron_home, monkeypatch):
    monkeypatch.setattr(locks, "boot_id", lambda: 1)
    d = cron_home / "state/active"
    token = register_active(d, "a", 3, pid=123, now_provider=FakeClock(T0))
    data = json.loads(token.read_text(encoding="utf-8"))
    assert data["pid"] == 123 and data["boot_id"] == 1
    assert data["task_id"] == "a" and data["weight"] == 3
    assert data["started_at"] == T0
    assert token.suffix == ".json"
    assert list(d.glob("*.tmp")) == []  # 原子写:无 tmp 残留
