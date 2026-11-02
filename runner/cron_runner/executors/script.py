from cron_runner.config import interpolate
from cron_runner.executors.base import ExecutorSpec

def build(task, step, variables):
    return ExecutorSpec(argv=[interpolate(step.run, variables)],
                        shell=True,
                        cwd=interpolate(task.workdir, variables) or None)
