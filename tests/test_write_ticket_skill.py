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


def section(text: str, heading: str) -> str:
    """Every `## <heading>` section's text, without fenced blocks and HTML comments: what a reader sees."""

    seen = re.sub(r"<!--.*?-->|^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    return "".join(re.findall(rf"^## {heading}\n(.*?)(?=^## |\Z)", seen, re.MULTILINE | re.DOTALL))


def rules_section(text: str) -> str:
    return section(text, "Rules")


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


def test_a_rule_line_a_reader_cannot_see_is_not_stated():
    text = SKILL.read_text("utf-8")
    hidden = re.sub(r"^(- `weight_bad`:.*\n)", r"<!--\n\1-->\n", text, count=1, flags=re.MULTILINE)
    assert agreement(hidden).only_in_rules == {"weight_bad"}


def test_the_skill_names_the_park_commands_and_each_label_of_the_set_in_its_section():
    text = SKILL.read_text("utf-8")
    for command in ("park", "unpark", "parks"):
        assert f"python -m orch_it tickets {command}" in text
    assert set(re.findall(r"^- `([a-z-]+)`:", section(text, "Labels"), re.MULTILINE)) == set(label_set())
    for dead in ("flow-fix", "Flow fix", "autopilot", "provisional", "hard-push"):  # not carried (decisions 2, 6)
        assert dead not in text, dead
