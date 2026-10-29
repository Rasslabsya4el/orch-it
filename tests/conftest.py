"""Shared test setup (#421 rule 2): no test reads or writes the real per-user settings file or data folder.

Every test gets ORCH_IT_SETTINGS set, through monkeypatch, to a settings file in a folder of its own under pytest's
temporary base. A test that sets the variable itself runs later and its own monkeypatch value wins.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory, monkeypatch):
    folder = tmp_path_factory.mktemp("settings")
    path = folder / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    # the per-user data folder (store, locks, caches) is the OS base plus "orch-it": point both bases at the test's own
    monkeypatch.setenv("LOCALAPPDATA", str(folder / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(folder / "share"))
