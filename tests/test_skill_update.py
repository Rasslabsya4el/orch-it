"""The update (#397 decision 4, rows 4 to 6): `skills check` fetches his upstream, compares it with the
manifest file by file and classifies each skill; `skills report`, the daily run, keeps one issue per
upstream commit; `skills update`, run by ORCH under the owner's gh, builds the new copies and manifest on
`origin/main` without touching the work tree, pushes them to a branch and keeps one pull request per
upstream commit, labelled `needs-triage` when a copy is renamed, removed or changed in contract."""

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
    """GitHub as the update sees it: its pull requests of `skills-upstream/` branches, the open issues the daily
    report keeps, and what it was asked."""

    def __init__(self, prs=None, issues=None, fail=()):
        self.prs = list(prs or [])          # {"url", "branch", "state"}
        self.issues = list(issues or [])    # {"url", "title", "labels"}
        self.fail = set(fail)
        self.created, self.labels, self.updated, self.closed = [], [], [], []
        self.opened, self.edited, self.closed_issues = [], [], []

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

    def update_branch(self, pr):
        self._maybe_fail("update_branch")
        self.updated.append(pr)

    def close_pr(self, pr, comment):
        self._maybe_fail("close_pr")
        self.closed.append((pr, comment))

    def update_issues(self):
        return [dict(issue) for issue in self.issues]  # every state: {"url", "title", "labels", "state"}

    def create_issue(self, title, body, label):
        url = f"https://example.invalid/issues/{len(self.issues) + 1}"
        self.opened.append((title, body, label))
        self.issues.append({"url": url, "title": title, "labels": [label], "state": "OPEN"})
        return url

    def edit_issue(self, url, body):
        self.edited.append((url, body))

    def close_issue(self, url, comment):
        self.closed_issues.append((url, comment))
        for issue in self.issues:
            if issue["url"] == url:
                issue["state"] = "CLOSED"


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
    assert branch == f"skills-upstream/{up[:12]}-{head[:8]}"
    assert "| `zoom-out` | removed |" in body and "| `wizard` | added |" in body
    assert "called by no role" in body
    assert forge.labels == [(done.url, "needs-triage")]
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
    assert git(world["repo"], "branch", "--list", "skills-upstream/*") == ""


def test_an_update_with_only_text_changes_and_additions_is_a_plain_pull_request(world):
    # run under the owner's gh, its own tests start and the normal merge applies (the brain's decision on #397)
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n"),
                  "skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, done.labels, forge.labels) == ("opened", [], [])


def test_a_skill_the_layer_does_not_copy_is_listed_and_does_not_wait_for_the_brain(world):
    # the brain's word on orch-it #35: only a copy renamed, removed or changed in contract waits for it
    newer(world, {"skills/productivity/teach/SKILL.md": skill("teach", description="new")})
    forge = FakeForge()
    run_update(world, forge)
    assert forge.labels == []
    [(_, _, body)] = forge.created
    assert "| `teach` | contract_changed | not copied (no job) |" in body
    found = update.check(world["repo"], cache=world["cache"])
    assert update.render(found).splitlines()[-1] == "ready-for-agent: no copy waits for the brain"


def test_a_skill_not_copied_removed_upstream_does_not_wait_but_a_copy_does(world):
    newer(world, {"skills/productivity/teach/SKILL.md": None})
    assert not update.check(world["repo"], cache=world["cache"]).needs_triage
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne slice.\nAt seams.\n",
                                                            description="the tdd skill, now with seams")})
    assert update.check(world["repo"], cache=world["cache"]).needs_triage


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
    open_pr = {"url": "https://example.invalid/pull/9", "branch": f"skills-upstream/{up[:12]}-00000000", "state": "OPEN"}
    forge = FakeForge(prs=[open_pr])
    assert run_update(world, forge).status == "already_open"
    assert forge.created == [] and forge.updated == [open_pr["url"]]


def test_a_pr_the_brain_closed_is_not_opened_again(world):
    up = newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    forge = FakeForge(prs=[{"url": "u/7", "branch": f"skills-upstream/{up[:12]}-11111111", "state": "CLOSED"}])
    done = run_update(world, forge)
    assert (done.status, done.url, forge.created) == ("declined", "u/7", [])


def test_a_newer_upstream_commit_supersedes_the_open_pr_of_an_older_one(world):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    old = {"url": "u/3", "branch": "skills-upstream/aaaaaaaaaaaa-11111111", "state": "OPEN"}
    forge = FakeForge(prs=[old])
    done = run_update(world, forge)
    assert forge.closed == [("u/3", f"Superseded by {done.url}: his upstream moved on.")]


def test_a_branch_an_earlier_run_pushed_without_its_pr_gets_its_pr(world):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge(fail={"create_pr"})
    with pytest.raises(SkillsError):
        run_update(world, forge)
    forge.fail.clear()
    done = run_update(world, forge)
    assert done.status == "opened" and len(forge.created) == 1


