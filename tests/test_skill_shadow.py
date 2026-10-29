"""A same-named personal skill (#397 decision 3, row 3): at session start a hook compares the repo's
skill names with the user's personal skills folder and writes one `skill.shadowed` event per clash;
it never blocks the session."""

import pytest
import json
import os
import subprocess
import sys
from pathlib import Path

from orch_it.events.store import Store
from orch_it.skills import shadow
from orch_it.skills.copies import NO_WINDOW
REPO = "acme-x/widgets-y"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def skill_folder(root, name, text="---\nname: x\n---\n"):
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(text, "utf-8")


def repo_with_skills(tmp_path, *names):
    repo = tmp_path / "repo"
    skills = repo / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "pocock.json").write_text(json.dumps({
        "upstream": "u", "commit": "c", "not_copied": {},
        "skills": {n: {"path": f"skills/x/{n}", "files": {}} for n in names if n != "write-ticket"}}), "utf-8")
    for name in names:
        skill_folder(skills, name, f"---\nname: {name}\n---\nrepo copy\n")
    return repo


def shadowed(store):
    return store.query("SELECT repo, role, session, skill, copy, same_text FROM events"
                       " WHERE kind = 'skill.shadowed' ORDER BY skill").rows


def test_one_clashing_personal_skill_writes_one_event_naming_it(tmp_path):
    repo = repo_with_skills(tmp_path, "tdd", "grilling", "write-ticket")
    personal = tmp_path / "personal"
    skill_folder(personal, "tdd", "---\nname: tdd\n---\nmy own tdd\n")
    skill_folder(personal, "my-notes")
    store = Store.open(tmp_path / "store")
    clashes = shadow.write_shadowed(store, repo=repo, personal=personal, repo_name=REPO, session="s-1")
    assert [c.skill for c in clashes] == ["tdd"]
    assert shadowed(store) == [(REPO, "hook", "s-1", "tdd", 1, 0)]


def test_no_clash_writes_no_event(tmp_path):
    repo = repo_with_skills(tmp_path, "tdd")
    personal = tmp_path / "personal"
    skill_folder(personal, "my-notes")
    store = Store.open(tmp_path / "store")
    assert shadow.write_shadowed(store, repo=repo, personal=personal, repo_name=REPO, session="s-1") == []
    assert shadowed(store) == []


def test_a_clash_with_a_skill_of_ours_and_the_same_text_are_told_apart(tmp_path):
    repo = repo_with_skills(tmp_path, "write-ticket")
    personal = tmp_path / "personal"
    skill_folder(personal, "write-ticket", "---\nname: write-ticket\n---\nrepo copy\n")
    store = Store.open(tmp_path / "store")
    shadow.write_shadowed(store, repo=repo, personal=personal, repo_name=REPO, session=None)
    assert shadowed(store) == [(REPO, "hook", None, "write-ticket", 0, 1)]


def test_no_personal_folder_and_no_repo_skills_are_no_clash(tmp_path):
    assert shadow.clashes(tmp_path / "none", tmp_path / "also-none") == []
    repo = repo_with_skills(tmp_path, "tdd")
    (tmp_path / "personal").mkdir()
    (tmp_path / "personal" / "tdd").write_text("a file, not a skill folder", "utf-8")
    skill_folder(tmp_path / "personal", "grilling")
    (tmp_path / "personal" / "grilling" / "SKILL.md").unlink()  # a folder with no SKILL.md is no skill
    assert shadow.clashes(repo, tmp_path / "personal") == []


def test_the_personal_folder_follows_the_claude_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert shadow.personal_folder() == tmp_path / "cfg" / "skills"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    assert shadow.personal_folder() == tmp_path / "home" / ".claude" / "skills"


def run_hook(tmp_path, stdin: bytes, cwd, env_extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR")}
    env |= {"CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"), "ORCH_IT_STORE": str(tmp_path / "store"),
            "ORCH_IT_SETTINGS": str(tmp_path / "settings.json")} | (env_extra or {})
    return subprocess.run([sys.executable, "-m", "orch_it", "skills", "shadowed"], input=stdin, cwd=cwd,
                          env=env, capture_output=True, timeout=60, creationflags=NO_WINDOW)


