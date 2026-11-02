import sys, time
from cron_runner.executors import get_builder
from cron_runner.executors.base import ExecutorSpec, execute
from cron_runner.tasks import Task, Step

def _task(**kw):
    base = dict(id="t", name="T", schedule="0 20 * * *", executor="script",
                steps=[Step(name="s", type="script", run="echo hi")])
    base.update(kw)
    return Task(**base)

def test_registry_unknown():
    try:
        get_builder("nope")
        assert False
    except ValueError as e:
        assert "nope" in str(e)

def test_script_build_interpolates():
    t = _task(steps=[Step(name="s", type="script", run="${PYTHON} -c print(1)")])
    spec = get_builder("script")(t, t.steps[0], {"PYTHON": "py", "WORKSPACE": "w"})
    assert spec.shell is True and spec.argv == ["py -c print(1)"]

def test_execute_success(tmp_path):
    spec = ExecutorSpec(argv=["echo hello"], shell=True)
    out = execute(spec, tmp_path, timeout=30)
    assert out["status"] == "success" and out["exit_code"] == 0
    assert "hello" in (tmp_path / "stdout.log").read_text(encoding="utf-8")

def test_execute_failure(tmp_path):
    spec = ExecutorSpec(argv=["exit 3"], shell=True)
    out = execute(spec, tmp_path, timeout=30)
    assert out["status"] == "failed" and out["exit_code"] == 3

def test_execute_timeout_kills(tmp_path):
    spec = ExecutorSpec(
        argv=[f'"{sys.executable}" -c "import time; time.sleep(30)"'], shell=True)
    out = execute(spec, tmp_path, timeout=1)
    assert out["status"] == "timeout"

def test_execute_gives_up_when_kill_fails(tmp_path, monkeypatch):
    # final review wave:taskkill 失败后最终 wait 必须有界(30s 上限),
    # 不得永远挂起;放弃后仍按 timeout 语义返回(returncode 可能仍是 None)。
    import cron_runner.executors.base as base

    class FakeProc:
        pid = 4242
        returncode = None
        def wait(self, timeout=None):
            raise base.subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

    killed = {}
    def fake_taskkill(argv, **kw):
        killed["argv"] = argv
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(base.subprocess, "run", fake_taskkill)
    monkeypatch.setattr(base.subprocess, "Popen", lambda *a, **k: FakeProc())
    spec = ExecutorSpec(argv=["irrelevant"], shell=True)
    out = execute(spec, tmp_path, timeout=1)
    assert killed["argv"][0] == "taskkill"      # 确实尝试过杀
    assert out["status"] == "timeout"
    assert out["exit_code"] is None             # 进程未死 → returncode 未知
