"""The update (#397 decision 4, rows 4 to 6): `skills check` fetches his upstream, compares it with the
manifest file by file and classifies each skill; `skills report`, the daily run, keeps one issue per
upstream commit; `skills update`, run by ORCH under the owner's gh, builds the new copies and manifest on
`origin/main` without touching the work tree, pushes them to a branch and keeps one pull request per
upstream commit; a change that waits for the brain (a skill added, a copy renamed, removed or changed in
contract, or a body breaking a shape rule) opens none, its issue is labelled `needs-triage`."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orch_it import language
from orch_it.skills import copies, update
from orch_it.skills.copies import Manifest, SkillsError

pytestmark = pytest.mark.usefixtures("ticket_language_ru")  # Russian ticket bodies (#413 unit 10a)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def goal_in_english(monkeypatch):
    """The Goal of the issue's human block comes out in English under the Russian table, so the shape refuses it
    (#413 unit 10b: `update.HUMAN_KEYS` names the table key of each field; the key's text is what the test replaces)."""
    real = language.text
    goal_key = update.HUMAN_KEYS["Goal"]
    monkeypatch.setattr(language, "text", lambda key, **fields: "the copies match his upstream" if key == goal_key
                        else real(key, **fields))


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


PARTS = ("upstream", "origin.git", "repo", "cache")  # the cache: the git folder his fetches go into


@pytest.fixture(scope="session")
def template(tmp_path_factory):
    """The world of his first commit, built once per pytest worker (#421 unit 8): his upstream (a local git repo),
    a bare origin, and our clone whose `main` holds the copies. It is built at the folders `world` hands the tests,
    because the manifest and the remote hold those absolute paths; a snapshot of it is what each test copies back.
    The snapshot is never written to, so no test sees another's commits."""

    live = tmp_path_factory.getbasetemp() / "world"
    live.mkdir()
    upstream = live / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    pinned = commit_files(upstream, {
        "skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne slice.\nAt seams.\n"),
        "skills/engineering/review/SKILL.md": skill("review", "Two axes.\n" * 20),
        "skills/engineering/zoom-out/SKILL.md": skill("zoom-out"),
        "skills/productivity/teach/SKILL.md": skill("teach"),
    }, "pinned")
    origin = live / "origin.git"
    git(live, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = live / "repo"
    git(live, "clone", "-q", str(origin), str(repo))
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
    update.fetch(live / "cache", str(upstream), "main", pinned)  # the cache a first check leaves: his commit in it
    snapshot = tmp_path_factory.mktemp("snapshot")
    for name in PARTS:
        shutil.copytree(live / name, snapshot / name)
    return {"live": live, "snapshot": snapshot, "pinned": pinned}


def _put_back(live: Path, snapshot: Path) -> None:
    """Make `live` a copy of `snapshot`: what a test left there (a folder, or a file where a folder goes) is gone."""
    if live.is_dir() and not live.is_symlink():
        update._remove_tree(live)
    elif live.exists():
        live.unlink()
    shutil.copytree(snapshot, live)


@pytest.fixture
def world(tmp_path, template):
    """This test's world: his upstream, our repo with its bare origin, and the cache, put back from the snapshot before
    the test. Copying folders starts no git process; the build the snapshot replaces made one per commit step."""

    for name in PARTS:
        _put_back(template["live"] / name, template["snapshot"] / name)
    live = template["live"]
    return {"upstream": live / "upstream", "pinned": template["pinned"], "repo": live / "repo",
            "origin": live / "origin.git", "cache": live / "cache", "tmp": tmp_path}


class FakeForge:
    """GitHub as the update sees it: its pull requests of `skills-upstream/` branches, the open issues the daily
    report keeps, and what it was asked."""

    def __init__(self, prs=None, issues=None, fail=()):
        self.prs = list(prs or [])          # {"url", "branch", "state"}
        self.issues = list(issues or [])    # {"url", "title", "labels"}
        self.fail = set(fail)
        self.created, self.labels, self.updated, self.closed = [], [], [], []
        self.opened, self.edited, self.closed_issues = [], [], []
        self.marked, self.order = [], []    # the pull requests given the ready mark; update_branch and mark_ready in order

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
        self.order.append(("update_branch", pr))

    def mark_ready(self, pr):
        self._maybe_fail("mark_ready")
        self.marked.append(pr)
        self.order.append(("mark_ready", pr))

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


def test_update_with_a_removed_copy_and_a_new_skill_opens_no_pr_and_never_touches_the_work_tree(world):
    newer(world, {"skills/engineering/zoom-out/SKILL.md": None,
                  "skills/engineering/wizard/SKILL.md": skill("wizard")})
    head = git(world["repo"], "rev-parse", "HEAD")
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, done.url, forge.created, forge.labels) == ("waits_for_brain", None, [], [])
    assert git(world["origin"], "branch", "--list", "skills-upstream/*") == ""
    # the work tree: same branch, same commit, no change, no new local branch
    assert git(world["repo"], "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(world["repo"], "rev-parse", "HEAD") == head
    assert git(world["repo"], "status", "--porcelain", "--untracked-files=all") == ""
    assert git(world["repo"], "branch", "--list", "skills-upstream/*") == ""


@pytest.mark.parametrize("files", [
    {"skills/engineering/zoom-out/SKILL.md": None},                                              # removed
    {"skills/engineering/review/SKILL.md": None,
     "skills/engineering/code-review/SKILL.md": skill("code-review", "Two axes.\n" * 20)},       # renamed
    {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne slice.\nAt seams.\n",
                                              description="the tdd skill, now with seams")},    # contract_changed
], ids=["removed", "renamed", "contract_changed"])
def test_a_copy_removed_renamed_or_changed_in_contract_opens_no_pr(world, files):
    newer(world, files)
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, forge.created, forge.labels) == ("waits_for_brain", [], [])


