"""A closing keyword in a pull request's title or body, read by the merge line at its merge: the line
refuses the merge before GitHub is asked for it, a typed `closing_keyword` error names the keyword, the
reference and which text, and the ticket stays open. A title or body edited after `pr open` counts: the line
reads them fresh at the merge. GitHub's own auto-merge is a door of its own, so the tests that name the line
alone turn it off on their pull request."""

from datetime import timedelta

import pytest
from fake_github import REPO
from line_world import line_world

from orch_it.merge import commands
from orch_it.merge.line import LineError, run_line
from orch_it.merge.settings import MergeSettings

URL = f"https://github.com/{REPO}/issues/10"


def line(hub, store, ci, tmp_path):
    return run_line(hub, store, REPO, "main", settings=MergeSettings(), clock=lambda: hub.now,
                    sleep=ci.sleep, root=tmp_path / "locks", holder="test line")


def merge_calls(hub):
    return [call for call in hub.calls if call[0] == "merge"]


def ticket_open(hub, number):
    return hub.issues[(REPO, number)].open


def test_a_title_with_a_closing_keyword_is_refused_before_the_merge_and_the_ticket_stays_open(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    hub.pulls[pr].auto_merge = None
    hub.pulls[pr].title = f"fixes {URL}"
    with pytest.raises(LineError) as caught:
        line(hub, store, ci, tmp_path)
    assert caught.value.code == "closing_keyword"
    text = str(caught.value)
    assert "fixes" in text and URL in text and "title" in text and "Refs <URL>" in text
    assert isinstance(caught.value, commands.ERRORS)  # the CLI prints it as `error: closing_keyword`
    assert merge_calls(hub) == [] and hub.pull(REPO, pr).state == "open" and ticket_open(hub, 10)


def test_a_body_that_resolves_the_ticket_is_refused_and_the_ticket_stays_open(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    hub.pulls[pr].auto_merge = None
    hub.pulls[pr].body = f"Resolves {URL}\n"
    with pytest.raises(LineError) as caught:
        line(hub, store, ci, tmp_path)
    assert caught.value.code == "closing_keyword" and "Resolves" in str(caught.value)
    assert "body" in str(caught.value) and "Refs <URL>" in str(caught.value)
    assert merge_calls(hub) == [] and ticket_open(hub, 10)


def test_a_body_edited_after_pr_open_to_resolve_the_ticket_is_refused_at_the_merge(tmp_path):
    hub, store, prs, ci = line_world(tmp_path, tickets=(10,))
    pr = prs[10]
    hub.pulls[pr].auto_merge = None
    edited = []

    def edit_the_body_while_the_checks_run():  # a person edits the body after `pr open`, before the checks end
        check = hub.check_runs.get(hub.head(pr), {}).get("linux")
        if not edited and check and check.state == "pending" and hub.now - check.started_at >= timedelta(
                minutes=ci.minutes - 1):
            hub.pulls[pr].body = f"Resolves {URL}\n"
            edited.append(hub.now)

    ci.on_tick.append(edit_the_body_while_the_checks_run)
    with pytest.raises(LineError) as caught:
        line(hub, store, ci, tmp_path)
    assert edited, "the body was never edited while the checks ran"
    assert caught.value.code == "closing_keyword" and "Resolves" in str(caught.value)
    assert merge_calls(hub) == [] and ticket_open(hub, 10)


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
    done = line(hub, store, ci, tmp_path)  # no LineError: the check let it through
    assert all(s.result != "refused" for s in done.served) and merge_calls(hub)
