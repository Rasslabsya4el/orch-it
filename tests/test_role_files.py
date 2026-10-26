"""The role files (#399 decision 1): one fresh file per role of the roles settings, each with its job,
its inputs filled at spawn, its steps, its report and the events it writes. The brain's file is the
owner's to merge (decision 2), so the repo may lack it."""

import re
from pathlib import Path

from orch_it.roles import RoleSet

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".claude" / "agents"
ROLES = RoleSet.load()


def text_of(role: str) -> str:
    return (AGENTS / f"{role}.md").read_text("utf-8")


def test_every_role_but_the_brain_has_its_file_and_every_file_is_a_role():
    files = {p.stem for p in AGENTS.glob("*.md")}
    assert set(ROLES.roles) - {"brain"} <= files <= set(ROLES.roles)


def test_each_file_names_its_spawn_and_its_report_and_events():
    for role, settings in ROLES.roles.items():
        if role == "brain" or not (AGENTS / f"{role}.md").exists():
            continue
        text = text_of(role)
        assert f"python -m orch_it spawn {role}" in text or f"spawn {role}" in text, role
        assert re.search(r"^## (?:Report|Inputs, steps, report)\b", text, re.MULTILINE), role
        if role != "worker-strong":  # it points at the worker's sections
            assert re.search(r"^## Events\b", text, re.MULTILINE), role


def test_no_unblocker_and_no_autopilot_role():
    assert not (AGENTS / "unblocker.md").exists() and not (AGENTS / "autopilot.md").exists()


def test_orch_starts_subagents_only_through_spawn():
    assert "python -m orch_it spawn <role>" in text_of("orch")
