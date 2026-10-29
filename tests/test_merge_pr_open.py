"""`pr open` (#400 decision 1): one pull request per ticket, opened with `Refs #N` and never a
closing keyword, after the acceptance rows are checked; no auto-merge set, the ticket parked on the
pull request through #398's park command, the `pr` step started. Run again, it reuses the same
pull request and puts it back in the line."""

from datetime import UTC, datetime, timedelta

import pytest
from fake_github import REPO, REQUIRED, FakeGitHub
from ticket_fixtures import fixture

from orch_it import tickets
from orch_it.events.store import Store
from orch_it.merge import marks, pr_open
from orch_it.merge.git import GitError
from orch_it.merge.pr_open import PrError, open_pr, parse_part
from orch_it.merge.settings import MergeSettings

TITLE, BODY = fixture("good_basic")
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
GOOD = """| row | result | evidence |
|---|---|---|
| 1 | pass | 4.90 = 4.90 |
| 2 | pass | 5.10 = 5.10 |
| 3 | na | the zone table has no 50667 yet: asked in #10 |
| 4 | after merge | the weekly report after the merge |
"""


class Git:
    """The work tree's git: records each call; `messages` are the branch's commit messages."""

    def __init__(self, messages=("Cart asks the shipping quote",), fail=None):
        self.messages = list(messages)
        self.calls = []
        self.fail = fail
        self.hub = None  # set by `opened`: `rev-parse <branch>` answers the branch's head there

    def __call__(self, args):
        self.calls.append(args)
        if self.fail and self.fail in args:
            raise GitError("git_failed", f"git {' '.join(args)}: rejected")
        if args[0] == "log":
            return "".join(f"{m}\x00" for m in self.messages)
        if args[0] == "rev-parse":
            return f"{self.hub.branches[args[-1].removesuffix('^{commit}')][-1]}\n"
        return ""


@pytest.fixture
def world(tmp_path):
    hub = FakeGitHub(NOW)
    hub.add(10, BODY, ["ready-for-agent"], title=TITLE)
    hub.required_by_base = {"main": set(REQUIRED)}  # main requires every job, as the protection of the layer's repo
    return hub, Store.open(tmp_path / "events")


def opened(world, acceptance=GOOD, *, git=None, branch="ticket-10", number=10, part=None, **settings):
    hub, store = world
    hub.branches.setdefault(branch, [*hub.branches["main"], hub.sha("h")])
    git = git or Git()
    git.hub = hub
    return open_pr(hub, store, REPO, number, acceptance=acceptance, branch=branch, base="main", git=git,
                   settings=MergeSettings(**settings), now=hub.now, part=part)


def test_it_opens_one_pull_request_with_refs_parks_the_ticket_and_sets_no_auto_merge(world):
    hub, store = world
    git = Git()
    done = opened(world, git=git)
    pull = hub.pull(REPO, done.pr)
    assert pull.body.startswith("Refs #10\n") and pull.title == TITLE
    assert marks.ready_since(hub.comments(REPO, done.pr)) is not None
    assert not [c for c in hub.calls if c[0] in ("auto_merge", "merge")]  # the line merges it later, not `pr open`
    assert not marks.closing_keywords(pull.body)
    assert ["push", "origin", "ticket-10:refs/heads/ticket-10"] in git.calls
    ticket = tickets.parse(hub.issues[(REPO, 10)].body, labels=hub.issues[(REPO, 10)].labels)
    assert ticket.park_lines == (f"Parked: pr_ended #{done.pr} until 2026-11-04",) and ticket.parked
    assert "ready-for-agent" in ticket.labels  # every other label kept (#398)
    assert (done.reused, done.head) == (False, pull.head_sha)
    rows = store.query("SELECT kind, step, json_extract(detail, '$.pr') FROM events ORDER BY at").rows
    assert ("step.started", "pr", done.pr) in rows and ("ticket.parked", None, None) in rows


def test_pr_open_hands_the_ready_mark_to_after_open(world, monkeypatch):
    calls = []
    monkeypatch.setattr(pr_open, "after_open", lambda *args: calls.append(args))
    done = opened(world)
    [(hub, repo, pr, head, ticket, now)] = calls
    assert (repo, pr, head, ticket) == (REPO, done.pr, done.head, 10)
    assert now == world[0].now and hub is world[0]


@pytest.mark.parametrize("requires", [set(REQUIRED), set(), {REQUIRED[0]}])
def test_after_open_writes_the_ready_mark_on_every_base_and_nothing_else(requires):
    hub = FakeGitHub(NOW)
    hub.required_by_base = {"main": requires}
    pr = hub.open_pull(branch="feature")
    pr_open.after_open(hub, REPO, pr, hub.head(pr), None, NOW)
    assert marks.ready_since(hub.comments(REPO, pr)) is not None
    assert [c for c in hub.calls if c[0] in ("auto_merge", "merge")] == []


