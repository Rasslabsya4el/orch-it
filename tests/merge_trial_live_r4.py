"""Row 4 of the live merge trial: the five pull requests of `merge_trial.row_4`, the repo's `follow` workflow left on.

The harness only makes each end's event (a pull request closed or merged, a comment on it) and waits: for the
`follow` run GitHub starts on that event, which must end `success`, and then reads the ticket back from GitHub and
judges it with the same predicate as `row_4` (`merge_trial.end_judges`). The store is not read (the workflow writes
its own artifact store), so each end records `"store": "not read (live)"`.

A red pull request has no event of that workflow: a check run ending starts none. Its end waits for its ticket to
show the red outcome, bounded like every wait; with no trigger that ever moves it, the end fails with that reason.

Every end is a record of `pr`, `ticket`, `follow` (the run's conclusion, None for the red end), `follow_run` (the
run id), the ticket's state, `store`, `error` (None when the end holds) and `passed`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import merge_trial
from merge_trial_live import wait_workflow_run

NOT_READ = "not read (live)"


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
        out["closed_unmerged"] = _follow_end(run, closed, "closed_unmerged", judge["closed_unmerged"], event="pull_request",
                                             moved=lambda: _close_unmerged(run, closed[2].pr))
        said = run.pull_request("comment", {f"trial/{run.tag}/comment.txt": "comment\n"})
        opened.append(said[2].pr)
        out["comment"] = _follow_end(run, said, "comment", judge["comment"], event="issue_comment",
                                     moved=lambda: _comment(run, said[2].pr))
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


def _follow_end(run: merge_trial._Run, made: tuple, end: str, judge: Callable[[dict], bool], *, event: str,
                moved: Callable[[], str | None]) -> dict:
    """One end: `moved` makes the event at a moment `since` (from the clock); then the `follow` run it starts must end
    `success`, and the ticket as GitHub holds it then must satisfy `judge`."""

    ticket, _, opened = made
    since = run.clock()
    head = moved()
    try:
        found = wait_workflow_run(run, merge_trial.FOLLOW_WORKFLOW, since, head_sha=head, event=event)
    except merge_trial.TrialTimeout as e:
        return _record(run, ticket, opened.pr, judge, error=f"{end}: {e}")
    error = None if found.conclusion == "success" else f"follow run {found.run_id} ended {found.conclusion}"
    return _record(run, ticket, opened.pr, judge, follow=found.conclusion, follow_run=found.run_id, error=error)


def _red_end(run: merge_trial._Run, made: tuple, judge: Callable[[dict], bool]) -> dict:
    """The red end: its checks end, then its ticket must show the red outcome within the bound."""

    ticket, _, opened = made
    try:
        run.wait("the red pull request's checks to end", lambda: run.ended_red(opened.pr))
    except merge_trial.TrialTimeout as e:
        return _record(run, ticket, opened.pr, judge, error=f"red: {e}")
    try:
        run.wait(f"ticket #{ticket} to show the red end", lambda: _showing(run, ticket, opened.pr, judge))
    except merge_trial.TrialTimeout as e:
        return _record(run, ticket, opened.pr, judge, error=(
            f"red: {e}; the {merge_trial.FOLLOW_WORKFLOW} workflow runs on no event that a check run ending makes, "
            "so no run moves this ticket"))
    return _record(run, ticket, opened.pr, judge)


def _showing(run: merge_trial._Run, ticket: int, pr: int, judge: Callable[[dict], bool]) -> dict | None:
    """The ticket's state as GitHub holds it when `judge` holds, else None (a wait's probe)."""

    state = run.read(ticket, pr)
    return state if judge(state) else None


def _record(run: merge_trial._Run, ticket: int, pr: int, judge: Callable[[dict], bool], *,
            follow: str | None = None, follow_run: str | None = None, error: str | None = None) -> dict:
    """One end's record: the ticket read back now, judged; the end holds when the judge holds and no error stands."""

    state = run.read(ticket, pr)
    return {"pr": pr, "ticket": ticket, "follow": follow, "follow_run": follow_run, "detail": error or "",
            **state, "store": NOT_READ, "error": error, "passed": bool(judge(state)) and error is None}
