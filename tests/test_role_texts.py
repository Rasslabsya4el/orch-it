"""Rules held as checks (#399 decision 4, rows 1 and 2): one check reads every text an agent works
from (the agent files, our skills, `CLAUDE.md`, `docs/`) and fails on what #382 found broken in the
old layer's files. It reads whatever files exist, so a role file added later is checked with no
edit of the check.

Each of #382's seven worst cases is rebuilt as a fixture tree under `tests/fixtures/roles/<case>/`,
laid over a copy of the repo's skills, and must be red with the right reason; the repo itself must
be green."""

import shutil
from pathlib import Path

import pytest

from orch_it.roles.texts import check

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "roles"


def tree_of(tmp_path: Path, case: str) -> Path:
    """A repo of the fixture `case` laid over a copy of the repo's skills and labels."""

    tree = tmp_path / case
    shutil.copytree(ROOT / ".claude" / "skills", tree / ".claude" / "skills")
    shutil.copytree(FIXTURES / case, tree, dirs_exist_ok=True)
    return tree


def codes(tree: Path) -> list[tuple[str, int, str]]:
    """The findings in the case's own files (the copied skills point at files the tree lacks)."""

    own = {p.relative_to(FIXTURES / tree.name).as_posix() for p in (FIXTURES / tree.name).rglob("*") if p.is_file()}
    return [(f.path, f.line, f.code) for f in check(tree) if f.path in own]


def test_a_path_to_a_folder_that_does_not_exist_is_red(tmp_path):
    # #382: `PYTHONPATH=<path>/src` to a folder that does not exist (orch.md:123)
    assert codes(tree_of(tmp_path, "dead-folder")) == [(".claude/agents/orch.md", 10, "path_missing")]


def test_a_pointer_to_a_section_that_does_not_exist_is_red(tmp_path):
    # #382: a dead pointer (worker-strong.md:23)
    assert codes(tree_of(tmp_path, "dead-pointer")) == [(".claude/agents/worker-strong.md", 8, "section_missing")]


def test_a_pointer_to_a_stub_file_or_an_empty_section_is_red(tmp_path):
    assert codes(tree_of(tmp_path, "stub")) == [(".claude/agents/worker.md", 8, "stub"),
                                                (".claude/agents/worker.md", 8, "stub")]


def test_a_check_with_no_source_is_red(tmp_path):
    # #382: a merge-budget check with no source (worker.md:52): the check is left to "the lead's own part of CLAUDE.md"
    assert codes(tree_of(tmp_path, "no-source")) == [(".claude/agents/worker.md", 8, "rule_deferred")]


def test_commands_and_flags_are_read_against_the_command_line(tmp_path):
    found = [(f.line, f.code, f.detail) for f in check(tree_of(tmp_path, "commands")) if f.path == "docs/commands.md"]
    assert found == [
        (3, "flag_unknown", "`python -m orch_it labels sync`: no option `--dry-run`"),
        (8, "command_unknown", "`python -m orch_it roles`: no command `check`"),
        (11, "command_unknown", "no module `orch_it.tickets.nohook`"),
        (13, "command_unknown", "`python -m orch_it tests run`: `plan9` is not one of its `--os` values"),
    ]


def test_a_rule_that_hands_work_to_a_removed_part_is_red(tmp_path):
    # #382: nobody takes a ticket with three failed attempts once the unblocker is gone (GLOSSARY.md:164)
    assert codes(tree_of(tmp_path, "no-taker")) == [(".claude/agents/orch.md", 10, "part_removed")]


def test_labels_are_read_against_the_label_set(tmp_path):
    found = [(f.line, f.code, f.detail) for f in check(tree_of(tmp_path, "labels")) if f.path == "docs/labels.md"]
    assert found == [
        (5, "label_unknown", "`stuck` is no label of the label set"),
        (7, "label_unknown", "`hard push` is no label of the label set"),
        (9, "label_unknown", "`autopilot` is no label of the label set"),
        (9, "part_removed", "`autopilot`: removed from the layer (#378); name what does its job now"),
        (11, "role_unknown", "`dispatcher` is no role of the roles settings"),
    ]


def test_a_model_written_into_a_text_is_red(tmp_path):
    # #382: a wrong brain model (PORTING.md:139-141); a model is a setting (decision 5)
    assert codes(tree_of(tmp_path, "model-literal")) == [("docs/porting.md", 3, "model_literal")] * 2  # flag and line


def test_a_merge_rule_said_in_two_files_is_red(tmp_path):
    # #382: the merge rule contradicting itself (.claude/agents/orch.md:29-32 vs docs/agents/orch.md:258-262)
    assert codes(tree_of(tmp_path, "merge-twice")) == [("docs/merging.md", 3, "rule_twice")]


def test_two_park_rules_in_one_file_are_red(tmp_path):
    # #382: two park-label rules (workflow.md:80-81 vs :141-143)
    assert codes(tree_of(tmp_path, "park-twice")) == [("docs/workflow.md", 14, "rule_twice")]


def test_a_rule_deferred_to_a_local_or_optional_file_is_red(tmp_path):
    assert codes(tree_of(tmp_path, "deferred")) == [(".claude/agents/worker.md", 8, "rule_deferred"),
                                                    (".claude/agents/worker.md", 10, "rule_deferred")]


def test_a_role_file_added_later_is_checked_with_no_edit_of_the_check(tmp_path):
    # row 2, the outside case: a role none of decision 1 names, with one break of each kind
    assert codes(tree_of(tmp_path, "reviewer-security")) == [
        (".claude/agents/reviewer-security.md", 9, "path_missing"),
        (".claude/agents/reviewer-security.md", 11, "command_unknown"),
        (".claude/agents/reviewer-security.md", 13, "label_unknown"),
        (".claude/agents/reviewer-security.md", 15, "skill_unknown"),
    ]


def test_each_of_the_seven_worst_cases_is_a_fixture():
    seven = {"no-source", "merge-twice", "dead-folder", "park-twice", "no-taker", "model-literal", "dead-pointer"}
    assert seven <= {p.name for p in FIXTURES.iterdir()}


def test_the_repo_is_green():
    assert [str(f) for f in check(ROOT)] == []
