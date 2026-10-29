"""The one scanner carries the check of #413 (unit 5): the values of the setup it runs on (account, repo,
host, home, user, projects folder, git name and e-mail) and the pattern kinds (bare python, a one-OS call,
a check name written as a literal), beside the kinds of #395.

Every value is invented (CODING_STANDARDS.md): the account `acme-dev`, the repo `acme-dev/orch-tools`, the
host `build-box-7`, the user `jdoe`. The setup is a throwaway git repo whose own config and environment name
them; nothing of the machine the test runs on is read or written. The seams are `python -m orch_it guards
scan` and `prepush.run_pre_push`."""

import os
import re
import socket
from pathlib import Path

import pytest

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.guards import prepush, scanner, values
from scan_repos import Repo

ACCOUNT = "acme-dev"
REPO = "acme-dev/orch-tools"
HOST = "build-box-7"
USER = "jdoe"
GIT_NAME = "Quinn Sample"
GIT_EMAIL = "quinn@sample.invalid"
JOB = "check-alpha"
RULE = "A value that differs per repo, account, machine or OS is a setting with its default, never a literal in code, tests, rules or tickets."
ZEROS = "0" * 40

# the 10 kinds: the file the line goes in, the line (a function of the setup), the labels one of which must be found
PLANTED = {
    "account": ("a.md", "the owner is {account} here\n", {"account"}),
    "repo": ("b.md", "clone {repo} now\n", {"repo"}),
    "host": ("c.txt", "ssh {host} now\n", {"host"}),
    "home": ("d.txt", "cd {home}/notes\n", {"home"}),
    "user": ("e.txt", "export USER={user}\n", {"user"}),
    "projects_folder": ("f.txt", "cd {projects}/other\n", {"projects_folder"}),
    "git_name": ("g.txt", "by {git_name} today\n", {"git_name"}),
    "git_email": ("h.txt", "mail {git_email} now\n", {"git_email"}),
    "bare_python": ("i.sh", "python -m pytest -q\n", {"bare_python"}),
    "one_os_call": ("j.py", "def stop(pid):\n    os.startfile(pid)\n", {"one_os_call"}),
    "check_name": ("k.py", "if name == '{job}':\n    pass\n", {"check_name_literal"}),
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text('{"repo": "acme-x/widgets-y"}', "utf-8")  # the repo the events are written under
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


class Setup:
    """The invented machine: a home folder, a host, a user, a git repo with an origin and an identity."""

    def __init__(self, tmp_path, monkeypatch):
        self.home = str(Path("/zz-fake") / USER)
        self.projects = str(tmp_path)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(self.home)))
        monkeypatch.setattr(socket, "gethostname", lambda: HOST)
        for name in ("USERNAME", "USER", "LOGNAME"):
            monkeypatch.setenv(name, USER)
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        monkeypatch.setattr(values, "default_run", self.no_gh)
        self.repo = Repo(tmp_path / "r")
        self.repo.git("config", "user.name", GIT_NAME)
        self.repo.git("config", "user.email", GIT_EMAIL)
        self.repo.git("remote", "add", "origin", f"https://github.com/{REPO}.git")

    @staticmethod
    def no_gh(cmd):
        raise OSError("no gh here")

    def line(self, text):
        return text.format(account=ACCOUNT, repo=REPO, host=HOST, home=self.home, user=USER, projects=self.projects,
                           git_name=GIT_NAME, git_email=GIT_EMAIL, job=JOB)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    return Setup(tmp_path, monkeypatch)


def workflow(setup):
    return {".github/workflows/ci.yml": f"name: ci\njobs:\n  tests:\n    name: {JOB}\n    runs-on: x\n"}


def planted(setup):
    files = {name: setup.line(text) for name, text, _ in PLANTED.values()}
    return setup.repo.commit(workflow(setup) | files)


