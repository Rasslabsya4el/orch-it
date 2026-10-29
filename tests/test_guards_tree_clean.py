"""The tree of this repo has no finding (#413 unit 8): `guards scan --tree` over HEAD, with the exceptions list
`guards-exceptions.toml` at the repo root, finds nothing. The setup it runs on is invented per run, so the
test reads nothing of the machine and plants no value in the tree: each value carries a random tail that cannot
stand in any file. The seam is `scanner.scan_tree` with the patterns `Patterns.of_run` makes of the work tree
(its exceptions list, its workflow job names and its settings file)."""

import uuid
from pathlib import Path

from orch_it.guards import exceptions, scanner

ROOT = Path(__file__).resolve().parent.parent


def _setup():
    tail = uuid.uuid4().hex[:12]
    answers = {
        ("git", "remote", "get-url", "origin"): f"https://github.com/acct{tail}/repo{tail}.git",
        ("gh", "api", "user", "--jq", ".login"): f"acct{tail}",
        ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"): f"/folder{tail}/repo{tail}/.git",
        ("git", "config", "user.name"): f"Person {tail}",
        ("git", "config", "user.email"): f"person{tail}@mail{tail}.invalid",
    }
    return dict(env={"USER": f"login{tail}"}, run=lambda cmd: answers[tuple(cmd)],
                home=f"/folder{tail}/home", host=f"host{tail}")


def test_the_tree_has_no_finding():
    patterns = scanner.Patterns.of_run(work_tree=ROOT, **_setup())

    hits = scanner.scan_tree(ROOT, patterns, "HEAD")

    assert [(hit.file, hit.line, hit.kind) for hit in hits] == []


def test_the_exceptions_list_is_in_the_repo_and_every_entry_has_its_reason():
    listed = exceptions.load_exceptions(ROOT / exceptions.FILE_NAME)  # raises for an entry with no reason

    assert listed.entries
