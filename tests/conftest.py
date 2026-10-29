"""Shared test setup (#421 rule 2): no test reads or writes the real per-user settings file or data folder.

Every test gets ORCH_IT_SETTINGS set, through monkeypatch, to a settings file in a folder of its own under pytest's
temporary base. A test that sets the variable itself runs later and its own monkeypatch value wins.
"""

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory, monkeypatch):
    folder = tmp_path_factory.mktemp("settings")
    path = folder / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    # the per-user data folder (store, locks, caches) is the OS base plus "orch-it": point both bases at the test's own
    monkeypatch.setenv("LOCALAPPDATA", str(folder / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(folder / "share"))


@pytest.fixture
def ticket_language_ru(isolated_settings):
    """The human block of a ticket is Russian (#413 unit 10a): the setting `human_language` is `ru`. For a test
    module whose ticket bodies (fixtures, texts the code writes) are Russian: `pytestmark = usefixtures(...)`."""
    Path(os.environ["ORCH_IT_SETTINGS"]).write_text(json.dumps({"human_language": "ru"}), "utf-8")


@pytest.fixture(scope="session")
def templates(tmp_path_factory):
    """One template cache for the whole session of a worker (#421 unit 10): every test module shares it, so a
    template is built once per worker, in that worker's own pytest base folder."""
    from repo_templates import Templates

    return Templates(tmp_path_factory.getbasetemp())
