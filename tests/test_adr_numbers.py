"""Every ADR has a number of its own (Rasslabsya4el/orchestration-layer#397): several sessions write
ADRs at once, each taking the next number it sees, so two files of one number reach `main` unless a
test refuses them."""

import re
from collections import defaultdict
from pathlib import Path

ADR_FOLDER = Path(__file__).parents[1] / "docs" / "adr"
ADR_NAME = re.compile(r"(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md")


def test_every_adr_file_is_named_number_dash_slug():
    names = [p.name for p in ADR_FOLDER.iterdir() if p.is_file()]
    assert names and all(ADR_NAME.fullmatch(name) for name in names), names


def test_no_two_adrs_share_a_number():
    by_number = defaultdict(list)
    for path in ADR_FOLDER.glob("*.md"):
        found = ADR_NAME.fullmatch(path.name)
        if found:
            by_number[found.group(1)].append(path.name)
    assert {n: names for n, names in by_number.items() if len(names) > 1} == {}
