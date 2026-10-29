"""The merge line's follow for a skills update's pull request (orch-it #412): its issue is the one
`skills report` opened for the pull request's upstream commit (titled `His skills moved to <commit>`,
found through the branch `skills-upstream/<commit>-<base>`). A red or no result of its required checks
sends that issue to the brain with the failing ids and takes `ready-for-agent` off; a green pull request
touches nothing. Seam: `follow` on FakeGitHub, as the other follow tests."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fake_github import FAILED, NO_RESULT, PASSED, REPO, FakeGitHub

from orch_it import tickets
from orch_it.events.store import Store
from orch_it.merge import marks, red_main
from orch_it.merge.follow import follow
from orch_it.merge.settings import MergeSettings
from orch_it.skills.update import done_text

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
COMMIT = "0123456789ab"
BRANCH = f"skills-upstream/{COMMIT}-abcdef12"
ISSUE = 20
TEST_ID = "tests/test_skills.py::test_copy_matches_manifest"


def skills_world(tmp_path, *, with_issue=True, labels=("ready-for-agent",)):
    """One skills update's pull request in the merge line (a ready mark) and its issue, ready."""

    hub = FakeGitHub(NOW)
    store = Store.open(tmp_path / "events")
    if with_issue:
        hub.add(ISSUE, "## For the human\n\n- **What:** his upstream moved.\n", list(labels),
                title=f"His skills moved to {COMMIT}")
    pr = hub.open_pull(body="His upstream moved. The copies are rewritten.", branch=BRANCH,
                       title="His skills move to 0123456789ab: text_changed skill")
    hub.comment(REPO, pr, marks.ready_comment(None, hub.head(pr), hub.now))
    return hub, store, pr


def run(hub, store, pr):
    return follow(hub, store, REPO, pr, base="main", settings=MergeSettings(), now=hub.now)


def labels(hub):
    return set(hub.issues[(REPO, ISSUE)].labels)


def test_red_sends_the_skills_issue_to_the_brain_with_the_failing_ids(tmp_path):
    hub, store, pr = skills_world(tmp_path)
    hub.checks_on(hub.head(pr), FAILED, failing=[TEST_ID])
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("red", ISSUE)
    assert "needs-triage" in labels(hub) and "ready-for-agent" not in labels(hub)
    said = hub.comments(REPO, ISSUE)[-1].body
    assert "To the brain" in said and TEST_ID in said
    assert marks.ready_since(hub.comments(REPO, pr)) is None


def test_no_result_the_second_time_sends_the_skills_issue_to_the_brain(tmp_path):
    hub, store, pr = skills_world(tmp_path)
    hub.checks_on(hub.head(pr), NO_RESULT, attempt=2, failing=None)
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("no_result", ISSUE)
    assert "needs-triage" in labels(hub) and "ready-for-agent" not in labels(hub)
    assert marks.ready_since(hub.comments(REPO, pr)) is None


def test_no_result_the_first_time_is_run_once_more_and_the_issue_is_untouched(tmp_path):
    hub, store, pr = skills_world(tmp_path)
    hub.checks_on(hub.head(pr), NO_RESULT, attempt=1, failing=None)
    before = len(hub.comments(REPO, ISSUE))
    done = run(hub, store, pr)
    assert done.outcome == "rerun"
    assert labels(hub) == {"ready-for-agent"} and len(hub.comments(REPO, ISSUE)) == before


def test_green_leaves_the_skills_issue_as_it_was(tmp_path):
    hub, store, pr = skills_world(tmp_path)
    hub.checks_on(hub.head(pr), PASSED)
    before = len(hub.comments(REPO, ISSUE))
    done = run(hub, store, pr)
    assert done.outcome == "green"
    assert labels(hub) == {"ready-for-agent"} and len(hub.comments(REPO, ISSUE)) == before


def test_red_with_no_issue_of_its_commit_leaves_the_line_and_touches_no_issue(tmp_path):
    hub, store, pr = skills_world(tmp_path, with_issue=False)
    hub.checks_on(hub.head(pr), FAILED, failing=[TEST_ID])
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("red", None)
    assert marks.ready_since(hub.comments(REPO, pr)) is None


def test_a_pull_request_with_no_ticket_that_goes_red_changes_no_issue_label_or_body(tmp_path):
    """Regression for the skills branch: a hand pull request on `branch-N`, with no ticket, is not a skills
    update, so a red touches no issue at all."""

    hub, store, _ = skills_world(tmp_path)
    pr = hub.open_pull(body="A hand change")
    hub.comment(REPO, pr, marks.ready_comment(None, hub.head(pr), hub.now))
    before = {key: (issue.labels, issue.body) for key, issue in hub.issues.items()}
    hub.checks_on(hub.head(pr), FAILED, failing=["t::x"])
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("red", None)
    assert {key: (issue.labels, issue.body) for key, issue in hub.issues.items()} == before
    assert not [w for w in hub.writes if w[1] == ISSUE]


def test_a_skills_red_while_its_issue_already_says_needs_triage_adds_no_second_label(tmp_path):
    """A skills red on an issue that already carries `needs-triage`: the issue ends with that one label, at most
    one write and one comment, and no error."""

    hub, store, pr = skills_world(tmp_path, labels=("needs-triage",))
    hub.checks_on(hub.head(pr), FAILED, failing=[TEST_ID])
    comments, writes = len(hub.comments(REPO, ISSUE)), len([w for w in hub.writes if w[1] == ISSUE])
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("red", ISSUE)
    assert labels(hub) == {"needs-triage"}
    assert len(hub.comments(REPO, ISSUE)) - comments == 1
    assert len([w for w in hub.writes if w[1] == ISSUE]) - writes <= 1


