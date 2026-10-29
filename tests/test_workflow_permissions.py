"""The token of every workflow holds exactly the permissions its code path reads or writes, each key with a
one-line reason beside it (the workflow-run ticket, unit 9). The defect it
guards: `main check` read the pull requests of a commit (`GhPullHub.pulls_of_commit`) with a token that held no
`pull-requests` key, so the run failed on GitHub only, where the token is the workflow's own. Only
`GET repos/<r>/actions/permissions` is out of every token's reach (it needs repository administration):
`GhPullHub.actions_enabled` takes that token's "Resource not accessible by integration" 403 as "on"."""

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"

# workflow file -> the token it may hold: what each key is for is written beside it in the file
EXPECTED = {
    # git fetch and the compare of a pull request (contents), a merge (pull-requests, contents), the ticket's
    # comments and labels (issues), rerun and cancel of a run (actions), the check runs of a head (checks)
    "follow.yml": {"contents": "write", "pull-requests": "write", "issues": "write", "actions": "write",
                   "checks": "read"},
    "reconcile.yml": {"contents": "write", "pull-requests": "write", "issues": "write", "actions": "write",
                      "checks": "read"},
    # checkout (contents), the pull requests of the commit a red run brings (pull-requests), the red-on-main
    # tickets (issues), the reports of a run and its rerun (actions)
    "main-check.yml": {"contents": "read", "pull-requests": "read", "issues": "write", "actions": "write"},
    "skills-update.yml": {"contents": "read", "issues": "write"},
    "ticket-shape.yml": {"contents": "read", "issues": "write"},
    "tests.yml": {"contents": "read", "actions": "read"},
}
KEY = re.compile(r"^  ([a-z-]+): (read|write|none)(?:\s+# (\S.*))?$")


def permissions_of(name: str) -> list[tuple[str, str, str | None]]:
    """(key, level, the comment beside it) of the top-level `permissions:` block of a workflow file."""

    lines = (WORKFLOWS / name).read_text("utf-8").splitlines()
    start = lines.index("permissions:")
    found = []
    for line in lines[start + 1:]:
        if not line.strip():
            break
        match = KEY.match(line)
        assert match, f"{name}: not a permission line: {line!r}"
        found.append(match.groups())
    return found


def test_every_workflow_file_is_in_the_table():
    assert sorted(path.name for path in WORKFLOWS.glob("*.yml")) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_workflow_holds_exactly_the_permissions_its_code_path_uses(name):
    assert {key: level for key, level, _ in permissions_of(name)} == EXPECTED[name]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_permission_key_says_why_in_one_line(name):
    for key, _, why in permissions_of(name):
        assert why and len(why.split()) >= 3, f"{name}: `{key}` has no reason beside it"

