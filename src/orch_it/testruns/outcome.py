"""The outcome rules of a test run (#394 decision 2).

`judge` reads a run's JUnit report and says how the run ended, the first rule that holds
deciding:

1. the run hit its bound and was killed: `no_result` `cut` (`suspended` when the machine slept
   across the bound), whatever its report says;
2. no report, or one half-written or no JUnit XML: `no_result` `summary_missing`;
3. 0 test cases: `no_result` `none_collected`;
4. a failed or errored case: `failed` `red`. A red case is red even when a requested id is also
   missing: the ticket's table lets both rows hold, and "red at any required place is red"
   (TEST-7) decides;
5. a requested id with no case: `no_result` `ids_not_collected`;
6. else `passed`.

No `no_result` is ever written as `failed`: a run says red only from a complete report holding
a red case. `RunEnd` also gives the `test_run` end detail and one `test.result` detail per test
case, so every writer of a test run (a place, the GitHub importer) writes the same rows from
the same report.

A requested id matches a case by its test id (`<classname>::<name>`, as `test.result` writes
it), by a pytest node id (`tests/test_x.py::TestC::test_y[a::b]` is
`tests.test_x.TestC::test_y[a::b]`), or, with no `::`, as a file, module, package or suite:
every case whose classname is it or under it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orch_it.testruns import junit

RED_OUTCOMES = ("failed", "error")


@dataclass(frozen=True)
class RunEnd:
    """How one test run ended. `failed` counts the failed and the errored cases together, as
    the `test_run` end detail's `failed` does; each case's own outcome stays in its row."""

    outcome: str
    reason: str | None
    cases: tuple[junit.Case, ...]
    summary_seen: bool
    missing_ids: tuple[str, ...] = ()

    @property
    def collected(self) -> int:
        return len(self.cases)

    @property
    def failed(self) -> int:
        return sum(case.outcome in RED_OUTCOMES for case in self.cases)

    @property
    def skipped(self) -> int:
        return sum(case.outcome == "skipped" for case in self.cases)

    @property
    def passed(self) -> int:
        return sum(case.outcome == "passed" for case in self.cases)

    def ended_detail(self) -> dict:
        """This end's part of the `test_run` `step.ended` detail."""

        return {"outcome": self.outcome, "reason": self.reason, "collected": self.collected,
                "passed": self.passed, "failed": self.failed, "skipped": self.skipped,
                "summary_seen": self.summary_seen}

    def result_details(self) -> list[dict]:
        """One `test.result` detail per case, in report order; a test id seen again in the
        same report is its next `attempt`."""

        attempts: dict[str, int] = {}
        details = []
        for case in self.cases:
            attempts[case.test_id] = attempts.get(case.test_id, 0) + 1
            details.append(case.result_detail(attempts[case.test_id]))
        return details


def judge(report_path: Path | None, *, requested_ids: list[str] | None = None,
          cut: bool = False, slept: bool = False) -> RunEnd:
    """How the run ended, from its report at `report_path` (None: the run wrote none).
    `requested_ids`: the ids the run was asked to run (None: the full suite). `cut`: the run
    hit its bound and its process tree was killed; `slept`: the machine slept during the run."""

    try:
        cases = tuple(junit.read(report_path)) if report_path is not None else None
    except junit.ReportUnreadable:
        cases = None
    seen = cases is not None
    cases = cases or ()
    if cut:
        return RunEnd("no_result", "suspended" if slept else "cut", cases, seen)
    if not seen:
        return RunEnd("no_result", "summary_missing", (), False)
    if not cases:
        return RunEnd("no_result", "none_collected", cases, True)
    if any(case.outcome in RED_OUTCOMES for case in cases):
        return RunEnd("failed", "red", cases, True)
    missing = _not_collected(requested_ids or [], cases)
    if missing:
        return RunEnd("no_result", "ids_not_collected", cases, True, missing)
    return RunEnd("passed", None, cases, True)


def _not_collected(requested: list[str], cases: tuple[junit.Case, ...]) -> tuple[str, ...]:
    ids = {case.test_id for case in cases}
    classnames = {case.classname for case in cases}
    missing = []
    for wanted in requested:
        if "::" in wanted:
            if wanted not in ids and _from_node_id(wanted) not in ids:
                missing.append(wanted)
            continue
        prefix = _module_of(wanted)
        if wanted in ids or any(c == prefix or c.startswith(prefix + ".") for c in classnames):
            continue
        missing.append(wanted)
    return tuple(dict.fromkeys(missing))


def _module_of(path: str) -> str:
    """`tests/sub/test_x.py` -> `tests.sub.test_x` (pytest's classname of a file); any other
    text unchanged."""

    if not path.endswith(".py"):
        return path
    return path[:-3].replace("\\", "/").strip("/").replace("/", ".")


def _split_node_id(node_id: str) -> list[str]:
    """`a.py::C::t[x::y]` -> `["a.py", "C", "t[x::y]"]`: a `::` inside a parameter's brackets
    does not split."""

    parts, start, depth, i = [], 0, 0, 0
    while i < len(node_id):
        char = node_id[i]
        if char == "[":
            depth += 1
        elif char == "]" and depth:
            depth -= 1
        elif depth == 0 and node_id.startswith("::", i):
            parts.append(node_id[start:i])
            start = i + 2
            i += 1
        i += 1
    return parts + [node_id[start:]]


def _from_node_id(node_id: str) -> str:
    """A pytest node id as its JUnit test id: `tests/test_x.py::TestC::test_y` ->
    `tests.test_x.TestC::test_y`."""

    file, *classes, name = _split_node_id(node_id)
    return f"{'.'.join([_module_of(file), *classes])}::{name}"
