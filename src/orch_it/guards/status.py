"""`guards status` (#395 decision 5, row 9): read `main`'s branch protection and the two hooks, report
six facts, and write them as three `check` steps (`branch_protection`, `hooks_path`,
`guard_install`). Push to main without green tests is held by GitHub's own branch protection
(part 0), so this only reads it, never changes it.

The six facts: the required checks are exactly the setting `required_checks`; the branch is strict;
admins are enforced; force push and deletion are off; the pre-push hook is installed
(`core.hooksPath` is our hooks folder and `hooks/pre-push` is there); the Claude Code hook is
installed. A protection that cannot be read fails its four facts and says why; nothing raises.

Who holds the required checks (#414 decision 7): the `required_checks` fact says whether GitHub's branch protection
requires every required name (`Report.required_holder` `github`) or not: the base has no protection (or its plan gives
none), or the protection leaves a required name out, and the merge line itself holds those names green before it moves
the base (`line`); a protection that cannot be read claims no holder (`None`).

Names held by GitHub alone (#414 unit 16): with GitHub's runs not starting the line posts only the names a suite run
covered, those of the OS it ran on and the OS-free ones. `Report.waiting` has a `Waiting` for each required name that holds
a batch in that case, true whether GitHub is out or not: a name that is a job on an OS no place of the settings (this
machine, the machines that are on) has, or (`os` None) a name the workflow cannot place on an OS at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orch_it import settings
from orch_it.guards import install
from orch_it.testruns import github
from orch_it.testruns.config import TestSettings, required_checks
from orch_it.testruns.environment import this_os
from orch_it.testruns.github_place import classify_checks, workflow_text_of

BRANCH = "main"
FACT_NAMES = ("required_checks", "strict", "enforce_admins", "force_push_and_deletion_off",
              "pre_push_hook", "claude_hook")


@dataclass(frozen=True)
class Fact:
    """One of the six facts: `ok` and a line of `detail` for the table."""

    name: str
    ok: bool
    detail: str


LINE_HOLDS = "required checks held by the merge line, not by GitHub"
GITHUB_HOLDS = "required checks held by GitHub's branch protection"


@dataclass(frozen=True)
class Waiting:
    """A required name only GitHub runs: `os` is the OS of its job that no place of the settings has, None when the
    workflow cannot say what the name is tied to (it is unclassified)."""

    name: str
    os: str | None


@dataclass(frozen=True)
class Report:
    facts: tuple[Fact, ...]
    required_holder: str | None = None  # who holds the required checks: `line`, `github`, or None when unknown
    waiting: tuple[Waiting, ...] = ()  # the required names only GitHub runs; they hold a batch while it does not

    @property
    def ok(self) -> bool:
        return all(fact.ok for fact in self.facts)


def read_protection(repo: str) -> dict:
    """`main`'s branch protection from the GitHub API, as the API answers it."""

    return json.loads(github.run_gh(["api", f"repos/{repo}/branches/{BRANCH}/protection"]))


def _unprotected(error: BaseException) -> bool:
    text = str(error).lower()
    plan_limit = "403" in text and ("upgrade" in text or "not available" in text)  # private repo, plan without protection
    return isinstance(error, github.GithubError) and ("not protected" in text or "404" in text or plan_limit)


def _enabled(protection: dict, key: str) -> bool | None:
    flag = protection.get(key)
    return flag.get("enabled") if isinstance(flag, dict) and isinstance(flag.get("enabled"), bool) else None


def _checks_of(protection: dict) -> tuple[list[str], bool | None]:
    """The names of the required checks (the older `contexts` and the newer `checks`) and `strict`."""

    section = protection.get("required_status_checks")
    if not isinstance(section, dict):
        return [], None
    names = {name for name in section.get("contexts") or [] if isinstance(name, str)}
    names |= {c["context"] for c in section.get("checks") or []
              if isinstance(c, dict) and isinstance(c.get("context"), str)}
    strict = section.get("strict")
    return sorted(names), strict if isinstance(strict, bool) else None


