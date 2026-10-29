"""The texts orch-it writes on a GitHub runner come out in the language of the checkout's `settings.json`
(#453 unit 3, acceptance row 1).

A runner has no installation file and no language variable: the only thing that says the language is the
`settings.json` at the root of the checkout. Each test makes a copy of the checkout (a real `git init` in a
tmp folder holding a copy of this repo's root `settings.json`), makes it the cwd, builds one of the texts
the workflows write through the code path the command takes, and checks that the human part holds the words of
the `ru` table when the file says ru and of the `en` table when the file is removed.

Seams, one per text: `update.checked_issue` (`skills report`), `check_main` (`main check`: the red-on-main
ticket in its singular, plural and by-commit forms, and the no-result ticket), `reconcile` (the workflow-watch
ticket, its comment for each way a run ends, and its closing comment), `gh_check.check_event` (the ticket-shape
comment), `python -m orch_it guards scan` over a tree and over a range (the citation line).
The words come from the tables themselves (the literal pieces of a key's string around its `{fields}`), never
from a literal of the test.
"""

import json
import re
import tomllib
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import pytest
from fake_github import FAILED, NO_RESULT, PASSED, REPO, REQUIRED, FakeGitHub
from fake_hub import FakeHub
from language_tree import write_language
from scan_repos import Repo

from orch_it import settings
from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.guards import values
from orch_it.merge.main_check import check_main
from orch_it.merge.reconcile import reconcile
from orch_it.merge.settings import MergeSettings
from orch_it.skills import update
from orch_it.skills.classify import Change
from orch_it.skills.copies import Manifest
from orch_it.tickets import gh_check
from orch_it.tickets.reader import READY_LABEL, TRIAGE_LABEL

ROOT = Path(__file__).parent.parent
LANGUAGES = ("ru", "en")
HUMAN_HEADING, AGENT_HEADING = "## For the human", "## For the agent"
OWNER = REPO.split("/")[0]
# put together at run time: a bare interpreter word written out in this file would be a finding of the repo's own scan
BARE = "py" + "thon"
RED_KEYS = ["red_main_title", "red_main_what", "red_main_goal", "red_main_plan", "red_main_result", "red_main_undo"]
WATCH_KEYS = ["watch_title", "watch_what", "watch_goal", "watch_plan", "watch_result", "watch_undo"]
SHAPE_KEYS = ["check_took_label", "human_field_language_rule", "not_checked_line", "not_checked_not_set"]
SCAN_KEYS = ["coding_rule", "not_checked_line", "not_checked_not_set"]


def table(code):
    return tomllib.loads((settings.PACKAGE_TABLES / f"{code}.toml").read_text("utf-8"))


def words(code, key):
    """The literal pieces of the string `key` in the table of `code`, around its `{fields}`."""

    return [piece.strip() for piece in re.split(r"\{[^}]*\}", table(code)[key]) if piece.strip()]


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """The copy of the checkout the runner starts in: a git work tree with the root `settings.json` copied in."""

    repo = Repo(tmp_path / "checkout")
    repo.commit({"settings.json": (ROOT / "settings.json").read_text("utf-8")})
    monkeypatch.chdir(repo.path)
    return repo


def in_language(checkout, code):
    """The copy's tree file says ru as the repo's root file does; for en the file is removed."""

    if code == "en":
        write_language(checkout.path, None)
        assert not (checkout.path / "settings.json").exists()
    else:
        assert json.loads((checkout.path / "settings.json").read_text("utf-8")) == {"human_language": code}


def human_part(body):
    return body.split(AGENT_HEADING)[0].split(HUMAN_HEADING)[1]


@dataclass
class Case:
    """What a builder is given: the checkout copy, a folder for its store, the language the tree file says, pytest's tools."""

    checkout: Repo
    folder: Path
    code: str
    monkeypatch: pytest.MonkeyPatch
    capsys: pytest.CaptureFixture


def no_command(cmd):
    """The one stub for every command a text's code could run (`gh`, `git config`): this machine has none of them."""

    raise OSError("no command here")


def required_jobs(first, rest=PASSED):
    return {name: first if place == 0 else rest for place, name in enumerate(REQUIRED)}


