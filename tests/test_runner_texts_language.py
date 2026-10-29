"""The texts orch-it writes on a GitHub runner come out in the language of the checkout's `settings.json`
(#453 unit 3, acceptance row 1).

A runner has no installation file and no language variable: the only thing that says the language is the
`settings.json` at the root of the checkout. Each test makes a copy of the checkout (a real `git init` in a
tmp folder holding a copy of this repo's root `settings.json`), makes it the cwd, builds one of the six texts
the workflows write through the code path the command takes, and checks that the human part holds the words of
the `ru` table when the file says ru and of the `en` table when the file is removed.

Seams, one per text: `update.checked_issue` (`skills report`), `check_main` (`main check`: the red-on-main
ticket and the no-result ticket), `reconcile` (the workflow-watch ticket, its comment and its closing comment),
`gh_check.check_event` (the ticket-shape comment), `python -m orch_it guards scan` (the citation line).
The words come from the tables themselves (the literal pieces of a key's string around its `{fields}`), never
from a literal of the test.
"""

import json
import re
import tomllib
from pathlib import Path

import pytest
from fake_github import FAILED, NO_RESULT, PASSED, REPO, FakeGitHub
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


# every builder returns (the text to look in, the table keys whose words are in it)

def skills_issue(checkout, tmp_path):
    change = Change("tdd", "text_changed", "skills/x/tdd/SKILL.md", "skills/x/tdd/SKILL.md", ["SKILL.md"])
    title, body, broken = update.checked_issue(update.Check(Manifest("u", "1" * 40, {}, {}), "2" * 40, {}, [change]))
    assert broken == []
    return human_part(body), list(update.HUMAN_KEYS.values())


def skills_issue_for_the_brain(checkout, tmp_path):
    added = Change("newskill", "added", "skills/x/newskill/SKILL.md", None, ["SKILL.md"])
    title, body, broken = update.checked_issue(update.Check(Manifest("u", "1" * 40, {}, {}), "2" * 40, {}, [added]))
    assert broken == []
    return human_part(body), list((update.HUMAN_KEYS | update.HUMAN_KEYS_BRAIN).values())


