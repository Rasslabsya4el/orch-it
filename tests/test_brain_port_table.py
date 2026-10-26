"""The brain port's rule table (orchestration-layer#403 decision 1; acceptance rows 1, 2, 3 and 9): every
sentence-rule of the four sources the brain's rules were ported from (today's `docs/agents/brain.md`,
`docs/agents/brain-reference.md`, `.claude/agents/brain.md` and the owner's memory note, their snapshots in
`tests/fixtures/brain_port/`) has one row in `docs/research/brain-port-table.md` with a verdict; a `kept`
rule stands verbatim at its new place; a `dropped` one names the map ticket that removed its part."""

import re
from pathlib import Path

import pytest

from brain_port_table import SOURCES, check, rows, rules

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "brain_port"
TABLE = ROOT / "docs" / "research" / "brain-port-table.md"


def sources() -> dict[str, str]:
    return {name: (FIXTURES / name).read_text("utf-8") for name in SOURCES}


def table_rows():
    return rows(TABLE.read_text("utf-8"))


def test_every_rule_of_the_four_sources_has_one_row_with_its_verdict():
    assert check(sources(), TABLE.read_text("utf-8"), ROOT) == []
    assert len(table_rows()) == sum(len(rules(name, text)) for name, text in sources().items())


def test_no_rule_is_dropped_without_the_ticket_that_removed_its_part():
    dropped = [row for row in table_rows() if row.verdict.startswith("dropped")]
    assert dropped and all(re.search(r"#\d+", row.verdict) for row in dropped)


def test_the_counts_the_table_states_are_its_rows():
    counts_line = re.search(r"^Counts: (.+?);", TABLE.read_text("utf-8"), re.M).group(1)
    stated = {}
    for verdict_and_count in counts_line.split(", "):
        verdict, count = verdict_and_count.rsplit(" ", 1)
        stated[verdict] = int(count)
    counted: dict[str, int] = {}
    for row in table_rows():
        key = row.verdict.split(":")[0]
        counted[key] = counted.get(key, 0) + 1
    assert stated == counted


def section_of(source: str, line: int) -> str:
    heading = ""
    for number, text in enumerate((FIXTURES / source).read_text("utf-8").split("\n"), 1):
        if number > line:
            break
        if text.startswith("## "):
            heading = text[3:]
    return heading


@pytest.mark.parametrize("section", ["The results review", "The order of work", "Helpers",
                                     "Loops: the unblocker and the goal stop", "Stopping a foreign process"])
def test_the_reference_s_sections_have_their_rows(section):
    found = [row for row in table_rows() if row.source == "brain-reference.md"
             and section_of(row.source, row.line) == section]
    assert found and all(row.verdict for row in found)


def test_the_memory_note_has_its_rows_he_does_no_step_the_system_can_do_among_them():
    found = [row for row in table_rows() if row.source == "note.md"]
    assert len(found) == len(rules("note.md", sources()["note.md"]))
    assert any(row.text.startswith("He does no step the system can do") and row.verdict == "kept" for row in found)


@pytest.mark.parametrize("opening", [
    "Before the tickets of a result he will see, the brain shows him what he will get",
    "The brain says plainly when something is impossible, unknown or beyond what it can check",
    "A goal that keeps failing stops being fixed and is re-thought",
    "His question is a question, never a decision.",
    "ends with a **TLDR** block",
])
def test_the_five_rules_the_owner_named_are_kept_at_their_new_place(opening):
    [row] = [row for row in table_rows() if row.source == "brain.md" and row.text.startswith(opening)]
    path, line = row.place.split(":")
    assert row.verdict == "kept" and path == "docs/agents/brain.md"
    near = " ".join(" ".join((ROOT / path).read_text("utf-8").split("\n")[int(line) - 1:int(line) + 3]).split())
    assert opening in near


def test_a_rule_planted_in_the_reference_is_found_without_a_row_until_it_gets_one():
    planted = sources()
    planted["brain-reference.md"] += "\nA planted rule: the brain hums while it reads.\n"
    table = TABLE.read_text("utf-8")
    problems = check(planted, table, ROOT)
    assert problems == [p for p in problems if "no row" in p] and len(problems) == 1
    assert "A planted rule: the brain hums while it reads." in problems[0]
    line = len(planted["brain-reference.md"].split("\n")) - 1
    no_verdict = table + f"| 999 | `brain-reference.md:{line}` | A planted rule: the brain hums while it reads. | - | |\n"
    assert any("no verdict" in p for p in check(planted, no_verdict, ROOT))
    given = table + (f"| 999 | `brain-reference.md:{line}` | A planted rule: the brain hums while it reads. | - "
                     "| dropped: a test of the check (#403) |\n")
    assert check(planted, given, ROOT) == []


def test_a_waiting_row_whose_place_says_nothing_of_the_part_is_refused():
    table = TABLE.read_text("utf-8")
    [row] = [row for row in table_rows() if row.text.startswith("**The core.** In a target")]
    [elsewhere] = [other.place for other in table_rows() if other.text.startswith("A weak model answers the words")]
    moved = table.replace(f"| `{row.place}` | {row.verdict}", f"| `{elsewhere}` | {row.verdict}", 1)
    assert any("says nothing of part 8" in p for p in check(sources(), moved, ROOT))


def test_a_kept_row_whose_text_is_not_at_its_place_is_refused():
    table = TABLE.read_text("utf-8").replace("| `docs/agents/brain.md:1` | kept |", "| `docs/agents/brain.md:60` | kept |",
                                             1)
    assert any("not found at" in p for p in check(sources(), table, ROOT))
