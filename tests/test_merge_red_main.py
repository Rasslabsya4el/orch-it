"""The red-on-main ticket (#400 decision 4): written in #398's shape with 0 violations whatever its
failing tests, and its failing ids read back exactly as written."""

import pytest

from orch_it import tickets
from orch_it.merge import red_main


@pytest.mark.parametrize("ids", [
    ["tests/test_a.py::test_x"], ["t::a", "t::b"], [f"t::{n}" for n in range(5)], [f"t::{n}" for n in range(21)],
    ["tests/test_a.py::test_x[case with spaces]", "tests/test_b.py::test_y[```]"],
])
@pytest.mark.parametrize("pr", [12, None])
def test_the_ticket_keeps_the_shape_and_its_ids_read_back(ids, pr):
    title, body = red_main.build(ids, sha="abc123", run_id="99", pr=pr)
    assert tickets.violations(tickets.parse(body, title=title, labels=red_main.LABELS)) == []
    assert red_main.ids_of(body) == frozenset(ids)
    assert title == f"Main is red: {len(ids)} {'test fails' if len(ids) == 1 else 'tests fail'}"


def test_thousands_of_failing_tests_fit_one_issue_body_and_the_rest_are_counted():
    ids = [f"tests/test_{n}.py::test_{'x' * 80}" for n in range(3000)]
    title, body = red_main.build(ids, sha="a", run_id="1", pr=None)
    assert len(body) <= red_main.BODY_MAX and "more failing tests" in body
    listed = red_main.ids_of(body)
    assert listed and listed < set(ids)
    assert tickets.violations(tickets.parse(body, title=title, labels=red_main.LABELS)) == []


def test_the_ids_are_read_with_windows_line_ends_too():
    _, body = red_main.build(["t::a", "t::b"], sha="a", run_id="1", pr=None)
    assert red_main.ids_of(body.replace("\n", "\r\n")) == {"t::a", "t::b"}


@pytest.mark.parametrize("body", [None, "", "no mark", f"{red_main.MARK}\n{red_main.IDS_HEADING}\n\nno fence",
                                  f"{red_main.MARK}\n{red_main.IDS_HEADING}\n\n```text\nt::a\n"])
def test_a_body_that_is_no_red_main_ticket_has_no_ids(body):
    assert red_main.ids_of(body) is None
