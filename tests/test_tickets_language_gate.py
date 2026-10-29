"""The language rule on GitHub (#453 unit 2): the runner reads `human_language` from the `settings.json` at the root of
its checkout's work tree, the file the writers read, so one language judges a ticket on both sides. A ticket the
layer's own code wrote is judged by the shape rules, the language rule included, and is still not judged by the literal
rules, which it trips on purpose (the layer's own commands, #424 unit 8). Invented text; the tree file is in tmp."""

import os
import tomllib
from pathlib import Path

import pytest
from fake_hub import REPO, FakeHub

from orch_it import language
from orch_it.tickets import gh_check
from orch_it.tickets.model import CODE_WRITTEN_MARK

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # the runner's tree and the ticket bodies are Russian

OWNER = REPO.split("/")[0]
RU_TABLE = tomllib.loads((Path(language.__file__).parent / "language_tables" / "ru.toml").read_text("utf-8"))
TITLE = "Cart shows the shipping cost before payment"

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
# one literal finding and nothing else: a one-OS process tool named in the agent block
AGENT_WITH_A_LITERAL = AGENT.replace("Rule: the cart", "Rule: taskkill ends the old cart run; the cart")
WHO = [{"author": OWNER, "kind": "User"}, {"author": "github-actions[bot]", "kind": "Bot"}]


def event(author="a-person", kind="User"):
    """The recorded `labeled` payload of the `ready-for-agent` label, as the workflow's event file holds it."""
    return {"action": "labeled", "repository": {"full_name": REPO, "owner": {"login": OWNER}},
            "issue": {"number": 1, "labels": [{"name": "ready-for-agent"}],
                      "user": {"login": author, "type": kind}},
            "label": {"name": "ready-for-agent"}}


def judged(body, **who):
    hub = FakeHub()
    hub.add(1, body, ["ready-for-agent"], title=TITLE)
    runner = {**os.environ, "GITHUB_WORKSPACE": str(Path.cwd())}
    return gh_check.check_event(event(**who), hub, env=runner), hub


def labels(hub):
    return hub.issues[(REPO, 1)].labels


def test_a_persons_ticket_in_the_tree_language_keeps_its_label():
    result, hub = judged(RUSSIAN_HUMAN + AGENT)
    assert (result.line, labels(hub)) == ("#1 kept", ("ready-for-agent",))
    assert (REPO, 1) not in hub.notes


def test_a_persons_ticket_in_english_loses_its_label_and_the_notice_is_in_russian():
    result, hub = judged(ENGLISH_HUMAN + AGENT)
    assert (result.outcome, result.codes) == ("removed", ["human_field_wrong_language"])
    assert labels(hub) == ()
    [note] = hub.notes[(REPO, 1)]
    assert RU_TABLE["check_took_label"].format(label="ready-for-agent") in note.body
    assert "- `human_field_wrong_language`: " + RU_TABLE["human_field_language_rule"] in note.body


@pytest.mark.parametrize("who", WHO, ids=["owner", "bot"])
def test_a_code_written_ticket_in_english_loses_its_label_on_the_language_rule_only(who):
    # the literal finding is in the body too: the literal rules skip a code-written ticket, the language rule does not
    result, hub = judged(f"{CODE_WRITTEN_MARK}\n{ENGLISH_HUMAN}{AGENT_WITH_A_LITERAL}", **who)
    assert (result.outcome, result.codes) == ("removed", ["human_field_wrong_language"])
    assert labels(hub) == ()


@pytest.mark.parametrize("who", WHO, ids=["owner", "bot"])
def test_a_code_written_ticket_with_only_a_literal_finding_keeps_its_label(who):
    result, hub = judged(f"{CODE_WRITTEN_MARK}\n{RUSSIAN_HUMAN}{AGENT_WITH_A_LITERAL}", **who)
    assert (result.outcome, labels(hub)) == ("kept", ("ready-for-agent",))
    assert (REPO, 1) not in hub.notes


def test_the_same_literal_finding_in_a_persons_ticket_removes_its_label():
    result, hub = judged(RUSSIAN_HUMAN + AGENT_WITH_A_LITERAL)
    assert (result.outcome, result.codes) == ("removed", ["literal_pattern"])
    assert labels(hub) == ()
