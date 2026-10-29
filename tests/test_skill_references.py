"""References (#397 decision 5, row 7): every role file, skill and doc of the repo names only skills
the repo holds, a copy of his or one of ours, and never in the namespaced form of his plugin. A
`renamed` or `removed` skill therefore fails its update pull request until the texts naming it are
fixed in it."""

import json
from pathlib import Path

from orch_it.skills.references import check, skill_calls

ROOT = Path(__file__).resolve().parents[1]


def repo_with(tmp_path, files: dict[str, str], copies=("tdd", "grilling", "domain-modeling"), ours=("write-ticket",),
              gone=None):
    repo = tmp_path / "repo"
    skills = repo / ".claude" / "skills"
    for name in (*copies, *ours):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", "utf-8")
    manifest = {"upstream": "u", "commit": "c",
                "skills": {n: {"path": f"skills/x/{n}", "files": {}} for n in copies},
                "not_copied": {"ask-matt": {"path": "skills/x/ask-matt", "why": "no job", "files": {}}}}
    if gone is not None:
        manifest["gone"] = gone
    (skills / "pocock.json").write_text(json.dumps(manifest), "utf-8")
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, "utf-8")
    return repo


def test_the_repo_names_no_skill_it_does_not_hold():
    assert check(ROOT) == []


def test_a_role_file_naming_a_removed_skill_is_red(tmp_path):
    repo = repo_with(tmp_path, {".claude/agents/worker.md":
                                "---\nname: worker\n---\n\nBefore the first edit, call the Skill tool with `tdd`.\n"
                                "When stuck, call the Skill tool with `zoom-out`.\n"},
                     gone={"zoom-out": "removed upstream at 1111111"})
    assert check(repo) == [".claude/agents/worker.md:6: calls `zoom-out`, which the repo holds neither as a copy nor"
                           " as ours (removed upstream at 1111111)"]


def test_a_research_record_quoting_an_old_skill_is_no_naming_but_a_look_alike_folder_is_read(tmp_path):
    said = "Then run the `finishing` skill.\n"
    repo = repo_with(tmp_path, {"docs/research/port-table.md": said, "docs/researchers/x.md": said})
    assert check(repo) == ["docs/researchers/x.md:1: names `finishing`, which the repo holds neither as a copy nor"
                           " as ours"]


def test_a_role_file_using_the_namespaced_form_is_red(tmp_path):
    repo = repo_with(tmp_path, {".claude/agents/worker.md": "Call the Skill tool with `mattpocock-skills:tdd`.\n"})
    assert check(repo) == [".claude/agents/worker.md:1: the namespaced form `mattpocock-skills:tdd`; name the copy `tdd`"]


def test_every_form_that_names_a_skill_is_read(tmp_path):
    repo = repo_with(tmp_path, {
        "docs/flow.md": "Run `/ask-matt` first.\n\nThen call the Skill tool twice, for \"grilling\" and\n\"diagnose\".\n",
        "CLAUDE.md": "The brain greets with mattpocock-skills:grilling\n",
        ".claude/agents/reviewer.md": "---\nname: reviewer\nskills:\n  - tdd\n  - review\n---\nBody.\n",
        ".claude/agents/brain.md": "---\nname: brain\nskills: grilling, old-grill\n---\nBody.\n",
        ".claude/skills/write-ticket/SKILL.md": "---\nname: write-ticket\n---\nCall the Skill tool with \"to-prd\".\n",
    }, gone={"to-prd": "renamed to to-spec upstream at 2222222"})
    assert check(repo) == [
        ".claude/agents/brain.md:3: preloads `old-grill`, which the repo holds neither as a copy nor as ours",
        ".claude/agents/reviewer.md:5: preloads `review`, which the repo holds neither as a copy nor as ours",
        ".claude/skills/write-ticket/SKILL.md:4: calls `to-prd`, which the repo holds neither as a copy nor as ours"
        " (renamed to to-spec upstream at 2222222)",
        "CLAUDE.md:1: the namespaced form `mattpocock-skills:grilling`; name the copy `grilling`",
        "docs/flow.md:1: names `/ask-matt`, his skill the repo does not copy (no job)",
        "docs/flow.md:3: calls `diagnose`, which the repo holds neither as a copy nor as ours",
    ]


