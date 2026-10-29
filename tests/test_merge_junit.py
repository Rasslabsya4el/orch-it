"""The script that merges one job's pytest phase reports into one (#421 rule 5)."""

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "merge_junit.py"
SPEC = importlib.util.spec_from_file_location("merge_junit", SCRIPT)
merge_junit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge_junit)

PART = '<testsuites><testsuite name="p" tests="1"><testcase name="{name}"/></testsuite></testsuites>'


def test_two_phase_reports_become_one_report_holding_every_test(tmp_path):
    first, second = tmp_path / "junit.xml", tmp_path / "junit-serial.xml"
    first.write_text(PART.format(name="a"), "utf-8")
    second.write_text(PART.format(name="b"), "utf-8")
    assert merge_junit.merge(first, [first, second]) == 2
    names = [case.get("name") for case in ET.parse(first).getroot().iter("testcase")]
    assert names == ["a", "b"]


def test_a_phase_that_wrote_no_report_shows_as_a_failed_test_not_as_nothing(tmp_path):
    second = tmp_path / "junit-serial.xml"
    second.write_text(PART.format(name="b"), "utf-8")
    out = tmp_path / "junit.xml"
    merge_junit.merge(out, [tmp_path / "missing.xml", second])
    cases = list(ET.parse(out).getroot().iter("testcase"))
    failed = [case for case in cases if case.find("failure") is not None]
    assert len(failed) == 1 and "missing.xml" in failed[0].get("name")
