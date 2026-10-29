"""The outcome rules of a test run (#394 decision 2), read from its JUnit XML report (decision 1).

Each case writes a small report by hand, the way pytest writes one, and asks `judge` for the
run's outcome and reason; the expected values are the decision's table."""

import time

from orch_it.events import schema
from orch_it.testruns.outcome import judge


def report(tmp_path, *cases: str, name: str = "junit.xml"):
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        f'<testsuite name="pytest" tests="{len(cases)}">{"".join(cases)}</testsuite></testsuites>',
        "utf-8")
    return path


def passing(name, classname="tests.test_math"):
    return f'<testcase classname="{classname}" name="{name}" time="0.010" />'


def failing(name, classname="tests.test_math"):
    return (f'<testcase classname="{classname}" name="{name}" time="0.020">'
            '<failure message="assert 1 == 2">boom</failure></testcase>')


def test_a_green_report_is_passed(tmp_path):
    end = judge(report(tmp_path, passing("test_adds"), passing("test_subtracts")))
    assert (end.outcome, end.reason) == ("passed", None)
    assert (end.collected, end.passed, end.failed) == (2, 2, 0)


def test_one_red_case_is_failed_and_names_that_test(tmp_path):
    end = judge(report(tmp_path, passing("test_adds"), failing("test_subtracts")))
    assert (end.outcome, end.reason) == ("failed", "red")
    rows = {row["test_id"]: row["outcome"] for row in end.result_details()}
    assert rows == {"tests.test_math::test_adds": "passed",
                    "tests.test_math::test_subtracts": "failed"}


def test_an_errored_case_is_red_too(tmp_path):
    errored = ('<testcase classname="tests.test_math" name="test_setup">'
               '<error message="fixture broke">trace</error></testcase>')
    end = judge(report(tmp_path, passing("test_adds"), errored))
    assert (end.outcome, end.reason, end.failed) == ("failed", "red", 1)
    assert [row["outcome"] for row in end.result_details()] == ["passed", "error"]


def test_a_deleted_report_is_no_result_summary_missing(tmp_path):
    end = judge(tmp_path / "never-written.xml")
    assert (end.outcome, end.reason, end.summary_seen) == ("no_result", "summary_missing", False)


