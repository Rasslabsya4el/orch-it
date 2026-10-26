"""The roles settings (#399 decision 5, row 4; the brain's answer on prices): each role's model,
effort and fallback model live in one packaged file, `roles render` writes them into the agent
files' frontmatter, and every model named there, primary or fallback, has a full row in the price
table."""

import json
from pathlib import Path

import pytest

from orch_it.events import pricing, schema
from orch_it.roles import RoleSet, RolesError, drift, render

ROOT = Path(__file__).resolve().parents[1]

# decision 1 and decision 2: the roles the layer keeps; decision 5: their defaults
DEFAULTS = {
    "brain": ("claude-opus-5-5", "high"),
    "orch": ("claude-sonnet-5-5", "high"),
    "worker": ("claude-haiku-5-5", "high"),
    "worker-strong": ("claude-sonnet-5-5", "high"),
    "reviewer-standards": ("claude-haiku-5-5", "high"),
    "reviewer-spec": ("claude-sonnet-5-5", "high"),
    "reviewer-edge": ("claude-sonnet-5-5", "high"),
    "researcher-find": ("claude-haiku-5-5", None),
    "researcher-how": ("claude-sonnet-5-5", None),
    "researcher-survey": ("claude-sonnet-5-5", None),
    "external-researcher": ("claude-sonnet-5-5", None),
}