def test_after_open_with_the_mark_already_at_the_head_writes_no_second_mark():
    hub = FakeGitHub(NOW)
    pr = hub.open_pull(branch="feature")
    head = hub.head(pr)
    pr_open.after_open(hub, REPO, pr, head, None, NOW)
    written = len(hub.comments(REPO, pr))
    pr_open.after_open(hub, REPO, pr, head, None, NOW, mark_written=True)
    assert len(hub.comments(REPO, pr)) == written  # the mark stands at the head: no second one


def test_the_acceptance_comment_on_the_ticket_holds_every_row_and_the_body_it_was_checked_on(world):
    hub, _ = world
    done = opened(world)
    found = marks.acceptance(hub.comments(REPO, 10))
    assert found.pr == done.pr and found.head == done.head
    assert [(r.id, r.result) for r in found.rows] == [("1", "pass"), ("2", "pass"), ("3", "na"), ("4", "after merge")]
    assert found.body == marks.body_hash(BODY)
    ready = marks.ready_since(hub.comments(REPO, done.pr))
    assert ready is not None and ready.ticket == 10


@pytest.mark.parametrize("acceptance, code", [
    ("", "acceptance_unreadable"),
    ("| row | check |\n|---|---|\n| 1 | x |\n", "acceptance_unreadable"),
    (GOOD.replace("| 2 | pass | 5.10 = 5.10 |\n", ""), "row_missing"),
    (GOOD.replace("| 2 | pass |", "| 2 | fail |"), "row_failed"),
    (GOOD + "| 9 | pass | ? |\n", "row_unknown"),
    (GOOD + "| 1 | pass | again |\n", "row_twice"),
    (GOOD.replace("| 3 | na | the zone table has no 50667 yet: asked in #10 |", "| 3 | na | |"), "row_bad"),
    (GOOD.replace("| 4 | after merge |", "| 4 | pass |"), "row_bad"),
    (GOOD.replace("| 1 | pass |", "| 1 | after merge |"), "row_bad"),
    (GOOD.replace("| 1 | pass |", "| 1 | green |"), "row_bad"),
])
def test_an_acceptance_that_does_not_pass_every_row_opens_nothing(world, acceptance, code):
    hub, _ = world
    with pytest.raises(PrError) as caught:
        opened(world, acceptance)
    assert caught.value.code == code
    assert not [c for c in hub.calls if c[0] == "create_pull"] and not hub.issues[(REPO, 10)].body.startswith("Parked:")


@pytest.mark.parametrize("message, found", [
    ("Closes #10", "Closes #10"), ("fix: resolves owner-x/repo-y#10", "resolves owner-x/repo-y#10"),
    ("FIXED #3", "FIXED #3"), ("Close: #10", "Close: #10"),
    ("fixes https://github.com/owner-x/repo-y/issues/10", "fixes https://github.com/owner-x/repo-y/issues/10"),
])
def test_a_closing_keyword_in_a_commit_is_refused_before_the_push(world, message, found):
    git = Git(["first", message])
    with pytest.raises(PrError) as caught:
        opened(world, git=git)
    assert caught.value.code == "closing_keyword" and found in str(caught.value)
    assert not [c for c in git.calls if c[0] == "push"]


def test_words_like_a_keyword_are_no_closing_keyword():
    assert marks.closing_keywords("Refs #10; the fixture closes the file; fixes nothing; closed-loop #4x") == []


def test_run_again_it_reuses_the_pull_request_of_its_branch_and_puts_it_back_in_the_line(world):
    hub, _ = world
    first = opened(world)
    hub.later(hours=1)
    hub.note(first.pr, marks.left_comment("red", "linux failed"), author="owner-x")
    from orch_it.tickets import park
    park.take_off(hub, hub.issues[(REPO, 10)])  # follow returned the ticket to ready
    hub.later(minutes=5)
    again = opened(world)
    assert (again.pr, again.reused) == (first.pr, True)
    assert len([c for c in hub.calls if c[0] == "create_pull"]) == 1
    assert marks.ready_since(hub.comments(REPO, again.pr)).at == hub.now
    assert tickets.parse(hub.issues[(REPO, 10)].body).park_lines == (f"Parked: pr_ended #{again.pr} until 2026-11-04",)


