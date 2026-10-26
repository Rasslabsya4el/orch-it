"""Edge inputs of the role-text check (#399 decision 4, the edge-input review of unit B): text that is
right and must stay green, breaks in the less usual forms that must be red, and sizes it must stay
quick on."""

import time
from pathlib import Path

import pytest

from orch_it.roles.texts import check


def found(tmp_path: Path, files: dict[str, str | bytes]) -> list[tuple[str, int, str]]:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return [(f.path, f.line, f.code) for f in check(tmp_path)]


RIGHT = {
    "anchor with an underscore": {"docs/a.md": "## my_var\n\nText.\n", "docs/y.md": "See [it](a.md#my_var).\n"},
    "anchor of a heading with a link": {"docs/a.md": "## [Foo](https://x.org)\n\nText.\n",
                                        "docs/y.md": "See [it](a.md#foo).\n"},
    "setext heading": {"docs/a.md": "Title\n=====\n\nText.\n", "docs/y.md": 'See `docs/a.md` section "Title".\n'},
    "indented heading": {"docs/a.md": "  ## Foo\n\nText.\n", "docs/y.md": "See [it](a.md#foo).\n"},
    "a pointed file with a BOM": {"docs/a.md": b"\xef\xbb\xbf# Foo\n\nText.\n", "docs/y.md": "See [it](a.md#foo).\n"},
    "a long fence holding a short one": {"docs/y.md": "````\n```\nsee `docs/nope.md`\n```\n````\n"},
    "a domain in a span": {"docs/y.md": "Clone from `github.com/foo/bar.md` or `example.com/docs/`.\n"},
    "a command over two lines": {"docs/y.md": "```\npython -m orch_it \\\n  roles render\n```\n"},
    "an option's prefix": {"docs/y.md": "Run `python -m orch_it roles render --work .`.\n"},
    "`-l` inside a title": {"docs/y.md": '`gh issue create --title "fix ls -l output" --body-file b.md`\n'},
    "a role of another kind": {"docs/y.md": "Send a `system` role message.\n"},
    "a big task called high effort": {"docs/y.md": "A high effort ticket takes days.\n"},
    "a haiku": {"docs/y.md": "A Haiku 5 7 5 is short.\n"},
    "one step label in two files": {"docs/a.md": "1. **Records.** Write the notes of the day.\n",
                                    "docs/b.md": "2. **Records.** Keep the run's numbers.\n"},
    "a research record quoting the old layer": {
        "docs/research/port-table.md": "| `docs/STATE.md` | the autopilot runs on Opus 5.5 at high |\n"},
    "an ADR saying a rule again": {"docs/adr/0001-x.md": "A ticket closes only when its pull request merges green.\n",
                                   "docs/y.md": "A ticket closes only when its pull request merges green.\n"},
}


@pytest.mark.parametrize("files", RIGHT.values(), ids=RIGHT.keys())
def test_right_text_stays_green(tmp_path, files):
    assert found(tmp_path, files) == []


def test_a_folder_that_merely_starts_like_research_is_still_read(tmp_path):
    assert found(tmp_path, {"docs/researchers/x.md": "See `docs/STATE.md`.\n"}) == [
        ("docs/researchers/x.md", 1, "path_missing")]


BROKEN = {
    "a link with a title": ({"docs/y.md": '[a](nope.md "t")\n'}, "path_missing"),
    "a reference link": ({"docs/y.md": "[a]: docs/nope.md\n"}, "path_missing"),
    "a test id": ({"docs/y.md": "Run `tests/nope.py::test_a`.\n"}, "path_missing"),
    "a line and column": ({"docs/y.md": "See `docs/nope.md:12:5`.\n"}, "path_missing"),
    "Section with a capital": ({"docs/a.md": "# A\n\nText.\n", "docs/y.md": 'See `docs/a.md` Section "Nope".\n'},
                               "section_missing"),
    "a root placeholder": ({"docs/y.md": "Use `PYTHONPATH=<path>/lib`.\n"}, "path_missing"),
    "Label first": ({"docs/y.md": "Label `stuck` goes on it.\n"}, "label_unknown"),
    "labels listed": ({"docs/y.md": "Apply the `priority` and `hard` labels to it.\n"}, "label_unknown"),
    "Role first": ({"docs/y.md": "Role `ghost` takes the ticket.\n"}, "role_unknown"),
    "python options before -m": ({"docs/y.md": "Run `python -X utf8 -m orch_it labels nope`.\n"}, "command_unknown"),
    "the py launcher": ({"docs/y.md": "Run `py -3 -m orch_it labels nope`.\n"}, "command_unknown"),
    "a dated model id": ({"docs/y.md": "The default is `claude-3-5-sonnet-20241022`.\n"}, "model_literal"),
    "a model in lower case": ({"docs/y.md": "The default is opus 4.8 for the brain.\n"}, "model_literal"),
    "an effort level": ({"docs/y.md": "The brain runs with effort level high.\n"}, "effort_literal"),
    "x-high": ({"docs/y.md": "The brain runs at x-high effort.\n"}, "effort_literal"),
    "one rule twice on a line": ({"docs/a.md": "Never merge a red ticket into main without a review. " * 2 + "\n"},
                                 "rule_twice"),
    "a stub with a BOM": ({"docs/a.md": b"\xef\xbb\xbf# Title\n", "docs/y.md": "See `docs/a.md`.\n"}, "stub"),
    "a CLAUDE.md of a folder": ({"src/CLAUDE.md": "See `docs/nope.md`.\n"}, "path_missing"),
    "a removed part": ({"docs/y.md": "The autopilot answers the grill.\n"}, "part_removed"),
}


@pytest.mark.parametrize("files, code", BROKEN.values(), ids=BROKEN.keys())
def test_a_break_in_a_less_usual_form_is_red(tmp_path, files, code):
    assert [c for _, _, c in found(tmp_path, files)] == [code]


def test_a_long_run_of_spaces_in_a_heading_is_quick(tmp_path):
    started = time.monotonic()
    found(tmp_path, {"docs/a.md": "# a" + " " * 3000 + "b\n\ntext\n", "docs/y.md": "See [it](a.md#ab).\n"})
    assert time.monotonic() - started < 3


def test_one_rule_said_2000_times_is_one_finding_per_repeat_and_quick(tmp_path):
    started = time.monotonic()
    out = found(tmp_path, {"docs/big.md": "Never merge a ticket into main without a green review run.\n\n" * 2000})
    assert time.monotonic() - started < 10
    assert len(out) == 1999 and {code for _, _, code in out} == {"rule_twice"}