def test_a_text_change_whose_issue_breaks_a_shape_rule_opens_no_pr(world, monkeypatch):
    goal_in_english(monkeypatch)  # the Goal in English under the Russian table: the shape refuses it
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, forge.created) == ("waits_for_brain", [])


def test_a_new_skill_that_waits_for_the_brain_supersedes_the_open_pr_of_an_older_one(world):
    up = newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    old = {"url": "u/3", "branch": "skills-upstream/aaaaaaaaaaaa-11111111", "state": "OPEN"}
    forge = FakeForge(prs=[old])
    done = run_update(world, forge)
    assert (done.status, forge.created) == ("waits_for_brain", [])
    assert forge.closed == [("u/3", f"Superseded by {up[:12]}: his upstream moved on.")]


def test_the_issue_of_a_needs_triage_change_says_the_brain_decides_and_no_pull_request_opens(world):
    newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    forge = FakeForge()
    report(world, forge)
    [(_, body, label)] = forge.opened
    assert label == "needs-triage" and "the brain decides" in body
    assert "opens no pull request" in body and "opens one pull request" not in body


def test_the_issue_of_a_text_change_says_skills_update_opens_one_pull_request(world):
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    forge = FakeForge()
    report(world, forge)
    [(_, body, label)] = forge.opened
    assert label == "ready-for-agent" and "opens one pull request" in body and "opens no pull request" not in body


def test_an_update_with_only_text_changes_is_a_plain_pull_request(world):
    # run under the owner's gh, its own tests start and the normal merge applies (the brain's decision on #397)
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, done.labels, forge.labels) == ("opened", [], [])


def test_an_update_with_a_new_skill_and_a_text_change_opens_no_pr(world):
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n"),
                  "skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, forge.created, forge.labels) == ("waits_for_brain", [], [])


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


def test_a_removed_copy_still_named_by_a_role_file_waits_for_the_brain_and_opens_no_pr(world):
    (world["repo"] / ".claude" / "agents" / "worker.md").write_text("Call the Skill tool with `zoom-out`.\n", "utf-8")
    commit_files(world["repo"], {}, "role names zoom-out")
    git(world["repo"], "push", "-q", "origin", "HEAD:main")
    newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    forge = FakeForge()
    assert (run_update(world, forge).status, forge.created) == ("waits_for_brain", [])


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
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    old = {"url": "u/3", "branch": "skills-upstream/aaaaaaaaaaaa-11111111", "state": "OPEN"}
    forge = FakeForge(prs=[old])
    done = run_update(world, forge)
    assert forge.closed == [("u/3", f"Superseded by {done.url}: his upstream moved on.")]


def test_a_branch_an_earlier_run_pushed_without_its_pr_gets_its_pr(world):
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
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
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    forge = FakeForge()
    run_update(world, forge)
    [(branch, _, _)] = forge.created
    git(world["repo"], "fetch", "-q", "origin", branch)
    changed = git(world["repo"], "-c", "core.quotePath=false", "diff", "--summary", "--name-status", "origin/main",
                  "FETCH_HEAD")
    assert "write-ticket" not in changed
    assert set(changed.split()) >= {"M", ".claude/skills/tdd/SKILL.md", ".claude/skills/pocock.json"}


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


