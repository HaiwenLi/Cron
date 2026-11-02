# runner/cron_runner/locks.py
import json, os, time, uuid
from dataclasses import dataclass
from pathlib import Path
import psutil

def boot_id() -> int:
    return int(psutil.boot_time())

def pid_alive(pid: int) -> bool:
    return psutil.pid_exists(pid)

@dataclass
class LockResult:
    acquired: bool
    reason: str = ""

def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _classify_lock(old, *, pid_checker, boot_provider, now, timeout_sec, grace_sec):
    """陈锁三判据;返回 steal reason,锁仍存活则返回 None。

    类型异常字段(pid 非整数 / started_at 非数值)一律判 corrupt,
    保证判据链对任意 JSON 输入不抛异常(I1)。
    """
    pid_v = old.get("pid")
    started_v = old.get("started_at")
    if not isinstance(pid_v, int) or isinstance(pid_v, bool) \
            or not isinstance(started_v, (int, float)) \
            or isinstance(started_v, bool):
        return "corrupt"
    if old.get("boot_id") != boot_provider():
        return "stale-boot"
    if not pid_checker(pid_v):
        return "stale-dead-pid"
    if now - started_v > timeout_sec + grace_sec:
        return "stale-age"
    return None

# 所有注入参数 None 默认值 + 函数体内晚绑定(默认参数 def 时求值,
# 早绑定会吞掉 monkeypatch)。
def acquire_task_lock(lock_path, task_id, timeout_sec, *, pid_checker=None,
                      boot_provider=None, now_provider=None, grace_sec=600,
                      _max_attempts=3):
    pid_checker = pid_checker or pid_alive
    boot_provider = boot_provider or boot_id
    now = now_provider() if now_provider else int(time.time())
    payload = {"pid": os.getpid(), "boot_id": boot_provider(),
               "task_id": task_id, "started_at": now}
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    pending_reason = ""
    for _ in range(_max_attempts):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except FileExistsError:
            # 分类在 try/except 内:任何异常(含判据链抛错)一律 corrupt
            try:
                old = _read_json(lock_path)
                if not isinstance(old, dict):
                    pending_reason = "corrupt"
                else:
                    reason = _classify_lock(old, pid_checker=pid_checker,
                                            boot_provider=boot_provider,
                                            now=now, timeout_sec=timeout_sec,
                                            grace_sec=grace_sec)
                    if reason is None:
                        return LockResult(False, "alive")
                    pending_reason = reason
            except Exception:
                pending_reason = "corrupt"
            # steal = unlink;竞争者可能已抢先删除(FileNotFoundError 容忍)。
            # 恰有一个偷者的下一次 O_EXCL 成功;败者重评发现新主人存活 → 拒绝。
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            continue
        # 读回验证:文件内容仍是自己的 payload 才真正持有;否则创建窗口内
        # 被人 unlink/replace 过 → 重评(continue),绝不双持有。
        got = _read_json(lock_path)
        if isinstance(got, dict) and got.get("pid") == payload["pid"] \
                and got.get("boot_id") == payload["boot_id"] \
                and got.get("started_at") == payload["started_at"]:
            return LockResult(True, pending_reason)
    return LockResult(False, "alive")

def register_active(active_dir, task_id, weight, *, pid=None, boot_provider=None,
                    now_provider=None):
    boot_provider = boot_provider or boot_id
    active_dir.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid() if pid is None else pid,
               "boot_id": boot_provider(), "task_id": task_id,
               "weight": weight,
               "started_at": now_provider() if now_provider else int(time.time())}
    token = active_dir / f"{uuid.uuid4().hex}.json"
    # 原子写(I2):tmp + os.replace,避免并发 active_weight 读到半截 JSON
    tmp = active_dir / f"{token.name}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, token)
    return token

def active_weight(active_dir, *, pid_checker=None, boot_provider=None):
    pid_checker = pid_checker or pid_alive
    boot_provider = boot_provider or boot_id
    total = 0
    if not active_dir.exists():
        return 0
    for p in list(active_dir.glob("*.json")):
        data = _read_json(p)
        if data is None or data.get("boot_id") != boot_provider() \
                or not pid_checker(data.get("pid", -1)):
            p.unlink(missing_ok=True)
            continue
        total += int(data.get("weight", 1))
    return total

def unregister(token: Path) -> None:
    Path(token).unlink(missing_ok=True)
