"""The daily update workflow (#397 decision 4, row 8): it runs on a schedule and on demand, one run at
a time, with the token rights an update pull request needs, and calls `skills update` with the tests
dispatched on its branch."""

import re
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "skills-update.yml"


def lines() -> list[str]:
    return [re.sub(r"\s+#.*$", "", line).rstrip() for line in WORKFLOW.read_text("utf-8").splitlines()
            if not line.lstrip().startswith("#")]


def section(name: str) -> list[str]:
    """The indented lines under a top-level key."""

    found, inside = [], False
    for line in lines():
        if line and not line[0].isspace():
            inside = line.startswith(f"{name}:")
        elif inside and line:
            found.append(line.strip())
    return found


def test_it_runs_daily_and_on_demand():
    on = section("on")
    assert "schedule:" in on and any(re.fullmatch(r'- cron: "\d+ \d+ \* \* \*"', item) for item in on)
    assert "workflow_dispatch:" in on


def test_one_run_at_a_time_never_cut():
    assert section("concurrency") == ["group: skills-update", "cancel-in-progress: false"]


def test_the_token_may_push_a_branch_open_a_pull_request_and_start_the_tests():
    assert set(section("permissions")) == {"contents: write", "pull-requests: write", "actions: write"}


def test_it_calls_the_update_with_the_tests_dispatched():
    text = "\n".join(lines())
    assert 'python -m orch_it skills update --repo "$REPO" --dispatch-tests' in text
    assert "GH_TOKEN: ${{ github.token }}" in text and "REPO: ${{ github.repository }}" in text
    assert "timeout-minutes: 15" in text


def test_the_dispatched_tests_are_the_workflow_that_exists():
    tests = WORKFLOW.with_name("tests.yml").read_text("utf-8")
    assert "workflow_dispatch:" in tests and "slow_and_live:" in tests