def test_a_fresh_update_hands_its_new_pull_request_the_ready_mark(world):
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, forge.marked) == ("opened", [done.url])


def test_an_open_pr_of_the_same_upstream_commit_is_brought_up_to_date_then_joins_the_line(world):
    # the outside case: a pull request the old code opened has no ready mark (a text change: no brain)
    up = newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    open_pr = {"url": "https://example.invalid/pull/9", "branch": f"skills-upstream/{up[:12]}-00000000", "state": "OPEN"}
    forge = FakeForge(prs=[open_pr])
    assert run_update(world, forge).status == "already_open"
    assert forge.order == [("update_branch", open_pr["url"]), ("mark_ready", open_pr["url"])]


def test_a_needs_triage_change_calls_no_mark_ready(world):
    newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    forge = FakeForge()
    assert (run_update(world, forge).status, forge.created, forge.marked) == ("waits_for_brain", [], [])


def test_an_open_pr_of_a_change_that_waits_for_the_brain_joins_no_line(world):
    up = newer(world, {"skills/engineering/zoom-out/SKILL.md": None})
    open_pr = {"url": "u/8", "branch": f"skills-upstream/{up[:12]}-33333333", "state": "OPEN"}
    forge = FakeForge(prs=[open_pr])
    done = run_update(world, forge)
    assert (done.status, forge.marked, forge.labels) == ("already_open", [], [("u/8", "needs-triage")])


def test_a_declined_pr_calls_no_mark_ready(world):
    up = newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    closed = {"url": "u/7", "branch": f"skills-upstream/{up[:12]}-11111111", "state": "CLOSED"}
    forge = FakeForge(prs=[closed])
    assert (run_update(world, forge).status, forge.marked) == ("declined", [])


def test_a_refused_mark_ready_is_raised_after_the_pull_request_is_opened(world):
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    forge = FakeForge(fail={"mark_ready"})
    with pytest.raises(SkillsError):
        run_update(world, forge)
    assert len(forge.created) == 1


def test_the_closing_text_of_the_issue_is_done_text(world):
    # no newer upstream commit: the copies sit at his pinned commit, whose open issue the report closes
    commit12 = world["pinned"][:12]
    forge = FakeForge(issues=[{"url": "i/9", "title": f"His skills moved to {commit12}", "labels": [], "state": "OPEN"}])
    update.report(world["repo"], forge=forge, cache=world["cache"])
    assert forge.closed_issues == [("i/9", update.done_text(commit12))]
    assert update.done_text(commit12) == f"Done: the copies are at {commit12}."


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


def test_a_new_skill_labels_the_issue_needs_triage_not_ready_for_an_agent(world):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    report(world, forge)
    [(_, body, label)] = forge.opened
    assert label == "needs-triage" and "| `wizard` | added |" in body


def test_an_update_with_only_a_new_skill_opens_no_pull_request(world):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, forge.created, forge.labels) == ("waits_for_brain", [], [])


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
    newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
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
    update._remove_tree(world["cache"])  # no cache yet: the first fetch fails on a cache built this run
    with pytest.raises(SkillsError) as e:
        update.check(world["repo"], cache=world["cache"], upstream=str(tmp_path / "nowhere"))
    assert e.value.code == "fetch_failed"


def test_an_upstream_that_cannot_be_reached_with_a_cache_is_its_own_error(world, tmp_path):
    # the cache exists: its first fetch fails, the cache is thrown away and built again, and that fails too
    with pytest.raises(SkillsError) as e:
        update.check(world["repo"], cache=world["cache"], upstream=str(tmp_path / "nowhere"))
    assert e.value.code == "fetch_failed"


def test_a_cache_removed_whole_is_built_again_by_the_check(world):
    update._remove_tree(world["cache"])  # no HEAD, no folder: the check initialises the cache and fetches into it
    found = update.check(world["repo"], cache=world["cache"])
    assert found.new_commit == world["pinned"]
    assert {c.cls for c in found.changes} == {"same"}
    assert (world["cache"] / "HEAD").exists() and update.render(found).splitlines()[-1] == "no change"


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


# the issue is a ticket (the brain's word on #397): its body passes #398's shape rules, checked at write

def shape_of(title, body):
    from orch_it.tickets import model, shape
    return [v.message for v in shape.violations(model.parse(body, title=title))]


