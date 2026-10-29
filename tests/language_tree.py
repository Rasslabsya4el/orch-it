"""Helpers for the tests of `human_language` (#453 unit 1): the setting lives in the `settings.json` at the root of
the git work tree, so a test that needs a language makes a git work tree and writes that file into it."""

import json
import subprocess
from pathlib import Path


def git_tree(folder: Path) -> Path:
    """A folder that is a git work tree of its own (`git init`); returns it."""

    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(folder)], check=True, capture_output=True)
    return folder


def write_language(folder: Path, code: str | None) -> None:
    """The `settings.json` at the root of `folder` with `human_language` set to `code`; `None` removes the file
    (the language unset), so a later call in the same folder never keeps an earlier value."""

    path = folder / "settings.json"
    if code is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps({"human_language": code}), "utf-8")
