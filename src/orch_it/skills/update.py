"""The update (#397 decision 4): `skills check`, `skills report` and `skills update`.

`check` fetches his upstream (`main` by default) into a cache folder outside any work tree (built again
once when broken), reads the pinned commit's skills and the new ones from their blobs, makes sure the
pinned tree is the one the manifest records file by file, and classifies every skill
(`orch_it.skills.classify`) with the renames git reports between the two commits.

`report` is the daily run: on a change it keeps one issue per upstream commit, "His skills moved to
<commit>", whose body is the class table; labelled `needs-triage` when a copy is renamed, removed or
changed in contract, else `ready-for-agent`. A later run updates that issue rather than opening
another, and closes the issue of an older upstream commit as superseded. It opens no pull request: a
pull request a workflow's own token opens starts no workflow, so its required checks never come (the
brain's decision on #397, after the probe orch-it #44).

`update` is run by ORCH, working that issue, under the owner's `gh`: it fetches `origin/main`, exports
its tree to a scratch folder, writes the new copies and manifest there (added skills copied, the names
his upstream dropped recorded as `gone`), runs the references check of decision 5 on it, builds the
commit with a scratch index and pushes it to `skills-upstream/<upstream commit>-<main commit>`; the
caller's work tree is never touched. Its pull request lists every skill with its class and merges as
any pull request does; one with a copy renamed, removed or changed in contract is labelled
`needs-triage`. A later run brings the open pull request of the same upstream commit up to date
instead of opening another, leaves one the brain closed alone, and closes the open one of an older
upstream commit as superseded. The forge (GitHub) sits behind `Forge`.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from orch_it import settings
from orch_it.skills import copies, references
from orch_it.skills.classify import Change, classify
from orch_it.skills.copies import GIT_TIMEOUT_SECONDS, NO_WINDOW, Manifest, SkillsError, Tree

BRANCH_PREFIX = "skills-upstream/"
BASE_REF = "main"
TRIAGE_LABEL = "needs-triage"
READY_LABEL = "ready-for-agent"
ISSUE_TITLE = "His skills moved to "
TRIAGE_CLASSES = ("renamed", "removed", "contract_changed")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_EMPTY_BLOB = "0" * 40


class Forge(Protocol):
    def update_prs(self) -> list[dict]: ...            # every pull request of a skills-upstream/ branch: url, branch, state
    def create_pr(self, branch: str, title: str, body: str) -> str: ...
    def add_label(self, pr: str, label: str) -> None: ...
    def update_branch(self, pr: str) -> None: ...      # merge main into the pull request's branch
    def close_pr(self, pr: str, comment: str) -> None: ...
    def update_issues(self) -> list[dict]: ...         # every open issue: url, title, labels
    def create_issue(self, title: str, body: str, label: str) -> str: ...
    def edit_issue(self, url: str, body: str, label: str, drop: list[str]) -> None: ...
    def close_issue(self, url: str, comment: str) -> None: ...


@dataclass
class Check:
    manifest: Manifest
    new_commit: str
    new_tree: Tree
    changes: list[Change]

    @property
    def changed(self) -> list[Change]:
        return [c for c in self.changes if c.cls != "same"]

    def held(self, change: Change) -> str:
        """`copy`, `not copied (<why>)`, or `new` for an added skill."""

        old = change.old_name or change.name
        if change.cls == "added":
            return "new"
        if old in self.manifest.not_copied:
            return f"not copied ({self.manifest.not_copied[old]['why']})"
        return "copy"

    @property
    def needs_triage(self) -> bool:
        """A copy renamed, removed or changed in contract (the brain's word on orch-it #35: a skill the
        layer does not copy is listed, and merges when green)."""

        return any(c.cls in TRIAGE_CLASSES and self.held(c) == "copy" for c in self.changes)


@dataclass
class Done:
    status: str               # no_change, opened, already_open, declined
    new_commit: str
    pr: str | None = None
    labels: list[str] = field(default_factory=list)


def _run(cwd: Path, *args: str, error_code: str = "git_failed", env: dict | None = None,
         stdin: bytes | None = None) -> str:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True, env=env,
                              timeout=GIT_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SkillsError(error_code, f"git {args[0]}: {e}") from None
    if done.returncode != 0:
        raise SkillsError(error_code, f"git {' '.join(args[:2])}: "
                                      f"{done.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return done.stdout.decode("utf-8", "replace").strip()


def _has_commit(cache: Path, commit: str) -> bool:
    try:
        _run(cache, "cat-file", "-e", f"{commit}^{{commit}}")
        return True
    except SkillsError:
        return False


def cache_folder(upstream: str) -> Path:
    return settings.data_folder() / "skills-upstream" / hashlib.sha256(upstream.encode()).hexdigest()[:16]


def _fetch_once(cache: Path, upstream: str, ref: str, pinned: str) -> str:
    if cache.exists() and not cache.is_dir():
        raise SkillsError("fetch_failed", f"the cache is not a folder: {cache}")
    if not (cache / "HEAD").exists():
        cache.mkdir(parents=True, exist_ok=True)
        _run(cache, "init", "-q", "--bare", error_code="fetch_failed")
    _run(cache, "fetch", "-q", "--no-tags", "--", upstream, f"+{ref}:refs/upstream/{ref}", error_code="fetch_failed")
    if not _has_commit(cache, pinned):
        _run(cache, "fetch", "-q", "--no-tags", "--", upstream, pinned, error_code="fetch_failed")
    return _run(cache, "rev-parse", "--verify", f"refs/upstream/{ref}^{{commit}}", error_code="fetch_failed")


def _remove_tree(path: Path) -> None:
    """Remove a folder git wrote, its read-only object files included (Windows will not otherwise)."""

    def writable_then_again(function, name, _):
        os.chmod(name, stat.S_IWRITE)
        function(name)

    shutil.rmtree(path, onexc=writable_then_again)


def fetch(cache: Path, upstream: str, ref: str, pinned: str) -> str:
    """The commit `ref` of `upstream` names now; the pinned commit and it are in the cache after. A
    cache that fails is ours to throw away: it is built again once."""

    try:
        return _fetch_once(cache, upstream, ref, pinned)
    except SkillsError:
        if not cache.is_dir():
            raise
        _remove_tree(cache)
        return _fetch_once(cache, upstream, ref, pinned)


def renames(cache: Path, old: str, new: str) -> dict[str, str]:
    """Skill folders git sees renamed between the two commits: old path -> new path."""

    out = _run(cache, "diff", "--name-status", "-M", "--diff-filter=R", old, new, "--", copies.SKILLS_ROOT)
    found = {}
    skill_file = "/" + copies.SKILL_FILE
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1].endswith(skill_file) and parts[2].endswith(skill_file):
            found[parts[1].rsplit("/", 1)[0]] = parts[2].rsplit("/", 1)[0]
    return found


def check(repo: str | Path, *, upstream: str | None = None, ref: str = "main", cache: Path | None = None) -> Check:
    """Every skill of his upstream `ref` classified against the manifest of the work tree `repo`."""

    manifest = Manifest.load(repo)
    if manifest is None:
        raise SkillsError("bad_manifest", f"{copies.MANIFEST_NAME}: missing")
    upstream = upstream or manifest.upstream
    if upstream.startswith("-") or not _COMMIT.fullmatch(manifest.commit):
        raise SkillsError("bad_manifest", "the upstream or the commit is no repo or commit")
    cache = cache or cache_folder(upstream)
    new_commit = fetch(cache, upstream, ref, manifest.commit)
    old_tree = copies.read_tree(cache, manifest.commit)
    recorded = {e["path"]: e["files"] for group in (manifest.skills, manifest.not_copied) for e in group.values()}
    actual = {path: {rel: copies.blob_hash(data) for rel, data in files.items()} for path, files in old_tree.items()}
    if recorded != actual:
        raise SkillsError("manifest_mismatch", f"the manifest is not his tree at {manifest.commit[:12]}")
    new_tree = old_tree if new_commit == manifest.commit else copies.read_tree(cache, new_commit)
    changes = classify(old_tree, new_tree, renames=renames(cache, manifest.commit, new_commit))
    return Check(manifest, new_commit, new_tree, changes)


def render(found: Check) -> str:
    """One line per changed skill, the skills per class, the counts, then what happens next."""

    lines = [f"upstream {found.manifest.upstream}: pinned {found.manifest.commit[:12]}, now {found.new_commit[:12]}"]
    for change in found.changed:
        paths = f"{change.old_path or '-'} -> {change.new_path or '-'}"
        why = f"  {', '.join(change.why)}" if change.why else ""
        lines.append(f"{change.cls:17} {change.name:30} {found.held(change)}  {paths}{why}")
    by_class: dict[str, list[str]] = {}
    for change in found.changes:
        by_class.setdefault(change.cls, []).append(change.name)
    lines += [f"{cls}: {', '.join(names)}" for cls, names in sorted(by_class.items())]
    lines.append(" ".join(f"{cls}={len(names)}" for cls, names in sorted(by_class.items())))
    lines.append("no change" if not found.changed else
                 (f"{TRIAGE_LABEL}: waits for the brain" if found.needs_triage else "no copy waits for the brain"))
    return "\n".join(lines) + "\n"


def new_manifest(found: Check) -> Manifest:
    old = found.manifest
    not_copied = {name: e["why"] for name, e in old.not_copied.items()}
    gone = dict(old.gone)
    at = found.new_commit[:12]
    for change in found.changes:
        if change.cls == "renamed" and change.old_name != change.name:
            if change.old_name in not_copied:
                not_copied[change.name] = f"{not_copied.pop(change.old_name)} (renamed from {change.old_name})"
            gone[change.old_name] = f"renamed to {change.name} upstream at {at}"
        elif change.cls == "removed":
            gone[change.name] = f"removed upstream at {at}"
    built = Manifest.build(found.new_tree, upstream=old.upstream, commit=found.new_commit, not_copied=not_copied)
    built.gone = {name: what for name, what in sorted(gone.items())
                  if name not in built.skills and name not in built.not_copied}
    return built


def _class_table(found: Check) -> str:
    rows = ["| skill | class | in the repo | upstream path | why |", "|---|---|---|---|---|"]
    for change in found.changes:
        held = "copied, called by no role" if change.cls == "added" else found.held(change)
        path = f"{change.old_path} -> {change.new_path}" if change.cls == "renamed" else (change.new_path or change.old_path)
        rows.append(f"| `{change.name}` | {change.cls} | {held} | `{path}` | {', '.join(change.why) or '-'} |")
    return "\n".join(rows)


def _moved(found: Check) -> str:
    return (f"His upstream `{found.manifest.upstream}` moved from `{found.manifest.commit}` to `{found.new_commit}`"
            " (#397 decision 4).")


def pr_body(found: Check, problems: list[str]) -> str:
    if found.needs_triage:
        rule = (f"Label `{TRIAGE_LABEL}`: a copy is renamed, removed or changed in what it takes or returns."
                " It waits for the brain.")
    else:
        rule = "No copy is renamed, removed or changed in contract: it merges as any pull request, when green."
    refs = ("references: none outside the repo's skills" if not problems else
            "references (decision 5; the tests are red until these are fixed in this pull request):\n"
            + "\n".join(f"- {p}" for p in problems))
    return "\n\n".join([
        _moved(found) + " The copies and `.claude/skills/pocock.json` are rewritten to the new commit.",
        rule, _class_table(found), refs, "Written by `python -m orch_it skills update`."])


def issue_body(found: Check) -> str:
    if found.needs_triage:
        rule = (f"Label `{TRIAGE_LABEL}`: a copy is renamed, removed or changed in what it takes or returns;"
                " the brain decides before the copies move.")
    else:
        rule = (f"Label `{READY_LABEL}`: no copy is renamed, removed or changed in contract.")
    return "\n\n".join([
        _moved(found), rule, _class_table(found),
        "To take it: `python -m orch_it skills update` under the owner's `gh` opens the pull request with the new"
        " copies; its own tests run, and it merges when green.",
        "Written by `python -m orch_it skills report` (the daily `skills-update` workflow)."])


def report(repo: str | Path, *, forge: Forge, upstream: str | None = None, ref: str = "main",
           cache: Path | None = None) -> Done:
    """The daily run: on a change, one issue per upstream commit, kept up to date; no pull request."""

    found = check(Path(repo), upstream=upstream, ref=ref, cache=cache)
    if not found.changed:
        return Done("no_change", found.new_commit)
    title = ISSUE_TITLE + found.new_commit[:12]
    label, other = (TRIAGE_LABEL, READY_LABEL) if found.needs_triage else (READY_LABEL, TRIAGE_LABEL)
    ours = [i for i in forge.update_issues() if i["title"].startswith(ISSUE_TITLE)]
    same = [i for i in ours if i["title"] == title]
    if same:
        url, status = same[0]["url"], "already_open"
        forge.edit_issue(url, issue_body(found), label, [other] if other in same[0]["labels"] else [])
    else:
        url, status = forge.create_issue(title, issue_body(found), label), "opened"
    for older in ours:
        if older["url"] != url:
            forge.close_issue(older["url"], f"Superseded by {url}: his upstream moved on to {found.new_commit[:12]}.")
    return Done(status, found.new_commit, url, [label])


def _export(repo: Path, commit: str, into: Path) -> None:
    data = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repo, capture_output=True,
                          timeout=GIT_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
    if data.returncode != 0:
        raise SkillsError("git_failed", f"git archive: {data.stderr.decode('utf-8', 'replace').strip()[:300]}")
    try:
        with tarfile.open(fileobj=io.BytesIO(data.stdout)) as archive:
            archive.extractall(into, filter="data")
    except (tarfile.TarError, OSError) as e:
        raise SkillsError("git_failed", f"the tree of {commit[:12]} could not be laid out: {e}") from None


def _commit_skills(repo: Path, base: str, scratch: Path, message: str) -> str:
    """A commit on `base` whose `.claude/skills/` is the scratch folder's, built with a scratch index."""

    folder = copies.SKILLS_FOLDER.as_posix()
    with tempfile.TemporaryDirectory() as index_dir:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(index_dir) / "index")}
        _run(repo, "read-tree", base, env=env)
        modes = {}  # path -> mode on base, read NUL-separated so no path comes back quoted
        for entry in _run(repo, "ls-tree", "-r", "-z", base, "--", folder).split("\0"):
            if entry:
                meta, _, path = entry.partition("\t")
                modes[path] = meta.split()[0]
        files = sorted(p for p in (scratch / folder).rglob("*") if p.is_file())
        rels = [p.relative_to(scratch).as_posix() for p in files]
        shas = _run(repo, "hash-object", "-w", "--no-filters", "--stdin-paths",
                    stdin="".join(f"{p}\n" for p in files).encode()).splitlines()
        info = [f"{modes.get(rel, '100644')} {sha}\t{rel}" for sha, rel in zip(shas, rels)]
        info += [f"0 {_EMPTY_BLOB}\t{rel}" for rel in sorted(modes.keys() - set(rels))]
        _run(repo, "update-index", "-z", "--index-info", env=env, stdin="".join(f"{line}\0" for line in info).encode())
        tree = _run(repo, "write-tree", env=env)
    return _run(repo, "commit-tree", tree, "-p", base, "-m", message)


