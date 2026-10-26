"""The daily update workflow (#397 decision 4, row 8): it runs on a schedule and on demand, one run at
a time, and calls `skills report`, which keeps one issue per upstream commit; it opens no pull request,
so its token may read the code and write issues, nothing more (the brain's decision on #397 after the
probe orch-it #44)."""

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


def test_the_token_may_read_the_code_and_write_issues_only():
    assert set(section("permissions")) == {"contents: read", "issues: write"}


def test_it_calls_the_report_and_never_the_update():
    text = "\n".join(lines())
    assert 'python -m orch_it skills report --repo "$REPO"' in text
    assert "GH_TOKEN: ${{ github.token }}" in text and "REPO: ${{ github.repository }}" in text
    assert "timeout-minutes: 15" in text
    assert "skills update" not in text and "--dispatch-tests" not in text
