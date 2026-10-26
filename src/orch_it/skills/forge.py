"""The forge of `skills update`: GitHub through `gh` (#397 decision 4).

`Forge` is the seam: the update asks it for an open pull request of a branch, to open one, to label
it, to set it to merge when green and to start the tests on the branch. A pull request opened with a
workflow's own token starts no workflow, so the daily run asks for the `tests` workflow by name.
"""

from __future__ import annotations

import json
import subprocess

from orch_it import labels
from orch_it.skills.copies import SkillsError

GH_TIMEOUT_SECONDS = 120
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GithubForge:
    def __init__(self, repo: str, base: str = "main"):
        self.repo = repo
        self.base = base

    def _gh(self, *args: str, stdin: str | None = None) -> str:
        try:
            done = subprocess.run(["gh", *args, "--repo", self.repo], input=stdin, capture_output=True, text=True,
                                  encoding="utf-8", timeout=GH_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SkillsError("forge_failed", f"gh {args[0]}: {e}") from None
        if done.returncode != 0:
            raise SkillsError("forge_failed", f"gh {' '.join(args[:2])}: {done.stderr.strip()[:300]}")
        return done.stdout.strip()

    def open_pr(self, branch: str) -> str | None:
        found = json.loads(self._gh("pr", "list", "--head", branch, "--state", "open", "--json", "url") or "[]")
        return found[0]["url"] if found else None

    def create_pr(self, branch: str, title: str, body: str) -> str:
        return self._gh("pr", "create", "--base", self.base, "--head", branch, "--title", title,
                        "--body-file", "-", stdin=body).splitlines()[-1]

    def add_label(self, pr: str, label: str) -> None:
        names = {item["name"] for item in json.loads(self._gh("label", "list", "--limit", "500", "--json", "name"))}
        if label not in names:  # the layer's own set says how it looks (#398 decision 6)
            want = labels.label_set()[label]
            self._gh("label", "create", label, "--color", want["color"], "--description", want["description"])
        self._gh("pr", "edit", pr, "--add-label", label)

    def merge_when_green(self, pr: str) -> None:
        self._gh("pr", "merge", pr, "--auto", "--merge")

    def dispatch_tests(self, branch: str) -> None:
        self._gh("workflow", "run", "tests.yml", "--ref", branch, "-f", "slow_and_live=false")
