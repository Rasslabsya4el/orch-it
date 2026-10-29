"""The settings of one installation: a JSON file in the per-user data folder of the OS.

`ORCH_IT_SETTINGS` names another settings file; `ORCH_IT_STORE` overrides the `store` setting.
A missing file is no settings at all, never an error.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from orch_it.events.schema import is_repo


class SettingsError(ValueError):
    """`code`: `settings_unreadable` (the settings file is no JSON object of settings),
    `bad_setting` (a setting of the wrong shape; the field names which part), `settings_busy`
    (another writer held the settings lock too long, or the file could not be replaced)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class HumanLanguageError(SettingsError):
    """`human_language` with no language code or no table: `bad_setting`, the one named bad setting the tickets
    hook refuses instead of failing open (D4 amd 2). Every other settings error keeps failing open."""


def data_folder() -> Path:
    """The per-user data folder of orch-it on this OS (#393 decision 3)."""

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "orch-it"


def settings_path() -> Path:
    return Path(os.environ.get("ORCH_IT_SETTINGS") or data_folder() / "settings.json")


def load() -> dict:
    path = settings_path()
    for attempt in range(40):  # Windows refuses a read while a writer replaces the file
        try:
            text = path.read_text("utf-8-sig")
            break
        except FileNotFoundError:
            return {}
        except PermissionError as e:
            if attempt == 39:
                raise SettingsError("settings_unreadable", f"{path}: {e}") from None
            time.sleep(0.05)
        except (OSError, UnicodeDecodeError) as e:
            raise SettingsError("settings_unreadable", f"{path}: {e}") from None
    try:
        settings = json.loads(text)
    except ValueError as e:
        raise SettingsError("settings_unreadable", f"{path}: {e}") from None
    if not isinstance(settings, dict):
        raise SettingsError("settings_unreadable", f"{path}: not an object")
    return settings


def save(key: str, value: object) -> Path:
    """Set one setting, keeping the others; the file is replaced whole, never half written, under
    the settings lock, so two writers at once both land."""

    with _locked():
        settings = load()
        settings[key] = value
        return _write(settings)


LOCK_WAIT_SECONDS = 10  # a writer waits this long for another one's lock
LOCK_STALE_SECONDS = 30  # a lock this old was left by a writer that died: it is taken away


