"""The repo's own skill copies (#397 decision 1, row 1): every skill of his with a job in the #390
table is copied at the pin (first `49dd158d`), each file's hash recomputed from the manifest; the hidden and the
`no job` ones are absent."""

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
PINNED = "49dd158d1076134a641b33efb035946536778336"  # the pin of the first copies (orch-it #17)

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


def manifest() -> Manifest:
    found = Manifest.load(ROOT)
    assert found is not None, "no .claude/skills/pocock.json"
    return found


def test_every_copy_is_on_disk_as_its_manifest_hashes_say():
    assert copies.verify(ROOT) == []


def test_the_copies_are_the_skills_with_a_job_at_the_pin():
    # the first copies were exactly these, at PINNED (row 1); an update moves the pin, copies what his upstream
    # adds and drops what it drops (recorded as gone), so the sets hold up to those
    found = manifest()
    assert found.upstream == "https://github.com/mattpocock/skills"
    assert re.fullmatch(r"[0-9a-f]{40}", found.commit)
    assert WITH_A_JOB - set(found.gone) <= set(found.skills)
    assert not (HIDDEN | NO_JOB) & set(found.skills)
    assert (HIDDEN | NO_JOB) - set(found.gone) <= set(found.not_copied)


def test_the_hidden_and_the_no_job_skills_have_no_folder():
    for name in HIDDEN | NO_JOB:
        assert not (ROOT / ".claude" / "skills" / name).exists(), name


def test_each_copy_names_itself_as_its_folder():
    for name in manifest().skills:
        head = (ROOT / ".claude" / "skills" / name / "SKILL.md").read_text("utf-8").split("\n---", 1)[0]
        assert f"\nname: {name}\n" in head + "\n", name


@pytest.mark.live
def test_the_manifest_matches_his_tree_on_github_at_its_pin():
    gh = shutil.which("gh")
    if gh is None:
        pytest.skip("no gh")
    done = subprocess.run([gh, "api", f"repos/mattpocock/skills/git/trees/{manifest().commit}?recursive=1"],
                          capture_output=True, timeout=60, creationflags=NO_WINDOW)
    assert done.returncode == 0, done.stderr
    upstream = {item["path"]: item["sha"] for item in json.loads(done.stdout)["tree"] if item["type"] == "blob"}
    found = manifest()
    for group in (found.skills, found.not_copied):
        for name, entry in group.items():
            listed = {rel: upstream[f"{entry['path']}/{rel}"] for rel in entry["files"]}
            assert entry["files"] == listed, name
            under = {p for p in upstream if p.startswith(entry["path"] + "/")}
            assert under == {f"{entry['path']}/{rel}" for rel in entry["files"]}, name
