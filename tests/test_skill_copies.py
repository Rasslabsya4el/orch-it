"""Skill copies (#397 decision 1, row 1): his skills are read from a git tree at one commit, written
into `.claude/skills/<name>/` byte for byte, and the manifest records the upstream path and the git
blob hash of every file, so a check recomputes each copy from the manifest alone."""

import hashlib
import json
import subprocess

import pytest

from orch_it.skills import copies
from orch_it.skills.copies import Manifest, SkillsError

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UPSTREAM = "https://github.com/mattpocock/skills"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          cwd=cwd, capture_output=True, check=True, creationflags=NO_WINDOW).stdout


def upstream_repo(tmp_path, files: dict[str, bytes], templates):
    """A throwaway git repo holding `files`; `core.autocrlf` on, so a reader of the work tree
    instead of the blobs would see CRLF. A copy of a template built once per worker (#421 unit 10)."""

    def build(folder):
        root = folder / "upstream"
        root.mkdir()
        git(root, "init", "-q")
        git(root, "config", "core.autocrlf", "true")
        for path, data in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(data)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "skills")
        return git(root, "rev-parse", "HEAD").decode().strip()

    return templates.copy(f"skill copies: upstream {sorted(files.items())!r}", build, tmp_path / "upstream",
                          part="upstream")


SKILL_FILES = {
    "README.md": b"# skills\n",
    "skills/engineering/README.md": b"# engineering\n",
    "skills/engineering/alpha/SKILL.md": b"---\nname: alpha\ndescription: first\n---\n\n# Alpha\n",
    "skills/engineering/alpha/scripts/run.sh": b"#!/bin/sh\necho alpha\n",
    "skills/engineering/alpha/agents/openai.yaml": b"name: alpha\n",
    "skills/productivity/beta/SKILL.md": b"---\nname: beta\ndescription: second\n---\n\nline one\nline two\n",
    "skills/in-progress/gamma/SKILL.md": b"---\nname: gamma\n---\n",
    "skills/deprecated/README.md": b"# gone\n",
    "skills/deprecated/old/SKILL.md": b"---\nname: old\n---\n",
}


