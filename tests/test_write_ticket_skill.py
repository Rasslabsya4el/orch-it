"""The `write-ticket` skill agrees with the shape rules (#398 decision 7, acceptance row 7): what
the skill says matches `orch_it.tickets.RULES` rule for rule, its template is a ticket the rules
pass, and a skill missing one rule fails the check."""

import json
import re
from pathlib import Path

import pytest

from orch_it import tickets
from orch_it.labels import label_set

SKILL = Path(__file__).parents[1] / ".claude" / "skills" / "write-ticket" / "SKILL.md"
RULE_LINE = re.compile(r"^- `([a-z_]+)`:", re.MULTILINE)


def section(text: str, heading: str) -> str:
    found = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return found.group(1) if found else ""


def agreement(text: str) -> tuple[set[str], set[str], list[str]]:
    """(codes only in the skill, codes only in the rules, the violations of the skill's template)."""

    stated = set(RULE_LINE.findall(section(text, "Rules")))
    # the template's own `## For the human` would end a section: take the first fence after the heading
    template = re.search(r"^## Template\n.*?```\n(Title: .*?)\n```", text, re.MULTILINE | re.DOTALL)
    title, _, body = template.group(1).partition("\n\n") if template else ("", "", "")
    found = tickets.violations(tickets.parse(body, title=title.removeprefix("Title: ")))
    return stated - set(tickets.RULES), set(tickets.RULES) - stated, [v.code for v in found]


def test_the_skill_states_every_shape_rule_and_no_other():
    only_in_skill, only_in_rules, _ = agreement(SKILL.read_text("utf-8"))
    assert (only_in_skill, only_in_rules) == (set(), set())


def test_the_skill_s_template_is_a_ticket_the_rules_pass():
    assert agreement(SKILL.read_text("utf-8"))[2] == []


@pytest.mark.parametrize("code", sorted(tickets.RULES))
def test_a_skill_missing_one_rule_fails_the_check(code):  # row 7: red on a fixture skill missing a rule
    text = SKILL.read_text("utf-8")
    missing = re.sub(rf"^- `{code}`:.*\n", "", text, count=1, flags=re.MULTILINE)
    assert missing != text
    assert agreement(missing)[1] == {code}


def test_a_skill_with_a_rule_the_code_lacks_fails_the_check():
    text = SKILL.read_text("utf-8").replace("## Rules\n", "## Rules\n\n- `title_rude`: the title is polite.\n", 1)
    assert agreement(text)[0] == {"title_rude"}


def test_the_skill_names_the_park_commands_and_every_label_of_the_set():
    text = SKILL.read_text("utf-8")
    for command in ("python -m orch_it tickets park", "python -m orch_it tickets unpark"):
        assert command in text
    for label in label_set():
        assert f"`{label}`" in text, label
    assert "flow-fix" not in text and "Flow fix" not in text  # dropped in the new layer (decision 2)
    assert json.loads(json.dumps(sorted(label_set())))  # the set the skill names is the file's own
