"""The ticket model (#398 decision 1): one parser reads a ticket body into its fields, and
every other part reads tickets only through it."""

from pathlib import Path

from orch_it import tickets

FIXTURES = Path(__file__).parent / "fixtures" / "tickets"


def fixture(name: str) -> tuple[str, str]:
    """(title, body) of a fixture file: its first line is `Title: <title>`, then a blank line."""

    text = (FIXTURES / f"{name}.md").read_text("utf-8")
    first, _, body = text.partition("\n\n")
    return first.removeprefix("Title: "), body


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
