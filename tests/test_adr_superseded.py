"""The merge line's decision is superseded (#414, unit 8b): the line merges ready pull
requests itself, in batches, with no GitHub auto-merge. The new ADR takes the next number and names ADR 0008 as
superseded; ADR 0008's Status line says so; GLOSSARY.md has no "Held member" heading; docs/events.md has no `--auto`."""

import re
from pathlib import Path

from test_adr_numbers import ADR_FOLDER, adr_files

ROOT = Path(__file__).resolve().parents[1]
NEW_ADR_PREFIX = "0009-"  # this decision's number, as written
OLD_ADR_FILE = "0008-merge-line-holds-auto-merge-tickets-follow-from-github.md"
GLOSSARY = ROOT / "GLOSSARY.md"
EVENTS = ROOT / "docs" / "events.md"


def new_adr_path() -> Path:
    found = [name for name in adr_files() if name.startswith(NEW_ADR_PREFIX)]
    assert len(found) == 1, found
    return ADR_FOLDER / found[0]


def status_line(path: Path) -> str:
    return next(line for line in path.read_text("utf-8").splitlines() if line.startswith("Status:"))


def test_the_new_adr_is_the_previous_highest_number_plus_one_with_no_gap_or_duplicate_before_it():
    # as of this decision: the folder's numbers up to it are 1..N with N the new ADR, so a later ADR (0010, ...) does not
    # change what this test asserts
    numbers = sorted(int(name[:4]) for name in adr_files())
    new_number = int(NEW_ADR_PREFIX[:4])
    before = [n for n in numbers if n <= new_number]
    assert before == list(range(1, new_number + 1)), before
    assert new_adr_path().name.endswith(".md")


def test_the_new_adr_is_accepted_and_names_adr_0008_as_superseded():
    path = new_adr_path()
    assert status_line(path) == "Status: accepted"
    text = path.read_text("utf-8")
    assert re.search(r"ADR 0008[^.\n]*superseded|superseded[^.\n]*ADR 0008", text), "ADR 0008 is not named as superseded"


def test_adr_0008_status_says_it_is_superseded_by_the_new_number():
    assert status_line(ADR_FOLDER / OLD_ADR_FILE).startswith("Status: superseded by ADR 0009")


def test_glossary_has_no_held_member_heading():
    assert not re.search(r"^\*\*Held member\*\*:", GLOSSARY.read_text("utf-8"), re.MULTILINE)


def test_events_doc_names_no_auto_merge_flag():
    assert "--auto" not in EVENTS.read_text("utf-8")