def _branch_facts(protection_reader, repo: str, required: tuple[str, ...]):
    """(the four protection facts, the `check` findings, the `check` facts, who holds the required checks)."""

    try:
        protection = protection_reader(repo)
        if not isinstance(protection, dict):
            raise ValueError("not a mapping")
    except Exception as error:
        holder = None
        if _unprotected(error):
            finding, detail, holder = "branch_unprotected", f"unprotected: {BRANCH} has no protection", "line"
        else:
            finding, detail = "protection_unreadable", "protection could not be read"
        facts = [Fact(name, False, detail) for name in FACT_NAMES[:4]]
        if holder:
            facts[0] = Fact(facts[0].name, False, f"{detail}; {LINE_HOLDS}")
        return facts, [finding], {"protection": finding.split("_", 1)[1]}, holder
    names, strict = _checks_of(protection)
    admins = _enabled(protection, "enforce_admins")
    force, deletion = _enabled(protection, "allow_force_pushes"), _enabled(protection, "allow_deletions")
    found = {
        "required_checks": (names == sorted(required), "required_checks_wrong"),
        "strict": (strict is True, "not_strict"),
        "enforce_admins": (admins is True, "admins_not_enforced"),
    }
    findings = [finding for ok, finding in found.values() if not ok]
    if force is not False:
        findings.append("force_push_allowed")
    if deletion is not False:
        findings.append("deletion_allowed")
    holder = "github" if set(required) <= set(names) else "line"  # a name the protection does not require is the line's
    details = {"required_checks": ", ".join(names) or "none", "strict": str(strict).lower(),
               "enforce_admins": str(admins).lower(),
               "force_push_and_deletion_off": f"allow_force_pushes={str(force).lower()}"
                                              f" allow_deletions={str(deletion).lower()}"}
    details["required_checks"] += f"; {GITHUB_HOLDS if holder == 'github' else LINE_HOLDS}"
    facts = [Fact(name, ok, details[name]) for name, (ok, _) in found.items()]
    facts.append(Fact("force_push_and_deletion_off", force is False and deletion is False,
                      details["force_push_and_deletion_off"]))
    return facts, findings, {"required_checks": names, "strict": strict, "enforce_admins": admins,
                             "allow_force_pushes": force, "allow_deletions": deletion}, holder


def waiting_names(text: str, required: tuple[str, ...], place_oses: set[str]) -> tuple[Waiting, ...]:
    """The names of `required` only GitHub runs, in the order of `required`: a job of the workflow `text` on an OS in none
    of `place_oses`, or a name the workflow cannot place on an OS (unclassified). An OS-free name is covered by the place
    that runs the suite and never waits."""

    classes = classify_checks(text, required)
    return tuple(Waiting(name, classes.bound.get(name)) for name in required
                 if (name in classes.bound and classes.bound[name] not in place_oses) or name in classes.unclassified)


def status(store, repo: str, work_tree: Path | str = ".", *,
           protection: Callable[[str], dict] | None = None, own_os: str | None = None) -> Report:
    """Read `repo`'s protection of `main` (`protection(repo)`, default the GitHub API) and the hooks
    of `work_tree`, write the three checks and return the six facts and the required names only GitHub runs (from the
    workflow of `work_tree`, the required checks, and the OSes of this machine, `own_os` by default, and of the
    machines that are on in the settings). Never raises on what it reads: a protection or a folder that cannot be read
    is a failed fact."""

    raw = settings.load()
    required = required_checks(raw, repo)
    test_settings = TestSettings.from_settings(raw, repo)
    place_oses = {machine.os for machine in test_settings.machines if machine.on}
    if test_settings.own_capacity > 0:
        place_oses.add(own_os or this_os())
    waiting = waiting_names(workflow_text_of(Path(work_tree)), required, place_oses)
    branch, branch_findings, branch_facts, holder = _branch_facts(protection or read_protection, repo, required)

    hooks_path = None
    pre_push_file = claude = False
    ours = False
    hook_findings: list[str] = []
    try:
        root = install.top_level(Path(work_tree))
        hooks_path = install.read_hooks_path(root)
        ours = install.is_ours(hooks_path, root)
        pre_push_file = install.pre_push_present(root)
        claude = install.claude_hook_present(root)
    except install.InstallError as error:
        hook_findings.append(error.code)
    if hooks_path is None and "not_a_repo" not in hook_findings:
        hook_findings.append("hooks_path_unset")
    elif hooks_path is not None and not ours:
        hook_findings.append("hooks_path_other")
    if not pre_push_file:
        hook_findings.append("pre_push_file_missing")
    pre_push_hook = ours and pre_push_file
    install_findings = ([] if pre_push_hook else ["pre_push_hook_missing"]) + (
        [] if claude else ["claude_hook_missing"])

    facts = [*branch, Fact("pre_push_hook", pre_push_hook,
                           f"core.hooksPath={hooks_path or 'unset'}"
                           f" {install.HOOKS_FOLDER}/{install.PRE_PUSH_FILE} "
                           f"{'present' if pre_push_file else 'missing'}"),
             Fact("claude_hook", claude, f"{'.'.join(install.CLAUDE_SETTINGS)} PreToolUse "
                                         f"{'present' if claude else 'missing'}")]
    for check, findings, detail in (
            ("branch_protection", branch_findings, branch_facts),
            ("hooks_path", hook_findings,
             {"hooks_path": hooks_path, "ours": ours, "pre_push_file": pre_push_file}),
            ("guard_install", install_findings, {"pre_push_hook": pre_push_hook, "claude_hook": claude})):
        install.write_check(store, repo, check, repo, "failed" if findings else "passed", findings, detail)
    return Report(tuple(facts), holder, waiting)
