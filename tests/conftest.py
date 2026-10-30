"""Shared test setup (#421 rule 2): no test reads or writes the real per-user settings file or data folder.

Every test gets ORCH_IT_SETTINGS set, through monkeypatch, to a settings file in a folder of its own under pytest's
temporary base. A test that sets the variable itself runs later and its own monkeypatch value wins.

The language (#453 unit 1) is read from the `settings.json` at the root of the cwd's git work tree. The cwd of every
test is a folder of its own that is no git tree (GIT_CEILING_DIRECTORIES stops the lookup at the test's base), so the
suite's language is English unless a test writes its own tree file.
"""

import pytest
from orch_it.testruns.junit import MARKER_PROPERTY

from language_tree import git_tree, write_language


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory, monkeypatch):
    folder = tmp_path_factory.mktemp("settings")
    path = folder / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    # the per-user data folder (store, locks, caches) is the OS base plus "orch-it": point both bases at the test's own
    monkeypatch.setenv("LOCALAPPDATA", str(folder / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(folder / "share"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(folder))
    cwd = folder / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)


@pytest.fixture(autouse=True)
def no_login_call(monkeypatch):
    """No test asks the real `gh` who the token is (#414 unit 12a): the account key of a counted call falls back to
    the repo's owner, and each test starts with no login cached. A test of the key puts `_read_login` back."""

    from orch_it.events import github_budget

    monkeypatch.setattr(github_budget, "_read_login", lambda: None)
    monkeypatch.setattr(github_budget, "_login", None)
    monkeypatch.setattr(github_budget, "_login_failed_at", None)


@pytest.fixture(autouse=True)
def no_real_gh_in_test_runs(monkeypatch):
    """No test starts the real `gh` (#414 unit 14): every `gh` call of the layer ends in `subprocess.run(["gh", ...])`
    inside `_call_gh` in `orch_it.testruns.github`, so that one process start refuses here as `gh_failed`. A github
    place then reads as GitHub unreadable and is off, whatever default `gh` it was built with (`run_tests`,
    `GithubPlace` with no `gh`, the merge command). The refusal sits at the process start, below `run_gh` and `_call_gh`,
    so a test of their error mapping (timeout, failed exit, missing `gh`) puts its own `subprocess.run` over it and
    still reaches the real code; any other command passes through. A test that wants a `gh` passes its own."""

    import subprocess

    from orch_it.testruns.github import GithubError

    real_run = subprocess.run

    def run(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and command and command[0] == "gh":
            raise GithubError("gh_failed", "no real gh in a test: " + " ".join(command[1:4]))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


@pytest.fixture
def ticket_language_ru(tmp_path, monkeypatch):
    """The human block of a ticket is Russian (#413 unit 10a): the `settings.json` at the root of the git work tree
    of the test's cwd sets `human_language` to `ru` (#453 unit 1; the installation file never carries it). For a test
    module whose ticket bodies (fixtures, texts the code writes) are Russian: `pytestmark = usefixtures(...)`."""
    tree = git_tree(tmp_path / "ru-tree")
    monkeypatch.chdir(tree)
    write_language(tree, "ru")


@pytest.fixture(scope="session")
def templates(tmp_path_factory):
    """One template cache for the whole session of a worker (#421 unit 10): every test module shares it, so a
    template is built once per worker, in that worker's own pytest base folder."""
    from repo_templates import Templates

    return Templates(tmp_path_factory.getbasetemp())


def pytest_collection_modifyitems(items):
    """A test carrying the `os_touching` marker says so in its JUnit report (a `<property>` of its case), so a reader
    of the report alone, the nightly's unmarked-failure check (#414 unit 4), sees which failures the marker covers."""

    for item in items:
        if item.get_closest_marker(MARKER_PROPERTY) is not None:  # the pytest marker and the report property share a name
            item.user_properties.append((MARKER_PROPERTY, "1"))
