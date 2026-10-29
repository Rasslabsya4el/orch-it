"""docs/events.md, the human list, agrees with closed_sets.json, the machine-readable one,
both ways (#393 decision 4): an item in one and not the other, or written differently, is a
difference. Both sides are read into the same shape and compared item by item."""

import copy
import importlib.resources
import json
import re
from pathlib import Path

DOC = Path(__file__).parents[1] / "docs" / "events.md"
FIELD = re.compile(r"`([^`]+)`(?: \(`([^`]+)`\)| \(≤(\d+)\))?")


def load_doc() -> str:
    return DOC.read_text(encoding="utf-8")


def load_file() -> dict:
    text = importlib.resources.files("orch_it.events").joinpath("closed_sets.json")
    return json.loads(text.read_text(encoding="utf-8"))


def table_rows(markdown: str, heading: str) -> list[list[str]]:
    """The cells of each body row of the first table under `## heading`."""

    rows: list[list[str]] = []
    inside = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == heading
        elif inside and line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows[2:]


def name_of(cell: str) -> str:
    return cell.strip("`")


def fields_in(cell: str) -> dict[str, dict]:
    """`a`, `b` (`list`), `c` (<=120) -> {"a": {}, "b": {"codes": "list"}, "c": {"max_chars": 120}}."""

    if cell == "none":
        return {}
    fields: dict[str, dict] = {}
    for name, codes, max_chars in FIELD.findall(cell):
        fields[name] = {"codes": codes} if codes else {"max_chars": int(max_chars)} if max_chars else {}
    return fields


def doc_sets(markdown: str) -> dict:
    envelope = {
        name_of(field): (type_, null == "yes", name_of(codes))
        for field, type_, null, codes in table_rows(markdown, "Envelope")
    }
    codes = {
        name_of(name): re.findall(r"`([^`]+)`", items)
        for name, items in table_rows(markdown, "Code lists")
    }
    steps = {
        name_of(step): {"started": fields_in(started), "ended": fields_in(ended)}
        for step, started, ended, _answers in table_rows(markdown, "Step ids")
    }
    kinds = {
        name_of(kind): fields_in(fields)
        for kind, fields, _answers in table_rows(markdown, "Point kinds")
    }
    return {"envelope": envelope, "codes": codes, "steps": steps, "kinds": kinds}


def file_sets(raw_json: dict) -> dict:
    envelope = {
        name: (spec["type"], spec["nullable"], spec.get("codes", ""))
        for name, spec in raw_json["envelope"].items()
    }
    steps = {
        step: {"started": dict(sides["started"]), "ended": dict(sides["ended"])}
        for step, sides in raw_json["steps"].items()
    }
    return {
        "envelope": envelope,
        "codes": {name: list(items) for name, items in raw_json["codes"].items()},
        "steps": steps,
        "kinds": {kind: dict(fields) for kind, fields in raw_json["kinds"].items()},
    }


def differences(doc: dict, file: dict) -> list[str]:
    """Every item named in one side only, or different between the two, by its dotted path."""

    found: list[str] = []

    def compare(path: str, in_doc: object, in_file: object) -> None:
        if isinstance(in_doc, dict) and isinstance(in_file, dict):
            for key in in_doc.keys() | in_file.keys():
                where = f"{path}.{key}" if path else key
                if key not in in_file:
                    found.append(f"{where}: only in the doc")
                elif key not in in_doc:
                    found.append(f"{where}: only in the file")
                else:
                    compare(where, in_doc[key], in_file[key])
        elif in_doc != in_file:
            found.append(f"{path}: the doc says {in_doc!r}, the file says {in_file!r}")

    compare("", doc, file)
    return sorted(found)


def test_the_doc_and_the_file_agree():
    assert differences(doc_sets(load_doc()), file_sets(load_file())) == []


