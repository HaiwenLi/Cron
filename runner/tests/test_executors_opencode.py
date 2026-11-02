from cron_runner.executors import get_builder
from cron_runner.executors.opencode import parse_output
from cron_runner.tasks import Task, Step

def _task(**kw):
    base = dict(id="t", name="T", schedule="0 20 * * *", executor="opencode",
                permission="auto", model=None,
                steps=[Step(name="s", type="prompt", run="do ${WORKSPACE}")])
    base.update(kw)
    return Task(**base)

VARS = {"WORKSPACE": "ws"}

def test_argv_default_auto():
    t = _task()
    spec = get_builder("opencode")(t, t.steps[0], VARS)
    assert spec.argv[:2] == ["opencode", "run"]
    assert "--auto" in spec.argv
    assert spec.argv[-1] == "do ws"
    assert "-m" not in spec.argv

def test_argv_with_model_and_permission_default():
    t = _task(model="prov/m", permission="default")
    spec = get_builder("opencode")(t, t.steps[0], VARS)
    assert ["-m", "prov/m"] == spec.argv[spec.argv.index("-m"):spec.argv.index("-m") + 2]
    assert "--auto" not in spec.argv

def test_parse_json_with_model():
    raw = '[{"info":{"model":"g/x"}},{"text":"hello"}]'
    out = parse_output(raw)
    assert out["model"] == "g/x" and "hello" in out["text"]

def test_parse_garbage_falls_back():
    out = parse_output("plain garbage")
    assert out["model"] is None and out["text"].startswith("plain")
