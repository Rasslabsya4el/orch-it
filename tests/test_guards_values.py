"""The values a setup has at run time (#413 unit 1): `read_values` reads the account, repo, host,
home, user, projects folder, git name and git e-mail from the machine or from GitHub Actions, and
`find_in` finds them in a text in any spelling.

Every value below is invented. The home folders are built with the path classes from a folder
name and a user name, so no home path is spelled in this committed file, and the file passes the
private-data scan that this repository's own pull requests go through.
"""

from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from orch_it.guards.values import KINDS, Finding, Values, default_run, find_in, read_values

HOME_POSIX = str(PurePosixPath("/") / "home" / "tester9")
HOME_WIN = str(PureWindowsPath("C:/") / "Users" / "tester9")
RUNNER_HOME = str(PurePosixPath("/") / "home" / "runner-zz")
MSYS_HOME = "/c/" + "/".join(PureWindowsPath(HOME_WIN).parts[1:])

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
    "user": "USER=tester9",
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


@pytest.mark.parametrize("remote", [
    "git@github.com-work:acct-x/proj-y.git",
    "git@github.com:acct-x/proj-y.git",
    "ssh://git@github.com-alias/acct-x/proj-y",
    "https://github.com/acct-x/proj-y/",
])
def test_the_origin_gives_the_repo_in_every_remote_spelling_and_ssh_alias(remote):
    values = machine(run=fake_run({**MACHINE, "remote get-url": remote + "\n", "api user": ""}))
    assert values.repo == ("acct-x/proj-y",)
    assert values.account == ("acct-x",)


def test_a_remote_that_is_not_github_gives_no_repo():
    values = machine(run=fake_run({**MACHINE, "remote get-url": "git@example.invalid:acct-x/proj-y.git"}))
    assert values.repo is None and ("repo", "not set") in values.not_checked


def test_a_clean_text_has_no_finding():
    assert find_in("nothing of the machine here\nacct-xylophone", machine()) == []


@pytest.mark.parametrize("spelling", [
    HOME_WIN,
    HOME_WIN.replace("\\", "/"),
    HOME_WIN.lower().replace("tester9", "TESTER9"),
    HOME_WIN.replace("\\", "\\\\"),
    MSYS_HOME,
])
def test_a_windows_home_is_found_in_every_spelling(spelling):
    assert "home" in kinds_in(f"open {spelling}\\file", machine())


def test_a_posix_home_is_found_and_a_longer_name_is_not():
    values = machine(home=HOME_POSIX)
    assert "home" in kinds_in(f"cd {HOME_POSIX}/x", values)
    assert "home" not in kinds_in(f"cd {HOME_POSIX}9/x", values)
    assert "home" not in kinds_in(HOME_WIN + "99", machine())


def identity_lines(name):
    return [
        str(PureWindowsPath("C:/") / "Users" / name / "x"),
        str(PurePosixPath("/") / "home" / name / "x"),
        str(PurePosixPath("/") / "srv" / name),  # at the end of a path
        f"/{name}/",
        f"ssh {name}@box",
        f"USER={name}",
        f"set USERNAME={name}",
        f"RUNNER_USER: {name}",
        f"docker run --user {name} img",
        f"user={name}",
        f"user.name = {name}",
        f'{{"USER": "{name}"}}',
    ]


@pytest.mark.parametrize("name", ["tester9", "user"])
def test_a_user_name_is_found_in_its_identity_contexts(name):
    values = machine(env={"USERNAME": name})
    for line in identity_lines(name):
        assert "user" in kinds_in(line, values), line


@pytest.mark.parametrize("name", ["tester9", "user"])
def test_a_user_name_as_a_bare_word_is_not_a_hit(name):
    values = machine(env={"USERNAME": name})
    for line in (f"the {name} runs it", f"each {name}, then the {name}s", f"{name} {name}",
                 f"{name}.json", f"Users of {name}-x", f"@{name} wrote this"):
        assert "user" not in kinds_in(line, values), line
    assert "user" not in kinds_in(f"x{name}@box and {name}9@box", values)


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


