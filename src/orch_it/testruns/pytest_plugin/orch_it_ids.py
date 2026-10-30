"""pytest plugin `-p orch_it_ids`: runs only the tests named in the file `ORCH_IT_IDS_FILE`.

Standalone on purpose (stdlib and pytest only, no import of orch_it): a test place puts this
folder on the test process's `PYTHONPATH`, also on a machine where orch-it is not installed.
The id rule is the outcome module's (`orch_it.testruns.outcome`), so a test this plugin keeps is
a test the run's judge finds, and the reverse; a test of each checks the two agree.

Each line of the file is an id (blank lines and `#` lines skipped; `\\` reads as `/`, a leading
`./` is dropped): a pytest node id, or the start of one up to a `::` (a file, a class) or a `[`
(every parametrization of a test); a JUnit test id as `test.result` writes it
(`tests.test_x.TestC::test_y`); or a bare file, folder or dotted module standing for every test
under it. A node id splits on `::` only before its first `[`, as pytest's JUnit report does. A
collected test that no id names is deselected; an id that names no test is no error here: the
run's report then lacks it, and the layer reads that as `ids_not_collected` (#394 decision 2).
With the variable unset, nothing is filtered.

The plugin also tells which tests read a placeholder file (#414 row 15): a file of private data that the place does
not have, stood in for by a stub. `ORCH_IT_PLACEHOLDERS` lists the stubs' paths (relative to the folder pytest runs
in, or absolute) separated by `os.pathsep`; the step of a workflow that writes the stubs sets it. A test whose setup,
call or teardown opens one of them through `open`, `io.open` or `Path.open` (so `read_text` and `read_bytes` too) gets
a `reads_placeholder` property in the JUnit report, and the layer reruns that test where the real file is. A file read
by another route (`os.open`, a library's own C reader) is not seen. With the variable unset, nothing is recorded.
"""

from __future__ import annotations

import builtins
import io
import os

import pytest

ENV_IDS = "ORCH_IT_IDS_FILE"
ENV_PLACEHOLDERS = "ORCH_IT_PLACEHOLDERS"
PLACEHOLDER_PROPERTY = "reads_placeholder"  # the same word as `junit.PLACEHOLDER_PROPERTY`: this file imports nothing of orch_it; a test holds the two equal


def _ids() -> list[str] | None:
    path = os.environ.get(ENV_IDS)
    if not path:
        return None
    with open(path, encoding="utf-8-sig") as f:
        lines = [line.strip() for line in f]
    return [line for line in lines if line and not line.startswith("#")]


def normal(wanted: str) -> str:
    wanted = wanted.strip().replace("\\", "/")
    while wanted.startswith("./"):
        wanted = wanted[2:]
    return wanted


def module_of(path: str) -> str:
    path = path.strip("/")
    return (path[:-3] if path.endswith(".py") else path).replace("/", ".")


def junit_parts(node_id: str) -> tuple[str, str]:
    """A node id as pytest's JUnit report writes it: (classname, name)."""

    base, bracket, params = node_id.partition("[")
    names = base.split("::")
    names[-1] += bracket + params
    return ".".join([module_of(names[0]), *names[1:-1]]), names[-1]


def selects(wanted: str, node_id: str) -> bool:
    """Does the (normal) id `wanted` name the test `node_id`?"""

    if wanted == node_id or node_id.startswith((wanted + "::", wanted + "[")):
        return True
    classname, name = junit_parts(node_id)
    if wanted == f"{classname}::{name}" or wanted.startswith(f"{classname}::") and name.startswith(
            wanted[len(classname) + 2:] + "["):
        return True
    file = node_id.partition("::")[0]
    if "::" not in wanted:
        if file == wanted.rstrip("/") or file.startswith(wanted.rstrip("/") + "/"):
            return True
        dotted = module_of(wanted)
        return classname == dotted or classname.startswith(dotted + ".")
    return False


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    wanted = _ids()
    if wanted is None:
        return
    wanted = [normal(w) for w in wanted]
    keep, drop = [], []
    for item in items:
        (keep if any(selects(w, item.nodeid) for w in wanted) else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep


def _placeholders() -> set[str]:
    """The real paths of the placeholder files the environment lists."""

    listed = os.environ.get(ENV_PLACEHOLDERS, "")
    return {os.path.normcase(os.path.realpath(p.strip())) for p in listed.split(os.pathsep) if p.strip()}


_PLACEHOLDERS_OPENED: list[str] = []  # the placeholder files the running test has opened so far


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    stubs = _placeholders()
    if not stubs:
        yield
        return
    _PLACEHOLDERS_OPENED.clear()
    real_builtin, real_io = builtins.open, io.open

    def watching(real):
        def opened(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                name = os.path.normcase(os.path.realpath(os.fsdecode(file)))
                if name in stubs and name not in _PLACEHOLDERS_OPENED:
                    _PLACEHOLDERS_OPENED.append(name)
            return real(file, *args, **kwargs)

        return opened

    builtins.open, io.open = watching(real_builtin), watching(real_io)
    try:
        yield
    finally:
        builtins.open, io.open = real_builtin, real_io


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_makereport(item, call):
    """The property goes on before the teardown report is made: that report carries the test's properties to the
    JUnit file."""

    if call.when == "teardown" and _PLACEHOLDERS_OPENED:
        item.user_properties.append((PLACEHOLDER_PROPERTY, "1"))
        _PLACEHOLDERS_OPENED.clear()
