"""Spawn (#399 decision 3, row 3): `python -m orch_it spawn <role> --issue N [--diff F] [--unit ID]` builds a
subagent's prompt from the role's input list: it fetches the issue to a file, writes the diff file, names
the worktree and the scratchpad, writes the `subagent` step with `prompt_has`, and prints the prompt. A
missing input is refused with its name, so a review without its diff never starts (#380: two of five spec
reviews said "0 findings" with no diff)."""

import json
import subprocess
from pathlib import Path

import pytest

from orch_it.events.store import Store
from orch_it.roles import RoleSet
from orch_it.roles.spawn import SpawnRefused, SpawnRequest, spawn
from repo_templates import empty_repo

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# #380's per-role findings: the inputs each spawned role needs (decision 1, decision 3)
INPUTS = {
    "orch": {"issue", "worktree", "scratchpad"},
    "worker": {"issue", "unit", "worktree", "scratchpad"},
    "worker-strong": {"issue", "unit", "worktree", "scratchpad"},
    "reviewer-standards": {"issue", "diff", "worktree", "scratchpad"},
    "reviewer-spec": {"issue", "diff", "worktree", "scratchpad"},
    "reviewer-edge": {"issue", "diff", "worktree", "scratchpad"},
    "researcher-find": {"issue", "worktree", "scratchpad"},
    "researcher-how": {"issue", "worktree", "scratchpad"},
    "researcher-survey": {"issue", "worktree", "scratchpad"},
    "external-researcher": {"issue", "worktree", "scratchpad"},
}
LINE_OF = {"issue": "Issue file:", "diff": "Diff file:", "unit": "Unit:", "worktree": "Worktree:",
           "scratchpad": "Scratchpad:"}
PLAN = "Plan.\n\n| unit | what | its test |\n|---|---|---|\n| u1 | the parser | `test_parser_reads_x` |\n"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def fake_gh(calls: list, *, body="The ticket's spec.", plan=PLAN):
    def gh(args):
        calls.append(args)
        assert args[:3] == ["issue", "view", "7"]
        return json.dumps({"number": 7, "title": "Parse x", "url": "https://github.com/o/r/issues/7", "body": body,
                           "labels": [{"name": "ready-for-agent"}], "state": "OPEN",
                           "comments": [{"author": {"login": "orch"}, "createdAt": "2026-10-26T05:00:00Z",
                                         "body": plan}]})
    return gh


def git_tree(path: Path, templates) -> Path:
    """A git work tree at `path`: a copy of the empty template repository, no `git init` of its own."""

    tree, _ = templates.copy("spawn: empty repo", empty_repo, path, part="repo")
    return tree


@pytest.fixture
def world(tmp_path, templates):
    diff = tmp_path / "unit.diff"
    diff.write_text("diff --git a/x.py b/x.py\n+print(1)\n", "utf-8")
    return {"store": Store.open(tmp_path / "store"), "worktree": git_tree(tmp_path / "tree", templates), "diff": diff,
            "scratch": tmp_path / "scratch", "plain": tmp_path}


def full(role: str, world) -> SpawnRequest:
    inputs = INPUTS[role]
    return SpawnRequest(issue=7, repo="o/r", worktree=world["worktree"], scratchpad=world["scratch"],
                        diff=str(world["diff"]) if "diff" in inputs else None,
                        unit="u1" if "unit" in inputs else None)


def subagent_steps(store: Store) -> list[dict]:
    rows = store.query("select role, ticket, run, detail from events where kind = 'step.started' and step = 'subagent'"
                       " order by at").rows
    return [{"role": r[0], "ticket": r[1], "run": r[2], **json.loads(r[3])} for r in rows]


@pytest.mark.parametrize("role", sorted(INPUTS))
def test_a_spawn_with_every_input_names_each_and_writes_the_subagent_step(role, world):
    done = spawn(role, full(role, world), roles=RoleSet.load(), store=world["store"], gh=fake_gh([]))
    lines = done.prompt.splitlines()
    for name in INPUTS[role]:
        assert any(line.startswith(LINE_OF[name]) for line in lines), (role, name)
    assert done.files["issue"].read_text("utf-8").startswith("# #7 Parse x")
    assert f"Spawn: {done.run}" in lines
    [step] = subagent_steps(world["store"])
    assert (step["role"], step["ticket"], step["run"]) == (role, 7, done.run)
    assert sorted(step["prompt_has"]) == sorted(INPUTS[role])
    assert step["prompt_chars"] == len(done.prompt)
    assert step["agent_file"] == f".claude/agents/{role}.md"
    assert step["model"] == RoleSet.load().roles[role].model


def missing(role: str, name: str, world) -> SpawnRequest:
    request = full(role, world)
    if name == "worktree":  # no worktree given, and the folder it runs in is no git work tree
        return request.but(worktree=None, cwd=world["plain"])
    if name == "scratchpad":  # one inside the work tree would put the issue and diff files in a commit
        return request.but(scratchpad=world["worktree"] / "scratch")
    return request.but(**{name: None})


