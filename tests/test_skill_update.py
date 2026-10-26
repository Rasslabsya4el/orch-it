"""The update (#397 decision 4, rows 4 to 6): `skills check` fetches his upstream, compares it with the
manifest file by file and classifies each skill; `skills update` writes the new copies and manifest on
a branch and opens one pull request listing every skill with its class, labelled `needs-triage` when a
copy is renamed, removed or changed in contract, set to merge when green otherwise."""

import json
import subprocess

import pytest

from orch_it.skills import copies, update
from orch_it.skills.copies import Manifest, SkillsError

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git(cwd, *args, check=True):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd,
                          capture_output=True, check=check, creationflags=NO_WINDOW).stdout.decode().strip()


def commit_files(root, files: dict[str, bytes | None], message):
    for path, data in files.items():
        target = root / path
        if data is None:
            git(root, "rm", "-q", "-r", path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def skill(name, body="Body.\n", description=None):
    return (f"---\nname: {name}\ndescription: {description or 'the ' + name}\n---\n\n# {name}\n\nWhat it is.\n\n"
            f"## Notes\n\n{body}").encode()


@pytest.fixture
def world(tmp_path):
    """His upstream (a local git repo), and our repo: a bare origin and a clone whose `main` holds the
    copies of his first commit."""

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    pinned = commit_files(upstream, {
        "skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne slice.\nAt seams.\n"),
        "skills/engineering/review/SKILL.md": skill("review", "Two axes.\n" * 20),
        "skills/engineering/zoom-out/SKILL.md": skill("zoom-out"),
        "skills/productivity/teach/SKILL.md": skill("teach"),
    }, "pinned")
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(origin), str(repo))
    tree = copies.read_tree(upstream, pinned)
    copies.write(repo, tree, Manifest.build(tree, upstream=str(upstream), commit=pinned,
                                            not_copied={"teach": "no job"}))
    (repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")  # as orch-it's: copies keep their bytes
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / ".claude" / "agents" / "worker.md").write_text("Call the Skill tool with `tdd`.\n", "utf-8")
    commit_files(repo, {}, "copies")
    git(repo, "push", "-q", "origin", "HEAD:main")
    return {"upstream": upstream, "pinned": pinned, "repo": repo, "origin": origin, "cache": tmp_path / "cache"}


class FakeForge:
    def __init__(self, open_prs=None):
        self.created, self.labels, self.auto, self.dispatched = [], [], [], []
        self.open_prs = open_prs or {}

    def open_pr(self, branch):
        return self.open_prs.get(branch)

    def create_pr(self, branch, title, body):
        self.created.append((branch, title, body))
        return f"https://example.invalid/pull/{len(self.created)}"

    def add_label(self, pr, label):
        self.labels.append((pr, label))

    def merge_when_green(self, pr):
        self.auto.append(pr)

    def dispatch_tests(self, branch):
        self.dispatched.append(branch)


def test_check_with_no_new_commit_is_no_change(world):
    found = update.check(world["repo"], cache=world["cache"])
    assert found.new_commit == world["pinned"]
    assert {c.cls for c in found.changes} == {"same"}
    assert update.render(found).splitlines()[-1] == "no change"


def test_check_classifies_his_newer_main_with_the_renames_git_reports(world):
    up = world["upstream"]
    commit_files(up, {
        "skills/engineering/review/SKILL.md": None,
        "skills/engineering/code-review/SKILL.md": skill("code-review", "Two axes.\n" * 20),
        "skills/engineering/zoom-out/SKILL.md": None,
        "skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n"),
        "skills/engineering/wizard/SKILL.md": skill("wizard"),
    }, "newer")
    found = update.check(world["repo"], cache=world["cache"])
    assert {(c.name, c.cls) for c in found.changes} == {
        ("code-review", "renamed"), ("zoom-out", "removed"), ("tdd", "text_changed"), ("wizard", "added"),
        ("teach", "same")}
    text = update.render(found)
    assert "renamed           code-review" in text and "copy" in text


