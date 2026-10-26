"""The red-on-main ticket (#400 decision 4): one ticket per set of tests that fail on main, written
in #398's ticket shape (it passes the shape rules: `build` checks it), ready for an agent, top
priority, its failing test ids in a fenced block under the layer's mark, the pull request that
brought them named. The follow reads it to tell a pull request red only on these tests (it parks
on this ticket, not counted) from one red on its own tests.
"""

from __future__ import annotations

from orch_it.tickets import parse, violations
from orch_it.tickets.model import PRIORITY_LABEL, READY_LABEL

MARK = "<!-- orch-it red-main -->"
IDS_HEADING = "Failing tests:"
LABELS = (READY_LABEL, PRIORITY_LABEL)
WEIGHT = 3


class RedMainShapeError(AssertionError):
    """The ticket `build` wrote breaks a shape rule: a bug of this module, never of the input."""


def build(ids: list[str], *, sha: str, run_id: str, pr: int | None) -> tuple[str, str]:
    """(title, body) of the red-on-main ticket for `ids`, failing on main at `sha` in run `run_id`."""

    ids = sorted(set(ids))
    count = len(ids)
    by = f"пулл-реквеста #{pr}" if pr else "коммита без пулл-реквеста"
    brought = f"#{pr}" if pr else "a commit with no pull request"
    tests = "тест падает" if count == 1 else ("теста падают" if 2 <= count % 10 <= 4 and count % 100 // 10 != 1
                                                 else "тестов падают")
    title = f"Main is red: {count} test{'s' if count != 1 else ''} fail"
    fence = "````" if any("```" in test_id for test_id in ids) else "```"
    body = f"""{MARK}
## For the human
- **What:** main красный: {count} {tests} после слияния {by}.
- **Goal:** main снова зелёный, и пулл-реквесты, красные только на этих тестах, идут дальше сами.
- **Plan:** починить или откатить то, что сломало эти тесты; что выбрать, решает этот тикет.
- **Result:** полный прогон тестов на main зелёный; тикет закрывается сам на первом зелёном прогоне.
- **Undo:** two-way door (один откат); touches каждый пулл-реквест, который ждёт этот тикет.

## For the agent
Facts: the `tests` run {run_id} on main at `{sha}` failed the tests listed below (each one red in the JUnit report
of that run); they came with {brought}.
Rule: make main green again by a fix or a revert; a pull request red only on these tests waits for this ticket.

| row | check | pass |
|---|---|---|
| 1 | each failing test below on main | passes in a full run on main |
| 2 | the full suite on main after the change | green on linux and windows |
| 3 | a test of the list that passes on a rerun with no change | still fixed or marked flaky with its reason |

Outside case: a listed test that passes on a rerun with no change, a flake rather than a break (row 3).
Same object: the follow parks a pull request red only on these tests on this ticket and returns it when this ticket
closes; the main check closes this ticket on the first green run of main.
Not in this ticket: tests that turn red on main later and are not listed here: they get their own ticket.
Weight: {WEIGHT}

{IDS_HEADING}

{fence}text
{chr(10).join(ids)}
{fence}
"""
    broken = violations(parse(body, title=title, labels=LABELS))
    if broken:
        raise RedMainShapeError(", ".join(v.message for v in broken))
    return title, body


def ids_of(body: str | None) -> frozenset[str] | None:
    """The failing test ids of a red-on-main ticket's body; None when the body is none."""

    text = (body or "").replace("\r\n", "\n")
    if MARK not in text or IDS_HEADING not in text:
        return None
    after = text[text.index(IDS_HEADING) + len(IDS_HEADING):].lstrip("\n")
    fence = after.split("\n", 1)[0]
    mark = fence[:len(fence) - len(fence.lstrip("`"))]
    if len(mark) < 3:
        return None
    lines = after.split("\n")[1:]
    ids = []
    for line in lines:
        if line.strip() == mark:
            return frozenset(ids)
        if line.strip():
            ids.append(line.strip())
    return None
