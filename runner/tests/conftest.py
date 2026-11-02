import pytest

@pytest.fixture
def cron_home(tmp_path):
    for sub in ("tasks.d", "tasks.d.local", "runs", "state", "state/locks",
                "state/active"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path
