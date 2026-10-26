"""The update (#397 decision 4, rows 4 to 6): `skills check` fetches his upstream, compares it with the
manifest file by file and classifies each skill; `skills update` builds the new copies and manifest on
`origin/main` without touching the work tree, pushes them to a branch and keeps one pull request per
upstream commit, labelled `needs-triage` when a skill is renamed, removed or changed in contract, set
to merge when green otherwise."""

import subprocess

import pytest

from orch_it.skills import copies, update
from orch_it.skills.copies import Manifest, SkillsError

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git(cwd, *args, check=True):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd,
                          capture_output=True, check=check, timeout=60, creationflags=NO_WINDOW).stdout.decode().strip()


def commit_files(root, files: dict[str, bytes | None], message):
    for path, data in files.items():
        if data is None:
            git(root, "rm", "-q", "-r", path)
            continue
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
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
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@example.invalid")
    tree = copies.read_tree(upstream, pinned)
    copies.write(repo, tree, Manifest.build(tree, upstream=str(upstream), commit=pinned,
                                            not_copied={"teach": "no job"}))
    (repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")  # as orch-it's: copies keep their bytes
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / ".claude" / "agents" / "worker.md").write_text("Call the Skill tool with `tdd`.\n", "utf-8")
    commit_files(repo, {}, "copies")
    git(repo, "push", "-q", "origin", "HEAD:main")
    return {"upstream": upstream, "pinned": pinned, "repo": repo, "origin": origin, "cache": tmp_path / "cache",
            "tmp": tmp_path}


class FakeForge:
    """GitHub as the update sees it: its pull requests of `skills-update/` branches, and what it was asked."""

    def __init__(self, prs=None, fail=()):
        self.prs = list(prs or [])          # {"url", "branch", "state"}
        self.fail = set(fail)
        self.created, self.labels, self.auto, self.dispatched, self.updated, self.closed = [], [], [], [], [], []

    def _maybe_fail(self, what):
        if what in self.fail:
            raise SkillsError("forge_failed", what)

    def update_prs(self):
        return list(self.prs)

    def create_pr(self, branch, title, body):
        self._maybe_fail("create_pr")
        url = f"https://example.invalid/pull/{len(self.prs) + 1}"
        self.created.append((branch, title, body))
        self.prs.append({"url": url, "branch": branch, "state": "OPEN"})
        return url

    def add_label(self, pr, label):
        self._maybe_fail("add_label")
        self.labels.append((pr, label))

    def merge_when_green(self, pr):
        self._maybe_fail("merge_when_green")
        self.auto.append(pr)

    def update_branch(self, pr):
        self.updated.append(pr)

    def close_pr(self, pr, comment):
        self.closed.append((pr, comment))

    def dispatch_tests(self, branch):
        self.dispatched.append(branch)


def run_update(world, forge, **kwargs):
    return update.update(world["repo"], forge=forge, cache=world["cache"], **kwargs)


def pushed(world, branch):
    clone = world["tmp"] / f"pushed-{len(list(world['tmp'].iterdir()))}"
    git(world["tmp"], "clone", "-q", "-b", branch, str(world["origin"]), str(clone))
    return clone


def newer(world, files):
    return commit_files(world["upstream"], files, "newer")


def test_check_with_no_new_commit_is_no_change(world):
    found = update.check(world["repo"], cache=world["cache"])
    assert found.new_commit == world["pinned"]
    assert {c.cls for c in found.changes} == {"same"}
    assert update.render(found).splitlines()[-1] == "no change"


def test_check_classifies_his_newer_main_with_the_renames_git_reports(world):
    newer(world, {
        "skills/engineering/review/SKILL.md": None,
        "skills/engineering/code-review/SKILL.md": skill("code-review", "Two axes.\n" * 20),
        "skills/engineering/zoom-out/SKILL.md": None,
        "skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n"),
        "skills/engineering/wizard/SKILL.md": skill("wizard"),
    })
    found = update.check(world["repo"], cache=world["cache"])
    assert {(c.name, c.cls) for c in found.changes} == {
        ("code-review", "renamed"), ("zoom-out", "removed"), ("tdd", "text_changed"), ("wizard", "added"),
        ("teach", "same")}
    text = update.render(found)
    assert "renamed           code-review" in text
    assert "renamed: code-review" in text and "same: teach" in text  # the list per class