def test_slash_commands_that_are_no_skill_of_his_are_left_alone(tmp_path):
    repo = repo_with(tmp_path, {"docs/a.md": "Use `/clear` or `/compact`, then the `/settings` route; see `/tdd`.\n"
                                             "A path like /tmp/ask-matt/x is no command.\n"})
    assert check(repo) == []


def test_what_is_never_read(tmp_path):
    repo = repo_with(tmp_path, {
        "tests/fixtures/roles/bad.md": "Call the Skill tool with `zoom-out`.\n",
        ".claude/worktrees/other/CLAUDE.md": "mattpocock-skills:tdd\n",
        ".venv/lib/x.md": "mattpocock-skills:tdd\n",
        "notes.txt": "Call the Skill tool with `zoom-out`.\n",
    })
    assert check(repo) == []


def test_an_unreadable_or_empty_doc_is_reported_not_skipped(tmp_path):
    repo = repo_with(tmp_path, {"docs/empty.md": ""})
    (repo / "docs" / "binary.md").write_bytes(b"\xff\xfe\x00bad")
    assert check(repo) == ["docs/binary.md: not UTF-8 text"]


def test_skill_calls_reads_his_forms():
    assert skill_calls('Call the Skill tool twice, for "grilling" and "domain-modeling".') == {"grilling", "domain-modeling"}
    assert skill_calls("calls the Skill tool with `tdd` to build the ticket; then stops.") == {"tdd"}
    assert skill_calls("Call the Skill tool with\n`codebase-design` for the vocabulary.") == {"codebase-design"}
    assert skill_calls("Use `git` here. Call the Skill tool with `tdd`. Then run `pytest`.") == {"tdd"}
    assert skill_calls("No tool named here: `tdd`.") == set()


def test_only_the_names_a_call_gives_are_read():
    assert skill_calls("Call the Skill tool with `tdd`, then run `pytest`.") == {"tdd"}
    assert skill_calls("- Call the Skill tool with `tdd`\n- Run `git`\n- Then `pytest`\n") == {"tdd"}
    assert skill_calls("| Skill tool | `tdd` |\n| runner | `pytest` |\n") == set()  # no "with" or "for"
    assert skill_calls("The Skill tool loads the skill and reads `pocock.json` for it.") == set()
    assert skill_calls('The Skill tool is "not" a shell.') == set()


def test_more_ways_a_text_names_a_skill(tmp_path):
    repo = repo_with(tmp_path, {
        ".claude/agents/a.md": "---\nname: a\nskills:\n- tdd\n- nope\n---\n",
        ".claude/agents/b.md": "---\nname: b\nskills: [\n  tdd,\n  nope-two\n]\n---\n",
        ".claude/agents/c.md": "﻿---\nname: c\nskills: [tdd] # the one it needs\n---\n",
        "docs/x.md": "Use the `zoom-out` skill here, and the `tdd` skill there. Run /ask-matt.\n",
        "docs/y.MD": "plain mattpocock-skills: prefix\n",
        "web/node_modules/pkg/README.md": "mattpocock-skills:tdd\n",
    })
    assert check(repo) == [
        ".claude/agents/a.md:5: preloads `nope`, which the repo holds neither as a copy nor as ours",
        ".claude/agents/b.md:5: preloads `nope-two`, which the repo holds neither as a copy nor as ours",
        "docs/x.md:1: names `/ask-matt`, his skill the repo does not copy (no job)",
        "docs/x.md:1: names `zoom-out`, which the repo holds neither as a copy nor as ours",
        "docs/y.MD:1: the namespaced form `mattpocock-skills:`; name the copy bare",
    ]


def test_a_broken_manifest_is_a_line_of_the_check_not_a_crash(tmp_path):
    repo = repo_with(tmp_path, {"docs/a.md": "Call the Skill tool with `tdd`.\n"})
    (repo / ".claude" / "skills" / "pocock.json").write_text("{half", "utf-8")
    [line] = check(repo)
    assert line.startswith("pocock.json: manifest_unreadable")


def test_the_manifest_keeps_its_gone_names_and_leaves_the_key_out_when_none(tmp_path):
    from orch_it.skills.copies import Manifest
    repo = repo_with(tmp_path, {}, gone={"zoom-out": "removed upstream at 1111111"})
    manifest = Manifest.load(repo)
    assert manifest.gone == {"zoom-out": "removed upstream at 1111111"}
    assert '"gone"' in manifest.text()
    manifest.gone = {}
    assert '"gone"' not in manifest.text()
