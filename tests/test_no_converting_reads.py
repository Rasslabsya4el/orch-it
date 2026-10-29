"""No git call in src/orch_it reads committed content through a converting path (#420).

A git read through a converting path gives bytes that depend on the machine's line-ending config or the tree's
attributes: `git archive` (it applies the attributes of the tree it archives), `cat-file` with `--filters` or
`--textconv`, and `show <rev>:<path>` whose bytes are hashed or written as a copy. The walk below finds every such
read in the package by its AST. A read that is known to hash nothing is listed by hand, with its reason and the
number of reads its function holds; a listed function holding another number of reads fails the test.

The detector is simple on purpose. A git call is a call or a list/tuple literal that names git: its callee is one
of GIT_HELPERS, or a literal "git" is among its arguments. Its argv is its positional arguments with the elements
of a list or tuple argument spliced in, in order. Reads are reported per (line, kind), and the enclosing function
of a read is the nearest def around it, so a read in a nested def is named by that def."""

import ast
from pathlib import Path
from typing import NamedTuple

import orch_it

SRC_FOLDER = Path(orch_it.__file__).parent
GIT_HELPERS = {"git", "_git", "_run", "_process"}  # callees whose literal arguments are git's argv
# (module, function) -> (reason, number of reads the function holds): a listed function holding another number
# of reads fails the test, so the listing covers exactly the reads it names.
LISTED_CONVERTING_READS = {
    ("brain/hook.py", "rules_from_origin"): (
        "show <rev>:RULES_FILE: the rules text the brain reads; it hashes nothing and is written nowhere", 1),
}


class Read(NamedTuple):
    module: str  # relative to the folder walked, POSIX
    line: int  # the line the git call's node starts on
    function: str  # the nearest enclosing def, or "<module>"
    kind: str  # "archive", "show" or "cat-file"


def converting_reads(source: str, module: str) -> list[Read]:
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.List, ast.Tuple)):
            continue
        if isinstance(node, (ast.List, ast.Tuple)) and isinstance(parents.get(node), ast.Call):
            continue  # the call that takes it as argv reports it
        argv = _argv(node)
        literals = [item.value for item in argv if _is_str(item)]
        callee = _callee(node)
        git = callee in GIT_HELPERS or "git" in literals
        for kind in _kinds(argv, literals, callee, git):
            found.setdefault((node.lineno, kind), Read(module, node.lineno, _scope_name(node, parents), kind))
    return sorted(found.values(), key=lambda read: (read.line, read.kind))


def _is_str(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _argv(node) -> list:
    """A call's positional arguments, a list or tuple argument spliced in place; a literal's own elements."""
    if not isinstance(node, ast.Call):
        return list(node.elts)
    argv = []
    for arg in node.args:
        argv += arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg]
    return argv


def _callee(node) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def _kinds(argv, literals, callee, git) -> list[str]:
    kinds = []
    if "archive" in literals and (callee in GIT_HELPERS or (
            "git" in literals and literals.index("git") < literals.index("archive"))):
        kinds.append("archive")
    if git and "show" in literals and any(_spells_rev_path(item) for item in argv):
        kinds.append("show")
    if "cat-file" in literals and ({"--filters", "--textconv"} & set(literals)):
        kinds.append("cat-file")
    return kinds


def _spells_rev_path(expr) -> bool:
    """A rev:path argument: a literal with a colon that is not an option, or a colon-carrying f-string,
    %-format, format call or concatenation with two dynamic parts."""
    if _is_str(expr):
        return ":" in expr.value and not expr.value.startswith("-")
    has_colon = any(_is_str(node) and ":" in node.value for node in ast.walk(expr))
    dynamic = sum(isinstance(node, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)) for node in ast.walk(expr))
    return has_colon and dynamic >= 2


def _scope_name(node, parents) -> str:
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


def walk_src(folder: Path = SRC_FOLDER) -> list[Read]:
    found = []
    for path in sorted(folder.rglob("*.py")):
        found += converting_reads(path.read_text("utf-8"), path.relative_to(folder).as_posix())
    return found


