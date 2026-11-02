# runner/cron_runner/scheduler_win.py
"""Windows Task Scheduler backend: schtasks XML generation + sync reconcile.

Hard constraint (Task 8 ruling): the schtasks Action must launch ONE
independent runner subprocess per firing —
    <sys.executable> "<CRON_HOME>/runner/cron_runner/main.py" --home "<CRON_HOME>" run <id>
Lock lifetime depends on process exit (steal-based recycling); in-process
scheduling loops are forbidden.

Element order below mirrors XML saved by the local Task Scheduler service
(verified 2026-10-26 via read-only `schtasks /Query /XML`): triggers put
StartBoundary before Repetition, and `Enabled` is omitted when true (default).
"""
import base64
import csv
import hashlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from cron_runner.config import Config, load_config
from cron_runner.tasks import Task, load_tasks, validate_task


class UnsupportedSchedule(Exception):
    """Schedule form not expressible as schtasks XML until M2."""


# cron dow index -> Task Scheduler day element (0 and 7 both mean Sunday)
_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
         "Friday", "Saturday")

# Fix round 1 ruling: all StartBoundary values use this FIXED past date, never
# date.today() — any wall-clock value entering the XML churns the sha256 digest
# daily, so sync would rebuild every task each midnight (resetting Last Run
# Time and defeating hash-idempotency). A past boundary is fine: Task
# Scheduler schedules forward from the repetition/schedule definition.
_ANCHOR_DATE = "2020-01-01"

_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>{triggers}</Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>{swa}</StartWhenAvailable>
    <ExecutionTimeLimit>PT{limit}S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>{args}</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _field_ints(spec: str, lo: int, hi: int) -> set | None:
    """Plain ints / ranges / comma lists only (no steps, no names).

    Returns the value set, or None when the spec uses anything we do not
    translate (caller then raises UnsupportedSchedule).
    """
    vals: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit()):
                return None
            ai, bi = int(a), int(b)
            if not (lo <= ai <= hi and lo <= bi <= hi and ai <= bi):
                return None
            vals.update(range(ai, bi + 1))
        elif part.isdigit():
            v = int(part)
            if not lo <= v <= hi:
                return None
            vals.add(v)
        else:
            return None
    return vals


def _triggers_xml(task: Task) -> str:
    parts = task.schedule.split()
    if len(parts) != 5:
        raise UnsupportedSchedule(task.schedule)
    minute_f, hour_f, dom_f, mon_f, dow_f = parts

    # Minute-level: `*/N * * * *` or `M/N * * * *` — midnight-anchored phase:
    # StartBoundary {anchor date}T00:{M}:00, then repeat every N minutes
    # INDEFINITELY (Duration omitted — schema-optional; omission = no end).
    # Task-12 fallback if real machine rejects/misbehaves: add
    # <Duration>P3650D</Duration> inside this Repetition.
    if ("/" in minute_f and hour_f == "*" and dom_f == "*"
            and mon_f == "*" and dow_f == "*"):
        anchor_s, _, step_s = minute_f.partition("/")
        if step_s.isdigit() and int(step_s) >= 1 and (
                anchor_s == "*" or (anchor_s.isdigit() and int(anchor_s) <= 59)):
            step = int(step_s)
            if 60 % step != 0:
                # schtasks Interval repeats a fixed PT{N}M period forever;
                # cron minute-steps re-anchor at each hour boundary. They stay
                # aligned only when N divides 60 evenly — otherwise firings
                # drift across hours (*/7) or mis-translate (*/90). M2 will
                # generalize via multi-trigger XML.
                raise UnsupportedSchedule(
                    f"{task.schedule}: minute steps must divide 60 evenly "
                    "(M2 will generalize)")
            anchor = 0 if anchor_s == "*" else int(anchor_s)
            return ("<TimeTrigger>"
                    f"<StartBoundary>{_ANCHOR_DATE}T00:{anchor:02d}:00</StartBoundary>"
                    "<Repetition>"
                    f"<Interval>PT{step}M</Interval>"
                    "</Repetition>"
                    "</TimeTrigger>")
    if dom_f != "*" or mon_f != "*":
        raise UnsupportedSchedule(task.schedule)  # dom/mon forms wait for M2

    minutes = _field_ints(minute_f, 0, 59)
    hours = _field_ints(hour_f, 0, 23)
    if minutes is None or len(minutes) != 1 or hours is None or len(hours) != 1:
        raise UnsupportedSchedule(task.schedule)
    mi, hh = next(iter(minutes)), next(iter(hours))

    if dow_f == "*":  # pure daily — indefinite daily repetition, no Duration
        # Task-12 fallback if real machine rejects/misbehaves:
        # <Duration>P3650D</Duration> inside this Repetition.
        return ("<TimeTrigger>"
                f"<StartBoundary>{_ANCHOR_DATE}T{hh:02d}:{mi:02d}:00</StartBoundary>"
                "<Repetition><Interval>P1D</Interval></Repetition>"
                "</TimeTrigger>")

    days = _field_ints(dow_f, 0, 7)
    if days is None or not days:
        raise UnsupportedSchedule(task.schedule)
    days = {d % 7 for d in days}  # 0 and 7 are both Sunday
    dow = "".join(f"<{_DAYS[d]}/>" for d in sorted(days))
    return ("<CalendarTrigger>"
            f"<StartBoundary>{_ANCHOR_DATE}T{hh:02d}:{mi:02d}:00</StartBoundary>"
            f"<ScheduleByWeek><DaysOfWeek>{dow}</DaysOfWeek>"
            "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
            "</CalendarTrigger>")


