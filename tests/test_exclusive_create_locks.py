"""Every exclusive create in src/orch_it handles the refusal it can get (the layer's repo#418).

A lock is made by an exclusive create: a folder made with mkdir, a file made with O_EXCL or mode "x". On
Windows a create racing a removal of the same name is refused with PermissionError, not FileExistsError, so a
create that only catches FileExistsError can crash a writer that should wait. The walk below finds every such
create in the package by its AST and passes it when an enclosing try (its body holds the create) catches one
of the refusals. A create that is not a lock is listed by hand, with its reason and the number of creates its
function holds.

A try in a caller does not count: the handler must be in the same function as the create, because the create
runs when its function runs, not when a caller's try runs.

The walk resolves the names os is reached by through the file's own imports: `import os as X`, and
`from os import mkdir as m`. An os.open whose flags is a Name is followed one assignment in the enclosing
function; a flags Name that the function does not assign counts as a create."""

import pytest
import ast
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import orch_it

SRC_FOLDER = Path(orch_it.__file__).parent
# PermissionError or a base of it: the names a handler catches a refusal under. IOError and EnvironmentError
# are OSError under other names; the walk compares names as written, so they stay in the set.
CAUGHT = {"PermissionError", "OSError", "IOError", "EnvironmentError", "Exception", "BaseException"}
# (module, function) -> (reason, number of creates the function holds): a listed function holding a second
# create fails the test, so the listing covers exactly the creates it names.
LISTED_NON_LOCKS = {
    ("research/mcp.py", "start"): ("makes a folder, not a lock", 1),
}


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


class Create(NamedTuple):
    module: str  # relative to src/orch_it, POSIX
    line: int
    function: str  # the nearest enclosing def, or "<module>"
    handled: bool  # an enclosing try whose body holds the create catches a refusal


def exclusive_creates(source: str, module: str) -> list[Create]:
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    imports = _imports(tree)
    found = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        scope = _enclosing_scope(call, parents)
        if _is_exclusive_create(call, imports, scope):
            found.append(Create(module, call.lineno, _scope_name(scope), _handled(call, parents)))
    return sorted(found, key=lambda create: create.line)


class _Imports(NamedTuple):
    modules: dict[str, str]  # local name -> module, from `import m` and `import m as x`
    names: dict[str, tuple[str, str]]  # local name -> (module, name), from `from m import n as x`


def _imports(tree: ast.AST) -> _Imports:
    modules: dict[str, str] = {}
    names: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    modules[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = (node.module, alias.name)
    return _Imports(modules, names)


def _callee(func: ast.expr, imports: _Imports) -> tuple[str | None, str | None]:
    """(module, name) of what a call names. A bare name is its import when the file imports it from a module,
    otherwise a builtin (module None). An attribute's owner name is read through the file's module aliases."""
    if isinstance(func, ast.Name):
        if func.id in imports.names:
            return imports.names[func.id]
        return None, func.id
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return imports.modules.get(func.value.id, func.value.id), func.attr
        return None, func.attr
    return None, None


def _is_exclusive_create(call: ast.Call, imports: _Imports, scope: ast.AST) -> bool:
    module, name = _callee(call.func, imports)
    if name in ("mkdir", "makedirs"):  # <expr>.mkdir, or os.mkdir / os.makedirs under any name it is imported as
        if isinstance(call.func, ast.Attribute) or module == "os":
            # Path.mkdir(mode, parents, exist_ok) and os.makedirs(name, mode, exist_ok): exist_ok is the third
            exist_ok = _argument(call, 2, "exist_ok")
            return not (isinstance(exist_ok, ast.Constant) and exist_ok.value is True)
        return False
    if name == "touch" and isinstance(call.func, ast.Attribute):  # <expr>.touch(mode, exist_ok): exist_ok defaults to True
        exist_ok = _argument(call, 1, "exist_ok")
        if exist_ok is None:  # absent: the default True, so no create
            return False
        return not (isinstance(exist_ok, ast.Constant) and exist_ok.value is True)  # a non-constant counts, as for mkdir
    if name == "open" and module == "os":  # os.open(path, flags, mode), under any alias
        return _os_open_is_create(call, scope)
    if name == "open":
        # builtin open and io.open: the mode is the second argument; Path.open (any other owner): the first
        mode_position = 1 if isinstance(call.func, ast.Name) or module == "io" else 0
        mode = _argument(call, mode_position, "mode")
        return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "x" in mode.value
    return False


def _os_open_is_create(call: ast.Call, scope: ast.AST) -> bool:
    flags = _argument(call, 1, "flags")
    if flags is None:
        return False
    if isinstance(flags, ast.Name):
        values = _assigned_values(flags.id, scope)
        if not values:  # not assigned in the enclosing scope: it cannot be told apart, so it counts
            return True
        return any(_has_o_excl(value) for value in values)
    return _has_o_excl(flags)


def _has_o_excl(expr: ast.AST) -> bool:
    return any(_is_o_excl(node) for node in ast.walk(expr))


def _assigned_values(name: str, scope: ast.AST) -> list[ast.expr]:
    """The values assigned to name in the scope, one hop only: the expression assigned, not what it is made of."""
    values = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            values.append(node.value)
        elif (isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name)
              and node.target.id == name and node.value is not None):
            values.append(node.value)
    return values