def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_the_tree_holds_each_skill_folder_with_its_files_as_committed(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    tree = copies.read_tree(root, commit)
    assert sorted(tree) == ["skills/engineering/alpha", "skills/in-progress/gamma", "skills/productivity/beta"]
    assert tree["skills/engineering/alpha"] == {
        "SKILL.md": SKILL_FILES["skills/engineering/alpha/SKILL.md"],
        "agents/openai.yaml": b"name: alpha\n",
        "scripts/run.sh": b"#!/bin/sh\necho alpha\n",
    }


def test_an_unknown_commit_is_refused_with_its_code(tmp_path, templates):
    root, _ = upstream_repo(tmp_path, SKILL_FILES, templates)
    with pytest.raises(SkillsError) as e:
        copies.read_tree(root, "0" * 40)
    assert e.value.code == "commit_unknown"


def test_a_tree_without_skills_is_refused_not_read_as_all_removed(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, {"README.md": b"empty\n"}, templates)
    with pytest.raises(SkillsError) as e:
        copies.read_tree(root, commit)
    assert e.value.code == "no_skills"


def test_two_skill_folders_of_one_name_are_refused(tmp_path, templates):
    files = dict(SKILL_FILES) | {"skills/misc/alpha/SKILL.md": b"---\nname: alpha\n---\n"}
    root, commit = upstream_repo(tmp_path, files, templates)
    with pytest.raises(SkillsError) as e:
        copies.read_tree(root, commit)
    assert e.value.code == "name_clash"


def test_copies_are_written_byte_for_byte_and_the_manifest_hashes_every_file(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    tree = copies.read_tree(root, commit)
    repo = tmp_path / "repo"
    manifest = Manifest.build(tree, upstream=UPSTREAM, commit=commit, not_copied={"gamma": "no job"})
    copies.write(repo, tree, manifest)

    skills = repo / ".claude" / "skills"
    assert sorted(p.name for p in skills.iterdir()) == ["alpha", "beta", "pocock.json"]
    assert (skills / "beta" / "SKILL.md").read_bytes() == SKILL_FILES["skills/productivity/beta/SKILL.md"]
    assert (skills / "alpha" / "scripts" / "run.sh").read_bytes() == b"#!/bin/sh\necho alpha\n"
    saved = json.loads((skills / "pocock.json").read_text("utf-8"))
    assert (saved["upstream"], saved["commit"]) == (UPSTREAM, commit)
    assert saved["skills"]["beta"] == {
        "path": "skills/productivity/beta",
        "files": {"SKILL.md": blob(SKILL_FILES["skills/productivity/beta/SKILL.md"])}}
    assert saved["not_copied"]["gamma"]["why"] == "no job"
    assert saved["not_copied"]["gamma"]["files"] == {"SKILL.md": blob(b"---\nname: gamma\n---\n")}
    assert copies.verify(repo) == []


def test_the_blob_hashes_are_the_ones_git_gives_the_upstream_files(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    manifest = Manifest.build(copies.read_tree(root, commit), upstream=UPSTREAM, commit=commit, not_copied={})
    listed = git(root, "ls-tree", "-r", commit, "skills/engineering/alpha").decode().splitlines()
    by_path = {line.split("\t")[1]: line.split()[2] for line in listed}
    assert {f"skills/engineering/alpha/{rel}": sha for rel, sha in manifest.skills["alpha"]["files"].items()} == by_path


def test_verify_names_every_changed_missing_and_extra_file(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    tree = copies.read_tree(root, commit)
    repo = tmp_path / "repo"
    copies.write(repo, tree, Manifest.build(tree, upstream=UPSTREAM, commit=commit, not_copied={"gamma": "no job"}))
    skills = repo / ".claude" / "skills"
    (skills / "beta" / "SKILL.md").write_bytes(b"---\nname: beta\ndescription: second\n---\n\nline one\nline 2\n")
    (skills / "alpha" / "scripts" / "run.sh").unlink()
    (skills / "alpha" / "notes.md").write_bytes(b"mine\n")
    (skills / "gamma").mkdir()
    (skills / "gamma" / "SKILL.md").write_bytes(b"x\n")
    assert copies.verify(repo) == [
        "alpha/notes.md: not in the manifest",
        "alpha/scripts/run.sh: missing",
        "beta/SKILL.md: hash differs",
        "gamma: not copied, yet present",
    ]


def test_a_crlf_copy_on_disk_differs_from_its_lf_blob(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    tree = copies.read_tree(root, commit)
    repo = tmp_path / "repo"
    copies.write(repo, tree, Manifest.build(tree, upstream=UPSTREAM, commit=commit, not_copied={}))
    path = repo / ".claude" / "skills" / "beta" / "SKILL.md"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert copies.verify(repo) == ["beta/SKILL.md: hash differs"]


def test_a_new_write_removes_dropped_skills_and_stale_files_but_never_ours(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    tree = copies.read_tree(root, commit)
    repo = tmp_path / "repo"
    copies.write(repo, tree, Manifest.build(tree, upstream=UPSTREAM, commit=commit, not_copied={}))
    ours = repo / ".claude" / "skills" / "write-ticket"
    ours.mkdir()
    (ours / "SKILL.md").write_bytes(b"ours\n")

    smaller = {"skills/engineering/alpha": {"SKILL.md": tree["skills/engineering/alpha"]["SKILL.md"]},
               "skills/in-progress/gamma": tree["skills/in-progress/gamma"]}
    copies.write(repo, smaller, Manifest.build(smaller, upstream=UPSTREAM, commit="1" * 40,
                                               not_copied={"gamma": "no job"}))
    skills = repo / ".claude" / "skills"
    assert sorted(p.name for p in skills.iterdir()) == ["alpha", "pocock.json", "write-ticket"]
    assert sorted(p.name for p in (skills / "alpha").iterdir()) == ["SKILL.md"]
    assert (ours / "SKILL.md").read_bytes() == b"ours\n"
    assert copies.verify(repo) == []


def test_a_copy_never_overwrites_a_skill_of_ours_of_the_same_name(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    tree = copies.read_tree(root, commit)
    repo = tmp_path / "repo"
    ours = repo / ".claude" / "skills" / "beta"
    ours.mkdir(parents=True)
    (ours / "SKILL.md").write_bytes(b"ours\n")
    with pytest.raises(SkillsError) as e:
        copies.write(repo, tree, Manifest.build(tree, upstream=UPSTREAM, commit=commit, not_copied={}))
    assert e.value.code == "copy_over_ours"
    assert (ours / "SKILL.md").read_bytes() == b"ours\n"
    assert not (repo / ".claude" / "skills" / "alpha").exists()


@pytest.mark.parametrize("text, code", [
    ("{not json", "manifest_unreadable"),
    ("[]", "bad_manifest"),
    ('{"upstream": "u", "commit": "c", "skills": {"a": {"path": "p"}}, "not_copied": {}}', "bad_manifest"),
    ('{"upstream": "u", "commit": "c", "skills": {"../a": {"path": "p", "files": {}}}, "not_copied": {}}',
     "bad_manifest"),
    ('{"upstream": "u", "commit": "c", "skills": {"a": {"path": "p", "files": {"../../x": "0"}}},'
     ' "not_copied": {}}', "bad_manifest"),
    ('{"upstream": "u", "commit": "c", "skills": {}, "not_copied": {}, "gone": {"../x": "removed"}}', "bad_manifest"),
    ('{"upstream": "u", "commit": "c", "skills": {}, "not_copied": {}, "gone": ["zoom-out"]}', "bad_manifest"),
])
def test_a_bad_or_half_written_manifest_is_refused_with_its_code(tmp_path, text, code):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "pocock.json").write_text(text, "utf-8")
    with pytest.raises(SkillsError) as e:
        Manifest.load(tmp_path)
    assert e.value.code == code


def test_a_write_cut_part_way_is_named_by_verify_and_finished_by_the_same_write(tmp_path, monkeypatch, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    tree = copies.read_tree(root, commit)
    repo = tmp_path / "repo"
    manifest = Manifest.build(tree, upstream=UPSTREAM, commit=commit, not_copied={})
    real, calls = copies._replace_bytes, []

    def cut(path, data):
        calls.append(path)
        if len(calls) == 3:
            raise OSError("disk gone")
        real(path, data)

    monkeypatch.setattr(copies, "_replace_bytes", cut)
    with pytest.raises(OSError):
        copies.write(repo, tree, manifest)
    monkeypatch.setattr(copies, "_replace_bytes", real)
    assert Manifest.load(repo) is None
    copies.write(repo, tree, Manifest.build(tree, upstream=UPSTREAM, commit=commit, not_copied={}))
    assert copies.verify(repo) == []
    assert not (repo / ".claude" / "skills" / "pocock.pending.json").exists()


def test_verify_names_a_cut_write(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES, templates)
    tree = copies.read_tree(root, commit)
    copies.write(tmp_path, tree, Manifest.build(tree, upstream=UPSTREAM, commit=commit, not_copied={}))
    (tmp_path / ".claude" / "skills" / "pocock.pending.json").write_text('["alpha"]', "utf-8")
    assert copies.verify(tmp_path) == ["pocock.pending.json: a write was cut part-way"]


def test_a_path_that_turns_from_a_file_into_a_folder_and_back_is_rewritten(tmp_path):
    repo = tmp_path / "repo"
    as_file = {"skills/x/a": {"SKILL.md": b"a\n", "refs": b"one file\n"}}
    as_folder = {"skills/x/a": {"SKILL.md": b"a\n", "refs/x.md": b"in a folder\n"}}
    for tree in (as_file, as_folder, as_file):
        copies.write(repo, tree, Manifest.build(tree, upstream=UPSTREAM, commit="1" * 40, not_copied={}))
        assert copies.verify(repo) == []
    assert (repo / ".claude" / "skills" / "a" / "refs").read_bytes() == b"one file\n"


@pytest.mark.parametrize("path", [
    "skills/x/Upper/SKILL.md",          # a name the manifest cannot hold
    "skills/x/con/SKILL.md",            # a name Windows cannot hold
    "skills/x/a/aux.md",
    "skills/x/a/q?.md",
    "skills/x/a/a:b.md",
    "skills/x/a/trail.",
    "skills/x/a/space ",
])
def test_a_name_no_manifest_or_windows_folder_holds_is_refused_before_any_write(tmp_path, path, templates):
    root, commit = commit_of_blobs(tmp_path, ["skills/x/a/SKILL.md", path], templates)
    with pytest.raises(SkillsError) as e:
        copies.read_tree(root, commit)
    assert e.value.code == "unsupported_entry"


def blob_repo(folder):
    """The template of commit_of_blobs: an empty repository holding the blob `x\\n`; returns its sha."""

    root = folder / "upstream"
    root.mkdir()
    git(root, "init", "-q")
    return subprocess.run(["git", "hash-object", "-w", "--stdin"], input=b"x\n", cwd=root, capture_output=True,
                          check=True, creationflags=NO_WINDOW).stdout.decode().strip()


def commit_of_blobs(tmp_path, paths, templates):
    """A commit built from blobs alone: names a Windows work tree could never hold reach the tree."""

    root, sha = templates.copy("skill copies: blob repo", blob_repo, tmp_path / "upstream", part="upstream")
    run = {"cwd": root, "capture_output": True, "check": True, "creationflags": NO_WINDOW}
    index = "".join(f"100644 {sha}\t{path}\n" for path in paths).encode()
    subprocess.run(["git", "-c", "core.protectNTFS=false", "update-index", "--add", "--index-info"],
                   input=index, **run)
    tree_sha = git(root, "write-tree").decode().strip()
    return root, git(root, "commit-tree", tree_sha, "-m", "odd").decode().strip()


def test_two_files_differing_only_in_case_are_refused(tmp_path, templates):
    root, commit = commit_of_blobs(tmp_path, ["skills/x/a/SKILL.md", "skills/x/a/Ref.md", "skills/x/a/ref.md"], templates)
    with pytest.raises(SkillsError) as e:
        copies.read_tree(root, commit)
    assert e.value.code == "unsupported_entry"


def test_a_stray_skill_file_at_the_skills_root_is_no_skill(tmp_path, templates):
    root, commit = upstream_repo(tmp_path, SKILL_FILES | {"skills/SKILL.md": b"stray\n"}, templates)
    assert len(copies.read_tree(root, commit)) == 3


def test_a_deeply_nested_manifest_is_unreadable_not_a_crash(tmp_path):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "pocock.json").write_text("[" * 100_000, "utf-8")
    with pytest.raises(SkillsError) as e:
        Manifest.load(tmp_path)
    assert e.value.code == "manifest_unreadable"


def test_no_manifest_reads_as_no_copies(tmp_path):
    assert Manifest.load(tmp_path) is None
    assert copies.verify(tmp_path) == ["pocock.json: missing"]
