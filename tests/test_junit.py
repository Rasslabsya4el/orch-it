"""The JUnit XML reader (#394 decision 1): every test case of a report of any runner, or a coded
refusal, never a partial list."""

import pytest

from orch_it.testruns import junit


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


def test_a_missing_report_is_refused_as_missing(tmp_path):
    with pytest.raises(junit.ReportUnreadable) as refused:
        junit.read(tmp_path / "gone.xml")
    assert refused.value.code == "missing"
