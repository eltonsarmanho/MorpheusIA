"""Shared pytest configuration.

Treats "no tests collected" (exit code 5) as success rather than failure.
Early scaffolding/config-only tasks in this project intentionally add no
tests yet; later tasks add real tests under tests/unit and tests/integration,
at which point this has no effect on the normal pass/fail exit codes.
"""


def pytest_sessionfinish(session, exitstatus):
    if exitstatus == 5:  # pytest.ExitCode.NO_TESTS_COLLECTED
        session.exitstatus = 0
