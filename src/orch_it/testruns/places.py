"""Test places (#394 decision 4): where one run of the repo's test command goes.

A place takes a `Request` and a scratch folder the runner owns, runs the command once on the
requested commit, and returns a `PlaceEnd` with the local path of the run's report (copied into
the scratch folder) and how the command ended. It raises `PlaceRefused` when it cannot take the
run and `PlaceVanished` when it is lost mid-run; the runner turns those into the `refused` and
`lost` outcomes. The own place runs in the caller's work tree as it is.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from orch_it.testruns import environment, process
from orch_it.testruns.pytest_plugin import FOLDER as PLUGIN_FOLDER

LOG_TAIL_CHARS = 2000
ENV_IDS = "ORCH_IT_IDS_FILE"


@dataclass(frozen=True)
class Request:
    work_tree: Path
    commit: str | None
    command: tuple[str, ...]
    test_env: dict[str, str]
    bound_seconds: float
    ids: list[str] | None = None


@dataclass(frozen=True)
class PlaceEnd:
    report: Path | None
    cut: bool
    slept: bool
    rc: int | None
    start_seconds: float
    run_seconds: float
    log_tail: str = ""


REFUSAL_CODES = ("busy", "os_missing", "place_off", "out_of_minutes")


class PlaceRefused(Exception):
    """The place could not take the run. `code` is one of `REFUSAL_CODES` (decision 2)."""

    def __init__(self, code: str, detail: str = ""):
        if code not in REFUSAL_CODES:
            raise ValueError(f"not a refusal code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class PlaceVanished(Exception):
    """The place was lost mid-run (the run's outcome is `lost` `place_vanished`)."""


def fill(command: tuple[str, ...], values: dict[str, str]) -> list[str]:
    """`command` with each `{name}` of `values` replaced in every argument."""

    filled = []
    for arg in command:
        for name, value in values.items():
            arg = arg.replace("{" + name + "}", value)
        filled.append(arg)
    return filled


def test_process_env(test_env: dict[str, str], ids_file: str | None, plugin_folder: str,
                     *, source=None, os_name: str | None = None) -> dict[str, str]:
    """The clean environment plus what the layer itself hands the test process: the ids file
    and the folder of the `orch_it_ids` plugin ahead of any `PYTHONPATH` of `test_env`."""

    env = environment.clean_environment(test_env, source=source, os_name=os_name)
    separator = ";" if (os_name or environment.this_os()) == "windows" else ":"
    env["PYTHONPATH"] = separator.join(p for p in (plugin_folder, test_env.get("PYTHONPATH")) if p)
    if ids_file:
        env[ENV_IDS] = ids_file
    return env


@dataclass
class OwnPlace:
    id: str = "own"
    os: str = field(default_factory=environment.this_os)

    def run(self, request: Request, scratch: Path) -> PlaceEnd:
        report = scratch / "junit.xml"
        ids_file = None
        if request.ids is not None:
            ids_file = scratch / "ids.txt"
            ids_file.write_text("".join(f"{i}\n" for i in request.ids), "utf-8")
        argv = fill(request.command, {"python": sys.executable, "report": str(report),
                                      "ids_file": str(ids_file or ""),
                                      "work_tree": str(request.work_tree)})
        env = test_process_env(request.test_env, str(ids_file) if ids_file else None,
                               str(PLUGIN_FOLDER))
        log = scratch / "output.log"
        try:
            done = process.run_bounded(argv, cwd=request.work_tree, env=env,
                                       bound_seconds=request.bound_seconds, log_path=log)
        except process.CommandMissing as e:
            return PlaceEnd(None, False, False, None, 0.0, 0.0, str(e)[-LOG_TAIL_CHARS:])
        return PlaceEnd(report if report.exists() else None, done.cut, done.slept, done.rc, 0.0,
                        done.seconds, _tail(log))


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(f.tell() - LOG_TAIL_CHARS * 4, 0))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return text[-LOG_TAIL_CHARS:]
