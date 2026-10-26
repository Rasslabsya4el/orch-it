"""The report-line hold (#399 decision 6, row 5): a SubagentStop hook reads the stopping subagent's own
transcript and holds its stop once when its report claims a skill it never called, a code unit has
no `tdd` line, a line its role owes is missing, the report is over its role's cap, or survey passed
its context line with no table named. Each verdict writes `report_line.checked`; a full report
passes. Before the hook, only workers were held, and sentences held nothing else (#381)."""

import json
from pathlib import Path

import pytest

from orch_it.events.store import Store
from orch_it.roles import RoleSet
from orch_it.roles.hold import judge, run_subagent_stop

ROLES = RoleSet.load()


def transcript(tmp_path: Path, *, skills=(), edits=(), context=1000, spawn="spawn-0001", report="done") -> Path:
    records = [{"type": "user", "message": {"role": "user", "content": f"You are the x.\nSpawn: {spawn}\n"}}]
    for skill in skills:
        records.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{skill}", "name": "Skill", "input": {"skill": skill}}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": context, "output_tokens": 5}}})
    for path in edits:
        records.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"e{path}", "name": "Edit", "input": {"file_path": path}}]}})
    records.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": report}],
                                                     "usage": {"input_tokens": 10, "cache_read_input_tokens": context,
                                                               "output_tokens": 5}}})
    path = tmp_path / "agent.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), "utf-8")
    return path


def payload(role: str, path: Path, report: str, *, active=False) -> dict:
    return {"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1", "agent_type": role,
            "agent_transcript_path": str(path), "last_assistant_message": report, "stop_hook_active": active,
            "cwd": str(path.parent)}


def lines(n: int) -> str:
    return "\n".join(f"- row {i}" for i in range(n))


# (role, what the case is, transcript keywords, report, held for)
CASES = [
    ("worker", "claims tdd with no call", {}, "Done.\ntdd: called", ["tdd"]),
    ("worker", "edits code with no tdd line", {"skills": ["tdd"], "edits": ["src/a.py"]}, "Done.", ["tdd"]),
    ("worker", "claims diagnosis with no call", {"skills": ["tdd"], "edits": ["src/a.py"]},
     "tdd: called\ndiagnosis: the cache was stale", ["diagnosis"]),
    ("worker-strong", "claims codebase-design with no call", {"skills": ["tdd"]},
     "tdd: called\ncodebase-design: called", ["codebase-design"]),
    ("worker-strong", "edits code with no tdd line", {"edits": ["src/b.py"]}, "Done.", ["tdd"]),
    ("reviewer-standards", "claims code-review with no call", {}, "0 findings\ncode-review: called", ["code-review"]),
    ("reviewer-standards", "over its cap", {}, "40 findings\n" + lines(40), ["report_cap"]),
    ("reviewer-spec", "claims code-review with no call", {}, "1 finding\ncode-review: called", ["code-review"]),
    ("reviewer-spec", "over its cap", {}, lines(31), ["report_cap"]),
    ("reviewer-edge", "claims tdd with no call", {}, "0 findings\ntdd: called", ["tdd"]),
    ("reviewer-edge", "over its cap", {}, lines(31), ["report_cap"]),
    ("researcher-find", "over its cap", {}, lines(41), ["report_cap"]),
    ("researcher-find", "claims research with no call", {}, "research: called, docs/research/a.md", ["research"]),
    ("researcher-how", "owes its research line", {"skills": ["research"]}, "The answer, with file:line.", ["research"]),
    ("researcher-how", "claims research with no call", {}, "research: called, docs/research/a.md", ["research"]),
    ("researcher-how", "over its cap", {"skills": ["research"]}, "research: called\n" + lines(60), ["report_cap"]),
    ("researcher-survey", "over its cap", {}, lines(41), ["report_cap"]),
    ("researcher-survey", "past its context line with no table", {"context": 120_000}, "Stopped at item 12.",
     ["table"]),
    ("external-researcher", "claims research with no call", {}, "research: called, docs/research/b.md", ["research"]),
]
FULL = [
    ("worker", {"skills": ["tdd"], "edits": ["src/a.py"]}, "Unit u1 done, 3 tests.\ntdd: called"),
    ("worker", {"edits": ["docs/a.md", "C:/scratch/x.py"]}, "Docs only.\ntdd: skipped (no code)"),
    ("worker-strong", {"skills": ["tdd", "codebase-design"], "edits": ["src/b.py"]},
     "tdd: called\ncodebase-design: called"),
    ("reviewer-standards", {}, "2 findings\n- one\n- two"),
    ("reviewer-spec", {}, "0 findings"),
    ("reviewer-edge", {"skills": ["tdd"]}, "1 finding, test_x fails\ntdd: called"),
    ("researcher-find", {}, lines(40)),
    ("researcher-how", {"skills": ["research"]}, "research: called, docs/research/a.md\n" + lines(10)),
    ("researcher-survey", {"context": 120_000}, "Stopped at item 12.\ntable: docs/research/table.md"),
    ("researcher-survey", {}, lines(20)),
    ("external-researcher", {}, "The bridge's report is at docs/research/b.md."),
]


