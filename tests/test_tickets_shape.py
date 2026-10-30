"""The shape rules (#398 decision 2, acceptance row 1): one function returns every violation of
a ticket, each with its code; a good body has none."""

import random
import time

import pytest

from orch_it import tickets
from orch_it.guards.values import read_values
from orch_it.tickets.shape import literal_violations
from ticket_fixtures import EXPECTED, FIXTURES, fixture

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # Russian ticket bodies (#413 unit 10a)


def codes_of(title, body) -> list[str]:
    return [v.code for v in tickets.violations(tickets.parse(body, title=title))]


def test_the_fixture_set_holds_twenty_bodies_and_one_per_rule():
    assert len(EXPECTED) >= 20
    assert {path.stem for path in FIXTURES.glob("*.md")} == set(EXPECTED)
    assert set(tickets.RULES) <= {code for codes in EXPECTED.values() for code in codes}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_violation_is_found_with_its_code_and_none_on_a_good_body(name):
    assert sorted(codes_of(*fixture(name))) == sorted(EXPECTED[name])


def test_every_code_found_is_a_rule_and_each_violation_says_where():
    for name in EXPECTED:
        for violation in tickets.violations(tickets.parse(fixture(name)[1], title=fixture(name)[0])):
            assert violation.code in tickets.RULES
            assert violation.message.startswith(f"{violation.code}: ")


def test_a_body_written_on_windows_reads_as_the_same_body():
    title, body = fixture("bad_human_field_order")
    assert codes_of(title, body.replace("\n", "\r\n")) == codes_of(title, body)
    assert codes_of(*fixture("good_basic")) == codes_of(fixture("good_basic")[0],
                                                        fixture("good_basic")[1].replace("\n", "\r\n"))


def test_a_field_out_of_place_names_the_field():
    found = tickets.violations(tickets.parse(fixture("bad_human_field_order")[1]))
    assert [(v.code, v.detail) for v in found if v.code == "human_field_order"] == [
        ("human_field_order", "Goal")]


def test_a_title_left_out_is_not_judged():
    assert codes_of(None, fixture("good_basic")[1]) == []


def test_a_half_written_body_never_raises_and_a_huge_one_is_read_in_time():
    _, body = fixture("real_395")
    for end in range(0, len(body), 97):
        tickets.violations(tickets.parse(body[:end], title=body[:end][:90]))
    noise = random.Random(398)
    junk = "".join(noise.choice("|#-*:` \n\tабвxyz()[]0123456789") for _ in range(20_000))
    tickets.violations(tickets.parse(junk, title=junk[:200]))
    huge = body * 40  # about a megabyte, past GitHub's own 65,536-character body limit
    started = time.monotonic()
    tickets.violations(tickets.parse(huge))
    assert time.monotonic() - started < 5


def test_an_empty_or_headless_body_is_refused_never_raised():
    for body in (None, "", "\ufeff", "\n\n\n", "## For the human", "## For the agent"):
        found = codes_of("Fine title", body)
        assert "human_block_missing" in found or "agent_block_missing" in found


@pytest.mark.parametrize("weight, codes", [("1", []), ("10", []), ("7 (two parts)", []), ("0", ["weight_bad"]),
                                           ("3.5", ["weight_bad"]), ("", ["weight_bad"]), ("1000", ["weight_bad"])])
def test_the_weight_is_a_whole_number_from_one_to_ten(weight, codes):
    title, body = fixture("good_basic")
    assert codes_of(title, body.replace("Weight: 3", f"Weight: {weight}")) == codes


def test_a_field_written_twice_is_out_of_order():
    title, body = fixture("good_basic")
    twice = body.replace("- **Undo:**", "- **What:** ещё раз то же самое.\n- **Undo:**")
    assert codes_of(title, twice) == ["human_field_order"]


def test_a_pipe_escaped_in_a_cell_stays_in_the_cell():
    title, body = fixture("good_basic")
    escaped = body.replace("| 1 | cart shipping cost for 10115 |", r"| 1 | cart cost for 10115 \| 10117 |")
    ticket = tickets.parse(escaped)
    assert ticket.acceptance[0].check == r"cart cost for 10115 \| 10117"
    assert ticket.acceptance[0].passes == "equals the payment page cost for 10115"


