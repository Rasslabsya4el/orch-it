"""The worktree-delete guard on real folders (#395 decision 2, rows 2, 3 and 4): a target that is
a protected root, an ancestor of one, or a folder holding a link outward is refused; the case
rule is the one measured on the folder, shown by faking the opposite rule on the same OS."""

import os
import subprocess
import sys

import pytest

from guard_links import make_junction, make_link
from orch_it.guards import paths

WINDOWS = sys.platform == "win32"
INSENSITIVE_HERE = sys.platform in ("win32", "darwin")


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def hit(target, roots, **options):
    """The guard's rule for deleting `target` against `roots`, or `pass` when it is let
    through; both the target and the roots resolved to their real paths first."""

    real = paths.resolve(str(target), None, windows=WINDOWS)
    roots = [paths.resolve(str(root), None, windows=WINDOWS) for root in roots]
    found = paths.judge(real, roots, windows=WINDOWS, **options)
    return found.rule if found else "pass"


def always(answer):
    return lambda folder: answer


@pytest.mark.skipif(INSENSITIVE_HERE, reason="the linux job's case-sensitive file system")
def test_on_a_case_sensitive_folder_wt_beside_worktree_Wt_passes_and_Wt_is_refused(tmp_path):
    (tmp_path / "Wt").mkdir()
    (tmp_path / "wt").mkdir()
    roots = [tmp_path / "Wt"]
    assert hit(tmp_path / "wt", roots) == "pass"
    assert hit(tmp_path / "Wt", roots) == "protected_root"
    # the opposite rule faked on the same folders gives the opposite verdict for `wt`
    assert hit(tmp_path / "wt", roots, case_rule=always(True)) == "protected_root"


@pytest.mark.skipif(not WINDOWS, reason="the windows job's case-insensitive file system")
def test_on_a_case_insensitive_folder_WT_names_the_worktree_wt_and_is_refused(tmp_path):
    (tmp_path / "wt").mkdir()
    roots = [tmp_path / "wt"]
    assert hit(tmp_path / "WT", roots) == "protected_root"
    assert hit(tmp_path / "wT" / ".." / "WT", roots) == "protected_root"
    # the opposite rule faked on the same folder gives the opposite verdict
    assert hit(tmp_path / "WT", roots, case_rule=always(False)) == "pass"


def test_an_ancestor_of_a_protected_root_is_refused(tmp_path):
    (tmp_path / "repo" / ".claude" / "worktrees" / "w1").mkdir(parents=True)
    roots = [tmp_path / "repo" / ".claude" / "worktrees" / "w1"]
    assert hit(tmp_path / "repo" / ".claude", roots) == "ancestor_of_root"
    assert hit(tmp_path, roots) == "ancestor_of_root"


def test_a_file_or_folder_inside_a_root_and_a_missing_target_pass(tmp_path):
    (tmp_path / "wt" / "src").mkdir(parents=True)
    (tmp_path / "wt" / "notes.txt").write_text("x")
    roots = [tmp_path / "wt"]
    assert hit(tmp_path / "wt" / "notes.txt", roots) == "pass"
    assert hit(tmp_path / "wt" / "src", roots) == "pass"
    assert hit(tmp_path / "gone", roots) == "pass"


def test_a_root_reached_through_a_link_is_refused(tmp_path):
    (tmp_path / "wt").mkdir()
    make_link(tmp_path / "door", tmp_path / "wt")
    assert hit(tmp_path / "door", [tmp_path / "wt"]) == "protected_root"


def test_a_folder_holding_a_link_to_another_worktree_is_refused(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "scratch" / "deep").mkdir(parents=True)
    make_link(tmp_path / "scratch" / "deep" / "to-other", tmp_path / "other")
    assert hit(tmp_path / "scratch", [tmp_path / "other"]) == "link_outward"


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows links")
def test_a_folder_holding_a_junction_to_another_worktree_is_refused(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "scratch").mkdir()
    make_junction(tmp_path / "scratch" / "j", tmp_path / "other")
    assert hit(tmp_path / "scratch", [tmp_path / "other"]) == "link_outward"


def test_a_folder_holding_a_link_inside_itself_passes(tmp_path):
    (tmp_path / "scratch" / "a" / "b").mkdir(parents=True)
    make_link(tmp_path / "scratch" / "to-b", tmp_path / "scratch" / "a" / "b")
    assert hit(tmp_path / "scratch", []) == "pass"


def test_a_tree_of_ten_thousand_entries_is_walked_to_its_one_link_at_the_end(tmp_path):
    tree = tmp_path / "big"
    for d in range(100):
        folder = tree / f"d{d:03}"
        folder.mkdir(parents=True)
        for f in range(99):
            (folder / f"f{f:02}").touch()
    (tmp_path / "outside").mkdir()
    make_link(tree / "d099" / "zz-link", tmp_path / "outside")
    assert sum(len(files) + len(dirs) for _, dirs, files in os.walk(tree)) >= 10_000
    assert hit(tree, []) == "link_outward"
    assert hit(tree, [], walk_links=False) == "pass"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_a_worktree_outside_the_usual_folder_is_protected_like_one_inside(tmp_path, tmp_path_factory):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "x")
    inside = repo / ".claude" / "worktrees" / "in"
    outside = tmp_path_factory.mktemp("elsewhere") / "out"
    git(repo, "worktree", "add", "-q", str(inside), "-b", "in")
    git(repo, "worktree", "add", "-q", str(outside), "-b", "out")
    roots = paths.protected_roots(str(inside), windows=WINDOWS)
    for target in (repo, inside, outside, repo / ".git"):
        assert hit(target, roots) == "protected_root", target
    assert hit(outside.parent, roots) == "ancestor_of_root"
    assert hit(outside / "x.txt", roots) == "pass"


