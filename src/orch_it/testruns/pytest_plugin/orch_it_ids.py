"""pytest plugin `-p orch_it_ids`: runs only the tests named in the file `ORCH_IT_IDS_FILE`.

Standalone on purpose (stdlib and pytest only, no import of orch_it): a test place puts this
folder on the test process's `PYTHONPATH`, also on a machine where orch-it is not installed.

Each line of the file is an id (blank lines and `#` lines skipped): a pytest node id, a JUnit
test id as `test.result` writes it (`tests.test_x.TestC::test_y`), or a bare file, module or
folder (`tests/test_x.py`, `tests/sub`) standing for every test under it. A collected test that
no id names is deselected; an id that names no test is no error here: the run's report then
lacks it, and the layer reads that as `ids_not_collected` (#394 decision 2). With the variable
unset, nothing is filtered.
"""

from __future__ import annotations

import os

import pytest

ENV_IDS = "ORCH_IT_IDS_FILE"


def _ids() -> list[str] | None:
    path = os.environ.get(ENV_IDS)
    if not path:
        return None
    with open(path, encoding="utf-8-sig") as f:
        lines = [line.strip() for line in f]
    return [line for line in lines if line and not line.startswith("#")]


def _junit_id(node_id: str) -> str:
    file, _, rest = node_id.partition("::")
    parts = _split(rest)
    module = file[:-3] if file.endswith(".py") else file
    classname = ".".join([module.replace("\\", "/").strip("/").replace("/", "."), *parts[:-1]])
    return f"{classname}::{parts[-1]}"


def _split(rest: str) -> list[str]:
    parts, start, depth, i = [], 0, 0, 0
    while i < len(rest):
        if rest[i] == "[":
            depth += 1
        elif rest[i] == "]" and depth:
            depth -= 1
        elif depth == 0 and rest.startswith("::", i):
            parts.append(rest[start:i])
            start = i + 2
            i += 1
        i += 1
    return parts + [rest[start:]]


def _selects(wanted: str, node_id: str, junit_id: str) -> bool:
    if wanted in (node_id, junit_id):
        return True
    if "::" in wanted:
        return False
    file = node_id.partition("::")[0]
    folder = wanted.replace("\\", "/").rstrip("/")
    if file == folder or file.startswith(folder + "/"):
        return True
    dotted = folder[:-3] if folder.endswith(".py") else folder
    dotted = dotted.replace("/", ".")
    classname = junit_id.rpartition("::")[0]
    return classname == dotted or classname.startswith(dotted + ".")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    wanted = _ids()
    if wanted is None:
        return
    keep, drop = [], []
    for item in items:
        node_id = item.nodeid
        junit_id = _junit_id(node_id)
        (keep if any(_selects(w, node_id, junit_id) for w in wanted) else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
