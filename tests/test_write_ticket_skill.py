"""The `write-ticket` skill agrees with the shape rules (#398 decision 7, acceptance row 7): what
the skill says matches `orch_it.tickets.RULES` rule for rule, its template is a ticket the rules
pass, and a skill missing one rule fails the check."""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from orch_it import tickets
from orch_it.labels import label_set

SKILL = Path(__file__).parents[1] / ".claude" / "skills" / "write-ticket" / "SKILL.md"
RULE_LINE = re.compile(r"^- `([a-z_]+)`:", re.MULTILINE)


@dataclass(frozen=True)
class Agreement:
    only_in_skill: set[str]
    only_in_rules: set[str]
    stated_twice: set[str]
    template_violations: list[str]


def rules_section(text: str) -> str:
    found = re.search(r"^## Rules\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return found.group(1) if found else ""


def agreement(text: str) -> Agreement:
    """How a skill text and the code's rules differ; the skill's rules are its `## Rules` lines."""

    stated = RULE_LINE.findall(rules_section(text))
    # the template's own `## For the human` would end a section: take the first fence after the heading
    template = re.search(r"^## Template\n.*?```\n(Title: .*?)\n```", text, re.MULTILINE | re.DOTALL)
    title, _, body = template.group(1).partition("\n\n") if template else ("", "", "")
    found = tickets.violations(tickets.parse(body, title=title.removeprefix("Title: ")))
    return Agreement(set(stated) - set(tickets.RULES), set(tickets.RULES) - set(stated),
                     {code for code in stated if stated.count(code) > 1}, [v.code for v in found])


def test_the_skill_states_every_shape_rule_once_and_no_other():
    found = agreement(SKILL.read_text("utf-8"))
    assert (found.only_in_skill, found.only_in_rules, found.stated_twice) == (set(), set(), set())


def test_the_template_of_the_skill_is_a_ticket_the_rules_pass():
    assert agreement(SKILL.read_text("utf-8")).template_violations == []


@pytest.mark.parametrize("code", sorted(tickets.RULES))
def test_a_skill_missing_one_rule_fails_the_check(code):
    text = SKILL.read_text("utf-8")
    missing = re.sub(rf"^- `{code}`:.*\n", "", text, count=1, flags=re.MULTILINE)
    assert missing != text
    assert agreement(missing).only_in_rules == {code}


def test_a_rule_only_in_prose_or_outside_the_rules_section_is_not_stated():
    text = SKILL.read_text("utf-8")
    moved = re.sub(r"^(- `weight_bad`:.*\n)", "", text, count=1, flags=re.MULTILINE)
    moved += "\n## After\n\nThe rule `weight_bad` in prose.\n\n- `weight_bad`: in another section\n"
    assert agreement(moved).only_in_rules == {"weight_bad"}


def test_a_skill_with_a_rule_the_code_lacks_or_one_rule_twice_fails_the_check():
    text = SKILL.read_text("utf-8").replace("## Rules\n", "## Rules\n\n- `title_rude`: the title is polite.\n", 1)
    assert agreement(text).only_in_skill == {"title_rude"}
    twice = SKILL.read_text("utf-8").replace("## Rules\n", "## Rules\n\n- `weight_bad`: again.\n", 1)
    assert agreement(twice).stated_twice == {"weight_bad"}


def test_the_skill_names_the_park_commands_and_every_label_of_the_set():
    text = SKILL.read_text("utf-8")
    for command in ("python -m orch_it tickets park", "python -m orch_it tickets unpark"):
        assert command in text
    for label in label_set():
        assert f"`{label}`" in text, label
    assert "flow-fix" not in text and "Flow fix" not in text  # dropped in the new layer (decision 2)
