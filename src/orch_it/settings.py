"""The settings of one installation: a JSON file in the per-user data folder of the OS.

`ORCH_IT_SETTINGS` names another settings file; `ORCH_IT_STORE` overrides the `store` setting.
A missing file is no settings at all, never an error.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


class SettingsError(ValueError):
    """`code`: `settings_unreadable` (the settings file is no JSON object of settings),
    `bad_setting` (a setting of the wrong shape; the field names which part)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


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
    try:
        text = path.read_text("utf-8-sig")
    except FileNotFoundError:
        return {}
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
    """Set one setting, keeping the others; the file is replaced whole, never half written."""

    path = settings_path()
    settings = load()
    settings[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", "utf-8")
    os.replace(temporary, path)
    return path


def store_path() -> Path:
    """Where this machine's store lives: `ORCH_IT_STORE`, else the `store` setting, else
    `<data folder>/events`."""

    chosen = os.environ.get("ORCH_IT_STORE") or load().get("store")
    if chosen is not None and not isinstance(chosen, str):
        raise SettingsError("settings_unreadable", f"store: {chosen!r} is no path")
    return Path(chosen) if chosen else data_folder() / "events"
