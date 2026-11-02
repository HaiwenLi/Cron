import pytest
from pathlib import Path
from cron_runner.config import load_config
from cron_runner.scheduler_win import (schtasks_xml, cmd_sync,
                                       UnsupportedSchedule, SchtasksBackend)
from cron_runner.tasks import load_tasks

def _cfg(cron_home):
    (cron_home / "config.yaml").write_text(
        "variables:\n  WORKSPACE: 'ws'\n", encoding="utf-8")
    return load_config(cron_home)

def _put(home, text):
    (home / "tasks.d/t.yaml").write_text(text, encoding="utf-8")

DAILY = """id: t
name: T
schedule: "0 20 * * *"
executor: script
steps:
  - name: s
    type: script
    run: echo x
"""

def test_daily_xml(cron_home):
    _put(cron_home, DAILY)
    [t] = load_tasks(cron_home)
    xml = schtasks_xml(t, _cfg(cron_home))
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "T20:00:00" in xml
    assert "<ExecutionTimeLimit>PT2100S</ExecutionTimeLimit>" in xml
    assert "run t" in xml
    assert "<Duration>" not in xml  # indefinite repetition (fix round 1)

def test_skip_policy_no_start_when_available(cron_home):
    _put(cron_home, DAILY.replace('executor: script',
        'executor: script\nmiss_policy: skip'))
    [t] = load_tasks(cron_home)
    assert "<StartWhenAvailable>false</StartWhenAvailable>" in schtasks_xml(t, _cfg(cron_home))

def test_weekly_xml(cron_home):
    _put(cron_home, DAILY.replace('"0 20 * * *"', '"30 8 * * 1-5"'))
    [t] = load_tasks(cron_home)
    xml = schtasks_xml(t, _cfg(cron_home))
    assert "<DaysOfWeek>" in xml and "Monday" in xml and "Friday" in xml
    assert "Sunday" not in xml
    assert "T08:30:00" in xml

def test_minute_xml_midnight_anchored(cron_home):
    _put(cron_home, DAILY.replace('"0 20 * * *"', '"*/5 * * * *"'))
    [t] = load_tasks(cron_home)
    xml = schtasks_xml(t, _cfg(cron_home))
    assert "<Interval>PT5M</Interval>" in xml
    assert "<Duration>" not in xml  # indefinite repetition (fix round 1)
    assert "T00:00:00" in xml

def test_xml_digest_stable_across_days(cron_home, monkeypatch):
    # 回归(reviewer finding):任何墙钟值进入 XML 都会让 digest 每日翻新 →
    # sync 每天重建所有任务(Last Run Time 被重置)。锚定日期后 XML 与日期无关。
    # 模块不再 import date;raising=False 让补丁保持防御性——
    # 若未来有人重新引入 sw.date 墙钟用法,补丁生效并使本测试失败。
    import cron_runner.scheduler_win as sw
    _put(cron_home, DAILY)
    [t] = load_tasks(cron_home)
    cfg = _cfg(cron_home)
    xml1 = sw.schtasks_xml(t, cfg)
    class FakeDate:
        @staticmethod
        def today():
            import datetime as _dt
            return _dt.date(2031, 2, 3)
    monkeypatch.setattr(sw, "date", FakeDate, raising=False)
    xml2 = sw.schtasks_xml(t, cfg)
    assert xml1 == xml2

def test_bridge_writes_utf16_via_powershell(monkeypatch, tmp_path):
    # 评审修复轮:为 DLP 明文桥接补零覆盖——测试不得真的调用 powershell
    # (powershell-free),只钉字节契约:命令串里的 base64 path+payload 解码后
    # 必须等于 write_text(..., encoding="utf-16") 的字节(BOM+UTF-16LE)。
    # 命令串格式见 _write_xml_readable_by_schtasks(两个参数间是", ")。
    import base64
    import re
    import cron_runner.scheduler_win as sw

    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(sw.subprocess, "run", fake_run)
    xml = "<Task>probe</Task>"
    path = tmp_path / "t.xml"
    sw._write_xml_readable_by_schtasks(str(path), xml)
    argv = captured["argv"]
    assert argv[0].endswith("powershell")
    assert "WriteAllBytes" in argv[-1]
    m = re.search(r"WriteAllBytes\(\[Text.Encoding\]::UTF8\.GetString\("
                  r"\[Convert\]::FromBase64String\('([^']+)'\)\),\s*"
                  r"\[Convert\]::FromBase64String\('([^']+)'\)\)",
                  argv[-1])
    assert m, f"unexpected bridge command: {argv[-1]}"
    assert base64.b64decode(m.group(1)).decode("utf-8") == str(path)
    assert base64.b64decode(m.group(2)) == b"\xff\xfe" + xml.encode("utf-16-le")

