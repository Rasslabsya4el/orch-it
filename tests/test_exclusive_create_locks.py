"""Every exclusive create in src/orch_it handles the refusal it can get (Rasslabsya4el/orchestration-layer#418).

A lock is made by an exclusive create: a folder made with mkdir, a file made with O_EXCL or mode "x". On
Windows a create racing a removal of the same name is refused with PermissionError, not FileExistsError, so a
create that only catches FileExistsError can crash a writer that should wait. The walk below finds every such
create in the package by its AST and passes it when an enclosing try (its body holds the create) catches one
of the refusals. A create that is not a lock is listed by hand, with its reason."""

import ast
from pathlib import Path
from typing import NamedTuple

import orch_it

SRC_FOLDER = Path(orch_it.__file__).parent
CAUGHT = {"PermissionError", "OSError", "IOError", "EnvironmentError", "Exception", "BaseException"}
LISTED_NON_LOCKS = {
    ("research/mcp.py", "start"): "makes a folder, not a lock",
}


class Create(NamedTuple):
    module: str  # relative to src/orch_it, POSIX
    line: int
    function: str  # the nearest enclosing def, or "<module>"
    handled: bool  # an enclosing try whose body holds the create catches a refusal


def exclusive_creates(source: str, module: str) -> list[Create]:
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found = [Create(module, call.lineno, _enclosing_function(call, parents), _handled(call, parents))
             for call in ast.walk(tree) if isinstance(call, ast.Call) and _is_exclusive_create(call)]
    return sorted(found, key=lambda create: create.line)


def _is_exclusive_create(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        name, owner = func.id, None
    elif isinstance(func, ast.Attribute):
        name, owner = func.attr, func.value
    else:
        return False
    if name in ("mkdir", "makedirs") and owner is not None:  # os.mkdir, os.makedirs, <expr>.mkdir
        exist_ok = _argument(call, None, "exist_ok")
        return not (isinstance(exist_ok, ast.Constant) and exist_ok.value is True)
    if name == "open" and isinstance(owner, ast.Name) and owner.id == "os":  # os.open(path, flags, mode)
        flags = _argument(call, 1, "flags")
        return flags is not None and any(_is_o_excl(node) for node in ast.walk(flags))
    if name == "open":  # builtin open, io.open, <expr>.open: the mode is the second argument
        mode = _argument(call, 1, "mode")
        return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "x" in mode.value
    return False


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


def _enclosing_function(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> str:
    node: ast.AST = call
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"



def exclusive_creates_without_handler(source: str, module: str) -> list[Create]:
    return [create for create in exclusive_creates(source, module) if not create.handled]


def walk_src() -> list[Create]:
    found = []
    for path in sorted(SRC_FOLDER.rglob("*.py")):
        found += exclusive_creates(path.read_text("utf-8"), path.relative_to(SRC_FOLDER).as_posix())
    return found


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


# --- the repo ------------------------------------------------------------------------------------

def test_every_exclusive_create_in_src_handles_a_refusal_or_is_listed():
    found = walk_src()
    assert found, "no exclusive create found in src/orch_it: the walk is broken"
    failing = [c for c in found if not c.handled and (c.module, c.function) not in LISTED_NON_LOCKS]
    assert [f"orch_it/{c.module}:{c.line} in {c.function}()" for c in failing] == []


def test_every_listed_non_lock_still_exists_as_a_create():
    seen = {(c.module, c.function) for c in walk_src()}
    assert [key for key in LISTED_NON_LOCKS if key not in seen] == []
