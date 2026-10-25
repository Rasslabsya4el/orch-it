"""The outside case of #394 (row 2): reports of runners other than pytest are read like
pytest's. `tests/fixtures/junit/` holds real, unedited reports written by jest-junit and
go-junit-report (tools and commands in its VERSIONS.md) for the same three results as a
pytest report: green, red (one failure) and empty. Each tool names its tests its own way, so
the per-test rows are compared by the test each one names, mapped here by hand."""

from pathlib import Path

import pytest

from orch_it.testruns.outcome import judge

FIXTURES = Path(__file__).parent / "fixtures" / "junit"

# each tool's test id -> the test it is (the sources under fixtures/junit/sources)
NAMES = {
    "pytest": {"test_math::test_adds": "adds", "test_math::test_subtracts": "subtracts",
               "test_math::test_skips": "skips"},
    "jest": {"math adds": "adds", "math subtracts": "subtracts", "math skips": "skips"},
    "go": {"example.com/math::TestAdds": "adds", "example.com/math::TestSubtracts": "subtracts",
           "example.com/math::TestSkips": "skips"},
}

EXPECTED = {
    "green": ("passed", None, {"adds": "passed", "subtracts": "passed", "skips": "skipped"}),
    "red": ("failed", "red", {"adds": "passed", "subtracts": "failed", "skips": "skipped"}),
    "empty": ("no_result", "none_collected", {}),
}


def rows_by_test(tool: str, scenario: str) -> tuple[str, str | None, dict[str, str]]:
    end = judge(FIXTURES / tool / f"{scenario}.xml")
    rows = {NAMES[tool][row["test_id"]]: row["outcome"] for row in end.result_details()}
    return end.outcome, end.reason, rows


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
@pytest.mark.parametrize("tool", sorted(NAMES))
def test_every_runner_report_gives_the_hand_counted_outcome_and_rows(tool, scenario):
    assert rows_by_test(tool, scenario) == EXPECTED[scenario]


@pytest.mark.parametrize("scenario", sorted(EXPECTED))
@pytest.mark.parametrize("tool", ["jest", "go"])
def test_a_jest_or_go_report_reads_the_same_as_the_pytest_report(tool, scenario):
    assert rows_by_test(tool, scenario) == rows_by_test("pytest", scenario)


def test_the_skip_reasons_of_each_runner_reach_the_rows():
    reasons = {tool: [row["skip_reason"] for row in judge(FIXTURES / tool / "green.xml").result_details()
                      if row["outcome"] == "skipped"] for tool in NAMES}
    assert reasons == {"pytest": ["not now"], "jest": [None], "go": ["Skipped"]}
