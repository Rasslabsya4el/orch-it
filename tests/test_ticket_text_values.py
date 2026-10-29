"""Rule 3 of the check on ticket text (#413 unit 6): a ticket body that writes a value of this setup,
or a pattern kind (bare python, a one-OS call, a check name), is refused when it is created or its
body is edited, together with the shape violations; on GitHub the ready label comes off. Every value
here is invented (CODING_STANDARDS.md: another account, another repo, another folder), and the values
are read from invented answers, never from this machine."""

import shlex
from pathlib import Path

import pytest
from fake_hub import REPO, FakeHub
from ticket_fixtures import fixture

from orch_it.guards.values import read_values
from orch_it.tickets import gh_check, hook
from orch_it.tickets.hub import LAYER_MARK

# the bodies of these tests are written in Russian
pytestmark = pytest.mark.usefixtures("ticket_language_ru")

ACCOUNT = "acme-dev"
REPO_NAME = "acme-dev/orch-tools"
HOST = "build-box-7"
HOME = "/opt/nightly-home"
USER = "jdoe"
PROJECTS = "/work/projects"
GIT_NAME = "Pat Example"
GIT_EMAIL = "pat@example.test"
CHECK = "acme-smoke-check"
ONE_OS_CALL = "winreg"
WITH_SKILL = Path(__file__).parent / "fixtures" / "transcripts" / "write_ticket_called.jsonl"
TITLE, BODY = fixture("good_basic")
LITERAL_CODES = {"literal_value", "literal_pattern"}

ANSWERS = {
    ("git", "remote", "get-url", "origin"): f"git@github.com:{REPO_NAME}.git\n",
    ("gh", "api", "user", "--jq", ".login"): f"{ACCOUNT}\n",
    ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"): f"{PROJECTS}/orch-tools/.git\n",
    ("git", "config", "user.name"): f"{GIT_NAME}\n",
    ("git", "config", "user.email"): f"{GIT_EMAIL}\n",
}


def values_from(answers=ANSWERS):
    """This setup's values, read from invented answers: a command not in `answers` gives no output."""

    return read_values(env={"USER": USER}, run=lambda cmd: answers[tuple(cmd)], home=HOME, host=HOST)


VALUES = values_from()

# kind -> (a line of a ticket body that writes the value, the kind as the finding names it)
LINES = {
    "account": (f"Set the owner to `{ACCOUNT}` in the settings.", "account"),
    "repo": (f"The repo {REPO_NAME} keeps the file.", "repo"),
    "host": (f"The run came from {HOST}.", "host"),
    "home": (f"The cache sits in {HOME}/cache.", "home"),
    "user": (f"Run it as USER={USER} on the box.", "user"),
    "projects_folder": (f"The clone sits in {PROJECTS}/orch-tools.", "projects_folder"),
    "git_name": (f"Commits carry the name {GIT_NAME}.", "git_name"),
    "git_email": (f"Commits carry {GIT_EMAIL}.", "git_email"),
    "bare_python": ("Run `python -m pytest -q` to check it.", "bare_python"),
    "one_os_call": (f"The fix calls {ONE_OS_CALL} to read the keys.", "one_os_call"),
    "check_name": (f"The check `{CHECK}` must pass.", "check_name_literal"),
}


@pytest.fixture
def repo(tmp_path):
    """A work tree whose workflows hold one job, named with an invented check name."""

    flows = tmp_path / ".github" / "workflows"
    flows.mkdir(parents=True)
    (flows / "ci.yml").write_text(f"name: ci\non: push\njobs:\n  {CHECK}:\n    name: {CHECK}\n"
                                  "    runs-on: ubuntu-latest\n", "utf-8")
    return tmp_path


def body_with(line, body=BODY):
    return f"{body}\n{line}\n"


def create_payload(tmp, body, title=TITLE):
    path = tmp / "body.md"
    path.write_text(body, "utf-8")
    command = f"gh issue create --repo {REPO} --title {shlex.quote(title)} --body-file {shlex.quote(str(path))}"
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp),
            "transcript_path": str(WITH_SKILL)}


def judge_create(tmp, body, values=VALUES, title=TITLE):
    return hook.judge(create_payload(tmp, body, title), hub=FakeHub(), repo_of=lambda folder: REPO, values=values)