def _is_o_excl(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "O_EXCL"
    return isinstance(node, ast.Name) and node.id == "O_EXCL"


def _argument(call: ast.Call, position: int | None, keyword: str) -> ast.expr | None:
    if position is not None and len(call.args) > position:
        return call.args[position]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _handled(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when an enclosing try, walking up to the function boundary, holds the create in its body and
    has a handler that catches a refusal. A create in a handler or a finally of a try does not count for it."""
    node: ast.AST = call
    while node in parents:
        parent = parents[node]
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return False
        if isinstance(parent, (ast.Try, ast.TryStar)) and any(stmt is node for stmt in parent.body):
            if any(_catches(handler) for handler in parent.handlers):
                return True
        node = parent
    return False


def _catches(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:  # bare except:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(_name(t) in CAUGHT for t in types)


def _name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _enclosing_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    """The nearest def around node, or the module when no def is around it."""
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return node


def _scope_name(scope: ast.AST) -> str:
    return scope.name if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"


def exclusive_creates_without_handler(source: str, module: str) -> list[Create]:
    return [create for create in exclusive_creates(source, module) if not create.handled]


def walk_src() -> list[Create]:
    found = []
    for path in sorted(SRC_FOLDER.rglob("*.py")):
        found += exclusive_creates(path.read_text("utf-8"), path.relative_to(SRC_FOLDER).as_posix())
    return found


def listing_problems(found: list[Create], listed: dict[tuple[str, str], tuple[str, int]]) -> list[str]:
    """What is wrong with a walk against a listing: an unhandled create that no listing names, and a listed
    function whose number of creates is not the number its listing names."""
    counts = Counter((c.module, c.function) for c in found)
    problems = [f"orch_it/{c.module}:{c.line} in {c.function}()"
                for c in found if not c.handled and (c.module, c.function) not in listed]
    problems += [f"orch_it/{module} {function}() holds {counts[(module, function)]} creates, listed {count}"
                 for (module, function), (_, count) in listed.items() if counts[(module, function)] != count]
    return problems


# --- the pure function, on small snippets ---------------------------------------------------------

def test_mkdir_caught_only_by_file_exists_is_reported():
    source = "try:\n    os.mkdir(p)\nexcept FileExistsError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


def test_mkdir_caught_by_a_tuple_with_permission_error_passes():
    source = "try:\n    os.mkdir(p)\nexcept (FileExistsError, PermissionError):\n    pass\n"
    assert exclusive_creates_without_handler(source, "demo.py") == []


def test_mkdir_caught_by_oserror_passes():
    source = "try:\n    os.mkdir(p)\nexcept OSError:\n    pass\n"
    assert exclusive_creates_without_handler(source, "demo.py") == []


def test_os_open_with_o_excl_caught_only_by_file_exists_is_reported():
    source = ("try:\n    h = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)\n"
              "except FileExistsError:\n    pass\n")
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


def test_builtin_open_mode_x_caught_only_by_file_exists_is_reported():
    source = "try:\n    with open(p, \"x\"):\n        pass\nexcept FileExistsError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


def test_open_mode_w_is_not_a_create():
    source = "with open(p, \"w\"):\n    pass\n"
    assert exclusive_creates(source, "demo.py") == []


def test_path_mkdir_with_exist_ok_is_not_a_create():
    source = "p.mkdir(exist_ok=True)\n"
    assert exclusive_creates(source, "demo.py") == []


def test_path_mkdir_outside_any_try_is_reported():
    source = "def start(p):\n    p.mkdir(parents=True)\n"
    assert [(c.line, c.function) for c in exclusive_creates_without_handler(source, "demo.py")] == [(2, "start")]


def test_only_the_try_body_counts_a_create_in_its_except_is_reported():
    source = "try:\n    pass\nexcept OSError:\n    os.mkdir(p)\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [4]


def test_only_the_try_body_counts_a_create_in_its_finally_is_reported():
    source = "try:\n    pass\nfinally:\n    os.mkdir(p)\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [4]


def test_a_create_in_the_body_of_a_try_that_catches_is_not_reported():
    source = "try:\n    os.mkdir(p)\nexcept OSError:\n    pass\n"
    assert exclusive_creates_without_handler(source, "demo.py") == []


def test_a_create_in_a_function_inside_a_try_does_not_take_the_try_handler():
    source = "try:\n    def start(p):\n        os.mkdir(p)\nexcept OSError:\n    pass\n"
    assert [c.function for c in exclusive_creates_without_handler(source, "demo.py")] == ["start"]


# --- gap 1: names imported from os, bare or under an alias -------------------------------------------

def test_bare_mkdir_imported_from_os_is_reported():
    source = "from os import mkdir\ntry:\n    mkdir(p)\nexcept FileExistsError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [3]


def test_bare_makedirs_imported_from_os_under_an_alias_is_reported():
    source = "from os import makedirs as md\ntry:\n    md(p)\nexcept FileExistsError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [3]


def test_bare_open_imported_from_os_as_alias_with_o_excl_is_reported():
    source = ("from os import open as os_open\ntry:\n    h = os_open(p, os.O_CREAT | os.O_EXCL)\n"
              "except FileExistsError:\n    pass\n")
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [3]


def test_bare_mkdir_not_imported_from_os_is_not_a_create():
    source = "from pathlib import Path\nmkdir(p)\n"
    assert exclusive_creates(source, "demo.py") == []


# --- gap 2: an import alias of os ----------------------------------------------------------------------

def test_os_open_under_an_import_alias_with_o_excl_is_reported():
    source = ("import os as _os\ntry:\n    h = _os.open(p, _os.O_CREAT | _os.O_EXCL)\n"
              "except FileExistsError:\n    pass\n")
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [3]


# --- gap 3: os.open with flags held in a Name ----------------------------------------------------------

def test_os_open_flags_name_assigned_with_o_excl_is_reported():
    source = ("def make(p):\n    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY\n"
              "    try:\n        h = os.open(p, flags)\n    except FileExistsError:\n        pass\n")
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [4]


def test_os_open_flags_name_not_assigned_in_the_function_is_reported():
    source = "def make(p, flags):\n    os.open(p, flags)\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


def test_os_open_flags_name_assigned_without_o_excl_is_not_a_create():
    source = "def read(p):\n    flags = os.O_RDONLY\n    os.open(p, flags)\n"
    assert exclusive_creates(source, "demo.py") == []


# --- gap 4: the mode argument of open ------------------------------------------------------------------

def test_path_open_mode_x_as_first_positional_is_reported():
    source = "try:\n    with p.open(\"x\"):\n        pass\nexcept FileExistsError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


def test_path_open_mode_w_is_not_a_create():
    source = "with p.open(\"w\"):\n    pass\n"
    assert exclusive_creates(source, "demo.py") == []


def test_builtin_open_with_mode_keyword_x_is_reported():
    source = "try:\n    with open(p, mode=\"x\"):\n        pass\nexcept FileExistsError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


def test_io_open_mode_x_as_second_positional_is_reported():
    source = "try:\n    with io.open(p, \"x\"):\n        pass\nexcept FileExistsError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


# --- gap 5: exist_ok given positionally ----------------------------------------------------------------

def test_makedirs_exist_ok_given_positionally_as_true_is_not_a_create():
    source = "os.makedirs(p, 0o777, True)\n"
    assert exclusive_creates(source, "demo.py") == []


def test_path_mkdir_exist_ok_given_positionally_as_true_is_not_a_create():
    source = "p.mkdir(0o777, True, True)\n"
    assert exclusive_creates(source, "demo.py") == []


def test_makedirs_exist_ok_given_positionally_as_false_is_a_create():
    source = "os.makedirs(p, 0o777, False)\n"
    assert [c.line for c in exclusive_creates(source, "demo.py")] == [1]


# --- gap 8: Path.touch creates a file with O_EXCL when exist_ok is False -------------------------------

def test_path_touch_exist_ok_false_caught_only_by_file_exists_is_reported():
    source = "try:\n    p.touch(exist_ok=False)\nexcept FileExistsError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


def test_path_touch_mode_and_exist_ok_false_positionally_is_reported():
    source = "p.touch(0o600, False)\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [1]


def test_path_touch_exist_ok_true_or_absent_is_not_a_create():
    source = "p.touch(exist_ok=True)\np.touch()\np.touch(0o600, True)\n"
    assert exclusive_creates(source, "demo.py") == []


def test_path_touch_exist_ok_false_caught_by_a_tuple_with_permission_error_passes():
    source = "try:\n    p.touch(exist_ok=False)\nexcept (FileExistsError, PermissionError):\n    pass\n"
    assert exclusive_creates_without_handler(source, "demo.py") == []


# --- gap 6: a listing covers exactly the creates it names ----------------------------------------------

def test_a_listed_function_holding_a_second_create_is_a_problem():
    source = "def start(p):\n    p.mkdir(parents=True)\n    p.mkdir(parents=True)\n"
    listed = {("demo.py", "start"): ("makes a folder, not a lock", 1)}
    assert len(listing_problems(exclusive_creates(source, "demo.py"), listed)) == 1


def test_a_listed_function_holding_exactly_its_listed_creates_passes():
    source = "def start(p):\n    p.mkdir(parents=True)\n    p.mkdir(parents=True)\n"
    listed = {("demo.py", "start"): ("makes folders, not locks", 2)}
    assert listing_problems(exclusive_creates(source, "demo.py"), listed) == []


# --- gap 7: guards for CAUGHT and the try boundary (green before and after) ----------------------------

def test_except_ioerror_catches_an_os_error_and_passes():
    source = "try:\n    os.mkdir(p)\nexcept IOError:\n    pass\n"
    assert exclusive_creates_without_handler(source, "demo.py") == []


def test_a_try_in_a_caller_does_not_handle_a_create_in_the_callee():
    source = "def make(p):\n    os.mkdir(p)\ntry:\n    make(p)\nexcept OSError:\n    pass\n"
    assert [c.line for c in exclusive_creates_without_handler(source, "demo.py")] == [2]


# --- the repo ------------------------------------------------------------------------------------

def test_every_exclusive_create_in_src_handles_a_refusal_or_is_listed():
    found = walk_src()
    assert found, "no exclusive create found in src/orch_it: the walk is broken"
    failing = [c for c in found if not c.handled and (c.module, c.function) not in LISTED_NON_LOCKS]
    assert [f"orch_it/{c.module}:{c.line} in {c.function}()" for c in failing] == []


def test_every_listed_non_lock_still_exists_as_a_create():
    seen = {(c.module, c.function) for c in walk_src()}
    assert [key for key in LISTED_NON_LOCKS if key not in seen] == []


def test_every_listed_non_lock_names_exactly_the_creates_it_holds():
    assert listing_problems(walk_src(), LISTED_NON_LOCKS) == []