def listing_problems(found: list[Read], listed: dict[tuple[str, str], tuple[str, int]]) -> list[str]:
    """What is wrong with a walk against a listing: a read that no listing names, and a listed function whose
    number of reads is not the number its listing names. Each problem names its file and line."""
    problems = [f"{r.module}:{r.line} in {r.function}() ({r.kind})"
                for r in found if (r.module, r.function) not in listed]
    for (module, function), (_, count) in listed.items():
        at = [r.line for r in found if (r.module, r.function) == (module, function)]
        if len(at) != count:
            problems.append(f"{module} {function}() holds {len(at)} reads at lines {at}, listed {count}")
    return problems


# --- the detector's own tests (on small snippets) ------------------------------

def test_git_archive_in_a_subprocess_list_is_reported_with_its_line():
    source = ("import subprocess\n"
              "data = subprocess.run([\"git\", \"archive\", \"--format=tar\", commit], cwd=repo)\n")
    reads = converting_reads(source, "demo.py")
    assert [(r.line, r.kind) for r in reads] == [(2, "archive")]


def test_git_archive_as_a_helper_argument_is_reported():
    source = "git(\"archive\", \"--format=tar\", commit)\n_run(repo, \"archive\", commit)\n"
    assert [r.line for r in converting_reads(source, "demo.py")] == [1, 2]


def test_show_with_an_fstring_rev_path_is_reported():
    source = "def read(ref, path):\n    return git(\"show\", f\"{ref}:{path}\")\n"
    reads = converting_reads(source, "demo.py")
    assert [(r.line, r.kind, r.function) for r in reads] == [(2, "show", "read")]


def test_show_with_a_percent_format_rev_path_is_reported():
    source = "git(\"show\", \"%s:%s\" % (ref, path))\n"
    assert [r.line for r in converting_reads(source, "demo.py")] == [1]


def test_cat_file_with_filters_or_textconv_is_reported():
    source = ("_run(repo, \"cat-file\", \"--filters\", sha)\n"
              "subprocess.run([\"git\", \"cat-file\", \"--textconv\", \"-p\", sha])\n")
    assert [r.line for r in converting_reads(source, "demo.py")] == [1, 2]


def test_cat_file_batch_ls_tree_and_show_format_are_not_reported():
    source = ("subprocess.run([\"git\", \"cat-file\", \"--batch\"], input=b\"\")\n"
              "subprocess.run([\"git\", \"ls-tree\", \"-r\", \"-z\", x])\n"
              "git(\"show\", \"--format=%B\", sha)\n"
              "_process(work_tree, *_SHOW, \"--end-of-options\", *shas)\n"
              "_SHOW = (\"show\", \"--format=%x00COMMIT%x00%H%x00%B%x00ENDMSG\")\n")
    assert converting_reads(source, "demo.py") == []


# --- the repo ----------------------------------------------------------------------------------------

def test_reads_in_src_are_exactly_the_listed_ones():
    found = walk_src()
    assert found, "no git read found in src/orch_it: the walk is broken"
    assert listing_problems(found, LISTED_CONVERTING_READS) == []


# Row 4: a planted `git archive` in a scratch module is red, naming its file:line; src/orch_it is green.
SCRATCH_ARCHIVE = ("import subprocess\n"
                   "\n"
                   "\n"
                   "def make(repo, commit):\n"
                   "    return subprocess.run([\"git\", \"archive\", \"--format=tar\", commit], cwd=repo)\n")


def test_planted_git_archive_in_a_scratch_module_is_red_and_src_is_green(tmp_path):
    (tmp_path / "scratch_archive.py").write_text(SCRATCH_ARCHIVE, "utf-8")
    problems = listing_problems(walk_src(tmp_path), LISTED_CONVERTING_READS)
    assert "scratch_archive.py:5 in make() (archive)" in problems
    assert listing_problems(walk_src(SRC_FOLDER), LISTED_CONVERTING_READS) == []