def test_a_reason_no_table_names_is_written_as_it_is():
    values = Values(not_checked=(("git_email", "some other reason"),))
    assert values.render_not_checked() == "not checked: git_email (some other reason)"


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
    assert kinds_in("USER=runneradm", values) == {"user"}


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
    unscanned = {"user"}  # the cases above write the name `user` in its identity contexts
    scanned = Values(**{kind: (getattr(real, kind) if kind not in unscanned else None) for kind in KINDS})
    assert find_in(Path(__file__).read_text("utf-8"), scanned) == []


# --- references to one GitHub object (#415 unit 7): an allowed place for a value in a text ----------------------
# The owner and the repo are built from parts; the same invented account and repo `machine()` reads.

OWNER = "acct" + "-x"
REPO = "proj" + "-y"
SLUG = OWNER + "/" + REPO
SHA_SHORT = "0a1b2c3"  # seven hex characters, the shortest commit reference
SHA_FULL = "0a1b2c3d" * 5  # forty hex characters, the longest
URL = "https://github.com/" + SLUG

ALLOWED_FORMS = [
    f"{URL}/issues/415",
    f"{URL}/pull/415",
    f"{URL}/commit/{SHA_SHORT}",
    f"{SLUG}#415",
    f"{SLUG}@{SHA_FULL}",
]


@pytest.mark.parametrize("form", ALLOWED_FORMS)
def test_a_reference_to_one_github_object_is_no_hit(form):
    assert find_in(f"Refs {form}", machine()) == []


@pytest.mark.parametrize("text, expected", [
    (f"Refs {URL}/pull/415/files {SLUG}", ["account", "repo"]),        # a repo value after `/files`
    (f"Refs {URL}/issues/415#issuecomment-1 {OWNER}", ["account"]),    # an account value after the fragment
    (f"Refs {SLUG}#415 {SLUG}", ["account", "repo"]),                  # a repo value after the short form
])
def test_what_follows_a_reference_is_ordinary_text(text, expected):
    # the reference itself is no hit; the setup value after it still is, so a span blanked over the tail fails here
    assert sorted(f.kind for f in find_in(text, machine())) == sorted(expected)


def test_a_relative_two_segment_path_with_a_hash_is_the_accepted_gap_of_the_short_form():
    # documented gap (values.py `_REFERENCE`): `dir/<value>#3` matches `owner/repo#N`, so the value is no hit
    assert find_in(f"see dir/{OWNER}#3 for it", machine()) == []
    assert kinds_in(f"see dir/{OWNER} for it", machine()) == {"account"}


@pytest.mark.parametrize("form", [
    URL,                                                   # the repo root
    URL + ".git",                                          # clone, https
    f"git@github.com:{SLUG}",                              # clone, ssh
    f"{URL}/tree/main/src",                                # tree
    f"{URL}/blob/main/README.md",                          # blob
    f"https://raw.githubusercontent.com/{SLUG}/main/README.md",  # raw
    f"{URL}/issues/{{n}}",                                 # issue with a template
    f"{URL}/issues/",                                      # issue with no number
])
def test_a_link_that_is_not_a_reference_to_one_object_is_still_refused(form):
    assert {"account", "repo"} <= kinds_in(f"Refs {form}", machine())


@pytest.mark.parametrize("commit", ["abcdef", "a" * 41])
def test_a_commit_reference_is_seven_to_forty_hex_characters(commit):
    assert {"account", "repo"} <= kinds_in(f"Refs {URL}/commit/{commit}", machine())


def test_a_bare_account_name_in_prose_is_a_hit():
    assert kinds_in(f"the owner is {OWNER} here", machine()) == {"account"}


def test_a_bare_account_next_to_an_allowed_link_is_still_a_hit_on_its_own_span():
    own_line = f"Refs {URL}/issues/415\nthe owner {OWNER} wrote it"
    assert [(f.kind, f.line) for f in find_in(own_line, machine())] == [("account", 2)]
    same_line = f"Refs {URL}/pull/415 by {OWNER}"
    assert [(f.kind, f.line) for f in find_in(same_line, machine())] == [("account", 1)]


def test_a_path_that_only_looks_like_a_short_reference_keeps_its_value():
    assert "home" in kinds_in(f"cd {HOME_POSIX}/{REPO}#3", machine(home=HOME_POSIX))
