"""The language of a ticket's human block is the setting `human_language` (#413 decision 6, unit 10a):
the shape check counts the letters of the language's own script (table key `human_field_letters`) and
refuses a block written in another script with `human_field_wrong_language`, in the words of that
language's table. Invented text; settings and tables in tmp folders."""

import json
import re
import tomllib
from pathlib import Path

import pytest

from language_tree import git_tree, write_language
from orch_it import language, settings, tickets

PACKAGE_TABLES = Path(language.__file__).parent / "language_tables"
CODE = "human_field_wrong_language"
CYRILLIC = re.compile("[Ѐ-ӿ]")

AGENT_BLOCK = """
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

ENGLISH = """## For the human
- **What:** the cart shows the shipping cost before the customer pays.
- **Goal:** the buyer sees the final price of the order before paying, not after it.
- **Plan:** the cart asks the same shipping calculation as the payment page does.
- **Result:** the cart cost equals the payment cost on two codes, one of them outside the named ones.
- **Undo:** two-way door (one revert); touches every order made through the cart.
"""

RUSSIAN = """## For the human
- **What:** стоимость доставки показывается в корзине до оплаты.
- **Goal:** покупатель видит итоговую цену заказа до оплаты, а не после неё.
- **Plan:** корзина спрашивает тот же расчёт доставки, что и страница оплаты.
- **Result:** цена в корзине равна цене на оплате на двух индексах, один из них вне названных.
- **Undo:** two-way door (один откат); touches каждый заказ, сделанный через корзину.
"""

GERMAN = """## For the human
- **What:** die Versandkosten erscheinen im Warenkorb, noch vor der Bezahlung.
- **Goal:** der Käufer sieht den Endpreis der Bestellung, bevor er bezahlt, und nicht erst danach.
- **Plan:** der Warenkorb fragt dieselbe Berechnung der Lieferung ab wie die Zahlungsseite.
- **Result:** der Preis im Warenkorb ist gleich dem Preis bei der Zahlung für zwei Postleitzahlen, eine davon außerhalb der genannten.
- **Undo:** two-way door (ein Rückgang); touches jede Bestellung über den Warenkorb.
"""


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.chdir(git_tree(tmp_path / "tree"))  # the language is the tree file's, read from the cwd (#453 unit 1)


def configure(tmp_path, **values):
    """`human_language` goes to the settings.json of the cwd's work tree; the rest to the installation file."""
    write_language(Path.cwd(), values.pop("human_language", None))
    (tmp_path / "settings.json").write_text(json.dumps(values), "utf-8")


def codes(human: str) -> list[str]:
    ticket = tickets.parse(human + AGENT_BLOCK, title="Cart shows the shipping cost before payment")
    return [v.code for v in tickets.violations(ticket)]


def plant_de(tmp_path) -> Path:
    """A German table with the keys of the shipped English one, the letters of Latin with umlauts."""

    folder = tmp_path / "my-tables"
    folder.mkdir()
    keys = tomllib.loads((PACKAGE_TABLES / "en.toml").read_text("utf-8"))
    keys["human_field_language_rule"] = "der Text jedes Feldes für den Menschen ist deutsch"
    keys["human_field_letters"] = "[A-Za-zÄÖÜäöüß]"
    (folder / "de.toml").write_text("".join(f"{k} = {json.dumps(v, ensure_ascii=False)}\n"
                                            for k, v in keys.items()), "utf-8")
    return folder


def test_unset_language_is_english():
    assert codes(ENGLISH) == []
    assert codes(RUSSIAN) == [CODE] * 5


def test_ru_passes_russian_and_refuses_english(tmp_path):
    configure(tmp_path, human_language="ru")
    assert codes(RUSSIAN) == []
    assert codes(ENGLISH) == [CODE] * 5


def test_a_planted_german_table_judges_by_its_own_letters(tmp_path):
    configure(tmp_path, human_language="de", language_tables_dir=str(plant_de(tmp_path)))
    assert codes(GERMAN) == []
    assert codes(RUSSIAN) == [CODE] * 5


def test_the_refusal_is_in_the_language_of_the_setting(tmp_path):
    configure(tmp_path, human_language="de", language_tables_dir=str(plant_de(tmp_path)))
    ticket = tickets.parse(RUSSIAN + AGENT_BLOCK)
    refusal = next(v for v in tickets.violations(ticket) if v.code == CODE)
    assert "der Text jedes Feldes" in refusal.message and "English" not in refusal.message
    configure(tmp_path, human_language="ru")
    refusal = next(v for v in tickets.violations(tickets.parse(ENGLISH + AGENT_BLOCK)) if v.code == CODE)
    assert CYRILLIC.search(refusal.rule)


def test_a_language_with_no_table_is_refused_not_answered_in_english(tmp_path):
    configure(tmp_path, human_language="fr")
    with pytest.raises(settings.SettingsError) as refused:
        codes(ENGLISH)
    assert "no table for 'fr'" in str(refused.value)


def test_the_undo_words_are_read_from_the_table(tmp_path):
    for name in ("en", "ru"):
        table = tomllib.loads((PACKAGE_TABLES / f"{name}.toml").read_text("utf-8"))
        assert "two-way door" in table["undo_words"] and "touches" in table["undo_words"]


def test_one_word_of_the_wrong_script_does_not_refuse_a_block_of_the_right_one(tmp_path):
    configure(tmp_path, human_language="ru")
    mixed = RUSSIAN.replace("стоимость доставки показывается", "стоимость доставки показывается (shipping)")
    assert codes(mixed) == []


def test_an_undo_word_inside_a_longer_word_is_still_text(tmp_path):
    configure(tmp_path, human_language="ru")
    fixed = RUSSIAN.replace("- **Undo:** two-way door (один откат); touches каждый заказ, сделанный через корзину.",
                            "- **Undo:** да touches")
    longer = fixed.replace("да touches", "да retouches")  # 2 Cyrillic letters against 9 Latin ones
    assert fixed != RUSSIAN and codes(fixed) == []
    assert codes(longer) == [CODE]
