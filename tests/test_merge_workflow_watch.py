"""The workflow watch's decision (#411 unit 2): a non-green latest run opens one watch ticket, a repeat
non-green run comments on it, a green run (outcome success) closes it. The decision is one pure function:
no I/O here. Test data uses invented names only (`example-flow`, repo `acme/example`)."""

import pytest

from orch_it import tickets
from orch_it.merge import workflow_watch as watch
from orch_it.tickets.reader import TRIAGE_LABEL
from orch_it.tickets.shape import TITLE_MAX

REPO = "acme/example"
SKIP = frozenset({"example-tests"})


def run(workflow="example-flow", run_id=4117, outcome="failure", jobs=3, ended="2026-10-28T09:00:00Z", skipped_jobs=0):
    url = f"https://github.com/{REPO}/actions/runs/{run_id}"
    return watch.WorkflowRun(workflow, run_id, url, ended, outcome, jobs, skipped_jobs)


def decide(latest, issues=(), skip=SKIP):
    return watch.decide(list(latest), list(issues), skip=skip)


def opened(actions):
    return [action for action in actions if isinstance(action, watch.Open)]


def shape_violations(action):
    return tickets.violations(tickets.parse(action.body, title=action.title, labels=watch.LABELS))


@pytest.mark.parametrize("outcome, jobs, phrase", [
    ("failure", 3, "failure"),
    ("cancelled", 3, "cancelled"),
    ("timed_out", 3, "timed out"),
    ("other", 0, "no job started"),
], ids=["failure", "cancelled", "timed_out", "no_job_started"])
def test_a_non_green_run_opens_one_watch_ticket(outcome, jobs, phrase):
    actions = decide([run(outcome=outcome, jobs=jobs)])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    action = actions[0]
    assert action.workflow == "example-flow"
    assert action.title == "Workflow example-flow did not end green"
    assert "example-flow" in action.body
    assert f"https://github.com/{REPO}/actions/runs/4117" in action.body
    assert "4117" in action.body
    assert phrase in action.body


def test_a_second_non_green_run_comments_and_opens_none():
    actions = decide([run(run_id=4118)], [watch.WatchIssue(7, "example-flow", last_run_id=4117)])
    assert opened(actions) == []
    assert len(actions) == 1 and isinstance(actions[0], watch.Comment)
    assert actions[0].issue_number == 7
    assert actions[0].run.run_id == 4118


def test_the_same_run_again_does_nothing():
    actions = decide([run(run_id=4117)], [watch.WatchIssue(7, "example-flow", last_run_id=4117)])
    assert actions == []


def test_a_green_run_closes_the_watch():
    actions = decide([run(outcome="success", jobs=3)], [watch.WatchIssue(7, "example-flow", last_run_id=4117)])
    assert len(actions) == 1 and isinstance(actions[0], watch.Close)
    assert actions[0].issue_number == 7


@pytest.mark.parametrize("outcome, jobs", [
    ("skipped", 2),
    ("other", 2),
], ids=["skipped_with_jobs", "other_with_jobs"])
def test_a_run_that_is_neither_green_nor_non_green_does_nothing_with_or_without_a_watch(outcome, jobs):
    assert decide([run(outcome=outcome, jobs=jobs)]) == []
    assert decide([run(outcome=outcome, jobs=jobs)], [watch.WatchIssue(7, "example-flow", 4117)]) == []


def test_a_green_run_with_no_watch_does_nothing():
    assert decide([run(outcome="success")]) == []


# a run GitHub skipped by its `if:` (conclusion skipped, no job started) is neither green nor non-green: the
# sweep's own comments start more skipped runs, so the watch must not open or comment on one; it looks past it to
# the latest run that is not skipped
def test_only_skipped_runs_do_nothing_with_or_without_a_watch():
    skipped_runs = [run(run_id=4117, outcome="skipped", jobs=0),
                    run(run_id=4118, outcome="skipped", jobs=0, ended="2026-10-28T10:00:00Z")]
    assert decide(skipped_runs) == []
    assert decide(skipped_runs, [watch.WatchIssue(7, "example-flow", last_run_id=4116)]) == []


