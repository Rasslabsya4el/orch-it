"""The forge of `skills update`: GitHub through `gh` (#397 decision 4).

`GithubForge` is the `Forge` of `orch_it.skills.update`, on the repo's one `gh` runner
(`orch_it.testruns.github.run_gh`: bounded, no prompt, no console window) or any `Gh` given. A pull
request opened with a workflow's own token starts no workflow, so the daily run asks for the `tests`
workflow on the branch by name. Any refusal is `SkillsError("forge_failed")`.
"""

from __future__ import annotations

import json
import os
import tempfile

from orch_it import labels
from orch_it.skills.copies import SkillsError
from orch_it.skills.update import BRANCH_PREFIX
from orch_it.testruns.github import Gh, GithubError, run_gh

LABEL_LIST_LIMIT = "500"
TESTS_WORKFLOW = "tests.yml"


class GithubForge:
    def __init__(self, repo: str, *, gh: Gh | None = None, base: str = "main"):
        self.repo = repo
        self.base = base
        self._run = gh or run_gh

    def _gh(self, *args: str) -> str:
        try:
            return self._run([*args, "--repo", self.repo])
        except GithubError as e:
            raise SkillsError("forge_failed", str(e)) from None

    def update_prs(self) -> list[dict]:
        """Every pull request of a skills-update branch, of any state and age: every page of the
        repo's pull requests, newest first, filtered by branch."""

        pick = (f'.[] | select(.head.ref | startswith("{BRANCH_PREFIX}")) | {{url: .html_url, branch: .head.ref,'
                ' state: (if .merged_at then "MERGED" elif .state == "open" then "OPEN" else "CLOSED" end)}')
        try:
            said = self._run(["api", "--paginate", f"repos/{self.repo}/pulls?state=all&per_page=100", "--jq", pick])
        except GithubError as e:
            raise SkillsError("forge_failed", str(e)) from None
        try:
            return [json.loads(line) for line in said.splitlines() if line.strip()]
        except ValueError as e:
            raise SkillsError("forge_failed", f"gh api pulls: {e}") from None

    def create_pr(self, branch: str, title: str, body: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "body.md")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            said = self._gh("pr", "create", "--base", self.base, "--head", branch, "--title", title, "--body-file", path)
        return said.strip().splitlines()[-1]

    def add_label(self, pr: str, label: str) -> None:
        names = {item["name"] for item in json.loads(self._gh("label", "list", "--limit", LABEL_LIST_LIMIT,
                                                              "--json", "name") or "[]")}
        if label not in names:  # the layer's own set says how it looks (#398 decision 6)
            want = labels.label_set()[label]
            self._gh("label", "create", label, "--color", want["color"], "--description", want["description"])
        self._gh("pr", "edit", pr, "--add-label", label)

    def merge_when_green(self, pr: str) -> None:
        self._gh("pr", "merge", pr, "--auto", "--merge")

    def update_branch(self, pr: str) -> None:
        self._gh("pr", "update-branch", pr)

    def close_pr(self, pr: str, comment: str) -> None:
        self._gh("pr", "close", pr, "--comment", comment)

    def dispatch_tests(self, branch: str) -> None:
        self._gh("workflow", "run", TESTS_WORKFLOW, "--ref", branch, "-f", "slow_and_live=false")