CASES = [(role, name) for role in sorted(INPUTS) for name in sorted(INPUTS[role])]


@pytest.mark.parametrize("role, name", CASES, ids=[f"{r}-{n}" for r, n in CASES])
def test_each_required_input_missing_in_turn_is_refused_naming_it(role, name, world):
    with pytest.raises(SpawnRefused) as refused:
        spawn(role, missing(role, name, world), roles=RoleSet.load(), store=world["store"], gh=fake_gh([]))
    assert refused.value.input == name
    [step] = subagent_steps(world["store"])
    assert name not in step["prompt_has"]
    ended = world["store"].query("select outcome, detail from events where kind = 'step.ended' and step = 'subagent'").rows
    assert [(outcome, json.loads(detail)["refused_input"]) for outcome, detail in ended] == [("refused", name)]
    assert json.loads(ended[0][1])["refused"] == refused.value.code


def test_every_role_but_the_brain_is_spawned_and_the_table_covers_them():
    roles = RoleSet.load()
    assert {name for name, role in roles.roles.items() if role.inputs} == set(INPUTS)
    assert {name: set(roles.roles[name].inputs) for name in INPUTS} == INPUTS
    assert len(CASES) == sum(len(v) for v in INPUTS.values()) == 35


def refusal(role: str, request: SpawnRequest, world, gh=None) -> tuple[str, str]:
    with pytest.raises(SpawnRefused) as refused:
        spawn(role, request, roles=RoleSet.load(), store=world["store"], gh=gh or fake_gh([]))
    return refused.value.code, refused.value.input


def test_an_input_that_cannot_be_used_is_refused_naming_it(world):
    def broken_gh(args):
        from orch_it.testruns.github import GithubError

        raise GithubError("gh_failed", "issue view 7: Could not resolve to an issue")

    empty = world["plain"] / "empty.diff"
    empty.write_text("\n", "utf-8")
    worker, spec = full("worker", world), full("reviewer-spec", world)
    assert refusal("worker", worker, world, gh=broken_gh) == ("issue_unreadable", "issue")
    assert refusal("worker", worker, world, gh=lambda args: "[]") == ("issue_unreadable", "issue")
    assert refusal("worker", worker.but(unit="u9"), world) == ("unit_not_found", "unit")
    assert refusal("reviewer-spec", spec.but(diff=str(empty)), world) == ("diff_empty", "diff")
    assert refusal("reviewer-spec", spec.but(diff="no/such.diff"), world) == ("diff_unreadable", "diff")
    assert refusal("reviewer-spec", spec.but(diff="--output=x"), world) == ("diff_unreadable", "diff")
    assert refusal("worker", worker.but(worktree=world["plain"] / "nope"), world) == ("worktree_not_git", "worktree")


def test_a_diff_given_as_a_git_range_is_written_from_the_worktree(world):
    tree = world["worktree"]
    git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    (tree / "a.txt").write_text("one\n", "utf-8")
    subprocess.run([*git, "add", "a.txt"], check=True, creationflags=NO_WINDOW)
    subprocess.run([*git, "commit", "-qm", "one"], check=True, creationflags=NO_WINDOW)
    (tree / "a.txt").write_text("two\n", "utf-8")
    subprocess.run([*git, "commit", "-qam", "two"], check=True, creationflags=NO_WINDOW)
    done = spawn("reviewer-standards", full("reviewer-standards", world).but(diff="HEAD~1...HEAD"),
                 roles=RoleSet.load(), store=world["store"], gh=fake_gh([]))
    assert b"+two" in done.files["diff"].read_bytes()


def test_the_unit_s_row_of_the_plan_is_in_the_worker_s_prompt(world):
    done = spawn("worker", full("worker", world), roles=RoleSet.load(), store=world["store"], gh=fake_gh([]))
    assert "Unit: u1, the plan's row: | u1 | the parser | `test_parser_reads_x` |" in done.prompt.splitlines()


