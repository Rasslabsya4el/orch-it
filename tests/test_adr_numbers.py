"""Every ADR has a number of its own (the layer's repo#397): several sessions write
ADRs at once, each taking the next number it sees, so two files of one number reach `main` unless a
test refuses them. An ADR is a file of `docs/adr/` whose name starts with a number; other files (a
README) are left alone."""

import re
from collections import defaultdict
from pathlib import Path

ADR_FOLDER = Path(__file__).parents[1] / "docs" / "adr"
NUMBERED = re.compile(r"(\d+)-")                         # any file that claims a number
ADR_NAME = re.compile(r"\d{4}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")


def adr_files() -> list[str]:
    return sorted(p.name for p in ADR_FOLDER.iterdir() if p.is_file() and NUMBERED.match(p.name))


def test_every_adr_is_named_four_digits_dash_slug_md():
    adrs = adr_files()
    assert adrs, "no ADR found"
    assert [name for name in adrs if not ADR_NAME.fullmatch(name)] == []


def test_no_two_adrs_share_a_number():
    names_by_number = defaultdict(list)
    for name in adr_files():
        names_by_number[int(NUMBERED.match(name).group(1))].append(name)  # 0005 and 005 are one number
    assert {number: names for number, names in names_by_number.items() if len(names) > 1} == {}
