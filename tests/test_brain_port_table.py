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


def test_a_sentence_said_twice_in_a_source_needs_two_rows_and_a_rule_two_rows_is_refused():
    twice = sources()
    twice["brain-reference.md"] += "\nReference for `docs/agents/brain.md`.\n"
    assert any("no row" in p for p in check(twice, TABLE.read_text("utf-8"), ROOT))
    table = TABLE.read_text("utf-8")
    first = next(line for line in table.split("\n") if line.startswith("| 1 |"))
    assert any("a second row" in p for p in check(sources(), table + first.replace("| 1 |", "| 998 |", 1) + "\n", ROOT))


def test_a_row_with_the_wrong_source_line_is_refused():
    table = TABLE.read_text("utf-8").replace("| 1 | `brain.md:1` |", "| 1 | `brain.md:99` |", 1)
    assert any("line" in p and "row 1" in p for p in check(sources(), table, ROOT))


def test_a_kept_row_pointing_before_its_text_is_refused():
    [row] = [row for row in table_rows() if row.text.startswith("A weak model answers the words")]
    path, line = row.place.split(":")
    table = TABLE.read_text("utf-8").replace(f"`{row.place}` | kept |", f"`{path}:{int(line) - 2}` | kept |", 1)
    assert any("not found at" in p for p in check(sources(), table, ROOT))


@pytest.mark.parametrize("verdict", ["dropped: see #12abc", "dropped: #", "waits on part x: y", "kept, mostly"])
def test_a_verdict_of_no_known_form_is_refused(verdict):
    table = TABLE.read_text("utf-8").replace("| `docs/agents/brain.md:1` | kept |",
                                             f"| `docs/agents/brain.md:1` | {verdict} |", 1)
    assert any("no verdict" in p for p in check(sources(), table, ROOT))


def test_a_place_outside_the_repo_or_not_utf8_is_a_problem_not_a_crash(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "bad.md").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "outside.md").write_text("y\n", "utf-8")
    table = ("| # | source | old text | new place | verdict |\n|---|---|---|---|---|\n"
             "| 1 | `note.md:1` | x | `docs/bad.md:1` | kept |\n| 2 | `note.md:3` | y | `../outside.md:1` | changed: a -> b |\n")
    problems = check({"note.md": "x\n\ny\n"}, table, repo)
    assert any("not UTF-8" in p for p in problems) and any("outside the repo" in p for p in problems)


def test_a_source_with_a_byte_order_mark_reads_as_without():
    bom = sources()
    bom["brain-reference.md"] = "﻿" + bom["brain-reference.md"]
    assert check(bom, TABLE.read_text("utf-8"), ROOT) == []


def test_a_kept_row_whose_text_is_not_at_its_place_is_refused():
    table = TABLE.read_text("utf-8").replace("| `docs/agents/brain.md:1` | kept |", "| `docs/agents/brain.md:60` | kept |",
                                             1)
    assert any("not found at" in p for p in check(sources(), table, ROOT))