def test_one_pull_request_per_ticket_another_branch_s_open_one_is_refused(world):
    hub, _ = world
    opened(world)
    with pytest.raises(PrError) as caught:
        opened(world, branch="ticket-10-again")
    assert caught.value.code == "ticket_has_pr"


def test_a_branch_whose_pull_request_names_another_ticket_is_refused(world):
    hub, _ = world
    hub.add(11, BODY, ["ready-for-agent"], title=TITLE)
    opened(world, number=11, branch="shared")
    with pytest.raises(PrError) as caught:
        opened(world, branch="shared")
    assert caught.value.code == "pr_other_ticket"


@pytest.mark.parametrize("setup, code", [
    (lambda hub: hub.close(10), "ticket_not_open"),
    (lambda hub: hub.issues.pop((REPO, 10)), "ticket_missing"),
    (lambda hub: hub.add(10, "no table here", ["ready-for-agent"]), "acceptance_missing"),
])
def test_a_ticket_that_cannot_take_a_pull_request_is_refused(world, setup, code):
    hub, _ = world
    setup(hub)
    with pytest.raises(PrError) as caught:
        opened(world)
    assert caught.value.code == code


def test_the_base_branch_itself_is_no_ticket_branch(world):
    with pytest.raises(PrError) as caught:
        opened(world, branch="main")
    assert caught.value.code == "branch_is_base"


def test_a_ticket_parked_on_something_else_is_refused_and_keeps_its_park(world):
    hub, store = world
    hub.add(14)
    from orch_it.tickets import park
    park.park(hub, store, REPO, 10, "ticket_closed", "#14", "2026-11-30", today=NOW.date())
    with pytest.raises(PrError) as caught:
        opened(world)
    assert caught.value.code == "parked_elsewhere"
    assert tickets.parse(hub.issues[(REPO, 10)].body).park_lines == ("Parked: ticket_closed #14 until 2026-11-30",)


def test_a_push_git_refuses_is_a_coded_error(world):
    with pytest.raises(PrError) as caught:
        opened(world, git=Git(fail="push"))
    assert caught.value.code == "push_failed"


def test_an_after_merge_row_may_be_left_out_of_the_measured_rows(world):
    hub, _ = world
    done = opened(world, GOOD.replace("| 4 | after merge | the weekly report after the merge |\n", ""))
    rows = marks.acceptance(hub.comments(REPO, 10)).rows
    assert [(r.id, r.result) for r in rows][-1] == ("4", "after merge") and done.pr


@pytest.mark.parametrize("branch", ["HEAD", "-x", "--receive-pack=echo", "a b", "a..b", "x.lock", "/x", "x/", ""])
def test_a_branch_name_git_would_read_as_another_thing_is_refused_before_git_runs(world, branch):
    git = Git()
    with pytest.raises(PrError) as caught:
        opened(world, git=git, branch=branch)
    assert caught.value.code == "branch_bad" and git.calls == []


def test_the_acceptance_names_the_head_the_rows_were_measured_on(world):
    hub, _ = world

    class Measured(Git):
        def __call__(self, args):
            if args[0] == "rev-parse":
                self.calls.append(args)
                return "elsewhere\n"
            return super().__call__(args)

    with pytest.raises(PrError) as caught:
        opened(world, git=Measured())
    assert caught.value.code == "head_moved"


def test_a_fork_s_branch_of_the_same_name_is_never_the_ticket_s_pull_request(world):
    hub, _ = world
    fork = hub.open_pull(branch="ticket-10", body="Refs #10")
    hub.pulls[fork].head_repo = "stranger/repo-y"
    done = opened(world)
    assert done.pr != fork and not done.reused


@pytest.mark.parametrize("field, text", [("body", "Refs #10\n\nCloses #10"), ("title", "Fixes GH-10")])
def test_a_reused_pull_request_whose_own_text_closes_an_issue_is_refused(world, field, text):
    hub, _ = world
    first = opened(world)
    setattr(hub.pulls[first.pr], field, text)
    with pytest.raises(PrError) as caught:
        opened(world)
    assert caught.value.code == "closing_keyword"


def test_a_pasted_mark_in_a_person_s_comment_is_no_mark_of_the_layer(world):
    hub, _ = world
    done = opened(world)
    hub.note(done.pr, marks.left_comment("red", "pasted"), author="a-stranger", at=hub.now + timedelta(minutes=1))
    said = marks.of_layer(hub.comments(REPO, done.pr), "owner-x")
    assert marks.ready_since(said) is not None