def scan(capsys, setup, *argv):
    code = main(["guards", "scan", "--work-tree", str(setup.repo.path), *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def found(out):
    """file -> the kinds the lines of a scan's output name for it."""
    kinds = {}
    for text in out.splitlines():
        match = re.match(r"\w+ content (\S+):\d+ (\w+)", text)
        if match:
            kinds.setdefault(match.group(1), set()).add(match.group(2))
    return kinds


def check_all_ten(by_file):
    for name, (file, _, labels) in PLANTED.items():
        assert labels <= by_file.get(file, set()), (name, by_file.get(file))


def every_finding_cites_the_rule(out):
    # a home path of the kinds of #395 (a tmp folder under a user's home on Windows) keeps its old line
    lines = [text for text in out.splitlines() if " content " in text and text.split()[3] in scanner.RULE_KINDS]
    assert len(lines) >= len(PLANTED) and all(RULE in text for text in lines), out


def test_the_rule_is_the_first_sentence_of_the_standard():
    standard = (Path(__file__).parent.parent / "CODING_STANDARDS.md").read_text("utf-8").splitlines()
    assert next(line for line in standard if line and not line.startswith("#")) == RULE
    assert scanner.RULE == RULE


def test_a_tree_scan_finds_a_planted_literal_of_each_kind_and_cites_the_rule(setup, capsys):
    planted(setup)
    code, out, _ = scan(capsys, setup, "--tree")
    assert code == 1
    by_file = found(out)
    check_all_ten(by_file)
    every_finding_cites_the_rule(out)


def test_a_range_scan_finds_the_literals_of_the_added_lines(setup, capsys):
    base = setup.repo.commit({"README.md": "clean\n"})
    planted(setup)
    code, out, _ = scan(capsys, setup, "--range", f"{base}..HEAD")
    assert code == 1
    check_all_ten(found(out))
    every_finding_cites_the_rule(out)


def test_a_range_scan_judges_a_line_by_the_file_it_is_in(setup, capsys):
    """An OS test above the call makes it a branch on the OS; the context is the file, not the added lines."""
    guarded = "import sys\nif sys.platform == 'win32':\n    os.startfile(1)\n"
    base = setup.repo.commit({"ok.py": guarded})
    setup.repo.commit({"ok.py": guarded + "# more\n"})
    code, out, _ = scan(capsys, setup, "--range", f"{base}..HEAD")
    assert "one_os_call" not in out


def test_a_push_of_each_kind_is_refused_naming_the_rule(setup, tmp_path, capsys):
    base = setup.repo.commit({"README.md": "clean\n"})
    head = planted(setup)
    store = Store.open(tmp_path / "store")
    result = prepush.run_pre_push(f"refs/heads/main {head} refs/heads/main {base}\n", store,
                                  remote_url="https://example.invalid/o/n.git", remote_name="origin",
                                  work_tree=setup.repo.path, visibility=lambda url: "public")
    err = capsys.readouterr().err
    assert result == ("", 1)
    for name, (file, _, labels) in PLANTED.items():
        assert any(line.split()[2].startswith(file) and line.split()[3].split()[0] in labels
                   for line in err.splitlines() if " content " in line), name
    every_finding_cites_the_rule(err)


def test_a_value_the_setup_lacks_is_named_not_checked_even_when_the_scan_is_clean(setup, capsys):
    setup.repo.git("config", "--unset", "user.email")
    setup.repo.commit({"README.md": "clean\n"})
    code, out, _ = scan(capsys, setup, "--tree")
    assert code == 0
    assert out.splitlines()[-1] == "not checked: git_email (not set)"
    assert out.splitlines()[0] == "hits=0 commits=1"
    # the other kinds still run
    setup.repo.commit({"a.txt": f"by {GIT_NAME}\n"})
    code, out, _ = scan(capsys, setup, "--tree")
    assert code == 1 and "git_name" in out and out.endswith("not checked: git_email (not set)\n")


def test_a_clean_scan_of_a_complete_setup_prints_no_not_checked_line(setup, capsys):
    setup.repo.commit({"README.md": "clean\n"})
    # gh cannot say the account; the origin does (it is read from there too)
    code, out, _ = scan(capsys, setup, "--tree")
    assert (code, out) == (0, "hits=0 commits=1\n")


def test_the_prepush_of_a_clean_push_names_what_it_did_not_check(setup, tmp_path, capsys):
    setup.repo.git("config", "--unset", "user.email")
    base = setup.repo.commit({"README.md": "clean\n"})
    head = setup.repo.commit({"b.txt": "fine\n"})
    result = prepush.run_pre_push(f"refs/heads/main {head} refs/heads/main {base}\n", Store.open(tmp_path / "s"),
                                  remote_url="https://example.invalid/o/n.git", remote_name="origin",
                                  work_tree=setup.repo.path, visibility=lambda url: "public")
    assert result == ("", 0)
    assert "not checked: git_email (not set)" in capsys.readouterr().err


def test_the_same_literal_in_research_and_in_an_exceptions_entry_passes(setup, capsys):
    text = setup.line("clone {repo} by {git_name}\n")
    setup.repo.commit({"docs/research/x.md": text, "notes.txt": text, "other.txt": text})
    code, out, _ = scan(capsys, setup, "--tree")
    assert code == 1 and set(found(out)) == {"notes.txt", "other.txt"}
    entries = ('[[exception]]\nfile = "notes.txt"\npattern = "clone"\nkinds = ["repo", "account", "git_name"]\nreason = "a record of the clone line"\n\n'
               '[[exception]]\nfile = "other.txt"\npattern = "clone"\nkinds = ["repo", "account", "git_name"]\nreason = "another record"\n')
    setup.repo.commit({"guards-exceptions.toml": entries})
    code, out, _ = scan(capsys, setup, "--tree")
    assert (code, found(out)) == (0, {})


def test_an_entry_excuses_only_its_kinds_on_a_line(setup, capsys):
    """A workflow line with a bare interpreter and a repo name: the entry for the interpreter leaves the repo."""

    entry = ('[[exception]]\nfile = "w.yml"\npattern = "python -m orch_it"\nkinds = ["bare_python"]\n'
             'reason = "the runner\'s interpreter"\n')
    setup.repo.commit({"w.yml": setup.line("        run: python -m orch_it --repo {repo}\n"), "guards-exceptions.toml": entry})
    code, out, _ = scan(capsys, setup, "--tree")
    assert code == 1 and found(out) == {"w.yml": {"repo", "account"}}


def test_the_settings_file_is_an_allowed_place(setup, tmp_path, monkeypatch, capsys):
    inside = setup.repo.path / "conf" / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(inside))
    setup.repo.commit({"conf/settings.json": f'{{"repo": "{REPO}"}}\n'})
    code, out, _ = scan(capsys, setup, "--tree")
    assert (code, found(out)) == (0, {})


def test_an_exceptions_entry_with_no_reason_exits_2_naming_the_entry(setup, capsys):
    setup.repo.commit({"notes.txt": f"clone {REPO}\n",
                       "guards-exceptions.toml": '[[exception]]\nfile = "notes.txt"\npattern = "clone"\nkinds = ["repo"]\n'})
    code, out, err = scan(capsys, setup, "--tree")
    assert (code, out) == (2, "")
    assert "entry 1" in err and "notes.txt" in err and "reason" in err


def test_a_scan_on_github_finds_the_runners_home(setup, monkeypatch, capsys):
    """GITHUB_ACTIONS=true: the run's home, user, repo and host are read from the runner's environment."""
    runner_home = "/zz-runner/ci-home"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("HOME", runner_home)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme-ci/ci-repo")
    monkeypatch.setenv("USER", "ci-runner-x")
    monkeypatch.setenv("COMPUTERNAME", "ci-box-9")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/zz-other-home")))
    setup.repo.commit({"a.txt": f"cd {runner_home}/work\n", "b.txt": "see acme-ci/ci-repo\n",
                       "c.txt": "export USER=ci-runner-x\n", "d.txt": "ssh ci-box-9 now\n"})
    code, out, _ = scan(capsys, setup, "--tree", "--machine-independent")
    assert code == 1
    assert found(out)["a.txt"] == {"home"} and found(out)["b.txt"] >= {"repo"}
    assert found(out)["c.txt"] == {"user"} and found(out)["d.txt"] == {"host"}
    assert RULE in out


def test_a_second_setup_is_refused_by_its_own_values_and_not_by_the_first_ones(setup, tmp_path):
    """Another user's home, host name and user, injected through `read_values`' arguments: the scan refuses
    THAT setup's values, and a text holding the first setup's values passes."""
    answers = {("git", "remote", "get-url", "origin"): "https://github.com/other-co/other-repo.git",
               ("gh", "api", "user", "--jq", ".login"): "other-co",
               ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"): "/zz-second/projects/x/.git",
               ("git", "config", "user.name"): "Robin Other", ("git", "config", "user.email"): "robin@other.invalid"}
    patterns = scanner.Patterns.of_machine(
        work_tree=setup.repo.path, env={"USER": "robin"}, home="/zz-second/robin", host="other-box-3",
        run=lambda cmd: answers[tuple(cmd)])
    second = {"h.txt": "cd /zz-second/robin/notes\n", "i.txt": "ssh other-box-3\n", "j.txt": "export USER=robin\n",
              "k.txt": "by Robin Other\n", "l.txt": "clone other-co/other-repo\n"}
    first = setup.line("{home}/a {host} export USER={user} {git_name} {repo}\n")
    setup.repo.commit(second | {"first.txt": first})
    hits = scanner.scan_tree(setup.repo.path, patterns)
    by_file = {}
    for hit in hits:
        by_file.setdefault(hit.file, set()).add(hit.kind)
    assert {"home"} <= by_file["h.txt"] and {"host"} <= by_file["i.txt"] and {"user"} <= by_file["j.txt"]
    assert {"git_name"} <= by_file["k.txt"] and {"repo"} <= by_file["l.txt"]
    assert "first.txt" not in by_file


def test_a_git_email_in_a_new_folder_file_is_refused_on_a_range_and_on_a_push(setup, tmp_path, capsys):
    base = setup.repo.commit({"README.md": "clean\n"})
    head = setup.repo.commit({"a/b/c.txt": f"mail {GIT_EMAIL}\n"})
    code, out, _ = scan(capsys, setup, "--range", f"{base}..HEAD")
    assert code == 1 and f"a/b/c.txt:1 git_email - {RULE}" in out
    for visibility in ("public", "private"):  # the standard's kinds run on every push
        result = prepush.run_pre_push(f"refs/heads/main {head} refs/heads/main {base}\n", Store.open(tmp_path / visibility),
                                      remote_url="https://example.invalid/o/n.git", remote_name="origin",
                                      work_tree=setup.repo.path, visibility=lambda url, v=visibility: v)
        assert result == ("", 1), visibility
        assert f"a/b/c.txt:1 git_email - {RULE}" in capsys.readouterr().err


def push_of(setup, tmp_path, base, head):
    return prepush.run_pre_push(f"refs/heads/main {head} refs/heads/main {base}\n", Store.open(tmp_path / "ps"),
                                remote_url="https://example.invalid/o/n.git", remote_name="origin",
                                work_tree=setup.repo.path, visibility=lambda url: "private")


def test_a_push_with_an_exceptions_entry_with_no_reason_is_refused_and_the_other_kinds_still_run(setup, tmp_path, capsys):
    base = setup.repo.commit({"README.md": "clean\n"})
    head = setup.repo.commit({"guards-exceptions.toml": '[[exception]]\nfile = "n.txt"\npattern = "x"\nkinds = ["git_name"]\n',
                              "n.txt": f"by {GIT_NAME}\n"})
    assert push_of(setup, tmp_path, base, head) == ("", 1)
    err = capsys.readouterr().err
    assert "guards-exceptions.toml" in err and "entry 1" in err and "reason" in err
    assert f"n.txt:1 git_name - {RULE}" in err  # the scan ran without the list


def test_a_push_reads_the_exceptions_list_of_the_pushed_commit_when_it_is_not_checked_out(setup, tmp_path, capsys):
    entry = '[[exception]]\nfile = "n.txt"\npattern = "by"\nkinds = ["git_name"]\nreason = "a record"\n'
    base = setup.repo.commit({"README.md": "clean\n"})
    head = setup.repo.commit({"guards-exceptions.toml": entry, "n.txt": f"by {GIT_NAME}\n"})
    setup.repo.git("checkout", "-q", base)  # the work tree is at the base: its tree has no list
    assert push_of(setup, tmp_path, base, head) == ("", 0)
    broken = setup.repo.commit({"guards-exceptions.toml": '[[exception]]\nfile = "n.txt"\n'}, message="broken")
    setup.repo.git("checkout", "-q", base)
    assert push_of(setup, tmp_path, base, broken) == ("", 1)
    assert "guards-exceptions.toml" in capsys.readouterr().err


def test_machine_independent_keeps_its_email_allowance(setup, capsys):
    setup.repo.commit({"a.txt": "mail jane.doe" + "@corp.io\n"})
    code, out, _ = scan(capsys, setup, "--tree", "--machine-independent")
    assert code == 1 and "email" in out
    code, out, _ = scan(capsys, setup, "--tree", "--machine-independent", "--allow-email", "jane.doe" + "@corp.io")
    assert code == 0
