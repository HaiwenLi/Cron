from datetime import datetime
from cron_runner.due import next_run, is_due

D = datetime(2026, 10, 26, 10, 0, 0)

def test_next_run_daily():
    assert next_run("0 20 * * *", D) == datetime(2026, 10, 26, 20, 0)

def test_next_run_skips_current_time():
    assert next_run("0 10 * * *", D) == datetime(2026, 10, 27, 10, 0)

def test_next_run_every_5min():
    assert next_run("*/5 * * * *", datetime(2026, 10, 26, 10, 3, 0)) == datetime(2026, 10, 26, 10, 5)

def test_is_due_no_cursor():
    assert is_due("0 20 * * *", None, D) is True

def test_is_due_future_slot():
    assert is_due("0 20 * * *", datetime(2026, 10, 25, 20, 0), D) is False

def test_is_due_past_slot():
    assert is_due("0 9 * * *", datetime(2026, 10, 25, 9, 0), D) is True
