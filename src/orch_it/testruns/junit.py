"""The JUnit XML reader (#394 decision 1): a report as written by pytest, jest-junit,
go-junit-report, gotestsum, Maven Surefire and most other runners, read into its test cases.

`read` returns every `<testcase>` under the root `<testsuites>` or `<testsuite>`, at any depth
(a test case inside another one is not a case of its own), in file order. The file is parsed in
chunks, so a huge report never sits in memory whole. A report that is missing, unreadable,
half-written, declares a DTD or an entity, or has another root raises `ReportUnreadable`; the
caller never gets a partial list.

A case's outcome is `failed` with a `<failure>` child, `error` with an `<error>` child (either
one wins over `<skipped>`), `skipped` with a `<skipped>` child, else `passed`.
"""

from __future__ import annotations

import math
import xml.parsers.expat
from dataclasses import dataclass
from pathlib import Path

ROOTS = ("testsuites", "testsuite")
CHUNK_BYTES = 1 << 20
SKIP_REASON_CHARS = 300
REPORT_ERROR_CODES = ("missing", "broken")
_RANK = {"passed": 0, "skipped": 1, "error": 2, "failed": 3}
_CHILD_OUTCOME = {"failure": "failed", "error": "error", "skipped": "skipped"}


class ReportUnreadable(Exception):
    """`code`: `missing` (no file, or the OS would not read it), `broken` (half-written, not
    XML, a DTD or entity in it, or not a JUnit report)."""

    def __init__(self, code: str, detail: str = ""):
        if code not in REPORT_ERROR_CODES:
            raise ValueError(f"not a report error code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Case:
    classname: str
    name: str
    outcome: str  # passed, failed, error or skipped
    seconds: float | None
    skip_reason: str | None  # the `message` (else `type`) of a skipped case's <skipped>

    @property
    def test_id(self) -> str:
        """`<classname>::<name>`, or the name alone when the classname is empty or the same
        (jest-junit writes the full title in both)."""

        if not self.classname or self.classname == self.name:
            return self.name
        return f"{self.classname}::{self.name}"

    def result_detail(self, attempt: int) -> dict:
        """The detail of this case's `test.result` event."""

        return {"test_id": self.test_id, "outcome": self.outcome, "skip_reason": self.skip_reason,
                "ms": None if self.seconds is None else round(self.seconds * 1000),
                "attempt": attempt}


class _Reader:
    def __init__(self):
        self.cases: list[Case] = []
        self.depth = 0
        self.root_seen = False
        self.case_depth = None  # the depth of the open <testcase>, None outside one
        self.open_case: dict | None = None

    def start(self, tag: str, attrs: dict) -> None:
        self.depth += 1
        if not self.root_seen:
            self.root_seen = True
            if tag not in ROOTS:
                raise ReportUnreadable("broken", f"root <{tag}>")
        if self.case_depth is None:
            if tag == "testcase":
                self.case_depth = self.depth
                self.open_case = {"classname": attrs.get("classname") or "",
                                  "name": attrs.get("name") or "", "outcome": "passed",
                                  "seconds": _seconds(attrs.get("time")), "skip_reason": None}
        elif self.depth == self.case_depth + 1 and tag in _CHILD_OUTCOME:
            outcome = _CHILD_OUTCOME[tag]
            case = self.open_case
            if _RANK[outcome] > _RANK[case["outcome"]]:
                case["outcome"] = outcome
                text = attrs.get("message") or attrs.get("type") if outcome == "skipped" else None
                case["skip_reason"] = text[:SKIP_REASON_CHARS] if text else None

    def end(self, tag: str) -> None:
        if self.case_depth == self.depth:
            self.cases.append(Case(**self.open_case))
            self.case_depth = None
            self.open_case = None
        self.depth -= 1


def _seconds(text: str | None) -> float | None:
    """A `time` attribute in seconds; `1,234.5` (a thousands comma) reads as 1234.5; anything
    that is no finite, non-negative number is unknown."""

    try:
        value = float(text.replace(",", "")) if text else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def _refuse(*_args) -> None:
    raise ReportUnreadable("broken", "a DTD or entity declaration")


def read(path: Path) -> list[Case]:
    reader = _Reader()
    parser = xml.parsers.expat.ParserCreate()
    parser.StartElementHandler = reader.start
    parser.EndElementHandler = reader.end
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    try:
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_BYTES):
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except FileNotFoundError:
        raise ReportUnreadable("missing", str(path)) from None
    except OSError as e:  # a folder, a file the OS would not open
        raise ReportUnreadable("missing", f"{path}: {e}") from None
    except xml.parsers.expat.ExpatError as e:
        raise ReportUnreadable("broken", f"{path}: {e}") from None
    return reader.cases
