"""A closing keyword in a pull request's title or body, read by the merge line at its merge: the line refuses
that pull request (a `refused` turn: a left mark with the reason, nothing merged), names the
keyword, its reference, the text and the line it starts on, says to write `Refs <URL>`, keeps the ticket open,
and goes on with the pull requests behind it. A title or body edited after `pr open` counts: the line reads
them fresh at the merge. The refusal tests mark the head green before the line starts, so the refusal is the only thing that stops the merge."""

from datetime import timedelta

from fake_github import PASSED, REPO
from line_world import line_world

from orch_it.events.store import Store
from orch_it.merge import marks
from orch_it.merge.line import run_line
from orch_it.merge.settings import MergeSettings
from required_names import LINUX_CHECK, WINDOWS_CHECK

URL = f"https://github.com/{REPO}/issues/10"


def line(hub, store, ci, tmp_path):
    return run_line(hub, store, REPO, "main", settings=MergeSettings(), clock=lambda: hub.now,
                    sleep=ci.sleep, root=tmp_path / "locks", holder="test line")


def merge_calls(hub):
    return [call for call in hub.calls if call[0] == "merge"]


def ticket_open(hub, number):
    return hub.issues[(REPO, number)].open


def green_now(hub, pr):
    """The head's required checks green before any tick: the line's first look at it sees green."""
    hub.checks_on(hub.head(pr), PASSED, started=hub.now)


def line_steps(store):
    return store.query("SELECT outcome FROM events WHERE kind = 'step.ended' AND step = 'line'").rows


def test_a_title_with_a_closing_keyword_refuses_that_pull_request_and_leaves_it_with_a_left_mark(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    hub.pulls[pr].title = f"fixes {URL}"
    green_now(hub, pr)
    assert marks.ready_since(hub.comments(REPO, pr)) is not None  # as `pr open` left it
    done = line(hub, store, ci, tmp_path)  # no exception: the line goes on
    [turn] = done.served
    assert turn.result == "refused" and turn.pr == pr
    text = turn.detail
    assert "fixes" in text and URL in text and "title at line 1" in text and "Refs <URL>" in text
    assert merge_calls(hub) == [] and ci.merged == []
    assert hub.pull(REPO, pr).state == "open" and marks.ready_since(hub.comments(REPO, pr)) is None
    assert ticket_open(hub, 10)
    assert marks.left_reason(hub.pull_comments(REPO, pr)) == "closing_keyword"
    assert line_steps(store) == [("passed",)]  # the run itself is not failed


def test_a_body_that_resolves_the_ticket_is_refused_and_names_the_line(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    hub.pulls[pr].body = f"Resolves {URL}\n"
    green_now(hub, pr)
    [turn] = line(hub, store, ci, tmp_path).served
    assert turn.result == "refused"
    assert "Resolves" in turn.detail and "body at line 1" in turn.detail and "Refs <URL>" in turn.detail
    assert merge_calls(hub) == [] and ticket_open(hub, 10)


def test_a_body_edited_after_pr_open_to_resolve_the_ticket_is_refused_at_the_merge(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    edited = []

    def edit_the_body_while_the_checks_run():  # a person edits the body after `pr open`, before the checks end
        check = hub.check_runs.get(hub.head(pr), {}).get(LINUX_CHECK)
        if not edited and check and check.state == "pending" and hub.now - check.started_at >= timedelta(
                minutes=ci.minutes - 1):
            hub.pulls[pr].body = f"Resolves {URL}\n"
            edited.append(hub.now)

    ci.on_tick.append(edit_the_body_while_the_checks_run)
    [turn] = line(hub, store, ci, tmp_path).served
    assert edited, "the body was never edited while the checks ran"
    assert turn.result == "refused" and "Resolves" in turn.detail
    assert merge_calls(hub) == [] and ticket_open(hub, 10)


def test_the_message_names_the_line_of_each_keyword_in_the_body(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    hub.pulls[pr].body = f"Refs #10\n\nFixes {URL}\nsome note\nResolves {URL}\n"
    green_now(hub, pr)
    [turn] = line(hub, store, ci, tmp_path).served
    assert turn.result == "refused"
    assert "body at line 3" in turn.detail and "body at line 5" in turn.detail
    assert any("line 3" in c.body and "line 5" in c.body for c in hub.pull_comments(REPO, pr))  # the left mark
    assert merge_calls(hub) == []


def test_a_refused_pull_request_does_not_stop_the_pull_request_behind_it(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10, 11))
    hub.pulls[prs[10]].title = f"fixes {URL}"
    green_now(hub, prs[10])
    done = line(hub, store, ci, tmp_path)
    assert [(s.pr, s.result) for s in done.served] == [(prs[10], "refused"), (prs[11], "merged")]
    assert ci.merged == [prs[11]] and ticket_open(hub, 10) and not ticket_open(hub, 11)
    assert line_steps(store) == [("passed",)]


def test_a_refused_pull_request_is_kept_out_of_the_next_run_until_a_new_ready_mark(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    hub.pulls[pr].body = f"Resolves {URL}\n"
    green_now(hub, pr)
    assert [s.result for s in line(hub, store, ci, tmp_path).served] == ["refused"]
    again = line(hub, store, ci, tmp_path)
    assert again.served == () and again.reason == "empty"
    assert marks.ready_since(hub.comments(REPO, pr)) is None and ticket_open(hub, 10)


def test_the_lines_own_refs_body_passes_and_merges(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    assert hub.pulls[pr].body.startswith("Refs #10")  # the body `pr open` wrote
    done = line(hub, store, ci, tmp_path)
    assert [s.result for s in done.served] == ["merged"] and ci.merged == [pr]


def test_a_ticket_line_passes_the_check(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    hub.pulls[pr].body = f"Ticket: {URL}\n"
    done = line(hub, store, ci, tmp_path)  # no refusal: the check let it through
    assert all(s.result != "refused" for s in done.served) and merge_calls(hub)
