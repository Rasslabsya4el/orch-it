"""The update classifier (#397 decision 4, rows 4 and 5): two upstream trees, the pinned one and his
newer one, give each skill one class: `same`, `added`, `renamed`, `removed`, `text_changed` or
`contract_changed`."""

import time

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


def test_a_skill_with_no_sections_has_its_whole_body_as_its_contract():
    # his `implement`: no heading below the title, its input said in a sentence
    old = {"skills/engineering/implement": skill("implement", "If the user passes a ticket reference, fetch it.\n"
                                                              "Commit your work to the current branch.\n")}
    new = {"skills/engineering/implement": skill("implement", "If the user passes a ticket reference or a URL, fetch it.\n"
                                                              "Commit your work to the current branch.\n")}
    [change] = classify(old, new)
    assert (change.cls, change.why) == ("contract_changed", ["body"])


def test_a_support_file_that_shapes_what_the_skill_writes_is_contract():
    for rel in ("AGENT-BRIEF.md", "OUT-OF-SCOPE.md", "HTML-REPORT.md", "triage-labels.md", "issue-tracker-github.md",
                "domain.md", "template.sh", "dependency-cruiser.config.cjs"):
        old = {"skills/x/a": skill("a", "Body.\n\n## Notes\n\nn\n", **{rel: "one\n"})}
        new = {"skills/x/a": skill("a", "Body.\n\n## Notes\n\nn\n", **{rel: "two\n"})}
        assert [(c.cls, c.why) for c in classify(old, new)] == [("contract_changed", [rel])], rel
    for rel in ("DEEPENING.md", "tests.md", "agents/openai.yaml", "CREDITS.md"):
        old = {"skills/x/a": skill("a", "Body.\n\n## Notes\n\nn\n", **{rel.replace("/", "__"): "one\n"})}
        new = {"skills/x/a": skill("a", "Body.\n\n## Notes\n\nn\n", **{rel.replace("/", "__"): "two\n"})}
        assert [c.cls for c in classify(old, new)] == ["text_changed"], rel


def test_a_byte_order_mark_before_the_frontmatter_is_read_through():
    old = {"skills/x/a": skill("a", "Body.\n\n## Notes\n\nn\n")}
    new = {"skills/x/a": {"SKILL.md": b"\xef\xbb\xbf" + old["skills/x/a"]["SKILL.md"]}}
    assert [c.cls for c in classify(old, new)] == ["text_changed"]


def test_a_heading_inside_a_code_fence_is_no_section():
    body = "Intro.\n\n## Notes\n\n```md\n## Output\nexample\n```\n\nMore.\n"
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


def one(old_body, new_body, old_extra="", new_extra="", **files):
    old = {"skills/x/a": skill("a", old_body, extra=old_extra, **files)}
    new = {"skills/x/a": skill("a", new_body, extra=new_extra, **files)}
    [change] = classify(old, new)
    return change.cls, change.why


NOTES = "Intro.\n\n## Notes\n\nn\n"


def test_a_template_file_in_a_subfolder_is_contract():
    old = {"skills/x/a": skill("a", NOTES, templates__pr="one\n")}
    new = {"skills/x/a": skill("a", NOTES, templates__pr="two\n")}
    assert [(c.cls, c.why) for c in classify(old, new)] == [("contract_changed", ["templates/pr"])]


def test_any_frontmatter_field_is_contract():
    assert one(NOTES, NOTES, new_extra="allowed-tools: Bash(rm:*)\n") == ("contract_changed", ["allowed-tools"])
    assert one(NOTES, NOTES, "model: sonnet\n", "model: opus\n") == ("contract_changed", ["model"])
    # the first of two same keys changing is a change too
    assert one(NOTES, NOTES, "user-invocable: true\nuser-invocable: false\n",
               "user-invocable: false\nuser-invocable: false\n") == ("contract_changed", ["user-invocable"])


def test_his_calls_in_other_wordings_are_read():
    for sentence in ('Call the `Skill` tool with "tdd".', 'Call the **Skill** tool with "tdd".',
                     'Call the skill tool with "tdd".', 'Call the Skill tool (e.g. "tdd") first.'):
        assert one(NOTES, NOTES + "\n" + sentence + "\n") == ("contract_changed", ["calls +tdd"]), sentence


def test_template_blocks_in_any_case_with_attributes_or_left_open():
    for open_tag, close_tag in (("<Spec-Template>", "</Spec-Template>"), ('<spec-template kind="a">', "</spec-template>"),
                                ("<spec_template>", "</spec_template>"), ("<spec-template>", "")):
        old = NOTES + f"\n{open_tag}\none\n{close_tag}\n"
        new = NOTES + f"\n{open_tag}\ntwo\n{close_tag}\n"
        cls, why = one(old, new)
        assert cls == "contract_changed" and why[0].startswith("template "), (open_tag, why)


