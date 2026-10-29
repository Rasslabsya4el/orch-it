"""The closing check (the home repo's ticket #422): a commit, a pull request title or body that holds a GitHub closing word before
a reference (`fixes <URL>`, `Resolves #N`, across a line break too) is refused before it lands, and the
`linux` job runs the check on every pull request beside the private-data scan.

Seams: the command `python -m orch_it merge closing-check` through `main()` on a throwaway repository
whose `origin/main` is set by `update-ref` (no remote needed), with the pull request's title and body in
the environment as the workflow passes them; and the `tests.yml` trigger types, read with the workflow test's
own line reader (`test_workflow.py`; the step's own pin lives there). Names are invented (CODING_STANDARDS).
"""

import re

import pytest

from orch_it.cli import main
from orch_it.skills.classify import Change
from orch_it.skills.copies import Manifest
from orch_it.skills.update import Check, pr_body
from scan_repos import Repo
from test_workflow import WORKFLOW

URL = "https://github.com/example-owner/example-repo/issues/7"


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


def test_a_commit_that_refers_to_the_ticket_with_refs_passes(tmp_path, capsys):
    repo, head = pull_request(tmp_path, f"Add the thing\n\nRefs {URL}")
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
    # The commit message of `_commit_skills` (src/orch_it/skills/update.py:486-487) and the title of `create_pr`
    # (src/orch_it/skills/update.py:491) are f-strings inside `update()`, which this test does not run: they keep
    # their own samples in their own shapes. The body is `pr_body` (src/orch_it/skills/update.py:278), built for real
    # from a `Check` of one changed skill.
    repo, head = pull_request(tmp_path, "His skills at 0123456789ab: 2 changed (#397 skills update)")
    found = Check(manifest=Manifest("upstream-skills", "1" * 40, {}, {}),
                  new_commit="0123456789abcdef0123456789abcdef01234567", new_tree={},
                  changes=[Change("pocock-tdd", "text_changed", "skills/pocock-tdd/SKILL.md",
                                  "skills/pocock-tdd/SKILL.md")])
    monkeypatch.setenv("PR_TITLE", "His skills move to 0123456789ab: text_changed pocock-tdd, renamed "
                                   "fix-issue, added resolve-trail ...")
    monkeypatch.setenv("PR_BODY", pr_body(found, []))
    code, out, err = check(capsys, repo, head)
    assert code == 0, out + err


def test_the_trigger_types_run_the_check_again_on_an_edit_and_keep_the_other_triggers():
    text = WORKFLOW.read_text("utf-8")
    types = re.search(r"^  pull_request:\n    types: \[([^\]]*)\]$", text, re.M)
    assert types, "the pull_request trigger has no types list"
    assert {t.strip() for t in types.group(1).split(",")} == {"opened", "synchronize", "reopened", "edited"}
    assert "  push:\n    branches: [main]" in text
    assert "  schedule:\n    - cron:" in text
