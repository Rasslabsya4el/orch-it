"""Row 4 of the live merge trial: the five pull requests of `merge_trial.row_4`, the repo's follow workflow left on.

The harness only makes each end's event (a pull request closed or merged, a comment on it) and waits: for the run of
the follow workflow (`merge_trial.FOLLOW_WORKFLOW`) that GitHub starts on that event, which must end `success`, and
then reads the ticket back from GitHub and judges it with the same predicate as `row_4` (`merge_trial.end_judges`).
The row_4 report outcome and the store events are not read live: the ticket-state predicates replace them, and each
end records `"store": "not read (live)"`.

A red pull request has no event that the follow workflow reacts to: a check run ending starts none. So the red end is
handled by the reconcile workflow: once the red pull request's required checks ended, the harness dispatches it, and its
sweep follows every pull request on GitHub. The red end waits for that run, requires `success`, then judges the ticket.

The comment end waits, before it posts the owner's comment, for the follow runs of its pull request's own marks to settle
(`_settled_follow_runs`, about 1 min on GitHub). A pull request whose required checks are green within that minute merges
before its comment's follow run ends, and then the end can no longer be judged open. So the comment
pull request carries an unmarked test, `tests/test_trial_slow_<tag>.py`, that sleeps `SLOW_SECONDS` (1 min 50 s, under the
change run's per-test bound of 2 min: `per_change_timeout_seconds` in pyproject.toml, read by `tests.yml`). Its `tests` run
stays busy past the follow run, so the pull request is still open when the comment's end is judged. The judge is unchanged;
the finally block closes every pull request the row opened.

Every end is a record of `pr`, `ticket`, `handled_by` (`follow` or `reconcile`), `run_conclusion` (the conclusion of
the run that handled it), `run_id` (its run id, None when none started), `since` (the moment the end's wait began, ISO
8601), the ticket's state, `store`, `error` (None when the end holds) and `passed`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import merge_trial
from merge_trial_live import dispatch_workflow, wait_workflow_run
from orch_it.testruns.github import GithubError

NOT_READ = "not read (live)"
# the comment pull request's slow test: its change run outlasts the comment's settle wait and follow run, and stays under
# the change run's per-test bound (`per_change_timeout_seconds` = 120 s, pyproject.toml:34; tests.yml runs it with -n)
SLOW_SECONDS = 110
SLOW_TEST = f"import time\n\n\ndef test_slow():\n    time.sleep({SLOW_SECONDS})\n"


def row_4_live(trial: merge_trial.Trial, hub: merge_trial.PullHub, store: merge_trial.Store,
               clock: Callable[[], datetime], sleep: Callable[[float], None], plan: merge_trial.Plan | None = None
               ) -> dict:
    """Row 4 live: each end followed by the repo's own `follow` workflow; the same shape as `row_4`."""

    run = merge_trial._Run(trial, hub, store, clock, sleep, plan, "r4", live=True)
    judge = merge_trial.end_judges(run.tag)
    out: dict = {"follow_workflow_disabled": False}  # left on: its runs are the follow of every end
    opened: list[int] = []
    try:
        # the red pull request first: its checks run while the others are done
        red_file = f"tests/test_trial_pr_red_{run.tag}.py"
        red = run.pull_request("red", {red_file: merge_trial.RED_ON_PR})
        opened.append(red[2].pr)
        closed = run.pull_request("closed", {f"trial/{run.tag}/closed.txt": "closed\n"})
        opened.append(closed[2].pr)
        out["closed_unmerged"] = _follow_end(run, closed, "closed_unmerged", judge["closed_unmerged"],
                                             event="pull_request", moved=lambda: _close_unmerged(run, closed[2].pr))
        said = run.pull_request("comment", {f"trial/{run.tag}/comment.txt": "comment\n",
                                            f"tests/test_trial_slow_{run.tag}.py": SLOW_TEST})
        opened.append(said[2].pr)
        out["comment"] = _follow_end(run, said, "comment", judge["comment"], event="issue_comment",
                                     moved=lambda: _comment(run, said[2].pr), settle=True)
        merged = run.pull_request("merged", {f"trial/{run.tag}/merged.txt": "merged\n"})
        opened.append(merged[2].pr)
        out["merged"] = _follow_end(run, merged, "merged", judge["merged"], event="pull_request",
                                    moved=lambda: run.land(merged[2].pr).head_sha)
        later = run.pull_request("after-merge", {f"trial/{run.tag}/later.txt": "later\n"}, body=merge_trial.FULL_BODY,
                                 rows=merge_trial.ROWS_FULL)
        opened.append(later[2].pr)
        out["after_merge"] = _follow_end(run, later, "after_merge", judge["after_merge"], event="pull_request",
                                         moved=lambda: run.land(later[2].pr).head_sha)
        out["red"] = _red_end(run, red, judge["red"])
    finally:
        for pr in opened:
            pull = hub.pull(run.repo, pr)
            if pull is not None and pull.state == "open":
                trial.close_pull(pr)
    ends = ("merged", "after_merge", "red", "closed_unmerged", "comment")
    out["passed"] = all(end in out and out[end]["passed"] for end in ends)
    return out