def test_a_skipped_run_after_an_open_watch_neither_comments_nor_closes_it():
    actions = decide([run(run_id=4118, outcome="skipped", jobs=0)],
                     [watch.WatchIssue(7, "example-flow", last_run_id=4117)])
    assert actions == []


def test_a_green_run_after_a_skipped_one_closes_the_watch():
    skipped_later = run(run_id=4118, outcome="skipped", jobs=0, ended="2026-10-28T10:00:00Z")
    green_earlier = run(run_id=4117, outcome="success", jobs=3, ended="2026-10-28T09:00:00Z")
    actions = decide([skipped_later, green_earlier], [watch.WatchIssue(7, "example-flow", last_run_id=4116)])
    assert len(actions) == 1 and isinstance(actions[0], watch.Close)
    assert actions[0].issue_number == 7
    assert actions[0].run.run_id == 4117


def test_a_failure_that_started_no_job_opens_one_watch_ticket():
    actions = decide([run(run_id=4117, outcome="failure", jobs=0)])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    assert actions[0].run.run_id == 4117
    assert "no job started" in actions[0].body


def test_a_skipped_run_newer_than_a_failure_leaves_the_failure_as_the_latest():
    skipped_later = run(run_id=4118, outcome="skipped", jobs=0, ended="2026-10-28T10:00:00Z")
    failed_earlier = run(run_id=4117, outcome="failure", jobs=3, ended="2026-10-28T09:00:00Z")
    actions = decide([skipped_later, failed_earlier])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    assert actions[0].run.run_id == 4117


def test_a_run_whose_every_job_was_skipped_is_neither_green_nor_non_green():
    all_skipped = run(run_id=4118, outcome="other", jobs=0, skipped_jobs=2)
    assert decide([all_skipped]) == []
    assert decide([all_skipped], [watch.WatchIssue(7, "example-flow", last_run_id=4117)]) == []


# a run GitHub cancelled before any job of it started (a newer run took its place in the concurrency queue) is
# replaced: passed over exactly like a skipped run, so the watch reads the newest run that is neither
def test_a_replaced_newest_run_over_a_green_run_closes_the_open_watch_and_nothing_else():
    replaced = run(run_id=4118, outcome="cancelled", jobs=0, ended="2026-10-28T10:00:00Z")
    green_earlier = run(run_id=4117, outcome="success", jobs=3, ended="2026-10-28T09:00:00Z")
    actions = decide([replaced, green_earlier], [watch.WatchIssue(7, "example-flow", last_run_id=4116)])
    assert len(actions) == 1 and isinstance(actions[0], watch.Close)
    assert actions[0].issue_number == 7
    assert actions[0].run.run_id == 4117
    assert opened(actions) == []
    assert not any(isinstance(action, watch.Comment) for action in actions)


def test_a_replaced_newest_run_over_a_cancelled_run_with_jobs_opens_one_watch_for_that_cut_run():
    replaced = run(run_id=4118, outcome="cancelled", jobs=0, ended="2026-10-28T10:00:00Z")
    cut = run(run_id=4117, outcome="cancelled", jobs=2, ended="2026-10-28T09:00:00Z")
    actions = decide([replaced, cut])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    assert actions[0].run.run_id == 4117
    assert "cancelled" in actions[0].body


def test_a_replaced_newest_run_over_a_failure_that_started_no_job_opens_one_watch_for_that_failure():
    replaced = run(run_id=4118, outcome="cancelled", jobs=0, ended="2026-10-28T10:00:00Z")
    failed = run(run_id=4117, outcome="failure", jobs=0, ended="2026-10-28T09:00:00Z")
    actions = decide([replaced, failed])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    assert actions[0].run.run_id == 4117
    assert "no job started" in actions[0].body


def test_the_skipped_workflow_does_nothing_with_or_without_a_watch():
    failing = run(workflow="example-tests", outcome="failure")
    assert decide([failing], skip=SKIP) == []
    assert decide([failing], [watch.WatchIssue(7, "example-tests", last_run_id=None)], skip=SKIP) == []


