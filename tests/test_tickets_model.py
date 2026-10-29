"""The ticket model (#398 decision 1): one parser reads a ticket body into its fields, and
every other part reads tickets only through it."""

import pytest

from orch_it import tickets
from ticket_fixtures import fixture

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # Russian ticket bodies (#413 unit 10a)


def test_a_body_is_read_into_its_fields():
    title, body = fixture("good_basic")
    ticket = tickets.parse(body, title=title, labels=["ready-for-agent", "priority"])

    assert ticket.title == "Cart shows the shipping cost before payment"
    assert list(ticket.human) == ["What", "Goal", "Plan", "Result", "Undo"]
    assert ticket.human["Plan"] == ("корзина спрашивает тот же расчёт доставки, что и страница"
                                    " оплаты, как только введён почтовый индекс.")
    assert ticket.human["Undo"].startswith("two-way door (один откат); touches")
    assert ticket.agent.startswith("Facts: the payment page")
    assert [row.id for row in ticket.acceptance] == ["1", "2", "3", "4"]
    assert [row.after_merge for row in ticket.acceptance] == [False, False, False, True]
    assert ticket.acceptance[2].check == "cart shipping cost for 50667, a zone none of the named ones is in"
    assert ticket.acceptance[2].passes == "equals the payment page cost for 50667"
    assert ticket.outside_case == "50667, a zone none of 10115 and 20095 is in (row 3)."
    assert ticket.class_none is None
    assert ticket.same_object.startswith("the payment page's `shipping_quote(postcode)`")
    assert ticket.not_in_ticket == "the shipping rates themselves; the payment page's layout."
    assert ticket.weight == 3
    assert ticket.adds == ()
    assert ticket.park_lines == ()
    assert ticket.ready and ticket.priority and not ticket.parked


def test_the_forms_a_person_writes_on_github_are_read_as_the_same_fields():
    title, body = fixture("good_basic")
    written = (body.replace("## For the human", "## for the Human ##")
               .replace("## For the agent", "## For the agent:")
               .replace("- **What:**", "* **What**:")
               .replace("Weight: 3", "- **Weight:** 3")
               .replace("Outside case:", "**Outside case:**")
               .replace("| 4 after merge |", "| 4, (after merge) |"))
    ticket, plain = tickets.parse(written, title=title), tickets.parse(body, title=title)
    assert ticket.human == plain.human
    assert ticket.weight == 3 and ticket.outside_case == plain.outside_case
    assert ticket.acceptance == plain.acceptance
    assert tickets.violations(ticket) == []


def test_comments_and_park_lines_may_stand_above_the_human_block():
    _, body = fixture("good_basic")
    ticket = tickets.parse("<!-- written by a template\n  of the repo -->\nParked: date 2026-12-01 until 2026-12-02\n"
                           + body + "\nParked: free text further down\n")
    assert ticket.human_block_on_top
    assert ticket.park_lines == ("Parked: date 2026-12-01 until 2026-12-02", "Parked: free text further down")


def test_a_none_line_carries_its_why_in_brackets_or_after_a_dash():
    assert tickets.parse("## For the agent\nClass: none (one fixed page)").class_none == "one fixed page"
    assert tickets.parse("## For the agent\nClass: none - one fixed page").class_none == "one fixed page"
    assert tickets.parse("## For the agent\nClass: none").class_none is None
    assert tickets.parse("## For the agent\nClass: none ()").class_none is None


def test_an_unclosed_fence_hides_the_rest_and_another_fence_mark_does_not_close_it():
    _, body = fixture("good_basic")
    ticket = tickets.parse(body.replace("Weight: 3", "~~~\n```\nWeight: 3"))
    assert ticket.weight_text is None and ticket.outside_case is None


def test_labels_are_read_as_github_names_them_whatever_form_they_come_in():
    _, body = fixture("good_basic")
    for labels in (["Ready-For-Agent"], [{"name": "ready-for-agent", "color": "0e8a16"}], "ready-for-agent"):
        assert tickets.parse(body, labels=labels).ready
    for labels in (None, 5, [None, 3, {"id": 1}], ""):
        assert tickets.parse(body, labels=labels).labels == frozenset()


def test_a_body_or_title_that_is_no_text_reads_as_none():
    for body in (b"## For the human", 12, ["x"]):
        assert tickets.parse(body).has_human_block is False
    assert tickets.parse("", title=123).title is None
    assert tickets.violations(tickets.parse(b"x", title=b"t"))


def test_fences_follow_commonmark():
    _, body = fixture("good_basic")
    tilde_holding_backticks = body.replace("Weight: 3", "~~~\n```\n~~~\nWeight: 3")
    assert tickets.parse(tilde_holding_backticks).weight == 3
    inline = body.replace("Weight: 3", "```x``` is inline code\nWeight: 3")
    assert tickets.parse(inline).weight == 3
    indented = body.replace("Weight: 3", "    ```\nWeight: 3")
    assert tickets.parse(indented).weight == 3


def test_a_weight_in_other_digits_is_no_weight():
    _, body = fixture("good_basic")
    assert tickets.parse(body.replace("Weight: 3", "Weight: ٣")).weight is None
