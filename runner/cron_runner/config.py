# runner/cron_runner/config.py
from dataclasses import dataclass, field
from pathlib import Path
import re
import yaml

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

class UndefinedVariableError(Exception):
    def __init__(self, name: str, text: str):
        super().__init__(f"undefined variable ${{{name}}} in: {text!r}")
        self.name = name

@dataclass
class Config:
    max_concurrent: int = 2
    retention_days: int = 14
    variables: dict = field(default_factory=dict)

def interpolate(text: str, variables: dict) -> str:
    def sub(m):
        name = m.group(1)
        if name not in variables:
            raise UndefinedVariableError(name, text)
        return variables[name]
    return _VAR_RE.sub(sub, text)

def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping")
    return data

def load_config(cron_home: Path) -> Config:
    base = _read(cron_home / "config.yaml")
    local = _read(cron_home / "config.local.yaml")
    merged = {**base, **local}
    variables = {"CRON_HOME": str(cron_home.resolve()), "PYTHON": "python"}
    variables.update(base.get("variables") or {})
    variables.update(local.get("variables") or {})
    return Config(
        max_concurrent=int(merged.get("max_concurrent", 2)),
        retention_days=int(merged.get("retention_days", 14)),
        variables=variables,
    )
