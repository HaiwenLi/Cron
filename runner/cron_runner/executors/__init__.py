from cron_runner.executors import opencode, script

_BUILDERS = {"script": script.build, "opencode": opencode.build}

def implemented() -> set:
    return set(_BUILDERS)

def get_builder(executor: str):
    if executor not in _BUILDERS:
        raise ValueError(f"unknown executor {executor!r}; known: {sorted(_BUILDERS)}")
    return _BUILDERS[executor]