def _close_unmerged(run: merge_trial._Run, pr: int) -> str:
    """Close the pull request without merging it; its head sha, the one the workflow's run is about."""

    head = run.hub.pull(run.repo, pr).head_sha
    run.trial.close_pull(pr)
    return head


def _comment(run: merge_trial._Run, pr: int) -> None:
    """The owner's comment on the pull request; a comment on a pull request is no head's event (None)."""

    run.trial.comment_as_person(pr, "Wait, please look at the zones before this lands.")


def _settled_follow_runs(run: merge_trial._Run) -> frozenset[str]:
    """The ids of the follow runs GitHub lists, taken once none of them is queued or in progress: the runs the marks
    of a pull request started before an end, which that end must not take. The probe answers a dict, so an empty
    listing is still an answer."""

    def probe() -> dict | None:
        listed = run.trial.workflow_runs(merge_trial.FOLLOW_WORKFLOW)
        if any(r.get("status") in ("queued", "in_progress") for r in listed):
            return None
        return {"ids": frozenset(str(r.get("id")) for r in listed)}

    return run.wait("the follow runs of the marks to settle", probe)["ids"]


def _follow_end(run: merge_trial._Run, made: tuple, end: str, judge: Callable[[dict], bool], *, event: str,
                moved: Callable[[], str | None], settle: bool = False) -> dict:
    """One end: when `settle`, the follow runs listed before the end settle first and the end ignores them; `moved`
    makes the event at a moment `since` (from the clock); then the `follow` run it starts must end `success`, and the
    ticket as GitHub holds it then must satisfy `judge`."""

    ticket, _, opened = made
    ignore = _settled_follow_runs(run) if settle else frozenset()
    since = run.clock()
    head = moved()
    try:
        found = wait_workflow_run(run, merge_trial.FOLLOW_WORKFLOW, since, head_sha=head, event=event,
                                  ignore_ids=ignore)
    except merge_trial.TrialTimeout as e:
        return _record(run, ticket, opened.pr, judge, handled_by="follow", since=since, error=f"{end}: {e}")
    error = None if found.conclusion == "success" else f"follow run {found.run_id} ended {found.conclusion}"
    return _record(run, ticket, opened.pr, judge, handled_by="follow", since=since,
                   run_conclusion=found.conclusion, run_id=found.run_id, error=error)


def _red_end(run: merge_trial._Run, made: tuple, judge: Callable[[dict], bool]) -> dict:
    """The red end: its checks end; then the `reconcile` workflow is dispatched and the run it starts must end
    `success`; then the ticket as GitHub holds it must satisfy `judge`. The reconcile run has three parts: the main
    check (`check_main`, reconcile.py:87-88), the workflow watch (reconcile.py:91-93) and the follow scope of the
    pull requests (`_to_follow`, reconcile.py:111-125). Any of them failing fails the red end with the run id. Each
    failure is this end's record."""

    ticket, _, opened = made
    since = run.clock()
    try:
        run.wait("the red pull request's checks to end", lambda: run.ended_red(opened.pr))
    except merge_trial.TrialTimeout as e:
        return _record(run, ticket, opened.pr, judge, handled_by="reconcile", since=since, error=f"red: {e}")
    since = run.clock()
    try:
        dispatch_workflow(run, merge_trial.RECONCILE_WORKFLOW)
    except (merge_trial.TrialError, GithubError) as e:
        return _record(run, ticket, opened.pr, judge, handled_by="reconcile", since=since,
                       error=f"red: the dispatch of {merge_trial.RECONCILE_WORKFLOW} failed: {e}")
    try:
        found = wait_workflow_run(run, merge_trial.RECONCILE_WORKFLOW, since, event="workflow_dispatch")
    except merge_trial.TrialTimeout as e:
        return _record(run, ticket, opened.pr, judge, handled_by="reconcile", since=since, error=f"red: {e}")
    error = None if found.conclusion == "success" else f"red: reconcile run {found.run_id} ended {found.conclusion}"
    return _record(run, ticket, opened.pr, judge, handled_by="reconcile", since=since,
                   run_conclusion=found.conclusion, run_id=found.run_id, error=error)


def _record(run: merge_trial._Run, ticket: int, pr: int, judge: Callable[[dict], bool], *, handled_by: str,
            since: datetime, run_conclusion: str | None = None, run_id: str | None = None,
            error: str | None = None) -> dict:
    """One end's record: the ticket read back now, judged; the end holds when the judge holds and no error stands."""
    state = run.read(ticket, pr)
    return {"pr": pr, "ticket": ticket, "handled_by": handled_by, "run_conclusion": run_conclusion,
            "run_id": run_id, "since": since.isoformat(), "detail": error or "", **state, "store": NOT_READ,
            "error": error, "passed": bool(judge(state)) and error is None}