def test_update_opens_one_pr_needing_triage_when_a_copy_is_renamed_or_removed(world):
    up = world["upstream"]
    newer = commit_files(up, {"skills/engineering/zoom-out/SKILL.md": None,
                              "skills/engineering/wizard/SKILL.md": skill("wizard")}, "newer")
    forge = FakeForge()
    done = update.update(world["repo"], forge=forge, cache=world["cache"])
    assert done.status == "opened"
    [(branch, title, body)] = forge.created
    assert branch == f"skills-update/{newer[:12]}"
    assert "| `zoom-out` | removed |" in body and "| `wizard` | added |" in body
    assert "called by no role" in body
    assert forge.labels == [(done.pr, "needs-triage")] and forge.auto == []
    # the branch on the origin holds the new copies and manifest, main is untouched
    pushed = world["repo"].parent / "pushed"
    git(world["repo"].parent, "clone", "-q", "-b", branch, str(world["origin"]), str(pushed))
    assert copies.verify(pushed) == []
    manifest = Manifest.load(pushed)
    assert manifest.commit == newer and "wizard" in manifest.skills and "zoom-out" not in manifest.skills
    assert manifest.gone == {"zoom-out": f"removed upstream at {newer[:12]}"}
    # the role file's call of tdd still holds, so the references check is green in the body
    assert "references: none outside the repo's skills" in body
    assert git(world["repo"], "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_update_with_only_text_changes_and_additions_merges_when_green(world):
    commit_files(world["upstream"], {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n"),
                                     "skills/engineering/wizard/SKILL.md": skill("wizard")}, "text")
    forge = FakeForge()
    done = update.update(world["repo"], forge=forge, cache=world["cache"], dispatch_tests=True)
    assert forge.labels == [] and forge.auto == [done.pr]
    assert forge.dispatched == [f"skills-update/{done.new_commit[:12]}"]


def test_a_not_copied_skill_changing_in_contract_also_waits_for_the_brain(world):
    commit_files(world["upstream"], {"skills/productivity/teach/SKILL.md": skill("teach", description="new")}, "c")
    forge = FakeForge()
    done = update.update(world["repo"], forge=forge, cache=world["cache"])
    assert forge.labels == [(done.pr, "needs-triage")]
    [(_, _, body)] = forge.created
    assert "| `teach` | contract_changed | not copied (no job) |" in body


def test_a_removed_copy_still_named_by_a_role_file_is_red_in_the_body(world):
    (world["repo"] / ".claude" / "agents" / "worker.md").write_text("Call the Skill tool with `zoom-out`.\n", "utf-8")
    commit_files(world["repo"], {}, "role names zoom-out")
    git(world["repo"], "push", "-q", "origin", "HEAD:main")
    commit_files(world["upstream"], {"skills/engineering/zoom-out/SKILL.md": None}, "gone")
    forge = FakeForge()
    update.update(world["repo"], forge=forge, cache=world["cache"])
    [(_, _, body)] = forge.created
    assert ".claude/agents/worker.md:1: calls `zoom-out`" in body


def test_an_open_pr_for_the_same_commit_is_not_opened_twice(world):
    newer = commit_files(world["upstream"], {"skills/engineering/wizard/SKILL.md": skill("wizard")}, "n")
    forge = FakeForge(open_prs={f"skills-update/{newer[:12]}": "https://example.invalid/pull/9"})
    done = update.update(world["repo"], forge=forge, cache=world["cache"])
    assert (done.status, done.pr, forge.created) == ("already_open", "https://example.invalid/pull/9", [])


def test_no_change_opens_nothing(world):
    forge = FakeForge()
    assert update.update(world["repo"], forge=forge, cache=world["cache"]).status == "no_change"
    assert forge.created == []


def test_a_copy_edited_by_hand_stops_the_update(world):
    (world["repo"] / ".claude" / "skills" / "tdd" / "SKILL.md").write_text("mine now\n", "utf-8")
    commit_files(world["repo"], {}, "edit")
    commit_files(world["upstream"], {"skills/engineering/wizard/SKILL.md": skill("wizard")}, "n")
    with pytest.raises(SkillsError) as e:
        update.update(world["repo"], forge=FakeForge(), cache=world["cache"])
    assert e.value.code == "copies_edited"


def test_a_dirty_work_tree_stops_the_update(world):
    (world["repo"] / "stray.txt").write_text("x", "utf-8")
    commit_files(world["upstream"], {"skills/engineering/wizard/SKILL.md": skill("wizard")}, "n")
    with pytest.raises(SkillsError) as e:
        update.update(world["repo"], forge=FakeForge(), cache=world["cache"])
    assert e.value.code == "work_tree_dirty"


def test_an_upstream_that_cannot_be_reached_is_its_own_error(world, tmp_path):
    with pytest.raises(SkillsError) as e:
        update.check(world["repo"], cache=world["cache"], upstream=str(tmp_path / "nowhere"))
    assert e.value.code == "fetch_failed"