def test_outside_a_repo_nothing_is_protected(tmp_path):
    assert paths.protected_roots(str(tmp_path), windows=WINDOWS) == ()


@pytest.mark.skipif(not WINDOWS, reason="a trailing-dot name is a Windows case; junctions are Windows links")
def test_a_link_whose_name_ends_in_a_dot_is_still_found(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "scratch").mkdir()
    make_junction(tmp_path / "scratch" / "lnk.", tmp_path / "other")
    assert hit(tmp_path / "scratch", [tmp_path / "other"]) == "link_outward"


# --- decision 2 widening (brain comment 6405080397): inside another worktree -----------------

def inside_hit(target, roots, own, **kw):
    real = paths.resolve(str(target), None, windows=WINDOWS)
    roots = [paths.resolve(str(r), None, windows=WINDOWS) for r in roots]
    own = paths.resolve(str(own), None, windows=WINDOWS)
    found = paths.judge(real, roots, windows=WINDOWS, own_root=own, **kw)
    return found.rule if found else "pass"


def two_worktrees(tmp_path):
    for name in ("own", "other", "main"):
        (tmp_path / name / "src").mkdir(parents=True)
    return [tmp_path / "own", tmp_path / "other", tmp_path / "main"]


def test_a_delete_inside_another_worktree_is_refused(tmp_path):
    roots = two_worktrees(tmp_path)
    own = tmp_path / "own"
    (tmp_path / "main" / "src" / "x.py").write_text("x")
    assert inside_hit(tmp_path / "other" / "src", roots, own) == "inside_other_root"
    assert inside_hit(tmp_path / "main" / "src" / "x.py", roots, own) == "inside_other_root"
    assert inside_hit(tmp_path / "other", roots, own) == "protected_root"


def test_a_delete_inside_the_own_worktree_passes(tmp_path):
    roots = two_worktrees(tmp_path)
    own = tmp_path / "own"
    (tmp_path / "own" / "scratch").mkdir()
    assert inside_hit(tmp_path / "own" / "src", roots, own) == "pass"
    assert inside_hit(tmp_path / "own" / "scratch", roots, own) == "pass"
    # the own worktree root itself is still a protected root
    assert inside_hit(tmp_path / "own", roots, own) == "protected_root"


def test_without_an_own_root_the_inside_rule_is_off(tmp_path):
    # the old behaviour (a path inside a root passes) holds when no own_root is given
    roots = two_worktrees(tmp_path)
    assert hit(tmp_path / "other" / "src", roots) == "pass"


def test_nested_roots_take_the_deepest_holding_root(tmp_path):
    # the main checkout holds its worktrees: a path can be below two roots at once
    main = tmp_path / "repo"
    (main / "build").mkdir(parents=True)
    guards = main / ".claude" / "worktrees" / "guards"
    tickets = main / ".claude" / "worktrees" / "tickets"
    (guards / "build").mkdir(parents=True)
    (tickets / "src").mkdir(parents=True)
    roots = [main, guards, tickets]
    # from the main checkout, a delete inside a worktree is refused
    assert inside_hit(tickets / "src", roots, main) == "inside_other_root"
    # from guards, a delete inside another worktree is refused; inside guards passes
    assert inside_hit(tickets / "src", roots, guards) == "inside_other_root"
    assert inside_hit(guards / "build", roots, guards) == "pass"
    # from the main checkout, a delete inside the main checkout (not in a worktree) passes
    assert inside_hit(main / "build", roots, main) == "pass"
    # own_root is the deepest root holding cwd
    assert paths.deepest_root(str(guards / "sub"), [paths.resolve(str(r), None, windows=WINDOWS) for r in roots], windows=WINDOWS) == paths.resolve(str(guards), None, windows=WINDOWS)


def test_a_rootless_delete_inside_own_worktree_passes_but_inside_another_refuses(tmp_path):
    # roots_too=False (a filtered find, git clean) aimed at a worktree root: own passes (it is
    # your own work), another refuses; the own worktree nests inside the main checkout
    main = tmp_path / "repo"
    (main / "src").mkdir(parents=True)
    guards = main / ".claude" / "worktrees" / "guards"
    tickets = main / ".claude" / "worktrees" / "tickets"
    guards.mkdir(parents=True)
    tickets.mkdir(parents=True)
    roots = [main, guards, tickets]
    own = guards
    assert inside_hit(guards, roots, own, roots_too=False) == "pass"
    assert inside_hit(tickets, roots, own, roots_too=False) == "inside_other_root"
    # the main checkout is above the worktree roots, so it is refused as their ancestor
    assert inside_hit(main, roots, own, roots_too=False) == "ancestor_of_root"
    # deleting the own worktree root itself (roots_too=True) is still refused
    assert inside_hit(guards, roots, own) == "protected_root"
