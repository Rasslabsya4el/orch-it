"""An invented setup for the tests of the scanner (#413 unit 5): the scan reads the values of the setup it runs on
(`values.read_values`), so a test that runs `Patterns.of_machine` or `guards scan` gets an invented one, complete,
and reads nothing of the machine it runs on (no git config, no `gh`). A test file imports `invented_setup`
(an autouse fixture); a test that names its own setup (`test_scan_wired.py`) sets its own and does not."""

import pytest

from orch_it.guards import values

INVENTED = {
    ("git", "remote", "get-url", "origin"): "https://github.com/acme-dev/orch-tools.git",
    ("gh", "api", "user", "--jq", ".login"): "acme-dev",
    ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"): "/zz-projects/orch-tools/.git",
    ("git", "config", "user.name"): "Pat Example",
    ("git", "config", "user.email"): "pat@example.test",
}


def invented_run(cmd):
    return INVENTED[tuple(cmd)]


@pytest.fixture(autouse=True)
def invented_setup(monkeypatch):
    real = values.read_values

    def read_values(*, env=None, run=None, home=None, host=None):
        return real(env=env, run=invented_run, home=home, host=host)

    monkeypatch.setattr(values, "read_values", read_values)
    for name in ("USERNAME", "USER", "LOGNAME"):
        monkeypatch.setenv(name, "zz-fake-user")