def world(case):
    case.folder.mkdir(exist_ok=True)
    return FakeGitHub(), Store.open(case.folder / "events")


# every builder returns (the text to look in, the table keys whose words are in it)

def skills_issue(case, change=None, keys=update.HUMAN_KEYS):
    change = change or Change("tdd", "text_changed", "skills/x/tdd/SKILL.md", "skills/x/tdd/SKILL.md", ["SKILL.md"])
    title, body, broken = update.checked_issue(update.Check(Manifest("u", "1" * 40, {}, {}), "2" * 40, {}, [change]))
    assert broken == []
    return human_part(body), list(keys.values())


def skills_issue_for_the_brain(case):
    added = Change("newskill", "added", "skills/x/newskill/SKILL.md", None, ["SKILL.md"])
    return skills_issue(case, added, update.HUMAN_KEYS | update.HUMAN_KEYS_BRAIN)


def red_on_main(case, *, failing, pr, count_key):
    """The red-on-main ticket for `failing` tests, brought by pull request `pr` (None: a commit with no pull request)."""

    hub, store = world(case)
    hub.main_run(hub.land(number=pr), required_jobs(FAILED), failing=failing)
    [number] = check_main(hub, store, REPO, "main").opened
    issue = hub.issues[(REPO, number)]
    by = "red_main_by_pr" if pr else "red_main_by_commit"
    return issue.title + "\n" + human_part(issue.body), [*RED_KEYS, by, count_key]


def no_result(case):
    hub, store = world(case)
    sha = hub.land()
    run_id = hub.main_run(sha, required_jobs(NO_RESULT))
    assert check_main(hub, store, REPO, "main").outcome == "rerun"
    hub.main_runs.pop(0)
    hub.main_run(sha, required_jobs(NO_RESULT), attempt=2, run_id=run_id)
    [number] = check_main(hub, store, REPO, "main").brain
    issue = hub.issues[(REPO, number)]
    assert TRIAGE_LABEL in issue.labels
    keys = ["no_result_title", "no_result_what", "no_result_goal", "no_result_plan", "no_result_result",
            "no_result_undo"]
    return issue.title + "\n" + human_part(issue.body), keys


def sweep(hub, store):
    return reconcile(hub, store, REPO, "main", settings=MergeSettings(), now=hub.now)


def watched(case):
    """A hub whose workflow `example-flow` ended red once and was swept: its watch ticket is open."""

    hub, store = world(case)
    hub.end_run("example-flow", 4117, "failure")
    sweep(hub, store)
    [number] = hub.issues_with_label(REPO, TRIAGE_LABEL)
    return hub, store, number


def watch_ticket(case):
    hub, store, number = watched(case)
    issue = hub.issues[(REPO, number)]
    return issue.title + "\n" + human_part(issue.body), WATCH_KEYS


def watch_comment(case, *, outcome, jobs, how_key):
    """The comment on the open watch ticket when the workflow's next run ends as `outcome` with `jobs` jobs started."""

    hub, store, number = watched(case)
    hub.later(minutes=5)
    hub.end_run("example-flow", 4118, outcome, jobs=jobs)
    sweep(hub, store)
    return hub.notes[(REPO, number)][-1].body, ["watch_comment", how_key]


def watch_closing_comment(case):
    hub, store, number = watched(case)
    hub.later(minutes=5)
    hub.end_run("example-flow", 4118, "success")
    sweep(hub, store)
    assert not hub.issues[(REPO, number)].open
    return hub.notes[(REPO, number)][-1].body, ["watch_close"]


def ticket_shape_comment(case):
    """A ticket by a person, its human fields in the other language than the tree file's: the check takes the
    label off and its comment says so, names the language rule and what the runner could not check."""

    other = table("en" if case.code == "ru" else "ru")
    human = "\n".join(f"- **{name}:** {other[key]}" for name, key in zip(
        ("What", "Goal", "Plan", "Result", "Undo"),
        ("red_main_what", "red_main_goal", "red_main_plan", "red_main_result", "red_main_undo")))
    hub = FakeHub()
    hub.add(1, f"{HUMAN_HEADING}\n{human}\n\n{AGENT_HEADING}\nFacts: none.\n", [READY_LABEL])
    event = {"action": "labeled", "repository": {"full_name": REPO, "owner": {"login": OWNER}},
             "issue": {"number": 1, "labels": [{"name": READY_LABEL}], "user": {"login": "someone", "type": "User"}},
             "label": {"name": READY_LABEL}}
    done = gh_check.check_event(event, hub, env={"GITHUB_WORKSPACE": str(case.checkout.path)}, run=no_command,
                                home="", host="")
    assert (done.outcome, "human_field_wrong_language" in done.codes) == ("removed", True)
    [comment] = hub.notes[(REPO, 1)]
    return comment.body, SHAPE_KEYS


