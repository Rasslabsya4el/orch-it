"""The settings of test runs (#394 decisions 1-5), read from the installation's settings file.

Keys (each optional): `test_command` (argv list; `{python}`, `{report}`, `{ids_file}` and
`{work_tree}` are filled per run; default pytest writing `--junitxml`), `test_run_bound_minutes`
(default 15), `test_env` (name -> value), `github_tests` (default on: the github place is offered whenever Actions runs for the repo, read at run time; false is the opt-out), `test_machines` (one
entry per machine place: `id`, `host`, `os`, `checkout`, optional `capacity` (1), `python`,
`on` (true), `origin` (default: the caller work tree's `origin`), `ssh_options` (list of extra
`ssh` arguments such as `["-p", "2222"]`)), `own_capacity` (1) and `github_capacity` (20, GitHub's
concurrent job cap on a free account), `github_priority` (a whole number, 0 for every repo: between repos of one
account a higher one starts first while the account is at its concurrency, #414 item 4; default in `defaults.json`, read
by `github_priority`), `required_checks` (the names of the checks a merge is held to; default
in `defaults.json` beside this module, read by `required_checks`), and the thin Windows job's four keys
(`windows_check`, `windows_os`, `windows_change_minutes`, `windows_change_runs`, `windows_slowest_listed`; defaults in the same
file, read by `windows_thin`), `place_bounds.queue_minutes` (the longest a GitHub job may wait in the account's
queue before the github place is left out, #414 item 6; default in `bound_defaults.json`). A key under `repos.<owner/name>` (any case)
overrides the same key for that repo.
A value of the wrong shape (a NUL in a command or `test_env` value, a bound over a week
included) raises `SettingsError("bad_setting")` naming the key.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from importlib import resources

from orch_it import settings
from orch_it.settings import SettingsError

DEFAULT_COMMAND = ("{python}", "-m", "pytest", "-q", "-p", "orch_it_ids", "--junitxml={report}")
OSES = ("linux", "windows", "macos")
MAX_BOUND_MINUTES = 7 * 24 * 60  # a week: a longer bound is no bound
REQUIRED_CHECKS_SETTING = "required_checks"
PRIORITY_SETTING = "github_priority"
REPLACED_SETTING = "github_required_jobs"  # the name `required_checks` replaced; a file that still has it is refused
_MACHINE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _default(key: str):
    """The default of the setting `key`, read from `defaults.json` beside this module."""

    text = resources.files(__package__).joinpath("defaults.json").read_text("utf-8")
    return json.loads(text)[key]


DEFAULT_REQUIRED_CHECKS = tuple(_default(REQUIRED_CHECKS_SETTING))
DEFAULT_GITHUB_PRIORITY = _default(PRIORITY_SETTING)
#: the longest a GitHub job may wait in the account's queue before the github place is left out: the same bound the
#: bound check holds (`place_bounds.queue_minutes`); read from its defaults file here, as `bound_check` imports the
#: merge package, which imports this module
DEFAULT_QUEUE_BOUND_MINUTES = json.loads(
    resources.files(__package__).joinpath("bound_defaults.json").read_text("utf-8"))["queue_minutes"]


def github_priority(raw: dict, repo: str) -> int:
    """The repo's priority between the repos of its account: the setting `github_priority`, a repo's own section
    (`repos.<owner/name>`, any case) overriding the top level, else the default. A whole number (not a bool); any
    other shape raises `SettingsError("bad_setting")`."""

    value = {**raw, **settings.repo_section(raw, repo)}.get(PRIORITY_SETTING, DEFAULT_GITHUB_PRIORITY)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError("bad_setting", f"{PRIORITY_SETTING}: a whole number")
    return value


def required_checks(raw: dict, repo: str) -> tuple[str, ...]:
    """The check names a merge is held to: the setting `required_checks`, a repo's own section
    (`repos.<owner/name>`, any case) overriding the top level, else the default. A non-empty list of
    non-empty names with no surrounding space; any other shape raises `SettingsError("bad_setting")`."""

    merged = {**raw, **settings.repo_section(raw, repo)}
    if REPLACED_SETTING in merged:
        raise SettingsError("bad_setting", f"{REPLACED_SETTING} replaced by {REQUIRED_CHECKS_SETTING}")
    if REQUIRED_CHECKS_SETTING not in merged:
        return DEFAULT_REQUIRED_CHECKS
    names = merged[REQUIRED_CHECKS_SETTING]
    if (not isinstance(names, list) or not names
            or not all(isinstance(name, str) and name and name == name.strip() for name in names)):
        raise SettingsError("bad_setting", REQUIRED_CHECKS_SETTING)
    return tuple(dict.fromkeys(names))


@dataclass(frozen=True)
class WindowsThin:
    """The thin Windows job (#414 unit 4): `check` is its check and job name; a per-change run's pytest takes
    `change_minutes` at most, judged on the median of the last `runs` per-change runs; a comment over it lists the
    `slowest` tests."""

    check: str
    os: str  # the `run_os` the store holds for its runs
    change_minutes: float
    runs: int
    slowest: int


def windows_thin(raw: dict, repo: str) -> WindowsThin:
    """The settings of the thin Windows job: each of its five keys from the settings (a repo's own section
    overriding the top level), else its default in `defaults.json`. A value of the wrong shape raises
    `SettingsError("bad_setting")` naming the key."""

    text = resources.files(__package__).joinpath("defaults.json").read_text("utf-8")
    merged = {**json.loads(text), **raw, **settings.repo_section(raw, repo)}
    names = []
    for key in ("windows_check", "windows_os"):
        if not (isinstance(merged[key], str) and merged[key] and merged[key] == merged[key].strip()):
            raise SettingsError("bad_setting", key)
        names.append(merged[key])
    return WindowsThin(*names, _positive("windows_change_minutes", merged["windows_change_minutes"]),
                       _count("windows_change_runs", merged["windows_change_runs"]),
                       _count("windows_slowest_listed", merged["windows_slowest_listed"]))


@dataclass(frozen=True)
class Machine:
    id: str
    host: str
    os: str
    checkout: str
    capacity: int = 1
    python: str | None = None  # default: `python` on Windows, `python3` elsewhere
    on: bool = True
    origin: str | None = None  # the url the machine clones and fetches from; default: the caller's origin
    ssh_options: tuple[str, ...] = ()  # extra `ssh` arguments before the host (-p, -i, -o ...)

    @property
    def place(self) -> str:
        return f"machine:{self.id}"


@dataclass(frozen=True)
class TestSettings:
    __test__ = False  # not a pytest class, whatever its name starts with

    command: tuple[str, ...] = DEFAULT_COMMAND
    bound_minutes: float = 15.0
    test_env: dict[str, str] = field(default_factory=dict)
    github_tests: bool = True  # the github place is offered whenever Actions runs for the repo; false never offers it
    machines: tuple[Machine, ...] = ()
    own_capacity: int = 1
    github_capacity: int = 20
    required_checks: tuple[str, ...] = DEFAULT_REQUIRED_CHECKS
    queue_bound_minutes: float = DEFAULT_QUEUE_BOUND_MINUTES

    @classmethod
    def from_settings(cls, raw: dict, repo: str) -> TestSettings:
        merged = dict(raw)
        repos = raw.get("repos", {})
        if not isinstance(repos, dict):
            raise SettingsError("bad_setting", "repos")
        # GitHub's owner/name is the same repo whatever its case
        own = next((value for key, value in repos.items() if str(key).lower() == repo.lower()), {})
        if not isinstance(own, dict):
            raise SettingsError("bad_setting", f"repos.{repo}")
        merged |= own
        values = {}
        if "test_command" in merged:
            command = merged["test_command"]
            if not (isinstance(command, list) and command
                    and all(isinstance(a, str) and "\x00" not in a for a in command)):
                raise SettingsError("bad_setting", "test_command")
            values["command"] = tuple(command)
        if "test_run_bound_minutes" in merged:
            values["bound_minutes"] = _positive("test_run_bound_minutes", merged["test_run_bound_minutes"])
        if "test_env" in merged:
            env = merged["test_env"]
            if not (isinstance(env, dict) and all(isinstance(k, str) and _ENV_NAME.fullmatch(k)
                                                  and isinstance(v, str) and "\x00" not in v
                                                  for k, v in env.items())):
                raise SettingsError("bad_setting", "test_env")
            values["test_env"] = dict(env)
        if "github_tests" in merged:
            if not isinstance(merged["github_tests"], bool):
                raise SettingsError("bad_setting", "github_tests")
            values["github_tests"] = merged["github_tests"]
        for key, name in (("own_capacity", "own_capacity"), ("github_capacity", "github_capacity")):
            if key in merged:
                values[name] = _count(key, merged[key])
        if "test_machines" in merged:
            values["machines"] = _machines(merged["test_machines"])
        values["required_checks"] = required_checks(raw, repo)
        if "place_bounds" in merged:
            bounds = merged["place_bounds"]
            if not isinstance(bounds, dict):
                raise SettingsError("bad_setting", "place_bounds")
            if "queue_minutes" in bounds:
                values["queue_bound_minutes"] = _positive("place_bounds.queue_minutes", bounds["queue_minutes"])
        return cls(**values)


def _positive(key: str, value: object) -> float:
    """A bound in minutes: more than 0, at most `MAX_BOUND_MINUTES`."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError("bad_setting", key)
    try:
        minutes = float(value)
    except OverflowError:
        raise SettingsError("bad_setting", key) from None
    if not (math.isfinite(minutes) and 0 < minutes <= MAX_BOUND_MINUTES):
        raise SettingsError("bad_setting", key)
    return minutes


def _count(key: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SettingsError("bad_setting", key)
    return value


def _machines(entries: object) -> tuple[Machine, ...]:
    if not isinstance(entries, list):
        raise SettingsError("bad_setting", "test_machines")
    machines, seen = [], set()
    for n, entry in enumerate(entries):
        where = f"test_machines[{n}]"
        if not isinstance(entry, dict):
            raise SettingsError("bad_setting", where)
        for key in ("id", "host", "os", "checkout"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise SettingsError("bad_setting", f"{where}.{key}")
        if entry["host"].startswith("-"):  # would read as an ssh option
            raise SettingsError("bad_setting", f"{where}.host")
        if not _MACHINE_ID.fullmatch(entry["id"]) or entry["id"] in seen:
            raise SettingsError("bad_setting", f"{where}.id")
        if entry["os"] not in OSES:
            raise SettingsError("bad_setting", f"{where}.os")
        python = entry.get("python")
        if python is not None and (not isinstance(python, str) or not python.strip()):
            raise SettingsError("bad_setting", f"{where}.python")
        origin = entry.get("origin")
        if origin is not None and (not isinstance(origin, str) or not origin.strip()):
            raise SettingsError("bad_setting", f"{where}.origin")
        options = entry.get("ssh_options", [])
        if not (isinstance(options, list) and all(isinstance(o, str) and o and "\x00" not in o
                                                  for o in options)):
            raise SettingsError("bad_setting", f"{where}.ssh_options")
        on = entry.get("on", True)
        if not isinstance(on, bool):
            raise SettingsError("bad_setting", f"{where}.on")
        seen.add(entry["id"])
        machines.append(Machine(entry["id"], entry["host"], entry["os"], entry["checkout"],
                                _count(f"{where}.capacity", entry.get("capacity", 1)), python, on,
                                origin, tuple(options)))
    return tuple(machines)