def main_world(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    hub = FakeGitHub()
    return hub, Store.open(tmp_path / "events")


def red_on_main(checkout, tmp_path):
    hub, store = main_world(tmp_path)
    hub.main_run(hub.land(number=57), {"linux": FAILED, "windows": PASSED}, failing=["t::a", "t::b"])
    [number] = check_main(hub, store, REPO, "main").opened
    issue = hub.issues[(REPO, number)]
    keys = ["red_main_title", "red_main_what", "red_main_goal", "red_main_plan", "red_main_result",
            "red_main_undo", "red_main_by_pr", "red_main_tests_few"]
    return issue.title + "\n" + human_part(issue.body), keys


def no_result(checkout, tmp_path):
    hub, store = main_world(tmp_path)
    sha = hub.land()
    run_id = hub.main_run(sha, {"linux": NO_RESULT, "windows": PASSED})
    assert check_main(hub, store, REPO, "main").outcome == "rerun"
    hub.main_runs.pop(0)
    hub.main_run(sha, {"linux": NO_RESULT, "windows": PASSED}, attempt=2, run_id=run_id)
    [number] = check_main(hub, store, REPO, "main").brain
    issue = hub.issues[(REPO, number)]
    assert TRIAGE_LABEL in issue.labels
    keys = ["no_result_title", "no_result_what", "no_result_goal", "no_result_plan", "no_result_result",
            "no_result_undo"]
    return issue.title + "\n" + human_part(issue.body), keys


def sweep(hub, store):
    return reconcile(hub, store, REPO, "main", settings=MergeSettings(), now=hub.now)


def watch_ticket(checkout, tmp_path):
    hub, store = main_world(tmp_path)
    hub.end_run("example-flow", 4117, "failure")
    sweep(hub, store)
    [number] = hub.issues_with_label(REPO, TRIAGE_LABEL)
    issue = hub.issues[(REPO, number)]
    keys = ["watch_title", "watch_what", "watch_goal", "watch_plan", "watch_result", "watch_undo"]
    return issue.title + "\n" + human_part(issue.body), keys


def watch_comment(checkout, tmp_path):
    hub, store = main_world(tmp_path)
    hub.end_run("example-flow", 4117, "failure")
    sweep(hub, store)
    [number] = hub.issues_with_label(REPO, TRIAGE_LABEL)
    hub.later(minutes=5)
    hub.end_run("example-flow", 4118, "timed_out")
    sweep(hub, store)
    return hub.notes[(REPO, number)][-1].body, ["watch_comment", "watch_how_timed_out"]


def watch_closing_comment(checkout, tmp_path):
    hub, store = main_world(tmp_path)
    hub.end_run("example-flow", 4117, "failure")
    sweep(hub, store)
    [number] = hub.issues_with_label(REPO, TRIAGE_LABEL)
    hub.later(minutes=5)
    hub.end_run("example-flow", 4118, "success")
    sweep(hub, store)
    assert not hub.issues[(REPO, number)].open
    return hub.notes[(REPO, number)][-1].body, ["watch_close"]


def ticket_shape_comment(checkout, tmp_path):
    """A ticket by a person, its human fields in the other language than the checkout's: the check takes the
    label off and its comment says so, names the language rule and what the runner could not check."""

    code = "ru" if (checkout.path / "settings.json").exists() else "en"
    other = table("en" if code == "ru" else "ru")
    fields = {name: other[key] for name, key in zip(
        ("What", "Goal", "Plan", "Result", "Undo"),
        ("red_main_what", "red_main_goal", "red_main_plan", "red_main_result", "red_main_undo"))}
    human = "\n".join(f"- **{name}:** {text}" for name, text in fields.items())
    hub = FakeHub()
    hub.add(1, f"{HUMAN_HEADING}\n{human}\n\n{AGENT_HEADING}\nFacts: none.\n", [READY_LABEL])
    event = {"action": "labeled", "repository": {"full_name": REPO, "owner": {"login": OWNER}},
             "issue": {"number": 1, "labels": [{"name": READY_LABEL}], "user": {"login": "someone", "type": "User"}},
             "label": {"name": READY_LABEL}}

    def nothing(cmd):
        raise OSError("no command here")

    done = gh_check.check_event(event, hub, env={"GITHUB_WORKSPACE": str(checkout.path)}, run=nothing,
                                home="", host="")
    assert (done.outcome, "human_field_wrong_language" in done.codes) == ("removed", True)
    [comment] = hub.notes[(REPO, 1)]
    return comment.body, ["check_took_label", "human_field_language_rule", "not_checked_line",
                          "not_checked_not_set"]


def guards_scan_citation(checkout, tmp_path, monkeypatch, capsys):
    """The `tests` workflow's `guards scan`: a hit of a rule kind is cited with the coding rule, and the line of
    what the setup could not check follows."""

    checkout.commit({"run.sh": "python -m pytest -q\n"})

    def no_command(cmd):
        raise OSError("no command here")

    monkeypatch.setattr(values, "default_run", no_command)
    code = main(["guards", "scan", "--work-tree", str(checkout.path), "--tree", "--machine-independent"])
    out = capsys.readouterr().out
    assert code == 1 and "bare_python" in out
    return out, ["coding_rule", "not_checked_line", "not_checked_not_set"]


BUILDERS = {"skills_issue": skills_issue, "skills_issue_for_the_brain": skills_issue_for_the_brain,
            "red_on_main": red_on_main, "no_result": no_result, "watch_ticket": watch_ticket,
            "watch_comment": watch_comment, "watch_closing_comment": watch_closing_comment,
            "ticket_shape_comment": ticket_shape_comment}


def build(name, checkout, tmp_path, monkeypatch, capsys):
    if name == "guards_scan_citation":
        return guards_scan_citation(checkout, tmp_path, monkeypatch, capsys)
    return BUILDERS[name](checkout, tmp_path)


NAMES = (*BUILDERS, "guards_scan_citation")


def test_the_root_settings_file_of_the_repo_says_ru():
    assert json.loads((ROOT / "settings.json").read_text("utf-8")) == {"human_language": "ru"}


@pytest.mark.parametrize("code", LANGUAGES)
@pytest.mark.parametrize("name", NAMES)
def test_a_runner_text_is_in_the_words_of_the_checkouts_language(name, code, checkout, tmp_path, monkeypatch, capsys):
    in_language(checkout, code)
    text, keys = build(name, checkout, tmp_path, monkeypatch, capsys)
    for key in keys:
        assert table("ru")[key] != table("en")[key], key  # a key the two tables share proves nothing
        missing = [word for word in words(code, key) if word not in text]
        assert missing == [], (name, code, key, missing)


@pytest.mark.parametrize("name", NAMES)
def test_the_same_text_differs_between_the_two_languages(name, checkout, tmp_path, monkeypatch, capsys):
    in_language(checkout, "ru")
    ru_text, _ = build(name, checkout, tmp_path / "ru", monkeypatch, capsys)
    in_language(checkout, "en")
    en_text, _ = build(name, checkout, tmp_path / "en", monkeypatch, capsys)
    assert ru_text != en_text
