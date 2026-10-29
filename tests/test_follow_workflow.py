"""The `follow` workflow (#400 decision 3): it runs the follow on the pull request and ticket events
that end or stop a pull request, uploads the events it wrote, and adds no required check: the
job names `linux` and `windows` stay the `tests` workflow's alone."""

import re
from pathlib import Path
from required_names import LINUX_CHECK, WINDOWS_CHECK

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
    # the store is set in the follow step's own env: the job-level env reads no `runner` context
    assert "          ORCH_IT_STORE: ${{ runner.temp }}/orch-it-events\n        run: python -m orch_it follow" in TEXT
    assert "    env:\n      ORCH_IT_STORE" not in TEXT


def test_it_runs_the_default_branch_s_code_never_the_pull_request_s():
    assert "ref: ${{ github.event.repository.default_branch }}" in TEXT
    assert "group: follow-${{ github.repository }}" in TEXT


def test_it_adds_no_required_check():
    jobs = re.findall(r"^  ([A-Za-z0-9_-]+):$", TEXT.split("\njobs:\n", 1)[1], re.MULTILINE)
    assert jobs == ["follow"]
    assert "pull_request:\n    types: [closed]" in TEXT  # never on opened or synchronize: no check on a head


def test_main_is_checked_when_a_tests_run_on_the_default_branch_ends():
    text = (WORKFLOWS / "main-check.yml").read_text("utf-8")
    assert "workflow_run:\n    workflows: [tests]\n    types: [completed]" in text
    assert ("github.event.workflow_run.head_branch == github.event.repository.default_branch &&\n"
            "      github.event.workflow_run.event != 'pull_request'") in text  # a fork's branch named main is no main
    assert 'python -m orch_it main check --repo "$GITHUB_REPOSITORY" --branch "$BRANCH" --role github' in text
    assert "name: orch-it-events-main-${{ github.run_id }}-${{ github.run_attempt }}" in text


def test_the_sweep_runs_on_a_schedule_and_by_hand_never_beside_a_follow():
    text = (WORKFLOWS / "reconcile.yml").read_text("utf-8")
    assert re.search(r'^  schedule:\n    - cron: "[^"]+"\n  workflow_dispatch:$', text, re.MULTILINE)
    assert "group: follow-${{ github.repository }}" in text
    assert 'python -m orch_it reconcile --repo "$GITHUB_REPOSITORY" --base "$BASE" --role github' in text


def test_the_merge_flow_s_workflows_add_no_job_named_like_a_required_check():
    for name in ("follow.yml", "main-check.yml", "reconcile.yml"):
        jobs = re.findall(r"^  ([A-Za-z0-9_-]+):$", (WORKFLOWS / name).read_text("utf-8").split("\njobs:\n", 1)[1],
                          re.MULTILINE)
        assert jobs and not {LINUX_CHECK, WINDOWS_CHECK} & set(jobs), name


def test_its_token_may_close_label_comment_and_rerun_but_no_more():
    permissions = TEXT.split("\npermissions:\n", 1)[1].split("\n\n", 1)[0].split("\n")
    assert sorted(re.sub(r"\s+#.*$", "", line.strip()) for line in permissions) == [
        "actions: write", "checks: read", "contents: write", "issues: write", "pull-requests: write"]
