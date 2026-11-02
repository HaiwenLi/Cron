import subprocess, time
from dataclasses import dataclass
from pathlib import Path

@dataclass
class ExecutorSpec:
    argv: list
    shell: bool = False
    cwd: str | None = None

def execute(spec: ExecutorSpec, run_dir: Path, timeout: int, env: dict | None = None) -> dict:
    start = time.monotonic()
    # shell=True contract: argv is a single-element command-string list. Join it
    # first -- Popen would run list2cmdline on the list and re-quote it, yielding
    # a doubled `cmd /c ""...""` command line that Windows cannot resolve.
    if spec.shell and isinstance(spec.argv, list):
        args = " ".join(spec.argv)
    else:
        args = spec.argv
    with open(run_dir / "stdout.log", "w", encoding="utf-8", errors="replace") as fo, \
         open(run_dir / "stderr.log", "w", encoding="utf-8", errors="replace") as fe:
        proc = subprocess.Popen(args, shell=spec.shell, cwd=spec.cwd,
                                stdout=fo, stderr=fe, env=env)
        try:
            code = proc.wait(timeout=timeout)
            status = "success" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
            # Bounded grace (final review wave): if taskkill failed or the
            # tree refuses to die, give up after 30s instead of hanging the
            # runner forever; report timeout with whatever returncode we have.
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            code, status = proc.returncode, "timeout"
    return {"exit_code": code, "duration_sec": round(time.monotonic() - start, 3),
            "status": status}
