"""The update classifier (#397 decision 4, rows 4 and 5): two upstream trees, the pinned one and his
newer one, give each skill one class: `same`, `added`, `renamed`, `removed`, `text_changed` or
`contract_changed`."""

from orch_it.skills.classify import classify


def skill(name, body="", *, description=None, extra="", **files):
    description = description or f"the {name} skill"
    text = f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n# {name.title()}\n\n{body}"
    return {"SKILL.md": text.encode()} | {rel.replace("__", "/"): data.encode() for rel, data in files.items()}


TDD_BODY = """TDD is the red to green loop.

## What a good test is

Tests verify behaviour through public interfaces.
They survive refactors.
A good test reads like a specification.

## Rules of the loop

- Red before green.
- One slice at a time.
"""

PINNED = {
    "skills/engineering/tdd": skill("tdd", TDD_BODY, tests__x="tests.md text\n"),
    "skills/engineering/review": skill("review", "Two axes.\n"),
    "skills/engineering/zoom-out": skill("zoom-out", "Zoom out.\n"),
    "skills/engineering/prototype": skill("prototype", "Throwaway code.\n\n## Notes\n\nKeep it small.\n"),
    "skills/engineering/research": skill("research", "Primary sources.\n"),
    "skills/productivity/grilling": skill("grilling", "Rounds.\n"),
    "skills/productivity/teach": skill("teach", "Lessons.\n"),
}


def classes(changes):
    return {(c.name, c.cls) for c in changes}


def test_every_class_on_one_update_and_every_other_skill_same():
    newer = dict(PINNED)
    newer["skills/engineering/wizard"] = skill("wizard", "A script for a human.\n")          # added
    newer["skills/engineering/code-review"] = newer.pop("skills/engineering/review")       # renamed (moved whole)
    del newer["skills/engineering/zoom-out"]                                              # removed
    newer["skills/engineering/prototype"] = skill(
        "prototype", "Throwaway code.\n\n## Notes\n\nKeep it small, and keep it on a branch.\n")  # a paragraph
    newer["skills/engineering/research"] = skill(
        "research", "Primary sources.\n", description="the research skill, with a background agent")

    changes = classify(PINNED, newer)
    assert classes(changes) == {
        ("wizard", "added"), ("code-review", "renamed"), ("zoom-out", "removed"),
        ("prototype", "text_changed"), ("research", "contract_changed"),
        ("tdd", "same"), ("grilling", "same"), ("teach", "same"),
    }
    by_name = {c.name: c for c in changes}
    assert (by_name["code-review"].old_path, by_name["code-review"].new_path) == (
        "skills/engineering/review", "skills/engineering/code-review")
    assert by_name["code-review"].old_name == "review"
    assert by_name["zoom-out"].new_path is None and by_name["wizard"].old_path is None
    assert by_name["research"].why == ["description"]
    assert by_name["prototype"].why == ["SKILL.md"]


def test_outside_case_one_line_in_the_middle_with_no_name_or_heading_change_is_text_changed():
    newer = dict(PINNED)
    newer["skills/engineering/tdd"] = skill(
        "tdd", TDD_BODY.replace("They survive refactors.", "They survive any refactor."), tests__x="tests.md text\n")
    # today's name-and-heading check sees nothing: same names, same headings
    headings = lambda tree: {p: [l for l in t["SKILL.md"].decode().splitlines() if l.startswith("#")]
                             for p, t in tree.items()}
    assert headings(newer) == headings(PINNED)
    assert classes(classify(PINNED, newer)) >= {("tdd", "text_changed")}
    assert {c.name for c in classify(PINNED, newer) if c.cls != "same"} == {"tdd"}


def test_a_rename_git_reports_counts_even_when_the_text_changed_too():
    newer = dict(PINNED)
    del newer["skills/engineering/review"]
    newer["skills/engineering/code-review"] = skill("code-review", "Two axes, Standards and Spec.\n")
    plain = classes(classify(PINNED, newer))
    assert {("review", "removed"), ("code-review", "added")} <= plain
    hinted = classify(PINNED, newer, renames={"skills/engineering/review": "skills/engineering/code-review"})
    assert {("code-review", "renamed")} <= classes(hinted)
    assert "review" not in {c.name for c in hinted}


def test_a_move_to_another_group_is_a_rename_of_its_path():
    newer = dict(PINNED)
    newer["skills/in-progress/teach"] = newer.pop("skills/productivity/teach")
    [change] = [c for c in classify(PINNED, newer) if c.name == "teach"]
    assert (change.cls, change.old_path, change.new_path) == ("renamed", "skills/productivity/teach",
                                                              "skills/in-progress/teach")


