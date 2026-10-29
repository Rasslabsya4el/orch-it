"""The closing check (https://github.com/Rasslabsya4el/orchestration-layer/issues/422): a commit, a pull request title or body that holds a GitHub closing word before
a reference (`fixes <URL>`, `Resolves #N`, across a line break too) is refused before it lands, and the
`linux` job runs the check on every pull request beside the private-data scan.

Seams: the command `python -m orch_it merge closing-check` through `main()` on a throwaway repository
whose `origin/main` is set by `update-ref` (no remote needed), with the pull request's title and body in
the environment as the workflow passes them; and the `tests.yml` step, read with the workflow test's own
line reader (`test_workflow.py`). Names are invented (CODING_STANDARDS).
"""

import re

import pytest

from orch_it.cli import main
from scan_repos import Repo
from test_workflow import WORKFLOW, job_lines, run_script, step_env, step_named, steps_of

URL = "https://github.com/acme-dev/orch-tools/issues/7"
STEP = "closing words in the pull request's commits, title and body"


@pytest.fixture(autouse=True)
def no_pull_request_text(monkeypatch):
    monkeypatch.delenv("PR_TITLE", raising=False)
    monkeypatch.delenv("PR_BODY", raising=False)


def pull_request(tmp_path, *messages):
    """A repository whose `origin/main` is the base commit, and whose head adds one commit per message:
    (the repository, the head sha)."""

    repo = Repo(tmp_path / "repo")
    base = repo.commit({"a.txt": "base\n"}, message="base work")
    repo.git("update-ref", "refs/remotes/origin/main", base)
    head = base
    for number, message in enumerate(messages):
        head = repo.commit({f"work{number}.txt": f"{number}\n"}, message=message)
    return repo, head


def check(capsys, repo, head):
    code = main(["merge", "closing-check", "--base", "main", "--head", head, "--work-tree", str(repo.path)])
    out, err = capsys.readouterr()
    return code, out, err


def test_a_commit_that_breaks_a_closing_word_from_its_reference_is_refused_and_its_sha_named(tmp_path, capsys):
    repo, head = pull_request(tmp_path, f"Add the thing\n\nfixes\n{URL}")
    code, out, err = check(capsys, repo, head)
    assert code == 1, out + err
    assert head[:12] in out
    assert "line 3" in out
    assert f"write `Refs {URL}`" in out
    assert "error:" not in err


def test_a_commit_with_a_ticket_line_and_a_closed_by_a_keyword_with_no_reference_passes(tmp_path, capsys):
    repo, head = pull_request(tmp_path, f"Add the thing\n\nTicket: {URL}\n\nclosed by a keyword in the test run")
    code, out, err = check(capsys, repo, head)
    assert code == 0, out + err
    assert "0 hits" in out


def test_a_pull_request_title_with_a_closing_word_before_a_reference_is_refused(tmp_path, capsys, monkeypatch):
    repo, head = pull_request(tmp_path, f"Ticket: {URL}")
    monkeypatch.setenv("PR_TITLE", f"Resolves {URL}")
    monkeypatch.setenv("PR_BODY", f"Refs {URL}")
    code, out, err = check(capsys, repo, head)
    assert code == 1, out + err
    assert "title" in out
    assert "body" not in out
    assert f"write `Refs {URL}`" in out


def test_a_pull_request_body_edited_to_a_closing_word_is_refused(tmp_path, capsys, monkeypatch):
    repo, head = pull_request(tmp_path, f"Ticket: {URL}")
    monkeypatch.setenv("PR_TITLE", "Add the thing")
    monkeypatch.setenv("PR_BODY", f"Resolves {URL}")
    code, out, err = check(capsys, repo, head)
    assert code == 1, out + err
    assert "body" in out


def test_the_skills_update_shapes_pass(tmp_path, capsys, monkeypatch):
    # the commit message of `_commit_skills` (src/orch_it/skills/update.py:486-487: `-m` of commit-tree)
    # and the title of `create_pr` (src/orch_it/skills/update.py:491, forge.py:77), in their own shapes
    repo, head = pull_request(tmp_path, "His skills at 0123456789ab: 2 changed (#397 skills update)")
    monkeypatch.setenv("PR_TITLE", "His skills move to 0123456789ab: text_changed pocock-tdd, renamed "
                                   "fix-issue, added resolve-trail ...")
    monkeypatch.setenv("PR_BODY", "His skills move to 0123456789ab. No copy is renamed, removed or changed in "
                                  "contract. Written by `python -m orch_it skills update`.")
    code, out, err = check(capsys, repo, head)
    assert code == 0, out + err


def test_the_linux_job_runs_the_check_on_pull_requests_before_pytest_with_the_text_in_env(tmp_path):
    jobs = job_lines(WORKFLOW.read_text("utf-8"))
    linux = steps_of(jobs["linux"])
    step = step_named(linux, STEP)
    assert "if: github.event_name == 'pull_request'" in "\n".join(step)
    pytest_step = step_named(linux, "pytest")
    assert linux.index(step) < linux.index(pytest_step)
    env = step_env(step)
    assert env["PR_TITLE"] == "${{ github.event.pull_request.title }}"
    assert env["PR_BODY"] == "${{ github.event.pull_request.body }}"
    assert run_script(step).strip() == 'python -m orch_it merge closing-check --base "$BASE_REF" --head "$HEAD"'
    assert "${{" not in run_script(step)
    assert STEP not in "\n".join(jobs["windows"])


def test_the_pull_request_trigger_runs_again_on_a_title_or_body_edit_and_keeps_the_other_triggers():
    text = WORKFLOW.read_text("utf-8")
    types = re.search(r"^  pull_request:\n    types: \[([^\]]*)\]$", text, re.M)
    assert types, "the pull_request trigger has no types list"
    assert {t.strip() for t in types.group(1).split(",")} == {"opened", "synchronize", "reopened", "edited"}
    assert "  push:\n    branches: [main]" in text
    assert "  schedule:\n    - cron:" in text
