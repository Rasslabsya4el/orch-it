"""The JUnit XML reader (#394 decision 1): every test case of a report of any runner, or a coded
refusal, never a partial list."""

import time
import tomllib
from pathlib import Path

import pytest

from orch_it.testruns import junit

# the nightly per-test bound of the `tests` workflow, read from the repo's settings, not retyped here
NIGHTLY_BOUND = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8"))[
    "tool"]["orch_it"]["tests"]["nightly_timeout_seconds"]


def write(tmp_path, text: str, name: str = "r.xml"):
    path = tmp_path / name
    path.write_text(text, "utf-8")
    return path


def outcomes(path):
    return [(case.test_id, case.outcome) for case in junit.read(path)]


def test_a_bare_testsuite_root_is_a_report(tmp_path):
    path = write(tmp_path, '<testsuite><testcase classname="c" name="t"/></testsuite>')
    assert outcomes(path) == [("c::t", "passed")]


def test_cases_in_nested_suites_are_read_in_file_order(tmp_path):
    path = write(tmp_path, '<testsuites><testsuite><testsuite><testcase name="a"/></testsuite>'
                           '<testcase name="b"/></testsuite><testsuite><testcase name="c"/>'
                           '</testsuite></testsuites>')
    assert [case.name for case in junit.read(path)] == ["a", "b", "c"]


def test_a_testcase_inside_a_testcase_is_no_case_of_its_own(tmp_path):
    path = write(tmp_path, '<testsuite><testcase name="outer"><testcase name="inner">'
                           '<failure/></testcase></testcase></testsuite>')
    assert outcomes(path) == [("outer", "passed")]


def test_a_failure_or_an_error_wins_over_a_skip(tmp_path):
    path = write(tmp_path, '<testsuite><testcase name="a"><skipped/><failure/></testcase>'
                           '<testcase name="b"><error/><skipped/></testcase></testsuite>')
    assert outcomes(path) == [("a", "failed"), ("b", "error")]


def test_only_a_skipped_case_carries_a_skip_reason(tmp_path):
    path = write(tmp_path, '<testsuite><testcase name="a"><skipped type="pytest.skip"/></testcase>'
                           '<testcase name="b"><failure message="boom"/></testcase></testsuite>')
    assert [case.skip_reason for case in junit.read(path)] == ["pytest.skip", None]


@pytest.mark.parametrize("time, seconds", [
    ("0.25", 0.25), ("1,234.5", 1234.5), ("", None), ("nan", None), ("inf", None),
    ("-1", None), ("soon", None)])
def test_a_case_s_time_is_seconds_or_unknown(tmp_path, time, seconds):
    path = write(tmp_path, f'<testsuite><testcase name="a" time="{time}"/></testsuite>')
    assert junit.read(path)[0].seconds == seconds


def test_a_report_bigger_than_one_read_chunk_is_read_whole(tmp_path):
    cases = "".join(f'<testcase classname="c" name="t{i}" time="0.001"/>' for i in range(30_000))
    path = write(tmp_path, f"<testsuite>{cases}</testsuite>")
    assert path.stat().st_size > junit.CHUNK_BYTES
    assert len(junit.read(path)) == 30_000


@pytest.mark.parametrize("text, code", [
    ("", "broken"), ("<testsuite>", "broken"), ("<html/>", "broken"),
    ('<?xml version="1.0"?><!DOCTYPE testsuite SYSTEM "x.dtd"><testsuite/>', "broken")])
def test_a_report_that_cannot_be_read_whole_is_refused_with_its_code(tmp_path, text, code):
    with pytest.raises(junit.ReportUnreadable) as refused:
        junit.read(write(tmp_path, text))
    assert refused.value.code == code


@pytest.mark.parametrize("encoding", ["gbk", "nope"])
def test_a_report_in_an_encoding_the_parser_cannot_read_is_broken(tmp_path, encoding):
    path = tmp_path / "r.xml"
    path.write_bytes(f'<?xml version="1.0" encoding="{encoding}"?><testsuite/>'.encode())
    with pytest.raises(junit.ReportUnreadable) as refused:
        junit.read(path)
    assert refused.value.code == "broken"


@pytest.mark.parametrize("time", ["1e306", "1e20", "1,5", "1_0"])
def test_an_absurd_or_ambiguous_time_is_unknown(tmp_path, time):
    path = write(tmp_path, f'<testsuite><testcase name="a" time="{time}"/></testsuite>')
    [case] = junit.read(path)
    assert case.seconds is None
    assert case.result_detail(1)["ms"] is None


def test_a_huge_name_is_cut_to_a_bounded_test_id(tmp_path):
    path = write(tmp_path, f'<testsuite><testcase classname="{"c" * 50_000}" name="{"n" * 50_000}"/></testsuite>')
    [case] = junit.read(path)
    assert len(case.test_id) <= 2 * junit.NAME_CHARS + 2


def test_a_suite_level_error_is_a_red_case(tmp_path):
    path = write(tmp_path, '<testsuites><testsuite name="calc.test.js"><error message="Cannot find module"/>'
                           '</testsuite><testsuite name="ok"><testcase name="a"/></testsuite></testsuites>')
    assert outcomes(path) == [("calc.test.js::(suite)", "error"), ("a", "passed")]


def test_a_suite_declaring_more_failures_than_its_cases_show_gets_a_red_case(tmp_path):
    path = write(tmp_path, '<testsuite name="s" failures="2" errors="1"><testcase name="a"><failure/></testcase>'
                           '<testcase name="b"/></testsuite>')
    assert outcomes(path) == [("a", "failed"), ("b", "passed"), ("s::(suite)", "error")]


def test_a_missing_report_is_refused_as_missing(tmp_path):
    with pytest.raises(junit.ReportUnreadable) as refused:
        junit.read(tmp_path / "gone.xml")
    assert refused.value.code == "missing"


@pytest.mark.slow  # nightly only: it writes and reads a report of some 40 MB
def test_a_report_of_half_a_million_cases_is_read_whole_before_its_bound(tmp_path):
    path = tmp_path / "huge.xml"
    with open(path, "w", encoding="utf-8") as f:
        f.write("<testsuites><testsuite>")
        for i in range(500_000):
            f.write(f'<testcase classname="pkg.mod{i % 997}" name="test_{i}" time="0.001"/>')
        f.write('<testcase name="last"><failure/></testcase></testsuite></testsuites>')
    started = time.monotonic()
    cases = junit.read(path)
    assert len(cases) == 500_001 and cases[-1].outcome == "failed"
    # The ceiling of the nightly bound: pytest-timeout is what names this test when it overruns the bound;
    # this assertion only fails a read that returns but took longer than the bound allows.
    assert time.monotonic() - started < NIGHTLY_BOUND