def settings_file(tmp_path, roles: dict, name="roles.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"roles": roles}), "utf-8")
    return path


def agent(work_tree: Path, role: str, frontmatter: str, body: str = "The job.\n") -> Path:
    path = work_tree / ".claude" / "agents" / f"{role}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"---\n{frontmatter}---\n\n{body}".encode())
    return path


def test_the_packaged_settings_hold_the_defaults_of_decision_5():
    roles = RoleSet.load()
    assert {name: (role.model, role.effort) for name, role in roles.roles.items()} == DEFAULTS


def test_every_role_has_a_fallback_and_the_opus_roles_fall_back_to_opus_4_8():
    roles = RoleSet.load()
    assert all(role.fallback and role.fallback != role.model for role in roles.roles.values())
    assert roles.roles["brain"].fallback == "claude-opus-4-8"


def test_every_model_the_settings_name_has_a_full_price_row():
    assert RoleSet.load().unpriced() == []


def test_a_planted_model_missing_from_the_price_table_fails_the_settings(tmp_path):
    roles = RoleSet.load(settings_file(tmp_path, {
        "worker": {"model": "claude-haiku-5-5", "effort": "high", "fallback": "claude-planted-9"},
        "brain": {"model": "claude-planted-10", "effort": "high", "fallback": "claude-opus-4-8"}}))
    assert roles.unpriced() == ["roles.brain.model: claude-planted-10 has no row in the price table",
                                "roles.worker.fallback: claude-planted-9 has no row in the price table"]


def test_a_model_missing_from_the_price_table_is_found():
    assert pricing.missing_rates("claude-planted-9") == ["model"]
    assert pricing.missing_rates("claude-haiku-5-5") == []  # tiers: both tiers carry every kind


def test_every_role_is_a_role_of_the_event_store():
    assert set(RoleSet.load().roles) <= schema.codes_of("envelope", "role")


@pytest.mark.parametrize("roles, field", [
    ({"worker": {"model": "", "fallback": "claude-haiku-5-5"}}, "roles.worker.model"),
    ({"worker": {"model": "haiku", "fallback": "claude-haiku-5-5"}}, "roles.worker.model"),
    ({"worker": {"model": "claude-haiku-5-5", "effort": "extreme", "fallback": "claude-sonnet-5"}},
     "roles.worker.effort"),
    ({"worker": {"model": "claude-haiku-5-5"}}, "roles.worker.fallback"),
    ({"worker": {"model": "claude-haiku-5-5", "fallback": "claude-haiku-5-5"}}, "roles.worker.fallback"),
    ({"worker": {"model": "claude-haiku-5-5", "fallback": "claude-sonnet-5", "modle": "x"}},
     "roles.worker.modle"),
    ({"Worker!": {"model": "claude-haiku-5-5", "fallback": "claude-sonnet-5"}}, "roles.Worker!"),
    ({}, "roles"),
])
def test_a_bad_setting_is_refused_naming_the_field(tmp_path, roles, field):
    with pytest.raises(RolesError) as refused:
        RoleSet.load(settings_file(tmp_path, roles))
    assert refused.value.code == "bad_setting"
    assert refused.value.field == field


@pytest.mark.parametrize("text", ["", "{", "[]", '{"roles": []}', "\xff"])
def test_an_unreadable_settings_file_is_refused(tmp_path, text):
    path = tmp_path / "roles.json"
    path.write_bytes(text.encode("latin-1"))
    with pytest.raises(RolesError) as refused:
        RoleSet.load(path)
    assert refused.value.code in ("settings_unreadable", "bad_setting")


def test_a_missing_settings_file_is_refused(tmp_path):
    with pytest.raises(RolesError) as refused:
        RoleSet.load(tmp_path / "none.json")
    assert refused.value.code == "settings_unreadable"


def two_roles(model_of_worker="claude-haiku-5-5"):
    return {"worker": {"model": model_of_worker, "effort": "high", "fallback": "claude-haiku-4-5-20251001"},
            "reviewer-spec": {"model": "claude-sonnet-5-5", "effort": "high", "fallback": "claude-sonnet-5"}}


def test_render_after_a_model_change_changes_only_that_file_s_frontmatter(tmp_path):
    tree = tmp_path / "tree"
    worker = agent(tree, "worker", "name: worker\ndescription: d\nmodel: claude-haiku-5-5\neffort: high\ntools: Read\n",
                   body="Its model is a setting.\n")
    spec = agent(tree, "reviewer-spec", "name: reviewer-spec\nmodel: claude-sonnet-5-5\neffort: high\n")
    spec_before = spec.read_bytes()
    roles = RoleSet.load(settings_file(tmp_path, two_roles("claude-sonnet-5-5")))
    assert render(tree, roles) == [(".claude/agents/reviewer-spec.md", "same"), (".claude/agents/worker.md", "changed")]
    assert worker.read_bytes() == (b"---\nname: worker\ndescription: d\nmodel: claude-sonnet-5-5\neffort: high\n"
                                   b"tools: Read\n---\n\nIts model is a setting.\n")
    assert spec.read_bytes() == spec_before
    assert drift(tree, roles) == []
    assert render(tree, roles) == [(".claude/agents/reviewer-spec.md", "same"), (".claude/agents/worker.md", "same")]


def test_render_adds_missing_lines_after_the_name_and_drops_an_effort_the_settings_leave_out(tmp_path):
    tree = tmp_path / "tree"
    worker = agent(tree, "worker", "name: worker\ndescription: d\n")
    find = agent(tree, "researcher-find", "name: researcher-find\nmodel: claude-haiku-5-5\neffort: low\n")
    roles = RoleSet.load(settings_file(tmp_path, two_roles() | {
        "researcher-find": {"model": "claude-haiku-5-5", "fallback": "claude-haiku-4-5-20251001"}}))
    render(tree, roles)
    assert worker.read_text("utf-8").startswith("---\nname: worker\nmodel: claude-haiku-5-5\neffort: high\ndescription: d\n---")
    assert find.read_text("utf-8").startswith("---\nname: researcher-find\nmodel: claude-haiku-5-5\n---")


def test_a_hand_edit_of_a_frontmatter_model_is_drift(tmp_path):
    tree = tmp_path / "tree"
    agent(tree, "worker", "name: worker\nmodel: claude-opus-4-8\neffort: high\n")
    agent(tree, "reviewer-spec", "name: reviewer-spec\nmodel: claude-sonnet-5-5\neffort: max\n")
    roles = RoleSet.load(settings_file(tmp_path, two_roles()))
    assert drift(tree, roles) == [
        ".claude/agents/reviewer-spec.md: effort is max, the roles settings say high",
        ".claude/agents/worker.md: model is claude-opus-4-8, the roles settings say claude-haiku-5-5",
    ]


def test_drift_names_a_file_of_no_role_and_a_file_with_no_frontmatter(tmp_path):
    tree = tmp_path / "tree"
    agent(tree, "unblocker", "name: unblocker\nmodel: claude-sonnet-5-5\n")
    (tree / ".claude" / "agents" / "worker.md").write_text("No frontmatter.\n", "utf-8")
    roles = RoleSet.load(settings_file(tmp_path, two_roles()))
    assert drift(tree, roles) == [".claude/agents/unblocker.md: no role `unblocker` in the roles settings",
                                  ".claude/agents/worker.md: no frontmatter"]
    with pytest.raises(RolesError) as refused:
        render(tree, roles)
    assert (refused.value.code, refused.value.field) == ("frontmatter_missing", ".claude/agents/worker.md")


def test_render_writes_nothing_when_one_file_cannot_be_rendered(tmp_path):
    tree = tmp_path / "tree"
    spec = agent(tree, "reviewer-spec", "name: reviewer-spec\nmodel: claude-haiku-5-5\n")
    (tree / ".claude" / "agents" / "worker.md").write_text("---\nname: worker\nnever closed\n", "utf-8")
    before = spec.read_bytes()
    with pytest.raises(RolesError):
        render(tree, RoleSet.load(settings_file(tmp_path, two_roles())))
    assert spec.read_bytes() == before


def test_an_agent_file_that_is_not_utf8_is_named_by_drift_and_refused_by_render(tmp_path):
    tree = tmp_path / "tree"
    path = tree / ".claude" / "agents" / "worker.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"---\nname: worker\nmodel: claude-haiku-5-5\n---\n\xff\xfe bad\n")
    roles = RoleSet.load(settings_file(tmp_path, two_roles()))
    assert drift(tree, roles) == [".claude/agents/worker.md: not UTF-8 text"]
    with pytest.raises(RolesError) as refused:
        render(tree, roles)
    assert (refused.value.code, refused.value.field) == ("agent_unreadable", ".claude/agents/worker.md")


def test_a_folder_named_like_an_agent_file_is_no_agent_file(tmp_path):
    tree = tmp_path / "tree"
    (tree / ".claude" / "agents" / "worker.md").mkdir(parents=True)
    roles = RoleSet.load(settings_file(tmp_path, two_roles()))
    assert drift(tree, roles) == []
    assert render(tree, roles) == []


def test_a_settings_file_nested_too_deep_or_naming_a_role_twice_is_refused(tmp_path):
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 200_000, "utf-8")
    with pytest.raises(RolesError) as refused:
        RoleSet.load(deep)
    assert refused.value.code == "settings_unreadable"
    twice = tmp_path / "twice.json"
    role = '{"model": "claude-haiku-5-5", "fallback": "claude-sonnet-5"}'
    twice.write_text(f'{{"roles": {{"worker": {role}, "worker": {role}}}}}', "utf-8")
    with pytest.raises(RolesError) as refused:
        RoleSet.load(twice)
    assert (refused.value.code, refused.value.field) == ("bad_setting", "roles.worker")