def test_the_doc_is_read_whole():
    sets = doc_sets(load_doc())
    assert len(sets["envelope"]) == 15
    assert len(sets["steps"]) == 24  # + line (#400)
    assert len(sets["kinds"]) == 51  # 47 of the list, + github.called (#414), + skill.shadowed (#397), lock.removed, line.served (#400)
    assert sets["steps"]["wait"] == {"started": {"reason": {"codes": "wait_reason"}, "on": {}},
                                     "ended": {"reason": {"codes": "wait_end_reason"}}}
    assert sets["kinds"]["tool.called"]["args_head"] == {"max_chars": 120}
    assert sets["kinds"]["machine.booted"] == {}


def test_a_planted_extra_kind_in_the_doc_is_found():
    doc = load_doc()
    last_row = next(line for line in doc.splitlines() if line.startswith("| `machine.resumed`"))
    planted = doc.replace(last_row, last_row + "\n| `planted.kind` | `x` | nothing |")
    found = differences(doc_sets(planted), file_sets(load_file()))
    assert len(found) == 1 and "planted.kind" in found[0]


def test_a_planted_extra_kind_in_the_file_is_found():
    raw = copy.deepcopy(load_file())
    raw["kinds"]["planted.kind"] = {"x": {}}
    found = differences(doc_sets(load_doc()), file_sets(raw))
    assert len(found) == 1 and "planted.kind" in found[0]


def test_a_planted_extra_code_in_the_doc_is_found():
    line = next(row for row in load_doc().splitlines() if row.startswith("| `outcome` |"))
    planted = load_doc().replace(line, line.rstrip(" |") + ", `planted_code` |")
    found = differences(doc_sets(planted), file_sets(load_file()))
    assert len(found) == 1 and "outcome" in found[0] and "planted_code" in found[0]


def test_a_planted_extra_code_in_the_file_is_found():
    raw = copy.deepcopy(load_file())
    raw["codes"]["outcome"].append("planted_code")
    found = differences(doc_sets(load_doc()), file_sets(raw))
    assert len(found) == 1 and "outcome" in found[0] and "planted_code" in found[0]


def test_a_test_run_reason_code_planted_in_one_side_only_is_found():
    """#394 row 8: the test run's reason codes are held to the same agreement."""

    raw = copy.deepcopy(load_file())
    raw["codes"]["test_run_reason"].append("planted_reason")
    found = differences(doc_sets(load_doc()), file_sets(raw))
    assert len(found) == 1 and "test_run_reason" in found[0] and "planted_reason" in found[0]

    line = next(row for row in load_doc().splitlines() if row.startswith("| `test_run_reason` |"))
    planted = load_doc().replace(line, line.rstrip(" |") + ", `planted_reason` |")
    found = differences(doc_sets(planted), file_sets(load_file()))
    assert len(found) == 1 and "test_run_reason" in found[0] and "planted_reason" in found[0]


def test_a_removed_step_id_in_the_doc_is_found():
    planted = "\n".join(
        row for row in load_doc().splitlines() if not row.startswith("| `wait` |")
    )
    found = differences(doc_sets(planted), file_sets(load_file()))
    assert len(found) == 1 and "wait" in found[0] and "only in the file" in found[0]


def test_a_planted_extra_step_id_and_field_in_the_file_are_found():
    raw = copy.deepcopy(load_file())
    raw["steps"]["planted_step"] = {"started": {}, "ended": {}}
    raw["steps"]["wait"]["ended"]["planted_field"] = {}
    found = differences(doc_sets(load_doc()), file_sets(raw))
    assert len(found) == 2
    assert any("planted_step" in item for item in found)
    assert any("wait.ended.planted_field" in item for item in found)


def test_a_changed_field_spec_in_the_doc_is_found():
    planted = load_doc().replace("`args_head` (≤120)", "`args_head`")
    found = differences(doc_sets(planted), file_sets(load_file()))
    assert len(found) == 1 and "tool.called.args_head" in found[0]
