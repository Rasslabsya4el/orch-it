"""The ticket bodies of the shape rules (#398 acceptance row 1), shared by every test that judges
a body: the model, the shape rules, the session hook and the GitHub check."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "tickets"

# fixture name -> every violation code it holds, written by hand from the rules
EXPECTED: dict[str, list[str]] = {
    name: codes for name, codes in json.loads((FIXTURES / "expected.json").read_text("utf-8")).items()
    if not name.startswith("_")}


def fixture(name: str) -> tuple[str, str]:
    """(title, body) of a fixture file: its first line is `Title: <title>`, then a blank line."""

    text = (FIXTURES / f"{name}.md").read_text("utf-8")
    first, _, body = text.partition("\n\n")
    return first.removeprefix("Title: "), body
