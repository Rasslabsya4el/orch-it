"""The shape check on GitHub (#398 decision 3, acceptance rows 2 and 3): when a ticket gets
`ready-for-agent`, or a ready ticket is edited, the same shape function judges it; with
violations the label comes off and a comment lists them; the body is never edited."""

import json
import re
import shlex
from dataclasses import replace
from pathlib import Path

import pytest
from fake_hub import REPO, FakeHub
from ticket_fixtures import EXPECTED, fixture

from orch_it import tickets
from orch_it.cli import main
from orch_it.tickets import commands, gh_check, hook
from orch_it.tickets.hub import LAYER_MARK

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # Russian ticket bodies (#413 unit 10a)

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ticket-shape.yml"
TRANSCRIPT = Path(__file__).parent / "fixtures" / "transcripts" / "write_ticket_called.jsonl"


@pytest.fixture(autouse=True)
def no_values_of_this_machine(monkeypatch):
    """The bodies of these tests are judged as text: no value of this machine and no workflow of the
    work tree is read (#413 unit 6)."""
    from orch_it.guards.values import Values
    monkeypatch.setattr("orch_it.guards.values.read_values", lambda **kwargs: Values())
    monkeypatch.setattr("orch_it.guards.kinds.read_job_names", lambda folder: frozenset())


def event(action, number, labels, label=None, repo=REPO):
    found = {"action": action, "repository": {"full_name": repo},
             "issue": {"number": number, "labels": [{"name": name} for name in labels]}}
    if label:
        found["label"] = {"name": label}
    return found


def ready(hub, name, number=1):
    title, body = fixture(name)
    hub.add(number, body, ["ready-for-agent", "priority"], title=title)
    return event("labeled", number, ["ready-for-agent", "priority"], "ready-for-agent")


def test_a_bad_ticket_made_ready_loses_the_label_and_gets_the_list_and_its_body_stays():
    hub = FakeHub()
    result = gh_check.check_event(ready(hub, "bad_empty_body"), hub)
    assert (result.outcome, result.codes) == ("removed", EXPECTED["bad_empty_body"])
    issue = hub.issues[(REPO, 1)]
    assert issue.labels == ("priority",)
    assert issue.body == fixture("bad_empty_body")[1]
    [said] = hub.notes[(REPO, 1)]
    assert LAYER_MARK in said.body
    for code in EXPECTED["bad_empty_body"]:
        assert f"`{code}`" in said.body


def test_a_good_ticket_made_ready_keeps_the_label_and_gets_no_comment():
    hub = FakeHub()
    assert gh_check.check_event(ready(hub, "good_basic"), hub).outcome == "kept"
    assert "ready-for-agent" in hub.issues[(REPO, 1)].labels and (REPO, 1) not in hub.notes


def test_an_edit_of_a_ready_ticket_is_judged_and_any_other_event_is_not():
    hub = FakeHub()
    ready(hub, "bad_weight_missing")
    assert gh_check.check_event(event("edited", 1, ["ready-for-agent"]), hub).outcome == "removed"
    hub.add(2, fixture("bad_weight_missing")[1], ["needs-triage"])
    assert gh_check.check_event(event("edited", 2, ["needs-triage"]), hub).outcome == "skipped"
    assert gh_check.check_event(event("labeled", 2, ["needs-triage", "priority"], "priority"), hub).outcome == "skipped"
    assert gh_check.check_event({"action": "opened"}, hub).outcome == "skipped"
    assert gh_check.check_event("not an event", hub).outcome == "skipped"