@pytest.mark.parametrize("plan, paths", [
    ("смотри src/orch_it/tickets/model.py:40", ["src/orch_it/tickets/model.py:40"]),
    ("смотри Makefile:12 и папку src/orch_it", ["Makefile:12", "src/orch_it"]),
    ("правило в .claude/skills и в docs/adr", [".claude/skills", "docs/adr"]),
    (r"смотри C:\repo\tools\x.ps1", [r"C:\repo\tools\x.ps1"]),
    ("запуск на Node.js, в 10:30, Linux/Windows, и/или 5.5", []),
])
def test_a_path_or_file_line_in_a_human_field_is_found_and_a_product_or_a_time_is_not(plan, paths):
    title, body = fixture("good_basic")
    written = body.replace("как только введён почтовый индекс.", f"как только введён индекс; {plan}.")
    found = [v.detail for v in tickets.violations(tickets.parse(written, title=title))]
    assert found == ([f"Plan: {', '.join(paths)}"] if paths else [])


@pytest.mark.parametrize("line, codes", [
    ("Outside case: 50667 (row #3).", []),
    ("Outside case: 50667 (rows 2 and 3).", []),
    ("Outside case: 50667 (3).", ["outside_case_no_row"]),
    ("Class: none - one fixed cart page", []),
])
def test_the_outside_case_names_its_row(line, codes):
    title, body = fixture("good_basic")
    written = body.replace("Outside case: 50667, a zone none of 10115 and 20095 is in (row 3).", line)
    assert codes_of(title, written) == codes


def test_an_events_row_is_any_row_whose_check_names_the_events():
    title, body = fixture("good_adds_events")
    assert codes_of(title, body.replace("| 5 | events: the", "| 5 | the events of the")) == []


@pytest.mark.parametrize("field, text", [
    ("What", "-" * 60_000), ("What", "../" * 8_000), ("What", "a.a/" * 8_000)], ids=["dashes", "dots", "folders"])
def test_a_long_run_of_path_marks_is_judged_in_time(field, text):
    title, body = fixture("good_basic")
    written = body.replace(f"- **{field}:**", f"- **{field}:** {text} ")
    started = time.monotonic()
    tickets.violations(tickets.parse(written, title=title))
    assert time.monotonic() - started < 2


def test_a_long_heading_or_row_cell_is_read_in_time():
    title, body = fixture("good_basic")
    written = (body.replace("Not in this ticket:", "## Not in this ticket" + " " * 100_000 + "x\nNot in:")
               .replace("| 1 |", "| 1" + " " * 100_000 + "x |"))
    started = time.monotonic()
    tickets.violations(tickets.parse(written, title=title))
    assert time.monotonic() - started < 2


def test_a_row_without_its_check_or_pass_is_half_written():
    title, body = fixture("good_basic")
    written = body.replace("| 2 | cart shipping cost for 20095 | equals the payment page cost for 20095 |",
                           "| 2 | | |")
    assert [(v.code, v.detail) for v in tickets.violations(tickets.parse(written, title=title))] == [
        ("acceptance_missing", "no check or pass in row 2")]


def test_a_row_named_before_a_bracket_and_a_title_github_trims_are_read_as_written():
    title, body = fixture("good_basic")
    assert codes_of(title, body.replace("(row 3).", "(row 3.)")) == []
    assert codes_of("x" * 80 + "  ", body) == []


def test_an_adds_line_that_adds_nothing_needs_no_events_row():
    title, body = fixture("good_basic")
    assert codes_of(title, body.replace("Weight: 3", "Weight: 3\nAdds: nothing, no step")) == []


@pytest.mark.parametrize("weight", ["7abc", "5-6", "-3"])
def test_a_weight_that_is_not_one_whole_number_is_bad(weight):
    title, body = fixture("good_basic")
    assert codes_of(title, body.replace("Weight: 3", f"Weight: {weight}")) == ["weight_bad"]


# #415 unit 7: the ticket shape check (`literal_violations`) judges a body with the same allowed references
def test_an_allowed_reference_is_no_literal_violation_and_a_bare_account_beside_it_is():
    account, repo = "acme" + "-dev", "orch" + "-tools"
    answers = {("gh", "api", "user", "--jq", ".login"): account + "\n",
               ("git", "remote", "get-url", "origin"): f"https://github.com/{account}/{repo}.git\n"}
    values = read_values(env={}, run=lambda cmd: answers.get(tuple(cmd), ""), home="", host="")
    link = f"https://github.com/{account}/{repo}/commit/0a1b2c3"
    assert literal_violations(f"Refs {link}\n", values=values) == []
    found = literal_violations(f"Refs {link}\nthe owner {account} wrote it\n", values=values)
    assert [(v.code, v.detail) for v in found] == [("literal_value", "account at line 2: a..v")]