def judge_edit(tmp, body, values=VALUES):
    hub = FakeHub()
    hub.add(10, BODY, ["ready-for-agent"], title=TITLE)
    path = tmp / "edit.md"
    path.write_text(body, "utf-8")
    payload = {"tool_name": "Bash",
               "tool_input": {"command": f"gh issue edit 10 --repo {REPO} --body-file {shlex.quote(str(path))}"},
               "cwd": str(tmp), "transcript_path": str(WITH_SKILL)}
    return hook.judge(payload, hub=hub, repo_of=lambda folder: REPO, values=values)


@pytest.mark.parametrize("kind", sorted(LINES))
def test_a_body_with_each_kind_of_value_is_refused_at_create(repo, kind):
    line, named = LINES[kind]
    refused = judge_create(repo, body_with(line))
    assert refused and LITERAL_CODES & set(refused.codes), kind
    assert f"{named} at line" in refused.message


@pytest.mark.parametrize("kind", sorted(LINES))
def test_a_body_with_each_kind_of_value_is_refused_at_a_body_edit(repo, kind):
    line, named = LINES[kind]
    refused = judge_edit(repo, body_with(line))
    assert refused and LITERAL_CODES & set(refused.codes), kind
    assert f"{named} at line" in refused.message


def test_a_clean_body_passes_at_create_and_at_an_edit(repo):
    assert not judge_create(repo, BODY)
    assert not judge_edit(repo, BODY)


def test_a_literal_and_a_shape_violation_are_reported_together_in_one_refusal(repo):
    bad_title, bad_body = fixture("bad_weight_missing")
    refused = judge_create(repo, body_with(LINES["account"][0], bad_body), title=bad_title)
    assert "weight_missing" in refused.codes and "literal_value" in refused.codes
    assert "weight_missing" in refused.message and "account at line" in refused.message


def test_the_refusal_ends_with_the_not_checked_line_when_a_kind_has_no_value(repo):
    no_email = values_from({k: v for k, v in ANSWERS.items() if k != ("git", "config", "user.email")})
    refused = judge_create(repo, body_with(LINES["account"][0]), values=no_email)
    # the setting is `ru` here (pytestmark): the line is the shipped table's, the words of guards/values.py's table
    assert refused.message.splitlines()[-1] == "не проверено: git_email (не задано)"


def test_a_clean_refusal_of_the_body_without_values_carries_no_not_checked_line(repo):
    refused = judge_create(repo, body_with(LINES["account"][0]))
    assert "не проверено" not in refused.message


# GitHub: the run's own environment gives the values; nothing of this machine is read

GITHUB_ENV = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPO_NAME, "GITHUB_REPOSITORY_OWNER": ACCOUNT,
              "GITHUB_ACTOR": ACCOUNT, "HOME": HOME}


def edited_event(number=1):
    return {"action": "edited", "repository": {"full_name": REPO},
            "issue": {"number": number, "labels": [{"name": "ready-for-agent"}]}}


def check(event, hub, repo):
    return gh_check.check_event(event, hub, env=GITHUB_ENV | {"GITHUB_WORKSPACE": str(repo)},
                                run=lambda cmd: "", home="", host="")


@pytest.mark.parametrize("line", [f"Owner `{ACCOUNT}` set.", f"The cache sits in {HOME}/cache."])
def test_a_ready_issue_edited_to_hold_a_value_of_the_run_loses_ready_and_gets_the_list(repo, line):
    hub = FakeHub()
    hub.add(1, body_with(line), ["ready-for-agent"], title=TITLE)
    result = check(edited_event(), hub, repo)
    assert result.outcome == "removed" and "literal_value" in result.codes
    assert "ready-for-agent" not in hub.issues[(REPO, 1)].labels
    [said] = hub.notes[(REPO, 1)]
    assert LAYER_MARK in said.body and "`literal_value`" in said.body
    # this run's environment names no host, user, git identity or projects folder: each is said, never clean
    assert said.body.splitlines()[-1] == ("не проверено: host (не задано), user (не задано), projects_folder (не задано), "
                                          "git_name (не задано), git_email (не задано)")


def test_a_clean_ready_issue_on_github_keeps_its_label_and_gets_no_comment(repo):
    hub = FakeHub()
    hub.add(1, BODY, ["ready-for-agent"], title=TITLE)
    assert check(edited_event(), hub, repo).outcome == "kept"
    assert (REPO, 1) not in hub.notes