def test_a_right_line_with_quotes_or_a_comment_is_left_as_written(tmp_path):
    tree = tmp_path / "tree"
    path = agent(tree, "worker", "name: worker\nmodel: 'claude-haiku-5-5' # pinned by the settings\neffort: \"high\"\n")
    before = path.read_bytes()
    assert render(tree, RoleSet.load(settings_file(tmp_path, two_roles()))) == [(".claude/agents/worker.md", "same")]
    assert path.read_bytes() == before


def test_a_model_line_given_twice_is_drift(tmp_path):
    tree = tmp_path / "tree"
    agent(tree, "worker", "name: worker\nmodel: claude-haiku-5-5\neffort: high\nmodel: claude-opus-4-8\n")
    assert drift(tree, RoleSet.load(settings_file(tmp_path, two_roles()))) == [
        ".claude/agents/worker.md: model is given 2 times"]


def test_a_write_that_fails_leaves_no_temporary_file_and_is_a_coded_error(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    agent(tree, "worker", "name: worker\nmodel: claude-opus-4-8\n")

    def refuse(*_):
        raise PermissionError("locked")

    monkeypatch.setattr("orch_it.roles.os.replace", refuse)
    with pytest.raises(RolesError) as refused:
        render(tree, RoleSet.load(settings_file(tmp_path, two_roles())))
    assert (refused.value.code, refused.value.field) == ("agent_unwritable", ".claude/agents/worker.md")
    assert sorted(p.name for p in (tree / ".claude" / "agents").iterdir()) == ["worker.md"]


def test_a_work_tree_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(RolesError) as refused:
        render(tmp_path / "none", RoleSet.load(settings_file(tmp_path, two_roles())))
    assert refused.value.code == "work_tree_missing"


def test_renders_racing_in_threads_all_end_with_the_settings(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    tree = tmp_path / "tree"
    path = agent(tree, "worker", "name: worker\nmodel: claude-opus-4-8\n")
    roles = RoleSet.load(settings_file(tmp_path, two_roles()))
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda _: render(tree, roles), range(6)))
    assert drift(tree, roles) == []
    assert [p.name for p in path.parent.iterdir()] == ["worker.md"]
    assert path.read_bytes().count(b"model:") == 1


def test_render_keeps_the_line_ends_of_a_crlf_file(tmp_path):
    tree = tmp_path / "tree"
    path = tree / ".claude" / "agents" / "worker.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"---\r\nname: worker\r\nmodel: claude-opus-4-8\r\n---\r\nBody.\r\n")
    render(tree, RoleSet.load(settings_file(tmp_path, two_roles())))
    assert path.read_bytes() == b"---\r\nname: worker\r\nmodel: claude-haiku-5-5\r\neffort: high\r\n---\r\nBody.\r\n"


def test_roles_render_on_the_command_line(tmp_path, capsys):
    from orch_it.cli import main

    tree = tmp_path / "tree"
    worker = agent(tree, "worker", "name: worker\nmodel: claude-haiku-5-5\neffort: high\n")
    assert main(["roles", "render", "--settings", str(settings_file(tmp_path, two_roles("claude-sonnet-5-5"))),
                 "--work-tree", str(tree)]) == 0
    assert capsys.readouterr().out.split() == ["file", "action", ".claude/agents/worker.md", "changed"]
    assert "model: claude-sonnet-5-5\n" in worker.read_text("utf-8")
    assert main(["roles", "render", "--settings", str(tmp_path / "none.json"), "--work-tree", str(tree)]) == 2
    assert capsys.readouterr().err.startswith("error: settings_unreadable")


def test_the_repo_s_agent_files_agree_with_the_roles_settings():
    assert drift(ROOT, RoleSet.load()) == []
