"""The shape check on GitHub and the tickets the layer's own code opens (orchestration-layer#424 unit 8): the
skills issue (`skills report`), the red-on-main ticket, its no-result ticket and the workflow-watch ticket are
written by code on the machine of the writer, in the writer's `human_language`, with its commands in them. The
workflow `ticket-shape` judges them in the checkout, whose `settings.json` is the one the writers read (#453 unit 2),
so the language rule judges them with the writer's language and the literal rules skip them. They must keep
`ready-for-agent`; a ticket a person wrote must not."""

import os

import pytest
from fake_hub import REPO, FakeHub

from orch_it.merge import red_main
from orch_it.merge import workflow_watch as watch
from orch_it.skills import update
from orch_it.skills.classify import Change
from orch_it.skills.copies import Manifest
from orch_it.tickets import gh_check
from orch_it.tickets.model import CODE_WRITTEN_MARK

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # the writer and the runner share one settings.json

OWNER = REPO.split("/")[0]
WORKFLOW = "name: tests\non: push\njobs:\n  linux:\n    name: tests\n    runs-on: ubuntu-latest\n    steps: []\n"
NL = chr(10)
LITERAL = "Facts: run `python -m pytest` by hand before the new run starts."  # one literal finding (#413 unit 6): a bare python


def red():
    return red_main.build(["t::a", "t::b"], sha="m0", run_id="7", pr=12)


def no_result():
    return red_main.build_no_result(sha="m0", run_id="7", jobs={"tests": "no result"})


def watched():
    # a workflow named like a check of the repo: its name is written between backticks
    return watch.build(watch.WorkflowRun("tests.yml", 4117, "https://github.com/acme/example/actions/runs/4117",
                                         "2026-10-28T09:00:00Z", "failure", 3, 0))


def skills():
    change = Change("tdd", "text_changed", "skills/x/tdd/SKILL.md", "skills/x/tdd/SKILL.md", ["SKILL.md"])
    return update.issue(update.Check(Manifest("u", "1" * 40, {}, {}), "2" * 40, {}, [change]))


OPENERS = {"skills": skills, "red_main": red, "no_result": no_result, "workflow_watch": watched}


@pytest.fixture
def runner(tmp_path):
    """The checkout the workflow runs in: its work tree is the cwd (`ticket_language_ru`), and a workflow with a
    check named `tests`. Returns the environment for `check_event`."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "tests.yml").write_text(WORKFLOW, "utf-8")
    return {**os.environ, "GITHUB_WORKSPACE": str(tmp_path)}


def with_a_literal(title, body):
    """The body with one literal finding in its agent block: the literal rules are what a code-written mark exempts."""
    heading = "## For the agent" + NL
    return title, body.replace(heading, heading + LITERAL + NL, 1)


def event(author=OWNER, kind="User", action="labeled", sender=None):
    made = {"action": action, "repository": {"full_name": REPO, "owner": {"login": OWNER}},
            "issue": {"number": 1, "labels": [{"name": "ready-for-agent"}], "user": {"login": author, "type": kind}},
            "label": {"name": "ready-for-agent"}}
    if sender is not None:
        made["sender"] = sender
    return made


def judged(title, body, runner, **who):
    hub = FakeHub()
    hub.add(1, body, ["ready-for-agent"], title=title)
    return gh_check.check_event(event(**who), hub, env=runner), hub


@pytest.mark.parametrize("opener", OPENERS)
@pytest.mark.parametrize("who", [{"author": OWNER, "kind": "User"}, {"author": "github-actions[bot]", "kind": "Bot"}])
def test_a_ticket_the_layers_code_wrote_keeps_its_label_in_the_shared_language(opener, who, runner):
    title, body = OPENERS[opener]()
    result, hub = judged(title, body, runner, **who)
    assert (result.line, hub.issues[(REPO, 1)].labels) == ("#1 kept", ("ready-for-agent",))
    assert (REPO, 1) not in hub.notes


@pytest.mark.parametrize("opener", OPENERS)
def test_every_opener_writes_the_mark(opener):
    assert CODE_WRITTEN_MARK in OPENERS[opener]()[1]


@pytest.mark.parametrize("opener", OPENERS)
def test_the_same_body_by_a_person_or_without_the_mark_is_judged_in_full(opener, runner):
    title, body = with_a_literal(*OPENERS[opener]())
    stranger, _ = judged(title, body, runner, author="someone-else", kind="User")
    unmarked, _ = judged(title, body.replace(CODE_WRITTEN_MARK, ""), runner)
    assert stranger.outcome == unmarked.outcome == "removed"
    assert stranger.codes == unmarked.codes == ["literal_pattern"]


def test_a_marked_ticket_that_breaks_a_structural_rule_still_loses_the_label(runner):
    title, body = skills()
    result, hub = judged(title, body.replace("Weight: 2", ""), runner)
    assert (result.outcome, result.codes) == ("removed", ["weight_missing"])
    assert hub.issues[(REPO, 1)].labels == ()


def _marked_skills():
    title, body = with_a_literal(*skills())
    assert body.startswith(CODE_WRITTEN_MARK + NL)
    return title, body


def test_the_mark_counts_on_line_one_or_under_another_comment_line_not_deeper(runner):
    title, body = _marked_skills()
    bare = body.replace(CODE_WRITTEN_MARK + NL, "", 1)
    under = "<!-- Parked: waiting -->" + NL + CODE_WRITTEN_MARK + NL + bare
    after_human = bare + NL + CODE_WRITTEN_MARK + NL
    heading = "## For the agent" + NL
    in_agent_text = bare.replace(heading, heading + CODE_WRITTEN_MARK + NL, 1)
    assert in_agent_text != bare
    outcomes = {name: judged(title, text, runner)[0].outcome
                for name, text in [("first", body), ("under", under), ("after", after_human), ("deep", in_agent_text)]}
    assert outcomes == {"first": "kept", "under": "kept", "after": "removed", "deep": "removed"}


def _edited(title, body, runner, sender, **who):
    hub = FakeHub()
    hub.add(1, body, ["ready-for-agent"], title=title)
    return gh_check.check_event(event(action="edited", sender=sender, **who), hub, env=runner)


def test_an_edit_of_a_marked_ticket_is_exempt_only_when_a_bot_or_the_owner_made_it(runner):
    title, body = _marked_skills()
    sent = {
        "stranger": {"login": "someone-else", "type": "User"},
        "owner": {"login": OWNER.upper(), "type": "User"},
        "bot": {"login": "github-actions[bot]", "type": "Bot"},
        "none": None,
        "junk": "someone",
    }
    outcomes = {name: _edited(title, body, runner, sender).outcome for name, sender in sent.items()}
    assert outcomes == {"stranger": "removed", "owner": "kept", "bot": "kept", "none": "removed", "junk": "removed"}


def test_a_non_edit_action_ignores_the_sender(runner):
    title, body = _marked_skills()
    hub = FakeHub()
    hub.add(1, body, ["ready-for-agent"], title=title)
    labeled = event(sender={"login": "someone-else", "type": "User"})
    assert gh_check.check_event(labeled, hub, env=runner).outcome == "kept"