def test_update_opens_one_pr_needing_triage_and_never_touches_the_work_tree(world):
    up = newer(world, {"skills/engineering/zoom-out/SKILL.md": None,
                       "skills/engineering/wizard/SKILL.md": skill("wizard")})
    head = git(world["repo"], "rev-parse", "HEAD")
    forge = FakeForge()
    done = run_update(world, forge)
    assert done.status == "opened"
    [(branch, title, body)] = forge.created
    assert branch == f"skills-update/{up[:12]}-{head[:8]}"
    assert "| `zoom-out` | removed |" in body and "| `wizard` | added |" in body
    assert "called by no role" in body
    assert forge.labels == [(done.pr, "needs-triage")] and forge.auto == []
    clone = pushed(world, branch)
    assert copies.verify(clone) == []
    manifest = Manifest.load(clone)
    assert manifest.commit == up and "wizard" in manifest.skills and "zoom-out" not in manifest.skills
    assert manifest.gone == {"zoom-out": f"removed upstream at {up[:12]}"}
    assert git(clone, "rev-parse", "HEAD~1") == head
    assert "references: none outside the repo's skills" in body
    # the work tree: same branch, same commit, no change, no new local branch
    assert git(world["repo"], "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(world["repo"], "rev-parse", "HEAD") == head
    assert git(world["repo"], "status", "--porcelain", "--untracked-files=all") == ""
    assert git(world["repo"], "branch", "--list", "skills-update/*") == ""


def test_update_with_only_text_changes_and_additions_merges_when_green_after_its_tests_start(world):
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n"),
                  "skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge(fail={"merge_when_green"})
    with pytest.raises(SkillsError) as e:
        run_update(world, forge, dispatch_tests=True)
    assert e.value.code == "forge_failed"
    [(branch, _, _)] = forge.created
    assert forge.dispatched == [branch] and forge.labels == []  # the tests started even so
    forge.fail.clear()
    done = run_update(world, forge, dispatch_tests=True)  # the next run finishes the job
    assert done.status == "already_open" and forge.auto == [done.pr]


def test_a_not_copied_skill_changing_in_contract_also_waits_for_the_brain(world):
    newer(world, {"skills/productivity/teach/SKILL.md": skill("teach", description="new")})
    forge = FakeForge()
    done = run_update(world, forge)
    assert forge.labels == [(done.pr, "needs-triage")]
    [(_, _, body)] = forge.created
    assert "| `teach` | contract_changed | not copied (no job) |" in body


def test_a_removed_copy_still_named_by_a_role_file_is_red_in_the_body(world):
    (world["repo"] / ".claude" / "agents" / "worker.md").write_text("Call the Skill tool with `zoom-out`.\n", "utf-8")
    commit_files(world["repo"], {}, "role names zoom-out")
    git(world["repo"], "push", "-q", "origin", "HEAD:main")
    newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    forge = FakeForge()
    run_update(world, forge)
    [(_, _, body)] = forge.created
    assert ".claude/agents/worker.md:1: calls `zoom-out`" in body


def test_an_open_pr_for_the_same_upstream_commit_is_brought_up_to_date_not_opened_again(world):
    up = newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    open_pr = {"url": "https://example.invalid/pull/9", "branch": f"skills-update/{up[:12]}-00000000", "state": "OPEN"}
    forge = FakeForge(prs=[open_pr], fail={"merge_when_green"})
    with pytest.raises(SkillsError):
        run_update(world, forge, dispatch_tests=True)
    assert forge.created == [] and forge.updated == [open_pr["url"]] and forge.dispatched == [open_pr["branch"]]


def test_a_pr_the_brain_closed_is_not_opened_again(world):
    up = newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    forge = FakeForge(prs=[{"url": "u/7", "branch": f"skills-update/{up[:12]}-11111111", "state": "CLOSED"}])
    done = run_update(world, forge)
    assert (done.status, done.pr, forge.created) == ("declined", "u/7", [])


def test_a_newer_upstream_commit_supersedes_the_open_pr_of_an_older_one(world):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    old = {"url": "u/3", "branch": "skills-update/aaaaaaaaaaaa-11111111", "state": "OPEN"}
    forge = FakeForge(prs=[old])
    done = run_update(world, forge)
    assert forge.closed == [("u/3", f"Superseded by {done.pr}: his upstream moved on.")]


def test_a_branch_an_earlier_run_pushed_without_its_pr_gets_its_pr(world):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge(fail={"create_pr"})
    with pytest.raises(SkillsError):
        run_update(world, forge)
    forge.fail.clear()
    done = run_update(world, forge)
    assert done.status == "opened" and len(forge.created) == 1


def test_the_update_starts_from_origin_main_whatever_the_work_tree_holds(world):
    git(world["repo"], "switch", "-q", "-c", "feature")
    commit_files(world["repo"], {"notes.md": b"mine\n"}, "unrelated")
    (world["repo"] / "stray.txt").write_text("not committed", "utf-8")
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    run_update(world, forge)
    [(branch, _, _)] = forge.created
    clone = pushed(world, branch)
    assert not (clone / "notes.md").exists()
    assert git(world["repo"], "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    assert (world["repo"] / "stray.txt").exists()


def test_no_change_opens_nothing(world):
    forge = FakeForge()
    assert run_update(world, forge).status == "no_change"
    assert forge.created == []


def test_a_copy_edited_on_main_stops_the_update(world):
    (world["repo"] / ".claude" / "skills" / "tdd" / "SKILL.md").write_text("mine now\n", "utf-8")
    commit_files(world["repo"], {}, "edit")
    git(world["repo"], "push", "-q", "origin", "HEAD:main")
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    with pytest.raises(SkillsError) as e:
        run_update(world, FakeForge())
    assert e.value.code == "copies_edited"


def test_an_upstream_that_cannot_be_reached_is_its_own_error(world, tmp_path):
    with pytest.raises(SkillsError) as e:
        update.check(world["repo"], cache=world["cache"], upstream=str(tmp_path / "nowhere"))
    assert e.value.code == "fetch_failed"


def test_a_broken_cache_is_built_again(world):
    update.check(world["repo"], cache=world["cache"])
    update._remove_tree(world["cache"] / "objects")
    assert update.check(world["repo"], cache=world["cache"]).new_commit == world["pinned"]
    update._remove_tree(world["cache"])
    world["cache"].write_text("a file where the cache goes", "utf-8")
    with pytest.raises(SkillsError) as e:
        update.check(world["repo"], cache=world["cache"])
    assert e.value.code == "fetch_failed"


@pytest.mark.parametrize("field, value", [("commit", "--upload-pack=touch marker"), ("upstream", "--upload-pack=x")])
def test_a_manifest_value_that_reads_as_an_option_is_refused(world, field, value):
    import json
    path = world["repo"] / ".claude" / "skills" / "pocock.json"
    data = json.loads(path.read_text("utf-8"))
    data[field] = value
    path.write_text(json.dumps(data), "utf-8")
    with pytest.raises(SkillsError) as e:
        update.check(world["repo"], cache=world["cache"])
    assert e.value.code == "bad_manifest"
    assert not (world["repo"] / "marker").exists()