def test_a_changed_support_file_is_text_changed_and_a_new_one_is_contract_changed():
    changed = dict(PINNED)
    changed["skills/engineering/tdd"] = skill("tdd", TDD_BODY, tests__x="tests.md text, longer\n")
    [tdd] = [c for c in classify(PINNED, changed) if c.name == "tdd"]
    assert (tdd.cls, tdd.why) == ("text_changed", ["tests/x"])

    grown = dict(PINNED)
    grown["skills/engineering/tdd"] = skill("tdd", TDD_BODY, tests__x="tests.md text\n", mocking="new\n")
    [tdd] = [c for c in classify(PINNED, grown) if c.name == "tdd"]
    assert (tdd.cls, tdd.why) == ("contract_changed", ["files: +mocking"])


def test_a_changed_template_or_format_file_is_contract_changed():
    old = {"skills/engineering/domain-modeling": skill("domain-modeling", "Glossary.\n", **{"ADR-FORMAT.md": "# {title}\n"})}
    new = {"skills/engineering/domain-modeling": skill("domain-modeling", "Glossary.\n",
                                                       **{"ADR-FORMAT.md": "# {title}\n\nStatus: accepted\n"})}
    [change] = classify(old, new)
    assert (change.cls, change.why) == ("contract_changed", ["ADR-FORMAT.md"])


def test_the_frontmatter_fields_of_the_contract():
    base = {"skills/engineering/implement": skill("implement", "Build it.\n")}
    for extra, why in [("disable-model-invocation: true\n", "disable-model-invocation"),
                       ("argument-hint: <ticket>\n", "argument-hint")]:
        new = {"skills/engineering/implement": skill("implement", "Build it.\n", extra=extra)}
        [change] = classify(base, new)
        assert (change.cls, change.why) == ("contract_changed", [why])
    # a value quoted where it was plain is the same value
    quoted = {"skills/engineering/implement": skill("implement", "Build it.\n", description='"the implement skill"')}
    assert [c.cls for c in classify(base, quoted)] == ["text_changed"]


def test_a_section_that_says_what_it_takes_or_returns_is_contract():
    old = {"skills/engineering/to-spec": skill(
        "to-spec", "Write a spec.\n\n## Process\n\n1. Read.\n\n<spec-template>\n## Problem\n</spec-template>\n\n"
                   "## Invocation\n\nRun with an issue number.\n")}
    template = {"skills/engineering/to-spec": skill(
        "to-spec", "Write a spec.\n\n## Process\n\n1. Read.\n\n<spec-template>\n## Problem Statement\n</spec-template>\n\n"
                   "## Invocation\n\nRun with an issue number.\n")}
    invocation = {"skills/engineering/to-spec": skill(
        "to-spec", "Write a spec.\n\n## Process\n\n1. Read.\n\n<spec-template>\n## Problem\n</spec-template>\n\n"
                   "## Invocation\n\nRun with an issue number or a URL.\n")}
    called = {"skills/engineering/to-spec": skill(
        "to-spec", "Write a spec.\n\n## Process\n\n1. Read. Call the Skill tool with \"grilling\".\n\n"
                   "<spec-template>\n## Problem\n</spec-template>\n\n## Invocation\n\nRun with an issue number.\n")}
    for new, why in [(template, "template spec-template"), (invocation, "section Invocation"),
                     (called, "calls +grilling")]:
        [change] = classify(old, new)
        assert (change.cls, change.why) == ("contract_changed", [why])


def test_a_heading_inside_a_code_fence_is_no_section():
    body = "Intro.\n\n```md\n## Output\nexample\n```\n\nMore.\n"
    old = {"skills/x/a": skill("a", body)}
    new = {"skills/x/a": skill("a", body.replace("example", "another example"))}
    assert [c.cls for c in classify(old, new)] == ["text_changed"]


def test_a_skill_md_without_frontmatter_or_unreadable_is_contract_changed_never_an_error():
    old = {"skills/x/a": skill("a", "Body.\n")}
    for broken in (b"no frontmatter at all\n", b"---\nname: a\n", b"\xff\xfe not utf-8", b""):
        [change] = classify(old, {"skills/x/a": {"SKILL.md": broken}})
        assert change.cls == "contract_changed"


def test_identical_trees_are_all_same_and_line_endings_count_as_text():
    assert {c.cls for c in classify(PINNED, PINNED)} == {"same"}
    crlf = dict(PINNED)
    crlf["skills/productivity/teach"] = {"SKILL.md": PINNED["skills/productivity/teach"]["SKILL.md"].replace(b"\n", b"\r\n")}
    [teach] = [c for c in classify(PINNED, crlf) if c.name == "teach"]
    assert teach.cls == "text_changed"


def test_two_removed_skills_with_one_moved_copy_match_once():
    old = {"skills/a/one": skill("x", "Same.\n"), "skills/b/two": skill("x", "Same.\n")}
    new = {"skills/c/three": skill("x", "Same.\n")}
    changes = classify(old, new)
    assert sorted((c.cls, c.old_path, c.new_path) for c in changes) == [
        ("removed", "skills/b/two", None), ("renamed", "skills/a/one", "skills/c/three")]
