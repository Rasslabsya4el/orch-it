"""The language rule on GitHub (#453 unit 2): the runner reads `human_language` from the `settings.json` at the root of
its checkout's work tree, the file the writers read, so one language judges a ticket on both sides. A ticket the
layer's own code wrote is judged by the shape rules, the language rule included, and is still not judged by the literal
rules, which it trips on purpose (the layer's own commands, #424 unit 8). Invented text; the tree file is in tmp.
The payload, the judge, the runner and the literal probe are the layer test file's (test_tickets_gh_check_layer.py)."""

import tomllib
from pathlib import Path

import pytest
from fake_hub import REPO

from orch_it import language
from orch_it.tickets.model import CODE_WRITTEN_MARK
from test_tickets_gh_check_layer import LITERAL, OWNER, judged, runner  # noqa: F401 (runner is a fixture)

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # the runner's tree and the ticket bodies are Russian

RU_TABLE = tomllib.loads((Path(language.__file__).parent / "language_tables" / "ru.toml").read_text("utf-8"))
TITLE = "Cart shows the shipping cost before payment"
PERSON = {"author": "a-person", "kind": "User"}
WHO = [{"author": OWNER, "kind": "User"}, {"author": "layer-helper[bot]", "kind": "Bot"}]

RUSSIAN_HUMAN = """## For the human
- **What:** стоимость доставки показывается в корзине до оплаты.
- **Goal:** покупатель видит итоговую цену заказа до оплаты, а не после неё.
- **Plan:** корзина спрашивает тот же расчёт доставки, что и страница оплаты.
- **Result:** цена в корзине равна цене на оплате на двух индексах, один из них вне названных.
- **Undo:** two-way door (один откат); touches каждый заказ, сделанный через корзину.
"""
ENGLISH_HUMAN = """## For the human
- **What:** the cart shows the shipping cost before the customer pays.
- **Goal:** the buyer sees the final price of the order before paying, not after it.
- **Plan:** the cart asks the same shipping calculation as the payment page does.
- **Result:** the cart cost equals the payment cost on two codes, one of them outside the named ones.
- **Undo:** two-way door (one revert); touches every order made through the cart.
"""
AGENT = """
## For the agent
Facts: the cart page has no call to `shipping_quote(postcode)`.
Rule: the cart calls the same function as the payment page.
Weight: 3

### Acceptance

| row | check | pass |
|---|---|---|
| 1 | cart cost for 10115 | equals the payment page cost |
| 2 | cart cost for 50667, a zone none of the named ones is in | equals the payment page cost |

Outside case: 50667, a zone none of the named ones is in (row 2).
Same object: none (nothing else reads the cart price).
"""
# the layer file's literal probe (one bare `python` command in the ticket text), in the agent block
AGENT_WITH_A_LITERAL = AGENT.replace("## For the agent\n", "## For the agent\n" + LITERAL + "\n", 1)


def labels(hub):
    return hub.issues[(REPO, 1)].labels


def test_a_persons_ticket_in_the_tree_language_keeps_its_label(runner):
    result, hub = judged(TITLE, RUSSIAN_HUMAN + AGENT, runner, **PERSON)
    assert (result.line, labels(hub)) == ("#1 kept", ("ready-for-agent",))
    assert (REPO, 1) not in hub.notes


def test_a_persons_ticket_in_english_loses_its_label_and_the_notice_is_in_russian(runner):
    result, hub = judged(TITLE, ENGLISH_HUMAN + AGENT, runner, **PERSON)
    assert (result.outcome, result.codes) == ("removed", ["human_field_wrong_language"])
    assert labels(hub) == ()
    [note] = hub.notes[(REPO, 1)]
    assert RU_TABLE["check_took_label"].format(label="ready-for-agent") in note.body
    assert "- `human_field_wrong_language`: " + RU_TABLE["human_field_language_rule"] in note.body


@pytest.mark.parametrize("who", WHO, ids=["owner", "bot"])
def test_a_code_written_ticket_in_english_loses_its_label_on_the_language_rule_only(who, runner):
    # the literal finding is in the body too: the literal rules skip a code-written ticket, the language rule does not
    result, hub = judged(TITLE, f"{CODE_WRITTEN_MARK}\n{ENGLISH_HUMAN}{AGENT_WITH_A_LITERAL}", runner, **who)
    assert (result.outcome, result.codes) == ("removed", ["human_field_wrong_language"])
    assert labels(hub) == ()


@pytest.mark.parametrize("who", WHO, ids=["owner", "bot"])
def test_a_code_written_ticket_with_only_a_literal_finding_keeps_its_label(who, runner):
    result, hub = judged(TITLE, f"{CODE_WRITTEN_MARK}\n{RUSSIAN_HUMAN}{AGENT_WITH_A_LITERAL}", runner, **who)
    assert (result.outcome, labels(hub)) == ("kept", ("ready-for-agent",))
    assert (REPO, 1) not in hub.notes


def test_the_same_literal_finding_in_a_persons_ticket_removes_its_label(runner):
    result, hub = judged(TITLE, RUSSIAN_HUMAN + AGENT_WITH_A_LITERAL, runner, **PERSON)
    assert (result.outcome, result.codes) == ("removed", ["literal_pattern"])
    assert labels(hub) == ()
