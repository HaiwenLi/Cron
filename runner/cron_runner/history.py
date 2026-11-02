# runner/cron_runner/history.py
import json, os, shutil
from datetime import datetime, timedelta
from pathlib import Path

def new_run_dir(runs_root: Path, task_id: str, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    base = now.strftime("%Y%m%d-%H%M%S")
    d = runs_root / task_id
    d.mkdir(parents=True, exist_ok=True)
    cand = d / base
    n = 1
    while cand.exists():
        n += 1
        cand = d / f"{base}-{n}"
    cand.mkdir()
    return cand

def write_meta(run_dir: Path, meta: dict) -> None:
    meta.setdefault("finished_at", datetime.now().isoformat(timespec="seconds"))
    tmp = run_dir / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, run_dir / "meta.json")

def _run_dirs(runs_root: Path, task_id: str) -> list:
    d = runs_root / task_id
    return sorted([p for p in d.iterdir() if p.is_dir()]) if d.exists() else []

def latest_run(runs_root: Path, task_id: str) -> dict | None:
    for p in reversed(_run_dirs(runs_root, task_id)):
        f = p / "meta.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None

def cleanup(runs_root: Path, retention_days: int, now: datetime | None = None):
    now = now or datetime.now()
    cutoff = now - timedelta(days=retention_days)
    deleted = []
    if not runs_root.exists():
        return deleted
    for task_dir in runs_root.iterdir():
        if not task_dir.is_dir():
            continue
        dirs = _run_dirs(runs_root, task_dir.name)
        for p in dirs[:-1]:  # 保留最新 1 个
            if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
                shutil.rmtree(p, ignore_errors=True)
                deleted.append(p)
    return deleted