def test_setext_and_indented_headings_are_sections():
    old = "Intro.\n\nInputs\n------\n\nan issue number\n\n## Notes\n\nn\n"
    assert one(old, old.replace("an issue number", "an issue or a URL")) == ("contract_changed", ["section Inputs"])
    old = "Intro.\n\n   ## Inputs\n\nan issue number\n\n## Notes\n\nn\n"
    assert one(old, old.replace("an issue number", "an issue or a URL")) == ("contract_changed", ["section Inputs"])
    old = "Intro.\n\n## Options\n\none\n\n## Notes\n\nn\n"
    assert one(old, old.replace("one", "two")) == ("contract_changed", ["section Options"])


def test_a_longer_fence_holding_a_shorter_one_does_not_hide_the_headings_after_it():
    old = "Intro.\n\n````md\n```\n## Output\n```\n````\n\n## Inputs\n\none\n\n## Notes\n\nn\n"
    assert one(old, old.replace("one", "two")) == ("contract_changed", ["section Inputs"])
    inline = "Intro.\n\n```x``` is inline.\n\n## Inputs\n\none\n\n## Notes\n\nn\n"
    assert one(inline, inline.replace("one", "two")) == ("contract_changed", ["section Inputs"])


def adversarial(lines: int, width: int) -> tuple[str, str]:
    """A text of `lines` repeated lines and a heading `width` spaces wide, each with one more line added."""

    many = NOTES + "\n" + "\n".join("the Skill tool and more words" for _ in range(lines)) + "\n"
    wide = NOTES + "\n## a" + " " * width + "x\n"
    return many, wide


def least_time(clock, old: str, new: str, repeat: int) -> float:
    """The least time, on `clock`, of `repeat` reads of the change `old` -> `new`: the least of a few reads, so a
    read that waited for a core on a shared machine does not count (#421 unit 10)."""

    best = float("inf")
    for _ in range(repeat):
        start = clock()
        one(old, new)
        best = min(best, clock() - start)
    return best


def test_long_adversarial_text_is_read_in_bounded_time():
    """The bound is the growth, not a number of seconds (#421 unit 10: 5 s failed on a shared core): a read that
    is linear in the text takes about ten times as long for ten times the text (10.5 on Windows, measured), a
    quadratic one about a hundred times. Each of the two reads is bounded on its own: the long many-line text and
    the long wide heading each must stay under 30 times the same read of the text one tenth as long. Both sizes of
    a read take the least of the same number of reads."""

    long_many, long_wide = adversarial(20_000, 40_000)
    short_many, short_wide = adversarial(2_000, 4_000)
    # a many-line read is about 0.2 s at 2,000 lines: CPU time (process_time) sits far above its 15.6 ms tick
    many_long = least_time(time.process_time, long_many, long_many + "x\n", 2)
    many_short = least_time(time.process_time, short_many, short_many + "x\n", 2)
    assert many_long < 30 * many_short, (many_long, many_short)
    # a wide read is about 0.1 ms at 4,000 characters, below the process_time tick, so it is timed with perf_counter
    wide_long = least_time(time.perf_counter, long_wide, long_wide + "x\n", 20)
    wide_short = least_time(time.perf_counter, short_wide, short_wide + "x\n", 20)
    assert wide_long < 30 * wide_short, (wide_long, wide_short)


def test_a_heading_with_a_verb_of_writing_is_no_contract_section():
    # his chief-of-staff at 5beb35b: "### Write history, not the present" tells how to write, not what it returns
    old = "Intro.\n\n## Wiki\n\nKeep a wiki.\n\n### Write history, not the present\n\nDate every claim.\n"
    assert one(old, old.replace("Date every claim.", "Date every claim, cite the commit.")) == (
        "text_changed", ["SKILL.md"])
    for heading in ("4. Write", "Write the config", "Read the glossary first"):  # steps that tell what to do
        body = f"Intro.\n\n## {heading}\n\none\n\n## Notes\n\nn\n"
        assert one(body, body.replace("one", "two"))[0] == "text_changed", heading
    for heading in ("Writes", "What it reads", "Produces", "Outputs"):  # sections that say what it reads or writes
        body = f"Intro.\n\n## {heading}\n\none\n\n## Notes\n\nn\n"
        assert one(body, body.replace("one", "two"))[0] == "contract_changed", heading