def test_the_ticket_is_judged_as_it_stands_now_not_as_the_event_held_it():
    hub = FakeHub()
    stale = ready(hub, "good_basic")
    hub.issues[(REPO, 1)] = replace(hub.issues[(REPO, 1)], body=fixture("bad_weight_missing")[1])
    assert gh_check.check_event(stale, hub).codes == ["weight_missing"]
    hub.add(3, fixture("bad_weight_missing")[1], [])  # its label was already taken off
    assert gh_check.check_event(event("labeled", 3, ["ready-for-agent"], "ready-for-agent"), hub).outcome == "skipped"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_hook_and_the_github_check_give_the_same_violations(name, tmp_path):  # row 2
    hub = FakeHub()
    title, body = fixture(name)
    on_github = gh_check.check_event(ready(hub, name), hub)
    path = tmp_path / "body.md"
    path.write_text(body, "utf-8")
    command = f"gh issue create --repo {REPO} --title {shlex.quote(title)} --body-file {shlex.quote(str(path))}"
    in_session = hook.judge({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(tmp_path),
                             "transcript_path": str(TRANSCRIPT)}, hub=FakeHub(), repo_of=lambda folder: REPO)
    # the literal codes (#413 unit 6) are compared too: the GitHub side has none of the hook-only codes, so the
    # in-session codes minus only those give the same list; the shape codes alone are checked against EXPECTED
    hook_only = set(hook.HOOK_CODES) - {"literal_value", "literal_pattern"}
    assert on_github.codes == [code for code in in_session.codes if code not in hook_only]
    assert [code for code in on_github.codes if code in tickets.RULES] == EXPECTED[name]


@pytest.mark.parametrize("bad", [
    {"action": "labeled", "issue": {"number": 1}, "repository": ["x"]},
    {"action": "edited", "issue": {"number": 1, "labels": 5}, "repository": {"full_name": REPO}},
    {"action": "labeled", "issue": {"number": True}, "repository": {"full_name": REPO}},
    {"action": "labeled", "issue": [], "repository": {"full_name": REPO}},
])
def test_a_payload_of_another_shape_is_skipped(bad):
    assert gh_check.check_event(bad, FakeHub()).outcome == "skipped"


def test_a_name_in_the_body_is_never_notified_by_the_comment():
    hub = FakeHub()
    title, body = fixture("good_basic")
    hub.add(1, body.replace("Weight: 3", "Weight: @someone"), ["ready-for-agent"], title=title)
    gh_check.check_event(event("labeled", 1, ["ready-for-agent"], "ready-for-agent"), hub)
    assert "@someone" not in hub.notes[(REPO, 1)][0].body


class CommentFails(FakeHub):
    def comment(self, repo, number, body):
        raise RuntimeError("HTTP 403")


def test_the_label_comes_off_before_the_comment_so_no_comment_says_what_did_not_happen():
    hub = CommentFails()
    with pytest.raises(RuntimeError):
        gh_check.check_event(ready(hub, "bad_weight_missing"), hub)
    assert "ready-for-agent" not in hub.issues[(REPO, 1)].labels


def test_check_github_reads_the_event_file_and_prints_what_it_did(tmp_path, monkeypatch, capsys):
    hub = FakeHub()
    path = tmp_path / "event.json"
    path.write_text(json.dumps(ready(hub, "bad_weight_missing")), "utf-8")
    monkeypatch.setattr(commands, "GhHub", lambda: hub)
    assert main(["tickets", "check-github", "--event", str(path)]) == 0
    assert capsys.readouterr().out == "#1 removed: weight_missing\n"
    path.write_text("{ not json", "utf-8")
    assert main(["tickets", "check-github", "--event", str(path)]) == 2


def test_the_workflow_runs_the_check_on_labeled_and_edited_issues_with_write_access_to_issues_only():
    text = WORKFLOW.read_text("utf-8")
    assert re.search(r"issues:\s*\n\s*types:\s*\[labeled, edited\]", text)
    assert re.search(r"permissions:\s*\n\s*issues: write[^\n]*\n\s*contents: read", text)
    assert "python -m orch_it tickets check-github --event \"$GITHUB_EVENT_PATH\"" in text
    assert "ready-for-agent" in text  # the job starts only for the ready label or a ready ticket's edit
    assert "linux:" not in text and "windows:" not in text  # never a second job of the required names