def test_spawn_on_the_command_line_prints_the_prompt_or_the_refusal(world, monkeypatch, capsys):
    from orch_it.cli import main

    monkeypatch.setattr("orch_it.roles.spawn.default_gh", lambda: fake_gh([]))
    common = ["--store", str(world["plain"] / "store"), "spawn"]
    where = ["--issue", "7", "--repo", "o/r", "--worktree", str(world["worktree"]), "--scratchpad", str(world["scratch"])]
    assert main([*common, "worker", *where, "--unit", "u1"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("You are the worker of the orchestration layer") and "Unit: u1" in out
    assert main([*common, "reviewer-spec", *where]) == 2
    assert capsys.readouterr().err.startswith("error: input_missing: diff")
    assert main([*common, "unblocker", *where]) == 2  # the roles settings name the roles spawn takes


PLAN_HEAD = "| unit | what | its test |\n|---|---|---|\n"


@pytest.mark.parametrize("plan, unit, code", [
    (PLAN, "unit", "unit_not_found"),  # the header row is no unit
    (PLAN, "---", "unit_not_found"),  # nor the separator
    (PLAN + "| u1 | another thing | t2 |\n", "u1", "unit_ambiguous"),
    ("```\n" + PLAN + "```\n", "u1", "unit_not_found"),  # a fenced example is no plan
    (PLAN_HEAD + "| | blank id | x |\n", " ", "input_missing"),
])
def test_a_unit_is_one_data_row_of_the_plan(world, plan, unit, code):
    assert refusal("worker", full("worker", world).but(unit=unit), world, gh=fake_gh([], plan=plan)) == (code, "unit")


def test_only_a_table_headed_unit_is_a_plan(world):
    attempts = "| attempt | reason |\n|---|---|\n| u1 | red on windows |\n"  # a newer table that is no plan
    calls: list = []

    def gh(args):
        raw = json.loads(fake_gh(calls)(args))
        raw["comments"].append({"author": {"login": "orch"}, "createdAt": "2026-10-26T06:00:00Z", "body": attempts})
        return json.dumps(raw)

    done = spawn("worker", full("worker", world), roles=RoleSet.load(), store=world["store"], gh=gh)
    assert "the parser" in done.prompt and "red on windows" not in done.prompt


def test_the_newest_plan_wins_over_an_example_in_the_body(world):
    done = spawn("worker", full("worker", world), roles=RoleSet.load(), store=world["store"],
                 gh=fake_gh([], body=PLAN_HEAD + "| u1 | EXAMPLE | x |\n"))
    assert "EXAMPLE" not in done.prompt and "the parser" in done.prompt


def test_bad_values_are_refused_before_gh_runs_or_a_file_is_written(world):
    calls: list = []
    for request, code in [(full("worker", world).but(repo="o/r/extra"), "repo_unknown"),
                          (full("worker", world).but(repo="--web/x"), "repo_unknown"),
                          (full("worker", world).but(issue=0), "issue_unreadable"),
                          (full("worker", world).but(issue=2**70), "issue_unreadable")]:
        with pytest.raises(SpawnRefused) as refused:
            spawn("worker", request, roles=RoleSet.load(), store=world["store"], gh=fake_gh(calls))
        assert refused.value.code == code
    assert calls == [] and not world["scratch"].exists()


def test_paths_in_the_prompt_are_absolute_and_a_file_is_no_scratchpad(world, monkeypatch):
    monkeypatch.chdir(world["plain"])
    done = spawn("worker", full("worker", world).but(scratchpad=Path("rel")), roles=RoleSet.load(),
                 store=world["store"], gh=fake_gh([]))
    paths = [line.split(": ", 1)[1] for line in done.prompt.splitlines() if line.startswith(("Issue file", "Scratchpad",
                                                                                             "Worktree"))]
    assert paths and all(Path(p).is_absolute() for p in paths)
    (world["plain"] / "a-file").write_text("x", "utf-8")
    assert refusal("worker", full("worker", world).but(scratchpad=world["plain"] / "a-file"), world) == (
        "scratchpad_unusable", "scratchpad")


def test_a_store_that_cannot_be_written_never_hides_the_refusal(world):
    store = Store.open(world["plain"] / "s2")
    for file in store.root.iterdir():
        file.unlink()
    store.root.rmdir()
    store.root.write_text("now a file", "utf-8")
    with pytest.raises(SpawnRefused) as refused:
        spawn("worker", full("worker", world).but(unit="nope"), roles=RoleSet.load(), store=store, gh=fake_gh([]))
    assert refused.value.code == "unit_not_found"


@pytest.mark.parametrize("role, lines", [
    ("reviewer-spec", ["Report cap: 30 lines"]),
    ("researcher-how", ["Report cap: 60 lines", "Report lines owed: research"]),
    ("researcher-survey", ["Report cap: 40 lines", "Context line: 100000 tokens, then hand back with `table: <path>`"]),
])
def test_the_prompt_names_the_role_s_report_rules_from_the_settings(world, role, lines):
    done = spawn(role, full(role, world), roles=RoleSet.load(), store=world["store"], gh=fake_gh([]))
    assert set(lines) <= set(done.prompt.splitlines())


def test_a_role_with_no_report_rule_gets_no_report_line(world):
    done = spawn("worker", full("worker", world), roles=RoleSet.load(), store=world["store"], gh=fake_gh([]))
    assert not any(line.startswith(("Report cap:", "Context line:")) for line in done.prompt.splitlines())


def test_the_brain_is_no_subagent(world):
    with pytest.raises(SpawnRefused) as refused:
        spawn("brain", full("orch", world), roles=RoleSet.load(), store=world["store"], gh=fake_gh([]))
    assert refused.value.code == "role_not_spawned"
