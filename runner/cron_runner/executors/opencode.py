import json
from cron_runner.config import interpolate
from cron_runner.executors.base import ExecutorSpec

def build(task, step, variables):
    argv = ["opencode", "run", "--format", "json", "--title", f"cron-{task.id}"]
    if task.model:
        argv += ["-m", task.model]
    if task.permission in ("auto", "yolo"):
        argv += ["--auto"]
    argv.append(interpolate(step.run, variables))
    return ExecutorSpec(argv=argv, shell=False,
                        cwd=interpolate(task.workdir, variables) or None)

def _find_model(obj):
    if isinstance(obj, dict):
        if "model" in obj and isinstance(obj["model"], str):
            return obj["model"]
        for v in obj.values():
            m = _find_model(v)
            if m:
                return m
    elif isinstance(obj, list):
        for v in obj:
            m = _find_model(v)
            if m:
                return m
    return None

def _collect_text(obj, acc):
    if isinstance(obj, dict):
        if isinstance(obj.get("text"), str):
            acc.append(obj["text"])
        for v in obj.values():
            _collect_text(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_text(v, acc)

def parse_output(raw: str) -> dict:
    try:
        data = json.loads(raw)
        acc = []
        _collect_text(data, acc)
        return {"model": _find_model(data), "text": "\n".join(acc)[:2000] or raw[:2000]}
    except Exception:
        return {"model": None, "text": raw[:2000]}