def guards_scan_citation(case, *, mode):
    """The `tests` workflow's `guards scan` (`range` is its pull request form, `tree` the whole checkout) over a
    commit that adds a bare interpreter call: the hit is cited with the coding rule, and the line of what the setup
    could not check follows. The environment is set here, so the result is the same on a runner (GITHUB_ACTIONS
    set, which would fill in more values) and on a machine."""

    case.monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    case.monkeypatch.setattr(values, "default_run", no_command)
    base = case.checkout.head
    head = case.checkout.commit({f"run-{case.code}.sh": f"{BARE} -m pytest -q\n"})
    where = ["--range", f"{base}..{head}"] if mode == "range" else ["--tree"]
    code = main(["guards", "scan", "--work-tree", str(case.checkout.path), *where, "--machine-independent"])
    out = case.capsys.readouterr().out
    assert code == 1 and "bare_python" in out
    return out, SCAN_KEYS


BUILDERS = {
    "skills_issue": skills_issue,
    "skills_issue_for_the_brain": skills_issue_for_the_brain,
    "red_on_main_few": partial(red_on_main, failing=["t::a", "t::b"], pr=57, count_key="red_main_tests_few"),
    "red_on_main_one": partial(red_on_main, failing=["t::a"], pr=57, count_key="red_main_tests_one"),
    "red_on_main_many": partial(red_on_main, failing=[f"t::{n}" for n in "abcde"], pr=57,
                                count_key="red_main_tests_many"),
    "red_on_main_by_commit": partial(red_on_main, failing=["t::a", "t::b"], pr=None, count_key="red_main_tests_few"),
    "no_result": no_result,
    "watch_ticket": watch_ticket,
    "watch_comment_failure": partial(watch_comment, outcome="failure", jobs=3, how_key="watch_how_failure"),
    "watch_comment_cancelled": partial(watch_comment, outcome="cancelled", jobs=3, how_key="watch_how_cancelled"),
    "watch_comment_timed_out": partial(watch_comment, outcome="timed_out", jobs=3, how_key="watch_how_timed_out"),
    "watch_comment_no_job": partial(watch_comment, outcome="success", jobs=0, how_key="watch_how_no_job"),
    "watch_closing_comment": watch_closing_comment,
    "ticket_shape_comment": ticket_shape_comment,
    "guards_scan_tree": partial(guards_scan_citation, mode="tree"),
    "guards_scan_range": partial(guards_scan_citation, mode="range"),
}


def build(name, checkout, folder, code, monkeypatch, capsys):
    return BUILDERS[name](Case(checkout, folder, code, monkeypatch, capsys))


@pytest.mark.parametrize("code", LANGUAGES)
@pytest.mark.parametrize("name", BUILDERS)
def test_a_runner_text_is_in_the_words_of_the_checkouts_language(name, code, checkout, tmp_path, monkeypatch, capsys):
    in_language(checkout, code)
    text, keys = build(name, checkout, tmp_path, code, monkeypatch, capsys)
    for key in keys:
        assert table("ru")[key] != table("en")[key], key  # a key the two tables share proves nothing
        missing = [word for word in words(code, key) if word not in text]
        assert missing == [], (name, code, key, missing)


@pytest.mark.parametrize("name", BUILDERS)
def test_the_same_text_differs_between_the_two_languages(name, checkout, tmp_path, monkeypatch, capsys):
    in_language(checkout, "ru")
    ru_text, _ = build(name, checkout, tmp_path / "ru", "ru", monkeypatch, capsys)
    in_language(checkout, "en")
    en_text, _ = build(name, checkout, tmp_path / "en", "en", monkeypatch, capsys)
    assert ru_text != en_text