def _settle(forge: Forge, found: Check, pr: str) -> list[str]:
    """Label the pull request `needs-triage` when a copy is renamed, removed or changed in contract."""

    if not found.needs_triage:
        return []
    forge.add_label(pr, TRIAGE_LABEL)
    return [TRIAGE_LABEL]


def update(repo: str | Path, *, forge: Forge, upstream: str | None = None, ref: str = "main",
           cache: Path | None = None) -> Done:
    repo = Path(repo)
    _run(repo, "fetch", "-q", "origin", BASE_REF)
    base = _run(repo, "rev-parse", "--verify", f"origin/{BASE_REF}^{{commit}}")
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir)
        _export(repo, base, scratch)
        problems = copies.verify(scratch)
        if problems:
            raise SkillsError("copies_edited", "; ".join(problems[:5]))
        found = check(scratch, upstream=upstream, ref=ref, cache=cache)
        if not found.changed:
            return Done("no_change", found.new_commit)
        prefix = f"{BRANCH_PREFIX}{found.new_commit[:12]}-"
        prs = forge.update_prs()
        older = [p["url"] for p in prs if p["state"] == "OPEN" and not p["branch"].startswith(prefix)]
        mine_open = [p for p in prs if p["state"] == "OPEN" and p["branch"].startswith(prefix)]
        mine_closed = [p for p in prs if p["state"] == "CLOSED" and p["branch"].startswith(prefix)]
        if mine_open:
            pr = mine_open[0]
            failed = _attempt(lambda: forge.update_branch(pr["url"]))
            failed += _attempt(lambda: _supersede(forge, older, pr["url"]))
            labels = _attempt_labels(forge, found, pr["url"], failed)
            if failed:
                raise failed[0]
            return Done("already_open", found.new_commit, pr["url"], labels)
        if mine_closed:
            _supersede(forge, older, mine_closed[0]["url"])
            return Done("declined", found.new_commit, mine_closed[0]["url"])
        branch = prefix + base[:8]
        copies.write(scratch, found.new_tree, new_manifest(found))
        refs = references.check(scratch)
        commit = _commit_skills(repo, base, scratch,
                                f"His skills at {found.new_commit[:12]}: {len(found.changed)} changed (#397 skills update)")
    if not _run(repo, "ls-remote", "--heads", "origin", branch):
        _run(repo, "push", "-q", "origin", f"{commit}:refs/heads/{branch}")
    title = f"His skills move to {found.new_commit[:12]}: " + ", ".join(
        f"{c.cls} {c.name}" for c in found.changed[:4]) + (" ..." if len(found.changed) > 4 else "")
    pr = forge.create_pr(branch, title[:200], pr_body(found, refs))
    failed = _attempt(lambda: _supersede(forge, older, pr))
    labels = _attempt_labels(forge, found, pr, failed)
    if failed:
        raise failed[0]
    return Done("opened", found.new_commit, pr, labels)


def _attempt_labels(forge: Forge, found: Check, pr: str, failed: list[SkillsError]) -> list[str]:
    labels: list[str] = []
    failed += _attempt(lambda: labels.extend(_settle(forge, found, pr)))
    return labels


def _attempt(step) -> list[SkillsError]:
    try:
        step()
        return []
    except SkillsError as e:
        return [e]


def _supersede(forge: Forge, older: list[str], by: str) -> None:
    """Close each open pull request of an older upstream commit; a refusal is raised after the rest."""

    failed = [e for url in older for e in _attempt(lambda: forge.close_pr(url, f"Superseded by {by}: his upstream moved on."))]
    if failed:
        raise failed[0]
