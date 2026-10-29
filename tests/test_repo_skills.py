"""The repo's own skill copies (#397 decision 1, row 1; decision 4 for a moved pin): every skill of
his with a job in the #390 table is copied at the manifest's pin (the first copies were at `49dd158d`,
orch-it #17), each file's hash recomputed from the manifest; the hidden and the `no job` ones are
absent."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from orch_it.skills import copies
from orch_it.skills.copies import Manifest

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The #390 table (research/pocock-compat-355, Part 1): the 16 rows with a job, and the owner-invoked
# skills #391 gave a job (#397 decision 1)
WITH_A_JOB = {
    "code-review", "codebase-design", "diagnosing-bugs", "domain-modeling", "grill-with-docs",
    "improve-codebase-architecture", "pr", "prototype", "research", "retro", "setup-matt-pocock-skills",
    "tdd", "wayfinder", "wizard", "grilling", "writing-for-agents",
    "triage", "handoff", "to-spec", "to-tickets", "implement", "implement-spec",
}
# the 7 hidden pairs of #390 part 5 (5.33, 5.38, 5.44, 6.1, 6.2, 6.4, 6.5) are these 4 skills
HIDDEN = {"claude-handoff", "git-guardrails-claude-code", "loop-me", "chief-of-staff"}
NO_JOB = {"ask-matt", "grill-me", "teach", "to-questionnaire", "wait-what", "migrate-to-shoehorn",
          "scaffold-exercises", "setup-pre-commit", "setup-ts-deep-modules", "writing-beats",
          "writing-fragments", "writing-shape"}
NOT_COPIED = HIDDEN | NO_JOB
PINNED = "49dd158d1076134a641b33efb035946536778336"  # the pin of the first copies (orch-it #17)
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def manifest() -> Manifest:
    found = Manifest.load(ROOT)
    assert found is not None, "no .claude/skills/pocock.json"
    return found


def test_every_copy_is_on_disk_as_its_manifest_hashes_say():
    assert copies.verify(ROOT) == []


def test_the_copies_are_the_skills_with_a_job_at_the_pin():
    # the first copies were exactly these (row 1); an update moves the pin, copies what his upstream adds and drops
    # what it drops (recorded as gone), so the sets hold up to those; his 38 skills of the first pin are not counted,
    # since an update may change the count
    found = manifest()
    assert found.upstream == "https://github.com/mattpocock/skills"
    assert FULL_COMMIT.fullmatch(found.commit)
    gone = set(found.gone)
    assert WITH_A_JOB - gone <= set(found.skills)
    assert not NOT_COPIED & set(found.skills)
    assert NOT_COPIED - gone <= set(found.not_copied)


def test_the_hidden_and_the_no_job_skills_have_no_folder():
    for name in NOT_COPIED:
        assert not (ROOT / ".claude" / "skills" / name).exists(), name


def test_each_copy_names_itself_as_its_folder():
    for name in manifest().skills:
        head = (ROOT / ".claude" / "skills" / name / "SKILL.md").read_text("utf-8").split("\n---", 1)[0]
        assert f"\nname: {name}\n" in head + "\n", name


def gh_api(path: str):
    gh = shutil.which("gh")
    if gh is None:
        pytest.skip("no gh")
    done = subprocess.run([gh, "api", path], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.live
def test_the_manifest_matches_his_tree_on_github_at_its_pin():
    found = manifest()
    tree = gh_api(f"repos/mattpocock/skills/git/trees/{found.commit}?recursive=1")["tree"]
    upstream = {item["path"]: item["sha"] for item in tree if item["type"] == "blob"}
    for group in (found.skills, found.not_copied):
        for name, entry in group.items():
            listed = {rel: upstream[f"{entry['path']}/{rel}"] for rel in entry["files"]}
            assert entry["files"] == listed, name
            under = {p for p in upstream if p.startswith(entry["path"] + "/")}
            assert under == {f"{entry['path']}/{rel}" for rel in entry["files"]}, name
    # every skill of his at the pin is a copy or left out, and no name the manifest calls gone is there
    folders = {p.rsplit("/", 1)[0] for p in upstream
               if p.endswith("/SKILL.md") and p.startswith("skills/") and not p.startswith("skills/deprecated/")}
    assert folders == {e["path"] for group in (found.skills, found.not_copied) for e in group.values()}
    assert not {f.rsplit("/", 1)[1] for f in folders} & set(found.gone)


@pytest.mark.live
def test_the_pin_is_the_first_one_or_a_later_commit_of_his():
    found = manifest()
    compared = gh_api(f"repos/mattpocock/skills/compare/{PINNED}...{found.commit}")
    assert compared["status"] in ("identical", "ahead"), compared["status"]