def test_our_files_beside_the_copies_keep_their_names_and_modes(world):
    ours = world["repo"] / ".claude" / "skills" / "write-ticket"
    ours.mkdir()
    (ours / "SKILL.md").write_bytes(b"---\nname: write-ticket\n---\n")
    (ours / "référence.md").write_bytes("é\n".encode())
    (ours / "run.sh").write_bytes(b"#!/bin/sh\n")
    git(world["repo"], "add", "-A")
    git(world["repo"], "update-index", "--chmod=+x", ".claude/skills/write-ticket/run.sh")
    git(world["repo"], "commit", "-q", "-m", "ours")
    git(world["repo"], "push", "-q", "origin", "HEAD:main")
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    run_update(world, forge)
    [(branch, _, _)] = forge.created
    git(world["repo"], "fetch", "-q", "origin", branch)
    changed = git(world["repo"], "-c", "core.quotePath=false", "diff", "--summary", "--name-status", "origin/main",
                  "FETCH_HEAD")
    assert "write-ticket" not in changed
    assert set(changed.split()) >= {"A", ".claude/skills/wizard/SKILL.md", "M", ".claude/skills/pocock.json"}


def test_older_open_prs_are_superseded_on_every_run_that_finds_a_change(world):
    up = newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    old = {"url": "u/3", "branch": "skills-upstream/aaaaaaaaaaaa-11111111", "state": "OPEN"}
    current = {"url": "u/4", "branch": f"skills-upstream/{up[:12]}-22222222", "state": "OPEN"}
    forge = FakeForge(prs=[old, current])
    assert run_update(world, forge).status == "already_open"
    assert forge.closed == [("u/3", "Superseded by u/4: his upstream moved on.")]
    declined = FakeForge(prs=[old, dict(current, state="CLOSED")])
    assert run_update(world, declined).status == "declined"
    assert declined.closed == [("u/3", "Superseded by u/4: his upstream moved on.")]


def test_a_branch_that_cannot_be_brought_up_to_date_still_gets_its_label(world):
    up = newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    current = {"url": "u/4", "branch": f"skills-upstream/{up[:12]}-22222222", "state": "OPEN"}
    forge = FakeForge(prs=[current], fail={"update_branch"})
    with pytest.raises(SkillsError):
        run_update(world, forge)
    assert forge.labels == [("u/4", "needs-triage")]


def report(world, forge):
    return update.report(world["repo"], forge=forge, cache=world["cache"])


def test_the_daily_report_opens_one_issue_for_a_change_labelled_ready_for_an_agent(world):
    up = newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    forge = FakeForge()
    done = report(world, forge)
    [(title, body, label)] = forge.opened
    assert (done.status, title, label) == ("opened", f"His skills moved to {up[:12]}", "ready-for-agent")
    assert "| `tdd` | text_changed | copy |" in body and "python -m orch_it skills update" in body
    assert forge.created == []  # the report opens no pull request


def test_a_copy_renamed_removed_or_changed_in_contract_labels_the_issue_needs_triage(world):
    newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    forge = FakeForge()
    report(world, forge)
    [(_, _, label)] = forge.opened
    assert label == "needs-triage"


def test_a_second_run_updates_the_same_issue_and_never_opens_another(world):
    up = newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    first = report(world, forge)
    second = report(world, forge)
    assert (second.status, second.url) == ("already_open", first.url)
    assert len(forge.opened) == 1 and [e[0] for e in forge.edited] == [first.url]
    assert [i["title"] for i in forge.issues] == [f"His skills moved to {up[:12]}"]


def test_a_label_a_person_set_on_the_issue_is_left_as_it_is(world):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    report(world, forge)
    forge.issues[0]["labels"] = ["needs-triage"]  # the brain held it back
    report(world, forge)
    assert forge.issues[0]["labels"] == ["needs-triage"] and len(forge.opened) == 1


def test_an_issue_the_brain_closed_is_not_opened_again(world):
    up = newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge(issues=[{"url": "i/7", "title": f"His skills moved to {up[:12]}", "labels": [],
                               "state": "CLOSED"}])
    done = report(world, forge)
    assert (done.status, done.url, forge.opened) == ("declined", "i/7", [])


def test_the_issue_closes_itself_once_the_copies_reach_its_commit(world):
    open_issue = {"url": "i/8", "title": f"His skills moved to {world['pinned'][:12]}", "labels": [], "state": "OPEN"}
    forge = FakeForge(issues=[open_issue, {"url": "i/9", "title": "His skills moved to the moon", "labels": [],
                                           "state": "OPEN"}])
    assert report(world, forge).status == "no_change"
    assert forge.closed_issues == [("i/8", f"Done: the copies are at {world['pinned'][:12]}.")]


def test_the_body_lists_the_unchanged_skills_on_one_line(world):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    report(world, forge)
    [(_, body, _)] = forge.opened
    assert "| `wizard` | added |" in body and "| `tdd` |" not in body
    assert "same: review, tdd, teach, zoom-out" in body


def test_the_issue_label_follows_the_classes_when_his_upstream_moves_on(world):
    first = newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    old = report(world, forge).url
    second = newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    new = report(world, forge)
    assert forge.closed_issues == [(old, f"Superseded by {new.url}: his upstream moved on to {second[:12]}.")]
    assert [(i["title"], i["labels"]) for i in forge.issues if i["state"] == "OPEN"] == [
        (f"His skills moved to {second[:12]}", ["needs-triage"])]
    assert first != second


def test_no_change_opens_no_issue(world):
    forge = FakeForge()
    assert report(world, forge).status == "no_change"
    assert forge.opened == [] and forge.edited == []


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