@pytest.mark.parametrize("role, case, keywords, report, held", CASES, ids=[f"{c[0]}: {c[1]}" for c in CASES])
def test_a_missing_or_unbacked_line_holds_the_stop(tmp_path, role, case, keywords, report, held):
    if role == "researcher-survey" and held == ["table"]:
        (tmp_path / "docs" / "research").mkdir(parents=True)
    path = transcript(tmp_path, report=report, **keywords)
    verdict = judge(payload(role, path, report), ROLES, work_tree=tmp_path)
    assert [label for label, _ in verdict.problems] == held
    assert any(not check["accepted"] and check["label"] == held[0] for check in verdict.checks)


@pytest.mark.parametrize("role, keywords, report", FULL, ids=[f"{f[0]} {i}" for i, f in enumerate(FULL)])
def test_a_full_report_passes(tmp_path, role, keywords, report):
    if "table:" in report:
        (tmp_path / "docs" / "research").mkdir(parents=True)
        (tmp_path / "docs" / "research" / "table.md").write_text("| item | source |\n", "utf-8")
    path = transcript(tmp_path, report=report, **keywords)
    assert judge(payload(role, path, report), ROLES, work_tree=tmp_path).problems == []


def test_every_role_with_a_report_rule_has_cases_both_ways():
    held = {role for role, role_settings in ROLES.roles.items() if role_settings.report is not None}
    assert held == {c[0] for c in CASES} == {f[0] for f in FULL}
    assert "orch" not in held and "brain" not in held  # sessions, not subagents


def events(store: Store, kind: str) -> list[dict]:
    return [json.loads(d) for (d,) in store.query("select detail from events where kind = :k order by at", {"k": kind}).rows]


def test_the_hook_blocks_once_with_every_problem_and_writes_the_checks(tmp_path):
    store = Store.open(tmp_path / "store")
    report = "Done.\ndiagnosis: stale cache"
    path = transcript(tmp_path, edits=["src/a.py"], report=report)
    out, code = run_subagent_stop(json.dumps(payload("worker", path, report)), store=store, repo="o/r")
    decision = json.loads(out)
    assert code == 0 and decision["decision"] == "block"
    assert "`diagnosis:`" in decision["reason"] and "`tdd`" in decision["reason"]
    checked = events(store, "report_line.checked")
    assert {(c["label"], c["accepted"]) for c in checked} == {("diagnosis", False), ("tdd", False)}
    assert next(c for c in checked if c["label"] == "tdd")["first_edit_path"] == "src/a.py"
    # the second stop of the same subagent passes: one hold, never a loop; its end is written
    out, code = run_subagent_stop(json.dumps(payload("worker", path, report, active=True)), store=store, repo="o/r")
    assert (out, code) == ("", 0)
    [ended] = store.query("select run, detail from events where kind = 'step.ended' and step = 'subagent'").rows
    assert ended[0] == "spawn-0001" and json.loads(ended[1])["outcome"] == "failed"


def test_a_full_report_passes_and_ends_its_spawn_passed(tmp_path):
    store = Store.open(tmp_path / "store")
    report = "Unit u1 done.\ntdd: called"
    path = transcript(tmp_path, skills=["tdd"], edits=["src/a.py"], report=report)
    assert run_subagent_stop(json.dumps(payload("worker", path, report)), store=store, repo="o/r") == ("", 0)
    assert {(c["label"], c["accepted"]) for c in events(store, "report_line.checked")} == {("tdd", True)}
    [ended] = events(store, "step.ended")
    assert ended["outcome"] == "passed" and ended["report_lines_present"] == ["tdd"]


def test_survey_past_its_line_writes_context_crossed(tmp_path):
    store = Store.open(tmp_path / "store")
    report = "Stopped at item 12."
    path = transcript(tmp_path, context=150_000, report=report)
    run_subagent_stop(json.dumps(payload("researcher-survey", path, report)), store=store, repo="o/r")
    [crossed] = events(store, "context.crossed")
    assert crossed["threshold"] == "context_line" and crossed["tokens"] >= 150_000


def test_the_repo_s_settings_run_the_hold_at_every_subagent_stop():
    settings = json.loads((Path(__file__).resolve().parents[1] / ".claude" / "settings.json").read_text("utf-8"))
    commands = [h["command"] for entry in settings["hooks"]["SubagentStop"] for h in entry["hooks"]]
    assert commands == ["python -m orch_it hook subagent-stop"]


def test_the_hook_command_blocks_through_a_real_process(tmp_path):
    import subprocess
    import sys

    report = "Done.\ntdd: called"
    path = transcript(tmp_path, report=report)
    done = subprocess.run([sys.executable, "-m", "orch_it", "--store", str(tmp_path / "store"), "hook", "subagent-stop"],
                          input=json.dumps(payload("worker", path, report)).encode(), capture_output=True, timeout=120,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert done.returncode == 0
    assert json.loads(done.stdout)["decision"] == "block"


@pytest.mark.parametrize("raw", ["", "{", "[]", json.dumps({"agent_type": "worker"}),
                                 json.dumps({"agent_type": "worker", "agent_transcript_path": "/no/such.jsonl",
                                             "last_assistant_message": "tdd: called"}),
                                 json.dumps({"agent_type": "orch", "last_assistant_message": "x"}),
                                 json.dumps({"agent_type": "general-purpose", "last_assistant_message": "tdd: called"})])
def test_the_hook_never_blocks_on_what_it_cannot_judge(tmp_path, raw):
    assert run_subagent_stop(raw, store=Store.open(tmp_path / "store"), repo="o/r") == ("", 0)
