import pytest
from cron_runner.config import load_config, interpolate, UndefinedVariableError

def test_defaults_and_builtin_vars(cron_home):
    cfg = load_config(cron_home)
    assert cfg.max_concurrent == 2
    assert cfg.retention_days == 14
    assert cfg.variables["CRON_HOME"] == str(cron_home.resolve())
    assert cfg.variables["PYTHON"] == "python"

def test_local_overrides_base(cron_home):
    (cron_home / "config.yaml").write_text(
        "max_concurrent: 2\nvariables:\n  WORKSPACE: 'base'\n", encoding="utf-8")
    (cron_home / "config.local.yaml").write_text(
        "max_concurrent: 5\nvariables:\n  WORKSPACE: 'local'\n", encoding="utf-8")
    cfg = load_config(cron_home)
    assert cfg.max_concurrent == 5
    assert cfg.variables["WORKSPACE"] == "local"

def test_interpolate_ok():
    assert interpolate("${A}/${B}", {"A": "x", "B": "y"}) == "x/y"

def test_interpolate_undefined_raises():
    with pytest.raises(UndefinedVariableError, match="NOPE"):
        interpolate("${NOPE}/x", {"A": "x"})
