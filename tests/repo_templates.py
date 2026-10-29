"""Template git repositories for the tests (#421 unit 10: the Windows time of the git processes).

On Windows one git process costs about 50 ms, and a test that runs `git init`, `git config` and `git commit` for its
own repository spends most of its time there. A test that needs a repository gets a copy of a template instead: the
template is built once per xdist worker, in that worker's own pytest base folder, and is never changed afterwards;
a folder copy starts no process. Each copy goes into the test's own tmp_path, so no test sees another test's
repository and no worker sees another worker's template.
"""

import contextlib
import hashlib
import shutil
import subprocess
import uuid
from pathlib import Path

from scan_repos import NO_WINDOW, Repo


class Templates:
    def __init__(self, base: Path):
        self.base = base
        self.built: dict[str, tuple[Path, object]] = {}

    def copy(self, key: str, build, dest: Path, part: str):
        """Copy `part` of the template `key` to `dest`. On first use `build(folder)` makes the template in a new
        folder, and what it returns (a sha, say) is handed to every copy of that template."""

        if key not in self.built:
            folder = self.base / "templates" / hashlib.sha1(key.encode()).hexdigest()[:16]
            folder.mkdir(parents=True)
            try:
                value = build(folder)
            except BaseException:
                # a half-made folder is set aside, not left under the key: the next copy of `key` builds again and
                # raises the real error (paths inside the template are absolute, so it cannot be renamed on success)
                with contextlib.suppress(OSError):
                    folder.rename(folder.with_name(f"{folder.name}.failed-{uuid.uuid4().hex[:8]}"))
                raise
            self.built[key] = (folder, value)
        folder, value = self.built[key]
        shutil.copytree(folder / part, dest)
        return dest, value


def empty_repo(folder: Path) -> None:
    """`folder/repo`: a `git init` and nothing else (the spawn tests start from it)."""

    subprocess.run(["git", "init", "-q", str(folder / "repo")], check=True, capture_output=True,
                   creationflags=NO_WINDOW)


def origin_base(folder: Path) -> dict:
    """`folder/origin`: a repository with its configuration and no commit; returns the environment its git
    commands run with (the one `Repo` gives its commands)."""

    # the copies' `origin` remote points at this worker's shared template origin; tests must not push to it
    return Repo(folder / "origin").env


def clone_of(parent: Path, files: dict, templates: Templates, name: str = "clone") -> Path:
    """`parent/name`: a fresh clone of a repo that tracks `files` (path -> text or bytes). The origin is a copy of
    the origin base; only its files, its commit and the clone are made per test variant."""

    def build(folder: Path) -> None:
        origin = folder / "origin"
        _, env = templates.copy("origin base", origin_base, origin, part="origin")
        for rel, data in files.items():
            (origin / rel).parent.mkdir(parents=True, exist_ok=True)
            (origin / rel).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        _git(origin, env, "add", "-A")
        _git(origin, env, "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-m", "the repo")
        _git(folder, env, "clone", "-q", str(origin), str(folder / "clone"))

    copy, _ = templates.copy(f"clone {sorted(files.items())!r}", build, parent / name, part="clone")
    return copy


def _git(cwd: Path, env: dict, *args) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, creationflags=NO_WINDOW,
                   env=env)