def test_a_second_pr_open_that_loses_the_park_closes_its_own_new_pull_request(world):
    hub, store = world
    from orch_it.tickets import park
    real = park.park

    def raced(*args, **kwargs):  # another pr open of the ticket parked it a moment before this one
        raise park.ParkError("already_parked", "Parked: pr_ended #1 until 2026-11-04")

    park.park = raced
    try:
        with pytest.raises(PrError) as caught:
            opened(world)
    finally:
        park.park = real
    assert caught.value.code == "ticket_has_pr"
    [lost] = [p for p in hub.pulls.values()]
    assert lost.state == "closed"


def test_orch_s_notes_go_into_the_body_and_are_held_to_the_same_keyword_rule(world):
    hub, store = world
    hub.branches["ticket-10"] = [*hub.branches["main"], hub.sha("h")]
    git = Git()
    git.hub = hub
    done = open_pr(hub, store, REPO, 10, acceptance=GOOD, branch="ticket-10", base="main", git=git,
                   settings=MergeSettings(), now=hub.now, notes="unit 1: code-review: called, 2 findings fixed")
    assert hub.pull(REPO, done.pr).body.rstrip().endswith("unit 1: code-review: called, 2 findings fixed")
    with pytest.raises(PrError) as caught:
        open_pr(hub, store, REPO, 10, acceptance=GOOD, branch="ticket-10", base="main", git=git,
                settings=MergeSettings(), now=hub.now, notes="this fixes #4")
    assert caught.value.code == "closing_keyword"


def test_the_park_lasts_past_the_pr_s_bound(world):
    done = opened(world, pr_max_hours=72)
    hub, _ = world
    assert tickets.parse(hub.issues[(REPO, 10)].body).park_lines == (f"Parked: pr_ended #{done.pr} until 2026-11-06",)


@pytest.mark.parametrize("body, found", [
    ("Refs #10\nPart: 1 of 2\n\nTitle\n", (1, 2)),
    ("Refs #10\nPart: 2 of 2", (2, 2)),
    ("Refs #10\r\nPart: 1 of 3\r\n", (1, 3)),
    ("Part: 3 of 2\n", None),
    ("Part: 0 of 2\n", None),
    ("Refs #10\n\nno part line here\n", None),
    ("Part: one of two\n", None),
    ("Part: 1/2\n", None),
    ("see Part: 1 of 2 in the middle\n", None),
    ("", None),
])
def test_part_of_reads_the_part_line_and_refuses_anything_else(body, found):
    assert marks.part_of(body) == found


@pytest.mark.parametrize("text", ["3/2", "0/2", "1/0", "1-2", "a/b", "", "1/2/3", "-1/2"])
def test_a_bad_part_is_refused_before_anything_is_written(text):
    with pytest.raises(PrError) as caught:
        parse_part(text)
    assert caught.value.code == "bad_part"


@pytest.mark.parametrize("part", [(3, 2), (0, 2), (1, 0), (-1, 2)])
def test_the_library_refuses_a_part_outside_one_to_n_and_writes_nothing(world, part):
    hub, _ = world
    with pytest.raises(PrError) as caught:
        opened(world, part=part)
    assert caught.value.code == "bad_part" and not hub.pulls and not hub.writes


def test_a_good_part_argument_is_read_as_k_and_n():
    assert parse_part("1/2") == (1, 2) and parse_part("2/2") == (2, 2)


def test_a_part_pull_request_says_part_k_of_n_after_refs(world):
    hub, _ = world
    done = opened(world, part=(1, 2))
    body = hub.pull(REPO, done.pr).body
    assert body.startswith("Refs #10\nPart: 1 of 2\n") and marks.part_of(body) == (1, 2)


def test_a_one_part_pull_request_writes_no_part_line(world):
    hub, _ = world
    done = opened(world)
    assert "Part:" not in hub.pull(REPO, done.pr).body and marks.part_of(hub.pull(REPO, done.pr).body) is None


def test_a_reused_pull_request_keeps_its_body_when_run_again_with_a_part(world):
    hub, _ = world
    first = opened(world, part=(1, 2))
    before = hub.pull(REPO, first.pr).body
    again = opened(world, part=(2, 2))
    assert again.reused and again.pr == first.pr
    assert hub.pull(REPO, again.pr).body == before


def test_a_merged_earlier_pull_request_of_the_ticket_does_not_refuse_the_next_part(world):
    hub, _ = world
    old = hub.open_pull(branch="ticket-10-part-1", body="Refs #10\nPart: 1 of 2\n", title=TITLE)
    hub.pulls[old].state = "merged"
    hub.pulls[old].merged_at = hub.now
    done = opened(world, branch="ticket-10-part-2", part=(2, 2))
    assert done.pr != old and not done.reused
    assert marks.part_of(hub.pull(REPO, done.pr).body) == (2, 2)