def test_a_truncated_report_is_no_result_summary_missing_never_failed(tmp_path):
    whole = report(tmp_path, passing("test_adds"), failing("test_subtracts")).read_bytes()
    for cut_at in (0, 1, len(whole) // 3, len(whole) - 20, len(whole) - 1):
        (tmp_path / "half.xml").write_bytes(whole[:cut_at])
        end = judge(tmp_path / "half.xml")
        assert (end.outcome, end.reason) == ("no_result", "summary_missing"), cut_at
        assert end.result_details() == []


def test_zero_collected_is_no_result_none_collected(tmp_path):
    end = judge(report(tmp_path))
    assert (end.outcome, end.reason, end.summary_seen) == ("no_result", "none_collected", True)


def test_a_requested_id_not_collected_is_no_result_ids_not_collected(tmp_path):
    path = report(tmp_path, passing("test_adds"))
    end = judge(path, requested_ids=["tests.test_math::test_adds", "tests.test_math::test_gone"])
    assert (end.outcome, end.reason) == ("no_result", "ids_not_collected")
    assert end.missing_ids == ("tests.test_math::test_gone",)


def test_requested_ids_match_by_pytest_node_id_and_by_bare_file(tmp_path):
    path = report(tmp_path, passing("test_adds"), passing("test_b[x y]", "tests.sub.test_more.TestC"))
    end = judge(path, requested_ids=[
        "tests/test_math.py::test_adds",            # a pytest node id
        "tests/sub/test_more.py::TestC::test_b[x y]",  # a class and a parameter with a space
        "tests/sub/test_more.py",                   # a bare file: every test of it
        "tests.test_math::test_adds"])              # a test id as test.result writes it
    assert (end.outcome, end.reason, end.missing_ids) == ("passed", None, ())


def test_requested_folders_and_dot_slash_paths_match_their_tests(tmp_path):
    path = report(tmp_path, passing("test_p", "tests.my.pkg.test_p"), passing("test_adds"))
    end = judge(path, requested_ids=["tests/my.pkg", "tests/my.pkg/", "tests\\my.pkg",
                                     "./tests/test_math.py", "./tests/test_math.py::test_adds", "tests"])
    assert (end.outcome, end.missing_ids) == ("passed", ())


def test_blank_requested_ids_are_no_ids(tmp_path):
    end = judge(report(tmp_path, passing("test_adds")), requested_ids=["", "   ", "tests.test_math::test_adds"])
    assert (end.outcome, end.missing_ids) == ("passed", ())


def test_many_unmatched_ids_over_many_suites_are_checked_quickly(tmp_path):
    cases = [passing("t", f"pkg{i}.mod") for i in range(20_000)]
    path = report(tmp_path, *cases)
    started = time.monotonic()
    end = judge(path, requested_ids=[f"other{i}" for i in range(2_000)])
    assert end.reason == "ids_not_collected" and len(end.missing_ids) == 2_000
    assert time.monotonic() - started < 10


def test_a_bare_file_with_no_test_collected_is_not_collected(tmp_path):
    end = judge(report(tmp_path, passing("test_adds")), requested_ids=["tests/test_other.py"])
    assert end.reason == "ids_not_collected"


def test_a_red_case_is_red_even_when_a_requested_id_is_missing(tmp_path):
    end = judge(report(tmp_path, failing("test_adds")), requested_ids=["tests/test_math.py::test_gone"])
    assert (end.outcome, end.reason) == ("failed", "red")


def test_a_run_cut_at_its_bound_is_no_result_cut_whatever_its_report_says(tmp_path):
    path = report(tmp_path, failing("test_adds"))
    assert (judge(path, cut=True).outcome, judge(path, cut=True).reason) == ("no_result", "cut")
    assert judge(tmp_path / "none.xml", cut=True).reason == "cut"
    assert judge(path, cut=True, slept=True).reason == "suspended"


def test_a_cut_run_keeps_the_rows_its_report_holds(tmp_path):
    end = judge(report(tmp_path, passing("test_adds")), cut=True)
    assert [row["test_id"] for row in end.result_details()] == ["tests.test_math::test_adds"]


def test_skips_ms_and_repeated_ids_reach_the_rows(tmp_path):
    skipped = ('<testcase classname="tests.test_math" name="test_skips" time="0.000">'
               '<skipped type="pytest.skip" message="not on windows">x</skipped></testcase>')
    end = judge(report(tmp_path, skipped, failing("test_flaky"), passing("test_flaky")))
    assert end.result_details() == [
        {"test_id": "tests.test_math::test_skips", "outcome": "skipped",
         "skip_reason": "not on windows", "ms": 0, "attempt": 1},
        {"test_id": "tests.test_math::test_flaky", "outcome": "failed", "skip_reason": None,
         "ms": 20, "attempt": 1},
        {"test_id": "tests.test_math::test_flaky", "outcome": "passed", "skip_reason": None,
         "ms": 10, "attempt": 2},
    ]
    assert (end.collected, end.passed, end.failed, end.skipped) == (3, 1, 1, 1)


def test_the_end_detail_carries_the_counts_and_the_outcome(tmp_path):
    end = judge(report(tmp_path, passing("test_adds"), failing("test_subtracts")))
    assert end.ended_detail() == {
        "outcome": "failed", "reason": "red", "collected": 2, "passed": 1, "failed": 1,
        "skipped": 0, "summary_seen": True}


def test_a_report_declaring_entities_is_refused_unread(tmp_path):
    (tmp_path / "bomb.xml").write_text(
        '<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<testsuite><testcase classname="c" name="&b;"/></testsuite>', "utf-8")
    assert judge(tmp_path / "bomb.xml").reason == "summary_missing"


def test_xml_that_is_no_junit_report_is_summary_missing(tmp_path):
    (tmp_path / "page.xml").write_text("<html><body>502 Bad Gateway</body></html>", "utf-8")
    assert judge(tmp_path / "page.xml").reason == "summary_missing"


def test_a_folder_in_the_report_s_place_is_summary_missing(tmp_path):
    (tmp_path / "folder.xml").mkdir()
    assert judge(tmp_path / "folder.xml").reason == "summary_missing"


def test_a_node_id_with_colons_inside_its_parameter_matches_its_case(tmp_path):
    path = report(tmp_path, passing("test_a[x::y]", "tests.test_t"))
    assert judge(path, requested_ids=["tests/test_t.py::test_a[x::y]"]).outcome == "passed"


def test_every_end_judge_gives_is_a_valid_test_run_end(tmp_path):
    """The closed sets are the only definition of an outcome and its reason (#393 decision 4):
    each end `judge` can give passes the store's validation as a `test_run` `step.ended`."""

    green = report(tmp_path, passing("test_adds"), name="green.xml")
    red = report(tmp_path, failing("test_adds"), name="red.xml")
    empty = report(tmp_path, name="empty.xml")
    ends = [judge(green), judge(red), judge(empty), judge(tmp_path / "none.xml"),
            judge(green, requested_ids=["tests/test_gone.py"]), judge(green, cut=True),
            judge(green, cut=True, slept=True)]
    assert {(e.outcome, e.reason) for e in ends} == {
        ("passed", None), ("failed", "red"), ("no_result", "none_collected"),
        ("no_result", "summary_missing"), ("no_result", "ids_not_collected"),
        ("no_result", "cut"), ("no_result", "suspended")}
    for end in ends:
        schema.validate({
            "id": "x", "kind": "step.ended", "at": "2026-10-25T10:00:00Z",
            "repo": "acme-x/widgets-y", "ticket": None, "role": "script", "session": None,
            "parent_session": None, "turn": None, "step": "test_run", "run": "r", "machine": "m",
            "os": "o", "layer_version": "0", "note": None, "detail": end.ended_detail()})