@contextlib.contextmanager
def _locked():
    """The settings lock: a folder beside the file, made by one writer at a time."""

    lock = settings_path().with_name(settings_path().name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            os.mkdir(lock)
            break
        except (FileExistsError, PermissionError):  # Windows: a create refused while another writer removes it
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    os.rmdir(lock)
                    continue
            except OSError:
                pass  # taken away by another writer meanwhile: try again
            if time.monotonic() > deadline:
                raise SettingsError("settings_busy", f"{lock}: held longer than {LOCK_WAIT_SECONDS} s") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _write(settings: dict) -> Path:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", "utf-8")
    for attempt in range(40):  # Windows refuses a replace while a reader holds the file open
        try:
            os.replace(temporary, path)
            return path
        except PermissionError:
            if attempt == 39:
                temporary.unlink(missing_ok=True)
                raise SettingsError("settings_busy", f"{path}: could not be replaced") from None
            time.sleep(0.05)
    return path


def repo_section(raw: dict, repo: str) -> dict:
    """The section `repos.<owner/name>` of the settings (the repo's name in any case), or `{}`.
    Two spellings of one repo, or a section that is no object, raise `bad_setting`."""

    repos = raw.get("repos", {})
    if not isinstance(repos, dict):
        raise SettingsError("bad_setting", "repos")
    spellings = [key for key in repos if str(key).lower() == repo.lower()]
    if len(spellings) > 1:
        raise SettingsError("bad_setting", f"repos: {repo} written {len(spellings)} ways")
    own = repos[spellings[0]] if spellings else {}
    if not isinstance(own, dict):
        raise SettingsError("bad_setting", f"repos.{repo}")
    return own


def save_in_repo(repo: str, key: str, value: object) -> Path:
    """Set one setting of the section `repos.<owner/name>`, keeping every other one; the repo
    keeps the spelling its section already has."""

    with _locked():
        raw = load()
        repo_section(raw, repo)  # a bad section is refused before anything is written
        repos = raw.setdefault("repos", {})
        spelling = next((name for name in repos if str(name).lower() == repo.lower()), repo)
        repos[spelling] = {**repos.get(spelling, {}), key: value}
        return _write(raw)


def default_repo() -> str | None:
    """The repo a command takes when it is given no `--repo` and its work tree has no GitHub origin:
    the top-level setting `repo` (`owner/name`), or None when the setting is absent. A value that is
    no `owner/name` is `bad_setting`. The callers, each reading the origin before this setting:
    cli `_repo_of` (flag, then origin, then this); hook `_repo_of` (origin, then this, a bad value
    printed to stderr and read as None); prepush (remote url, then hook `_repo_of`). Never a literal."""

    chosen = load().get("repo")
    if chosen is None:
        return None
    if not is_repo(chosen):
        raise SettingsError("bad_setting", f"repo: {chosen!r} is no owner/name")
    return chosen


PACKAGE_TABLES = Path(__file__).parent / "language_tables"  # the tables orch-it ships (`en.toml`, `ru.toml`)
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*")  # a table's file stem: never a path


def language_tables_dirs() -> list[Path]:
    """The folders searched for a language table, first match wins: the folder of the setting
    `language_tables_dir` (an installation adds or replaces a table there), then the package's own."""

    chosen = load().get("language_tables_dir")
    if chosen is None:
        return [PACKAGE_TABLES]
    if not isinstance(chosen, str) or not chosen:
        raise SettingsError("bad_setting", f"language_tables_dir: {chosen!r} is no folder")
    return [Path(chosen), PACKAGE_TABLES]


def table_path(language: str) -> Path | None:
    """The table file of a language in the first folder that has one, or None."""

    return next((p for folder in language_tables_dirs() if (p := folder / f"{language}.toml").is_file()), None)


TREE_SETTINGS = "settings.json"  # the settings file a work tree carries at its root (its one name and format)
GIT_SECONDS = 30  # a git call that takes longer says no work tree: the language then reads English
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def tree_settings(root: Path) -> dict | None:
    """The settings file a work tree carries at its root, parsed; None when it has none. A file that is
    no JSON object raises `SettingsError`, as the installation's does. The one reader of that file: the
    required checks (`research.names`) and the language read it through here."""

    path = Path(root) / TREE_SETTINGS
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError) as e:
        raise SettingsError("settings_unreadable", f"{path}: {e}") from None
    if not isinstance(raw, dict):
        raise SettingsError("settings_unreadable", f"{path}: not an object")
    return raw


def _work_tree_of_cwd() -> Path | None:
    """The top level of the git work tree around the current folder; None when there is none or git cannot say."""

    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GIT_SECONDS,
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    top = done.stdout.strip()
    return Path(top) if done.returncode == 0 and top else None


def human_language(work_tree: Path | None = None) -> str:
    """The language of text written to a human: the `human_language` of the `settings.json` at the root of the
    git work tree (`work_tree`, else the tree around the current folder), `en` when there is no such tree, no such
    file or no such key. The installation file never sets it: a `human_language` there is `bad_setting`, naming
    the tree file the value moves to (D4 amd 2). A value with no table (`<language>.toml` in a folder of
    `language_tables_dirs`) is `bad_setting` naming the files looked for: no other language is written instead."""

    installation = load()
    tree = Path(work_tree) if work_tree is not None else _work_tree_of_cwd()
    if "human_language" in installation:
        where = str(tree / TREE_SETTINGS) if tree is not None else f"the {TREE_SETTINGS} of the git work tree"
        raise HumanLanguageError(
            "bad_setting", f"human_language: {installation['human_language']!r} is set in the installation file; "
                           f"move it to {where}")
    if tree is None:
        return "en"
    chosen = (tree_settings(tree) or {}).get("human_language", "en")
    if not isinstance(chosen, str) or not _LANGUAGE.fullmatch(chosen):
        raise HumanLanguageError("bad_setting", f"human_language: {chosen!r} is no language code")
    if table_path(chosen) is None:
        expected = " or ".join(str(folder / f"{chosen}.toml") for folder in language_tables_dirs())
        raise HumanLanguageError("bad_setting", f"human_language: no table for {chosen!r} (expected {expected})")
    return chosen


def store_path() -> Path:
    """Where this machine's store lives: `ORCH_IT_STORE`, else the `store` setting, else
    `<data folder>/events`."""

    chosen = os.environ.get("ORCH_IT_STORE") or load().get("store")
    if chosen is not None and not isinstance(chosen, str):
        raise SettingsError("settings_unreadable", f"store: {chosen!r} is no path")
    return Path(chosen) if chosen else data_folder() / "events"
