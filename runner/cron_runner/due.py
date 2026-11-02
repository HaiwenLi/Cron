# runner/cron_runner/due.py
from datetime import datetime
from croniter import croniter

def next_run(schedule: str, after: datetime) -> datetime:
    it = croniter(schedule, after.replace(microsecond=0))
    return it.get_next(datetime)

def is_due(schedule: str, cursor: datetime | None, now: datetime) -> bool:
    if cursor is None:
        return True
    return next_run(schedule, cursor) <= now.replace(microsecond=0)
