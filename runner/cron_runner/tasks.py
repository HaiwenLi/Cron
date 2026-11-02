# runner/cron_runner/tasks.py
from dataclasses import dataclass
from pathlib import Path
import yaml
from croniter import croniter
from cron_runner.config import Config, interpolate, UndefinedVariableError
from cron_runner.executors import implemented

_EXECUTORS = {"script", "opencode", "kimi", "pi"}
_MISS = {"coalesce", "skip", "catchup"}
_PERM = {"default", "yolo", "auto"}
_TYPES = {"script", "prompt", "skill"}

class TaskValidationError(Exception):
    pass

@dataclass
class Step:
    name: str
    type: str
    run: str
    skill: str | None = None
    output: str | None = None

@dataclass
class Task:
    id: str
    name: str
    schedule: str
    executor: str
    steps: list
    enabled: bool = True
    miss_policy: str = "coalesce"
    permission: str = "auto"
    model: str | None = None
    timeout: int = 1800
    weight: int = 1
    workdir: str = "${WORKSPACE}"
    notify: tuple = ("toast",)

def _parse(path: Path) -> Task:
    # Containment boundary (final review wave): one malformed task file must
    # surface as TaskValidationError carrying its filename — never as a raw
    # yaml.YAMLError/KeyError/TypeError that crashes unrelated commands.
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TaskValidationError(f"{path.name}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise TaskValidationError(
            f"{path.name}: expected a top-level mapping, "
            f"got {type(data).__name__}")
    fname = path.stem
    if data.get("id") != fname:
        raise TaskValidationError(
            f"{path.name}: id {data.get('id')!r} must match filename {fname!r}")
    data.pop("id")
    try:
        steps = [Step(**s) for s in data.pop("steps")]
        return Task(id=fname, steps=steps, **data)
    except KeyError as e:
        raise TaskValidationError(
            f"{path.name}: missing required key {e}") from e
    except TypeError as e:
        raise TaskValidationError(f"{path.name}: {e}") from e

def load_tasks(cron_home: Path) -> list:
    by_id: dict = {}
    for rel in ("tasks.d", "tasks.d.local"):
        d = cron_home / rel
        if not d.exists():
            continue
        for p in sorted(d.glob("*.yaml")):
            by_id[p.stem] = _parse(p)
    return list(by_id.values())

def validate_task(task: Task, cfg: Config) -> list:
    errs = []
    if not croniter.is_valid(task.schedule):
        errs.append(f"schedule invalid: {task.schedule!r}")
    if task.executor not in _EXECUTORS:
        errs.append(f"executor must be one of {sorted(_EXECUTORS)}, got {task.executor!r}")
    elif task.executor not in implemented():
        errs.append(f"executor {task.executor!r} not implemented until M2 "
                    f"(implemented: {sorted(implemented())})")
    if task.miss_policy not in _MISS:
        errs.append(f"miss_policy must be one of {sorted(_MISS)}")
    if task.permission not in _PERM:
        errs.append(f"permission must be one of {sorted(_PERM)}")
    if task.weight < 1:
        errs.append("weight must be >= 1")
    if task.timeout < 60:
        errs.append("timeout must be >= 60s")
    if len(task.steps) < 1:
        errs.append("at least one step required")
    elif len(task.steps) > 1:
        errs.append(f"multi-step steps arrive in M3, got {len(task.steps)}")
    else:
        st = task.steps[0]
        if st.type not in _TYPES:
            errs.append(f"step.type must be one of {sorted(_TYPES)}")
        if task.executor == "script" and st.type != "script":
            errs.append("executor 'script' requires step.type 'script'")
        if task.executor != "script" and st.type == "script":
            errs.append("agent executors require step.type prompt|skill")
    probe = dict(cfg.variables)
    probe.setdefault("WORKSPACE", "")
    probe["RUN_DIR"] = "<RUN_DIR>"
    for label, text in (("workdir", task.workdir), ("run", task.steps[0].run if task.steps else "")):
        try:
            interpolate(text, probe)
        except UndefinedVariableError as e:
            errs.append(f"{label}: {e}")
    return errs
