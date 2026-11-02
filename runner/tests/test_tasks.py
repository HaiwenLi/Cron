import pytest
from cron_runner.config import load_config
from cron_runner.tasks import load_tasks, validate_task, TaskValidationError

VALID = """id: t1
name: T1
schedule: "0 20 * * *"
executor: script
steps:
  - name: s
    type: script
    run: echo hi
"""

def _put(home, rel, text):
    p = home / rel
    p.write_text(text, encoding="utf-8")

def test_load_and_overlay_replace(cron_home):
    _put(cron_home, "tasks.d/t1.yaml", VALID)
    _put(cron_home, "tasks.d.local/t1.yaml",
         VALID.replace("schedule: \"0 20 * * *\"", "schedule: \"1 21 * * *\""))
    tasks = {t.id: t for t in load_tasks(cron_home)}
    assert tasks["t1"].schedule == "1 21 * * *"
    assert len(tasks) == 1

def test_id_must_match_filename(cron_home):
    _put(cron_home, "tasks.d/other.yaml", VALID)  # id: t1 != other
    with pytest.raises(TaskValidationError, match="other.yaml"):
        load_tasks(cron_home)

def test_validate_ok(cron_home):
    _put(cron_home, "tasks.d/t1.yaml", VALID)
    (cron_home / "config.yaml").write_text(
        "variables:\n  WORKSPACE: 'ws'\n", encoding="utf-8")
    cfg = load_config(cron_home)
    [t] = load_tasks(cron_home)
    assert validate_task(t, cfg) == []

def test_validate_collects_errors(cron_home):
    _put(cron_home, "tasks.d/t1.yaml", """id: t1
name: T1
schedule: "not cron"
executor: nope
miss_policy: bad
permission: worse
weight: 0
timeout: 1
steps:
  - name: s
    type: prompt
    run: ${UNDEF}/x
""")
    cfg = load_config(cron_home)
    [t] = load_tasks(cron_home)
    errs = validate_task(t, cfg)
    joined = "; ".join(errs)
    assert "schedule" in joined and "executor" in joined
    assert "miss_policy" in joined and "permission" in joined
    assert "weight" in joined and "timeout" in joined
    assert "UNDEF" in joined

def test_m1_rejects_multistep(cron_home):
    _put(cron_home, "tasks.d/t1.yaml", VALID.rstrip() + "\n  - name: s2\n    type: script\n    run: echo b\n")
    cfg = load_config(cron_home)
    [t] = load_tasks(cron_home)
    assert any("M3" in e for e in validate_task(t, cfg))

# --- malformed-task containment (final review wave) ----------------------
# 单个坏文件必须以 TaskValidationError(带文件名)报出,不许让裸
# yaml.YAMLError/KeyError/TypeError 炸出去。

def test_malformed_yaml_becomes_task_validation_error(cron_home):
    _put(cron_home, "tasks.d/good.yaml", VALID.replace("id: t1", "id: good"))
    _put(cron_home, "tasks.d/zz-bad.yaml",
         "id: zz-bad\nname: [unclosed\nschedule: \"*/5 * * * *\"\n")
    with pytest.raises(TaskValidationError, match="zz-bad.yaml"):
        load_tasks(cron_home)

def test_missing_steps_rejected(cron_home):
    _put(cron_home, "tasks.d/t1.yaml",
         "id: t1\nname: T1\nschedule: \"0 20 * * *\"\nexecutor: script\n")
    with pytest.raises(TaskValidationError, match="steps"):
        load_tasks(cron_home)

def test_unknown_top_level_key_rejected(cron_home):
    _put(cron_home, "tasks.d/t1.yaml", VALID.rstrip() + "\nwhatever: 1\n")
    with pytest.raises(TaskValidationError, match="whatever"):
        load_tasks(cron_home)

def test_validate_rejects_unimplemented_executor(cron_home):
    # controller 原文只替换 executor,但 VALID 的 id 是 t1 而 filename 是 k,
    # 会先撞 id/filename 不变量(Task 2)——补替换 id 使其可加载。
    _put(cron_home, "tasks.d/k.yaml", VALID.replace("id: t1", "id: k")
         .replace("executor: script", "executor: kimi"))
    cfg = load_config(cron_home)
    [t] = load_tasks(cron_home)
    errs = validate_task(t, cfg)
    assert any("not implemented" in e and "kimi" in e for e in errs)
