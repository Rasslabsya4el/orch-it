"""The shipped instructions: the rule that no per-repo, per-account, per-machine or per-OS
value is a literal lives in `src/orch_it/shipped/` and is pulled into the repo's root `CLAUDE.md` by
an import line; the root `CODING_STANDARDS.md` is a byte copy of the shipped one, because the
reviewer's Read tool does not follow import lines. `problems` is what keeps those copies honest.

The data here is invented: a folder of its own, a repo of its own, no owner's name."""

import shutil
from pathlib import Path

from orch_it.shipped import RULE_FIRST_SENTENCE, problems

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "src/orch_it/shipped/CLAUDE.md",
    "src/orch_it/shipped/CODING_STANDARDS.md",
    "CLAUDE.md",
    "CODING_STANDARDS.md",
]


def layout_of(tmp_path: Path) -> Path:
    """A copy of the four instruction files under their own relative paths, in `tmp_path`."""

    for rel in FILES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    return tmp_path


def test_the_repo_is_fine():
    assert problems(ROOT) == []


def test_every_instruction_file_holds_the_first_sentence():
    # the root CLAUDE.md holds it through its import line, which must resolve to the shipped file
    assert RULE_FIRST_SENTENCE in (ROOT / "src/orch_it/shipped/CLAUDE.md").read_bytes().decode("utf-8")
    assert RULE_FIRST_SENTENCE in (ROOT / "src/orch_it/shipped/CODING_STANDARDS.md").read_bytes().decode("utf-8")
    assert RULE_FIRST_SENTENCE in (ROOT / "CODING_STANDARDS.md").read_bytes().decode("utf-8")
    lines = (ROOT / "CLAUDE.md").read_bytes().decode("utf-8").splitlines()
    imported = [line[1:] for line in lines if line.startswith("@")]
    assert imported == ["src/orch_it/shipped/CLAUDE.md"]
    assert RULE_FIRST_SENTENCE in (ROOT / imported[0]).read_bytes().decode("utf-8")


def test_the_root_claude_md_imports_the_shipped_one():
    lines = (ROOT / "CLAUDE.md").read_bytes().decode("utf-8").splitlines()
    assert "@src/orch_it/shipped/CLAUDE.md" in lines


def test_a_copy_of_the_layout_is_fine_before_any_change(tmp_path):
    assert problems(layout_of(tmp_path)) == []


def test_removing_the_first_sentence_from_the_shipped_claude_md_is_a_problem(tmp_path):
    root = layout_of(tmp_path)
    shipped = root / "src/orch_it/shipped/CLAUDE.md"
    shipped.write_bytes(shipped.read_bytes().replace(RULE_FIRST_SENTENCE.encode("utf-8"), b"Gone."))
    assert problems(root) != []


def test_changing_the_shipped_standards_without_the_root_copy_is_a_problem(tmp_path):
    root = layout_of(tmp_path)
    shipped = root / "src/orch_it/shipped/CODING_STANDARDS.md"
    shipped.write_bytes(shipped.read_bytes() + b"\nA new line in the shipped copy only.\n")
    assert problems(root) != []


def test_deleting_the_import_line_is_a_problem(tmp_path):
    root = layout_of(tmp_path)
    claude = root / "CLAUDE.md"
    kept = [line for line in claude.read_bytes().decode("utf-8").splitlines(keepends=True)
            if line.strip() != "@src/orch_it/shipped/CLAUDE.md"]
    claude.write_bytes("".join(kept).encode("utf-8"))
    assert problems(root) != []


def test_the_shipped_folder_is_package_data():
    pyproject = (ROOT / "pyproject.toml").read_bytes().decode("utf-8")
    assert "shipped/*.md" in pyproject