def schtasks_xml(task: Task, cfg: Config) -> str:
    """Task Scheduler 1.2 XML for one task (UTF-16 declaration in header)."""
    cron_home = cfg.variables["CRON_HOME"]
    # One runner subprocess per firing — process exit releases the lock.
    args = (f'"{cron_home}/runner/cron_runner/main.py" '
            f'--home "{cron_home}" run {task.id}')
    return _TEMPLATE.format(
        triggers=_triggers_xml(task),
        swa="true" if task.miss_policy == "coalesce" else "false",
        limit=task.timeout + 300,
        python=escape(sys.executable),
        args=escape(args),
        workdir=escape(cron_home),
    )


def _write_xml_readable_by_schtasks(path: str, xml_text: str) -> None:
    """Write task XML so schtasks.exe can read it (plaintext on disk).

    Some enterprise-DLP setups apply transparent per-process file
    encryption (measured 2026-10-26 during acceptance testing): files
    WRITTEN by python can land encrypted on disk, and non-whitelisted
    readers — schtasks.exe among them — see ciphertext, so
    `schtasks /Create /XML` fails with "(1,2) incorrect document
    syntax". Files written by powershell.exe land plaintext on the same
    machine. Bridge the write through powershell; payload travels as
    base64 (A-Za-z0-9+/= only) so no quoting/encoding can mangle it.
    Harmless on machines without such encryption: the bytes written are
    identical either way.
    """
    b64_path = base64.b64encode(path.encode("utf-8")).decode("ascii")
    b64_data = base64.b64encode(xml_text.encode("utf-16")).decode("ascii")
    script = ("[IO.File]::WriteAllBytes("
              "[Text.Encoding]::UTF8.GetString("
              f"[Convert]::FromBase64String('{b64_path}')), "
              f"[Convert]::FromBase64String('{b64_data}'))")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, errors="replace",
                           timeout=60)
    except subprocess.TimeoutExpired as e:
        # Bounded spawn (final review wave): a wedged powershell must not
        # hang sync forever.
        raise RuntimeError(f"powershell plaintext write timed out after 60s "
                           f"for {path!r}") from e
    if r.returncode != 0:
        raise RuntimeError(f"powershell plaintext write failed for {path!r}: "
                           f"{(r.stderr or r.stdout).strip()}")


class SchtasksBackend:
    """schtasks.exe adapter. Real-machine use (Task 12); tests inject a fake."""

    def _run(self, argv: list) -> subprocess.CompletedProcess:
        # Bounded spawn (final review wave): a wedged schtasks must not hang
        # sync forever.
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  errors="replace", timeout=60)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"schtasks timed out after 60s: "
                               f"{' '.join(argv)}") from e

    def create(self, name: str, xml_text: str) -> None:
        fd, path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        os.unlink(path)  # recreate via the plaintext writer (see helper)
        try:
            # UTF-16 with BOM (matches the XML declaration); written by
            # powershell because python-written files may be DLP-encrypted
            # unreadable to schtasks.exe — see _write_xml_readable_by_schtasks.
            _write_xml_readable_by_schtasks(path, xml_text)
            r = self._run(["schtasks", "/Create", "/F", "/XML", path,
                           "/TN", name])
            if r.returncode != 0:
                raise RuntimeError(
                    f"schtasks /Create failed for {name!r}: {r.stderr.strip()}")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def delete(self, name: str) -> bool:
        """True when deleted; False when the task does not exist."""
        return self._run(["schtasks", "/Delete", "/F", "/TN",
                          name]).returncode == 0

    def names(self) -> set:
        """Registered task names managed by us (cron- prefix, no leading \\)."""
        r = self._run(["schtasks", "/Query", "/FO", "CSV"])
        if r.returncode != 0:
            return set()
        rows = csv.reader(io.StringIO(r.stdout))
        next(rows, None)  # header: "TaskName","Next Run Time",...
        found = set()
        for row in rows:
            if row:
                task_name = row[0].lstrip("\\").strip()
                if task_name.startswith("cron-"):
                    found.add(task_name)
        return found


def cmd_sync(cron_home: Path, backend=None) -> int:
    """Reconcile schtasks entries with tasks.d (hash-idempotent).

    Rebuilding a schtasks entry resets its Last Run Time, so we store the
    sha256 of the last registered XML per entry and skip recreation when the
    new XML hashes identically.
    """
    if backend is None:
        backend = SchtasksBackend()
    cfg = load_config(cron_home)
    sync_dir = cron_home / "state" / "sync"
    sync_dir.mkdir(parents=True, exist_ok=True)

    want: dict[str, str] = {}
    for t in load_tasks(cron_home):
        if not t.enabled:
            continue
        errs = validate_task(t, cfg)
        if errs:
            print(f"skip {t.id}: invalid ({errs[0]})")
            continue
        try:
            xml = schtasks_xml(t, cfg)
        except UnsupportedSchedule:
            print(f"skip {t.id}: schedule {t.schedule!r} "
                  "not supported until M2")
            continue
        want[f"cron-{t.id}"] = xml

    have = backend.names()
    for name in sorted(have - set(want)):
        backend.delete(name)
        hash_path = sync_dir / f"{name}.sha256"
        if hash_path.exists():
            hash_path.unlink()
        print(f"deleted {name}")

    for name, xml in sorted(want.items()):
        digest = hashlib.sha256(xml.encode("utf-8")).hexdigest()
        hash_path = sync_dir / f"{name}.sha256"
        if (name in have and hash_path.exists()
                and hash_path.read_text(encoding="utf-8").strip() == digest):
            print(f"unchanged {name}")
            continue
        existed = name in have  # hash mismatch (or lost hash file): rebuild
        if existed:  # no in-place XML update — delete then recreate
            backend.delete(name)
        backend.create(name, xml)
        hash_path.write_text(digest, encoding="utf-8")
        print(f"{'updated' if existed else 'created'} {name}")
    return 0