def test_a_skills_red_only_on_tests_red_on_main_pins_the_red_main_branch_first_and_skips_the_brain(tmp_path):
    """Pins today's order: `_red` runs the red-on-main rule before the skills rule, so a skills pull request red
    only on tests red on main parks its issue on the red-main ticket and the brain does not see it."""

    hub, store, pr = skills_world(tmp_path)
    title, body = red_main.build([TEST_ID], sha="m0", run_id="7", pr=None)
    red = hub.create_issue(REPO, title, body, list(red_main.LABELS))
    hub.checks_on(hub.head(pr), FAILED, failing=[TEST_ID])
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("red_on_main", ISSUE)
    held = tickets.parse(hub.issues[(REPO, ISSUE)].body, labels=hub.issues[(REPO, ISSUE)].labels)
    assert held.parked and f"#{red}" in held.park_lines[0]
    assert "needs-triage" not in labels(hub)


def merged_pr(hub, pr):
    hub.checks_on(hub.head(pr), PASSED)
    assert hub.merge(REPO, pr, hub.head(pr), "merge")


def closes(hub):
    return [call for call in hub.calls if call[0] == "close_issue"]


def test_a_merged_skills_pull_request_closes_its_open_issue_with_the_done_text(tmp_path):
    """The close path of a skills issue: no acceptance table is judged, the closing comment is the follow mark and
    `done_text` of the commit in the branch, and the issue closes as completed."""

    hub, store, pr = skills_world(tmp_path)
    merged_pr(hub, pr)
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("closed_skills", ISSUE)
    assert not hub.issues[(REPO, ISSUE)].open and hub.issues[(REPO, ISSUE)].state_reason == "completed"
    assert hub.comments(REPO, ISSUE)[-1].body == marks.follow_mark("closed") + done_text(COMMIT)
    assert done_text(COMMIT) == "Done: the copies are at 0123456789ab."


def test_a_merged_skills_pull_request_whose_issue_says_needs_triage_is_closed_too(tmp_path):
    """The brain's decision was the merge itself: the `needs-triage` label does not hold the issue open."""

    hub, store, pr = skills_world(tmp_path, labels=("needs-triage",))
    merged_pr(hub, pr)
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("closed_skills", ISSUE)
    assert not hub.issues[(REPO, ISSUE)].open
    assert hub.comments(REPO, ISSUE)[-1].body.endswith(done_text(COMMIT))


def test_a_merged_skills_pull_request_with_no_issue_of_its_commit_stays_merged_alone_and_touches_no_issue(tmp_path):
    """An open skills issue of another upstream commit is not this pull request's issue: nothing closes."""

    hub, store, pr = skills_world(tmp_path, with_issue=False)
    hub.add(21, "## For the human\n\n- **What:** an older move.\n", ["ready-for-agent"],
            title="His skills moved to ffffffffffff")
    merged_pr(hub, pr)
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("merged_alone", None)
    assert closes(hub) == [] and hub.issues[(REPO, 21)].open


def test_a_merged_pull_request_with_neither_ticket_nor_skills_issue_stays_merged_alone(tmp_path):
    hub, store, _ = skills_world(tmp_path)
    pr = hub.open_pull(body="A hand change")
    hub.comment(REPO, pr, marks.ready_comment(None, hub.head(pr), hub.now))
    merged_pr(hub, pr)
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("merged_alone", None)
    assert closes(hub) == [] and hub.issues[(REPO, ISSUE)].open


def test_a_merged_skills_pull_request_writes_its_comment_and_its_ticket_closed_events(tmp_path):
    """The events list the close the way the ticket path writes it: the closing comment, then `ticket.closed`."""

    hub, store, pr = skills_world(tmp_path)
    merged_pr(hub, pr)
    run(hub, store, pr)
    rows = store.query("SELECT kind, ticket, json_extract(detail, '$.comment_kind') FROM events "
                       "WHERE kind IN ('comment.written', 'ticket.closed')").rows
    assert ("comment.written", ISSUE, "closing") in [tuple(row) for row in rows]
    assert ("ticket.closed", ISSUE, None) in [tuple(row) for row in rows]


def test_a_skills_issue_edited_between_its_read_and_its_close_is_raced_and_nothing_is_written(tmp_path, monkeypatch):
    """The ticket path's re-read and refusal: an issue changed since it was read is not closed, nor commented."""

    hub, store, pr = skills_world(tmp_path)
    merged_pr(hub, pr)
    read, reads = hub.issue, []

    def edited_after_the_first_read(repo, number):
        found = read(repo, number)
        if number == ISSUE and found is not None:
            reads.append(number)
            if len(reads) == 2:
                return replace(found, body=found.body + "\nedited meanwhile\n")
        return found

    monkeypatch.setattr(hub, "issue", edited_after_the_first_read)
    done = run(hub, store, pr)
    monkeypatch.undo()
    assert done.outcome == "raced"
    assert hub.issues[(REPO, ISSUE)].open and closes(hub) == [] and hub.comments(REPO, ISSUE) == []


def test_a_second_follow_after_the_comment_but_a_refused_close_posts_no_second_done_text(tmp_path, monkeypatch):
    hub, store, pr = skills_world(tmp_path)
    merged_pr(hub, pr)

    def refused(repo, number, reason):
        raise RuntimeError("the close was refused")

    monkeypatch.setattr(hub, "close_issue", refused)
    with pytest.raises(RuntimeError):
        run(hub, store, pr)
    assert hub.issues[(REPO, ISSUE)].open
    monkeypatch.undo()
    done = run(hub, store, pr)
    assert done.outcome == "closed_skills"
    assert [c.body for c in hub.comments(REPO, ISSUE) if done_text(COMMIT) in c.body] == [
        marks.follow_mark("closed") + done_text(COMMIT)]
    assert not hub.issues[(REPO, ISSUE)].open
