"""The `follow` workflow (#400 decision 3): it runs the follow on the pull request and ticket events
that end or stop a pull request, uploads the events it wrote, and adds no required check: the
job names `linux` and `windows` stay the `tests` workflow's alone."""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"
TEXT = (WORKFLOWS / "follow.yml").read_text("utf-8")


def test_it_runs_on_every_event_that_moves_a_ticket():
    for event, types in [("pull_request", "closed"), ("pull_request_review", "submitted"),
                         ("pull_request_review_comment", "created"), ("issue_comment", "created"),
                         ("issues", "edited, closed")]:
        assert re.search(rf"^  {event}:\n    types: \[{types}\]$", TEXT, re.MULTILINE), event


def test_it_calls_the_follow_on_the_event_and_keeps_its_events():
    assert 'python -m orch_it follow --event "$GITHUB_EVENT_PATH" --repo "$GITHUB_REPOSITORY" --role github' in TEXT
    assert "name: orch-it-events-follow-${{ github.run_id }}-${{ github.run_attempt }}" in TEXT
    assert "ORCH_IT_STORE: ${{ runner.temp }}/orch-it-events" in TEXT


def test_it_runs_the_default_branch_s_code_never_the_pull_request_s():
    assert "ref: ${{ github.event.repository.default_branch }}" in TEXT
    assert "group: follow-${{ github.repository }}" in TEXT


def test_it_adds_no_required_check():
    jobs = re.findall(r"^  ([A-Za-z0-9_-]+):$", TEXT.split("\njobs:\n", 1)[1], re.MULTILINE)
    assert jobs == ["follow"]
    assert "pull_request:\n    types: [closed]" in TEXT  # never on opened or synchronize: no check on a head


def test_its_token_may_close_label_comment_and_rerun_but_no_more():
    permissions = TEXT.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0].split("\n")
    assert sorted(line.strip() for line in permissions) == [
        "actions: write", "contents: write", "issues: write", "pull-requests: write"]