def test_unsupported_schedule(cron_home):
    _put(cron_home, DAILY.replace('"0 20 * * *"', '"0 9 1,15 * *"'))
    [t] = load_tasks(cron_home)
    try:
        schtasks_xml(t, _cfg(cron_home))
        assert False
    except UnsupportedSchedule:
        pass

# --- minute-step guard (final review wave) -------------------------------
# schtasks Interval 是固定周期无限重复;cron 分钟步进每小时在 :00 重置相位。
# 只有 60 % N == 0 时两者逐次对齐 —— 否则(如 */7)跨小时边界漂移,
# M1 拒绝,M2 泛化。

def test_minute_step_not_dividing_60_rejected(cron_home):
    _put(cron_home, DAILY.replace('"0 20 * * *"', '"*/7 * * * *"'))
    [t] = load_tasks(cron_home)
    with pytest.raises(UnsupportedSchedule, match="divide 60 evenly"):
        schtasks_xml(t, _cfg(cron_home))

def test_minute_offset_step_drift_rejected(cron_home):
    _put(cron_home, DAILY.replace('"0 20 * * *"', '"5/7 * * * *"'))
    [t] = load_tasks(cron_home)
    with pytest.raises(UnsupportedSchedule, match="divide 60 evenly"):
        schtasks_xml(t, _cfg(cron_home))

@pytest.mark.parametrize("n", [1, 5, 10, 15, 20, 30, 60])
def test_minute_steps_dividing_60_ok(cron_home, n):
    _put(cron_home, DAILY.replace('"0 20 * * *"', f'"*/{n} * * * *"'))
    [t] = load_tasks(cron_home)
    xml = schtasks_xml(t, _cfg(cron_home))
    assert f"<Interval>PT{n}M</Interval>" in xml
    assert "<Duration>" not in xml  # indefinite repetition unchanged

def test_minute_offset_step_dividing_60_ok(cron_home):
    # `5/15`:anchored 00:05、每 15 分钟 —— 60%15==0,跨小时不漂移。
    _put(cron_home, DAILY.replace('"0 20 * * *"', '"5/15 * * * *"'))
    [t] = load_tasks(cron_home)
    xml = schtasks_xml(t, _cfg(cron_home))
    assert "<Interval>PT15M</Interval>" in xml and "T00:05:00" in xml

class FakeBackend:
    def __init__(self):
        self.store = {}
        self.creates = 0
    def create(self, name, xml):
        self.store[name] = xml
        self.creates += 1
    def delete(self, name):
        return self.store.pop(name, None) is not None
    def names(self):
        return set(self.store)

def test_sync_reconcile_idempotent(cron_home):
    _put(cron_home, DAILY)
    be = FakeBackend(); be.store["cron-ghost"] = "<xml/>"
    assert cmd_sync(cron_home, backend=be) == 0
    assert set(be.store) == {"cron-t"}
    first = be.store["cron-t"]
    assert be.creates == 1
    cmd_sync(cron_home, backend=be)  # 第二次:哈希命中 → 不重建
    assert be.store["cron-t"] == first
    assert be.creates == 1  # 幂等:未重create

def test_sync_removes_disabled(cron_home):
    _put(cron_home, DAILY)
    be = FakeBackend()
    cmd_sync(cron_home, backend=be)
    assert "cron-t" in be.names()
    (cron_home / "tasks.d/t.yaml").write_text(
        DAILY.replace("executor: script", "executor: script\nenabled: false"),
        encoding="utf-8")
    cmd_sync(cron_home, backend=be)
    assert "cron-t" not in be.names()