def test_six_workflows_with_two_non_green_open_two_watch_tickets():
    latest = [
        run(workflow="example-flow", outcome="success"),
        run(workflow="nightly-example", outcome="success"),
        run(workflow="docs-site", outcome="skipped", jobs=1),
        run(workflow="release-cut", outcome="timed_out", run_id=4201),
        run(workflow="moonlight-sync", outcome="failure", run_id=4202),
        run(workflow="lint-only", outcome="success"),
    ]
    actions = decide(latest)
    assert [action.workflow for action in opened(actions)] == ["release-cut", "moonlight-sync"]
    assert len(actions) == 2


def test_several_watches_of_one_workflow_comment_on_the_lowest_number_only():
    issues = [watch.WatchIssue(9, "example-flow", last_run_id=4000),
              watch.WatchIssue(7, "example-flow", last_run_id=4000)]
    actions = decide([run(run_id=4118)], issues)
    assert len(actions) == 1 and isinstance(actions[0], watch.Comment)
    assert actions[0].issue_number == 7


def test_a_green_run_closes_every_open_watch_of_its_workflow():
    issues = [watch.WatchIssue(9, "example-flow", last_run_id=4000),
              watch.WatchIssue(7, "example-flow", last_run_id=4000)]
    actions = decide([run(outcome="success")], issues)
    assert [(type(action), action.issue_number) for action in actions] == [(watch.Close, 7), (watch.Close, 9)]


def test_two_runs_of_one_workflow_give_one_action_for_the_later_one():
    earlier_green = run(run_id=4117, outcome="success", ended="2026-10-28T09:00:00Z")
    later_green = run(run_id=4118, outcome="success", ended="2026-10-28T10:00:00Z")
    issues = [watch.WatchIssue(7, "example-flow", last_run_id=4116)]
    assert decide([earlier_green, later_green], issues) == decide([later_green], issues)

    earlier_red = run(run_id=4117, outcome="failure", ended="2026-10-28T09:00:00Z")
    later_red = run(run_id=4118, outcome="failure", ended="2026-10-28T10:00:00Z")
    actions = decide([later_red, earlier_red])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    assert actions[0].run.run_id == 4118


def test_a_tie_in_end_time_keeps_the_greater_run_id():
    lower = run(run_id=4119, outcome="failure", ended="2026-10-28T09:00:00Z")
    greater = run(run_id=4120, outcome="failure", ended="2026-10-28T09:00:00Z")
    actions = decide([greater, lower])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    assert actions[0].run.run_id == 4120


@pytest.mark.parametrize("outcome, jobs", [
    ("failure", 3),
    ("cancelled", 3),
    ("timed_out", 3),
    ("other", 0),
], ids=["failure", "cancelled", "timed_out", "no_job_started"])
def test_the_watch_body_keeps_the_shape_with_no_violations(outcome, jobs):
    action = decide([run(workflow="nightly-example", outcome=outcome, jobs=jobs)])[0]
    ticket = tickets.parse(action.body, title=action.title, labels=watch.LABELS)
    assert tickets.violations(ticket) == []
    assert watch.LABELS == (TRIAGE_LABEL,)


def test_the_watch_body_names_no_workflow_but_its_own():
    action = decide([run(workflow="example-flow")])[0]
    assert "tests" not in action.body


# a workflow GitHub lists with no name (its file path), a name with `#12`, the word "parked" in any case,
# a name longer than the title allows, and a name with a comment end in it: each opens a shape-clean watch
@pytest.mark.parametrize("name", [
    ".github/workflows/x.yml",
    "CI #12",
    "Parked nightly",
    "nightly-" + "x" * 61,
    "a --> b",
], ids=["path", "hash_number", "parked", "seventy_chars", "comment_end"])
def test_a_workflow_name_outside_the_title_rules_still_opens_a_shape_clean_watch(name):
    actions = decide([run(workflow=name, outcome="failure")])
    assert len(actions) == 1 and isinstance(actions[0], watch.Open)
    action = actions[0]
    assert shape_violations(action) == []
    assert len(action.title) <= TITLE_MAX
    assert watch.workflow_of(action.body) == name


def test_the_exact_name_is_read_back_from_the_mark_and_a_body_without_it_names_none():
    action = decide([run(workflow="example-flow")])[0]
    assert watch.workflow_of(action.body) == "example-flow"
    assert watch.workflow_of("no mark here") is None