def git_repo(repo):
    run = {"cwd": repo, "capture_output": True, "check": True, "timeout": 60, "creationflags": NO_WINDOW}
    subprocess.run(["git", "init", "-q"], **run)
    subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"], **run)


def test_the_hook_reads_its_session_from_stdin_and_writes_the_event_silently(tmp_path):
    repo = repo_with_skills(tmp_path, "tdd")
    git_repo(repo)
    skill_folder(tmp_path / "cfg" / "skills", "tdd")
    done = run_hook(tmp_path, json.dumps({"session_id": "abc", "cwd": str(repo),
                                          "hook_event_name": "SessionStart"}).encode(), cwd=tmp_path)
    assert (done.returncode, done.stdout) == (0, b"")
    assert shadowed(Store.open(tmp_path / "store")) == [(REPO, "hook", "abc", "tdd", 1, 0)]


def test_the_hook_never_blocks_on_bad_input(tmp_path):
    repo = repo_with_skills(tmp_path, "tdd")
    git_repo(repo)
    skill_folder(tmp_path / "cfg" / "skills", "tdd")
    for stdin in (b"", b"{half", b"[]", b'{"cwd": 5}', b"\xff\xfe"):
        done = run_hook(tmp_path, stdin, cwd=repo)
        assert (done.returncode, done.stdout) == (0, b""), stdin
    # a working directory with no GitHub origin: nothing to name the repo by, still no block
    plain = repo_with_skills(tmp_path / "plain", "tdd")
    done = run_hook(tmp_path, json.dumps({"session_id": "x", "cwd": str(plain)}).encode(), cwd=plain)
    assert (done.returncode, done.stdout) == (0, b"")
    # an unusable store: the hook fails open with its error on stderr
    (tmp_path / "bad-store").write_text("a file", "utf-8")
    done = run_hook(tmp_path, json.dumps({"session_id": "x", "cwd": str(repo)}).encode(), cwd=repo,
                    env_extra={"ORCH_IT_STORE": str(tmp_path / "bad-store")})
    assert (done.returncode, done.stdout) == (0, b"")
    assert b"store_not_a_folder" in done.stderr


def test_the_repo_runs_the_hook_at_every_session_start():
    settings = json.loads((Path(__file__).resolve().parents[1] / ".claude" / "settings.json").read_text("utf-8"))
    commands = [hook["command"] for entry in settings["hooks"]["SessionStart"] for hook in entry["hooks"]]
    assert 'sh "$CLAUDE_PROJECT_DIR"/hooks/orch-it -m orch_it skills shadowed' in commands


def test_a_resumed_or_compacted_session_writes_no_new_event(tmp_path):
    repo = repo_with_skills(tmp_path, "tdd")
    git_repo(repo)
    skill_folder(tmp_path / "cfg" / "skills", "tdd")
    for source in ("startup", "resume", "compact", "clear"):
        run_hook(tmp_path, json.dumps({"session_id": "abc", "cwd": str(repo), "source": source}).encode(), cwd=repo)
    assert len(shadowed(Store.open(tmp_path / "store"))) == 2  # startup and clear


def test_a_session_standing_in_a_subfolder_reads_its_project(tmp_path):
    repo = repo_with_skills(tmp_path, "tdd")
    git_repo(repo)
    (repo / "src" / "deep").mkdir(parents=True)
    skill_folder(tmp_path / "cfg" / "skills", "tdd")
    done = run_hook(tmp_path, json.dumps({"session_id": "abc", "cwd": str(repo / "src" / "deep")}).encode(), cwd=repo)
    assert done.returncode == 0
    assert shadowed(Store.open(tmp_path / "store")) == [(REPO, "hook", "abc", "tdd", 1, 0)]


def test_letter_case_aside_and_an_unreadable_manifest_leave_copy_unknown(tmp_path):
    repo = repo_with_skills(tmp_path, "tdd")
    (repo / ".claude" / "skills" / "pocock.json").write_text("{half", "utf-8")
    personal = tmp_path / "personal"
    skill_folder(personal, "TDD")
    [clash] = shadow.clashes(repo, personal)
    assert (clash.skill, clash.copy, clash.same_text) == ("tdd", None, False)
    store = Store.open(tmp_path / "store")
    shadow.write_shadowed(store, repo=repo, personal=personal, repo_name=REPO, session=None)
    assert shadowed(store) == [(REPO, "hook", None, "tdd", None, 0)]
