"""The merge line's follow for a skills update's pull request (orch-it #412): its issue is the one
`skills report` opened for the pull request's upstream commit (titled `His skills moved to <commit>`,
found through the branch `skills-upstream/<commit>-<base>`). A red or no result of its required checks
sends that issue to the brain with the failing ids and takes `ready-for-agent` off; a green pull request
touches nothing. Seam: `follow` on FakeGitHub, as the other follow tests."""

from datetime import UTC, datetime

from fake_github import FAILED, NO_RESULT, PASSED, REPO, FakeGitHub

from orch_it import tickets
from orch_it.events.store import Store
from orch_it.merge import red_main
from orch_it.merge.follow import follow
from orch_it.merge.settings import MergeSettings

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
COMMIT = "0123456789ab"
BRANCH = f"skills-upstream/{COMMIT}-abcdef12"
ISSUE = 20
TEST_ID = "tests/test_skills.py::test_copy_matches_manifest"


def skills_world(tmp_path, *, with_issue=True, labels=("ready-for-agent",)):
    """One skills update's pull request in the merge line (auto-merge on) and its issue, ready."""

    hub = FakeGitHub(NOW)
    store = Store.open(tmp_path / "events")
    if with_issue:
        hub.add(ISSUE, "## For the human\n\n- **What:** his upstream moved.\n", list(labels),
                title=f"His skills moved to {COMMIT}")
    pr = hub.open_pull(body="His upstream moved. The copies are rewritten.", branch=BRANCH,
                       title="His skills move to 0123456789ab: text_changed skill", auto_merge="squash")
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
    assert hub.pull(REPO, pr).auto_merge is None


def test_no_result_the_second_time_sends_the_skills_issue_to_the_brain(tmp_path):
    hub, store, pr = skills_world(tmp_path)
    hub.checks_on(hub.head(pr), NO_RESULT, attempt=2, failing=None)
    done = run(hub, store, pr)
    assert (done.outcome, done.ticket) == ("no_result", ISSUE)
    assert "needs-triage" in labels(hub) and "ready-for-agent" not in labels(hub)
    assert hub.pull(REPO, pr).auto_merge is None


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
    assert hub.pull(REPO, pr).auto_merge is None


def test_a_pull_request_with_no_ticket_that_goes_red_changes_no_issue_label_or_body(tmp_path):
    """Regression for the skills branch: a hand pull request on `branch-N`, with no ticket, is not a skills
    update, so a red touches no issue at all."""

    hub, store, _ = skills_world(tmp_path)
    pr = hub.open_pull(body="A hand change", auto_merge="squash")
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
