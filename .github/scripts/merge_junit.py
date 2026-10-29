"""Merge the JUnit reports of one job's pytest phases into one report (#421 rule 5): `merge_junit.py OUT PART...`
writes OUT as a `<testsuites>` holding every `<testsuite>` of every PART, in the order given. A PART that is
missing is left out; one that cannot be parsed stops the merge with an error, so a half-written report is never
passed as a whole one. OUT may be one of the PARTs."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def merge(out: Path, parts: list[Path]) -> int:
    merged = ET.Element("testsuites")
    for part in parts:
        if not part.exists():
            continue
        root = ET.parse(part).getroot()
        merged.extend([root] if root.tag == "testsuite" else list(root))
    ET.ElementTree(merged).write(out, encoding="utf-8", xml_declaration=True)
    return sum(len(list(suite.iter("testcase"))) for suite in merged)


if __name__ == "__main__":
    print(f"merged report: {merge(Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]])} test cases")