def test_the_issue_of_his_real_move_to_5beb35b_is_a_ticket_with_no_shape_violation():
    from pathlib import Path
    from orch_it.skills.classify import Change
    root = Path(__file__).resolve().parents[1]
    manifest = Manifest.load(root)
    manifest.commit = "49dd158d1076134a641b33efb035946536778336"
    paths = {**{n: e["path"] for n, e in manifest.skills.items()}, **{n: e["path"] for n, e in manifest.not_copied.items()}}
    changes = [Change(name, "text_changed" if name == "chief-of-staff" else "same", path, path,
                      ["SKILL.md"] if name == "chief-of-staff" else []) for name, path in sorted(paths.items())]
    found = update.Check(manifest, "5beb35b040e3de97e54fa9e386d7e42d15c295c2", {}, changes)
    title, body = update.issue(found)
    assert title == "His skills moved to 5beb35b040e3"
    assert shape_of(title, body) == []
    assert "| `chief-of-staff` | text_changed |" in body


def test_the_issue_of_a_renamed_copy_is_a_ticket_with_no_shape_violation(world):
    newer(world, {"skills/engineering/review/SKILL.md": None,
                  "skills/engineering/code-review/SKILL.md": skill("code-review", "Two axes.\n" * 20)})
    forge = FakeForge()
    report(world, forge)
    [(title, body, label)] = forge.opened
    assert shape_of(title, body) == [] and label == "needs-triage"


def test_a_bad_template_gives_an_issue_without_ready_for_agent_that_lists_its_violations(world, monkeypatch):
    goal_in_english(monkeypatch)  # the Goal in English under the Russian table: the shape refuses it
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    done = report(world, forge)
    [(_, body, label)] = forge.opened
    assert label != "ready-for-agent" and done.labels == [label]
    assert "human_field_wrong_language" in body


def test_a_body_with_violations_on_a_later_run_leaves_the_brains_labels(world, monkeypatch):
    newer(world, {"skills/engineering/wizard/SKILL.md": skill("wizard")})
    forge = FakeForge()
    report(world, forge)
    forge.issues[0]["labels"] = ["ready-for-agent"]
    goal_in_english(monkeypatch)
    assert report(world, forge).status == "already_open"
    assert forge.issues[0]["labels"] == ["ready-for-agent"] and "human_field_wrong_language" in forge.edited[-1][1]


def test_a_huge_change_stays_inside_a_github_body_and_its_shape():
    from orch_it.skills.classify import Change
    manifest = Manifest("u", "1" * 40, {}, {})
    changes = [Change(f"skill-{n}", "contract_changed", f"skills/x/skill-{n}", f"skills/x/skill-{n}",
                      ["a reason\nWeight: 99 | with `marks`"] * 3) for n in range(2000)]
    title, body, broken = update.checked_issue(update.Check(manifest, "2" * 40, {}, changes))
    assert len(body) < 65_536 and broken == [] and "1800 more changed skills" in body


@pytest.mark.parametrize("setting", ["autocrlf", "eol", "attributes"], ids=["autocrlf-true", "eol-crlf", "tree-eol-crlf"])
def test_an_update_pushes_the_upstream_blobs_whatever_the_line_ending_setting(world, monkeypatch, setting):
    # the copies are the committed bytes: git's line-ending conversion (machine config or the tree's attributes) must
    # reach neither the check of the copies nor the pull request's blobs (#420 rows 1 to 3)
    config = world["tmp"] / "gitconfig"
    config.write_text("[core]\n\tautocrlf = true\n" if setting == "autocrlf" else "", "utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))  # an isolated global config, never the developer's
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    if setting == "eol":
        git(world["repo"], "config", "core.autocrlf", "false")
        git(world["repo"], "config", "core.eol", "crlf")
    attributes = b"* text eol=crlf\n" if setting == "attributes" else None  # None: the base has no .gitattributes
    commit_files(world["repo"], {".gitattributes": attributes}, "the base's line endings")
    git(world["repo"], "push", "-q", "origin", "HEAD:main")
    up = newer(world, {"skills/engineering/tdd/SKILL.md": skill("tdd", "Red then green.\nOne thin slice.\nAt seams.\n")})
    forge = FakeForge()
    done = run_update(world, forge)
    assert (done.status, forge.labels) == ("opened", [])
    [(branch, _, _)] = forge.created
    git(world["repo"], "fetch", "-q", "origin", branch)
    for name, upstream_path in [("tdd", "skills/engineering/tdd/SKILL.md"),
                                ("review", "skills/engineering/review/SKILL.md")]:
        pushed_blob = git(world["repo"], "rev-parse", f"FETCH_HEAD:.claude/skills/{name}/SKILL.md")
        assert pushed_blob == git(world["upstream"], "rev-parse", f"{up}:{upstream_path}")
