"""The values a setup has at run time (#413 unit 1): `read_values` reads the account, repo, host,
home, user, projects folder, git name and git e-mail from the machine or from GitHub Actions, and
`find_in` finds them in a text in any spelling.

Every value below is invented. The home folders are put together from parts, so no home path
stands in this committed file: this repository's own pull requests go through the private-data scan.
"""

from pathlib import Path

import pytest

from orch_it.guards.values import KINDS, Finding, Values, default_run, find_in, read_values

HOME_POSIX = "/ho" + "me/tester9"
HOME_WIN = "C:" + "\\Us" + "ers\\tester9"
RUNNER_HOME = "/ho" + "me/runner-zz"

MACHINE = {
    "user.name": "Tess Tester",
    "user.email": "t9@example.invalid",
    "remote get-url": "https://github.com/acct-x/proj-y.git\n",
    "git-common-dir": "/srv/ws/proj-y/.git\n",
    "api user": "acct-x\n",
}


def fake_run(answers):
    """A `run` that answers the command holding one of the keys and fails on any other."""

    def run(cmd):
        line = " ".join(cmd)
        for key, answer in answers.items():
            if key in line:
                return answer
        raise FileNotFoundError(cmd[0])

    return run


def machine(**over):
    arguments = dict(env={"USERNAME": "tester9"}, run=fake_run(MACHINE), home=HOME_WIN, host="testbox-17")
    return read_values(**{**arguments, **over})


def kinds_in(text, values):
    return {finding.kind for finding in find_in(text, values)}


LINES = {
    "account": "the owner is acct-x here",
    "repo": "clone acct-x/proj-y now",
    "host": "ssh testbox-17 -p 22",
    "home": f"cd {HOME_WIN}\\src",
    "user": "logged in as tester9",
    "projects_folder": "cd /srv/ws/other",
    "git_name": "Author: Tess Tester",
    "git_email": "mail to T9@Example.Invalid please",
}


def test_every_kind_is_read_and_found_with_its_line():
    values = machine()
    assert set(LINES) == set(KINDS)
    for kind, line in LINES.items():
        findings = find_in("first\nsecond\n" + line + "\n", values)
        assert any(f.kind == kind and f.line == 3 for f in findings), kind
    assert values.not_checked == ()
    assert values.render_not_checked() == ""


def test_a_clean_text_has_no_finding():
    assert find_in("nothing of the machine here\nacct-xylophone", machine()) == []


@pytest.mark.parametrize("spelling", [
    HOME_WIN,
    HOME_WIN.replace("\\", "/"),
    HOME_WIN.lower().replace("tester9", "TESTER9"),
    HOME_WIN.replace("\\", "\\\\"),
    "/c/Us" + "ers/tester9",
])
def test_a_windows_home_is_found_in_every_spelling(spelling):
    assert "home" in kinds_in(f"open {spelling}\\file", machine())


def test_a_posix_home_is_found_and_a_longer_name_is_not():
    values = machine(home=HOME_POSIX)
    assert "home" in kinds_in(f"cd {HOME_POSIX}/x", values)
    assert "home" not in kinds_in(f"cd {HOME_POSIX}9/x", values)
    assert "home" not in kinds_in(HOME_WIN + "99", machine())


def test_a_name_inside_a_longer_word_is_not_a_hit():
    values = machine()
    assert "host" not in kinds_in("xtestbox-179", values)
    assert "host" in kinds_in("(testbox-17)", values)
    assert "user" not in kinds_in("tester99", values)


def test_a_finding_carries_the_kind_the_line_and_only_the_ends_of_the_text():
    (finding,) = [f for f in find_in("ssh testbox-17", machine()) if f.kind == "host"]
    assert finding == Finding(kind="host", redacted="t..7", line=1)
    assert "testbox-17" not in repr(finding)
    assert "testbox-17" not in repr(machine())


def test_an_absent_value_is_listed_with_its_reason_and_the_others_are_still_found():
    values = read_values(env={}, run=fake_run({"user.email": "t9@example.invalid"}), home="", host="")
    assert values.git_email == ("t9@example.invalid",)
    assert values.account is None and values.host is None and values.home is None
    reasons = dict(values.not_checked)
    assert set(reasons) == set(KINDS) - {"git_email"}
    assert set(reasons.values()) == {"not set"}
    line = values.render_not_checked()
    assert line.startswith("not checked: ")
    assert "account (not set)" in line and "git_email" not in line
    assert kinds_in("mail t9@example.invalid", values) == {"git_email"}


def test_a_value_too_short_to_scan_is_listed_and_never_scanned():
    values = machine(env={"USERNAME": "ab"})
    assert values.user is None
    assert ("user", "too short to scan") in values.not_checked
    assert "user (too short to scan)" in values.render_not_checked()
    assert "user" not in kinds_in("ab ab ab", values)
    assert "git_email" in kinds_in("t9@example.invalid", values)


def test_github_actions_gives_the_runner_values_no_list_wrote_down():
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "acct-x/proj-y",
        "GITHUB_REPOSITORY_OWNER": "acct-x",
        "GITHUB_ACTOR": "actor-q",
        "HOME": RUNNER_HOME,
        "RUNNER_TEMP": RUNNER_HOME + "/work/_temp",
        "USER": "runneradm",
        "COMPUTERNAME": "ci-host-5",
    }
    values = read_values(env=env, run=fake_run({}), home="", host="")
    assert kinds_in(f"path {RUNNER_HOME}/work/x", values) == {"home"}
    assert "home" in kinds_in(f"temp {RUNNER_HOME}/work/other", values)
    assert kinds_in("by actor-q", values) == {"account"}
    assert kinds_in("repo acct-x/proj-y", values) == {"repo", "account"}
    assert kinds_in("on ci-host-5", values) == {"host"}
    assert kinds_in("as runneradm", values) == {"user"}


def test_the_env_of_a_plain_machine_is_not_read_as_a_runner():
    values = read_values(env={"HOME": RUNNER_HOME, "GITHUB_REPOSITORY": "acct-x/proj-y"},
                         run=fake_run({}), home="", host="")
    assert values.home is None and values.repo is None


def test_no_value_of_the_real_machine_stands_in_this_file():
    def run(cmd):  # the real git, never the network
        if cmd[0] != "git":
            raise FileNotFoundError(cmd[0])
        return default_run(cmd)

    real = read_values(run=run)
    unscanned = {"user"}  # a short common word as a user name would hit this file's prose
    scanned = Values(**{kind: (getattr(real, kind) if kind not in unscanned else None) for kind in KINDS})
    assert find_in(Path(__file__).read_text("utf-8"), scanned) == []
