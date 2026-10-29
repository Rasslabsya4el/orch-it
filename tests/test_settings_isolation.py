"""Every test reads and writes its own settings file, never the real per-user one (#421 rule 2).

tests/conftest.py sets ORCH_IT_SETTINGS for each test to a file in that test's own temporary folder.
"""

import os
from pathlib import Path

from orch_it import settings


def test_the_settings_variable_names_a_fresh_file_under_this_tests_temporary_folder(tmp_path):
    named = os.environ.get("ORCH_IT_SETTINGS", "")
    assert named, "ORCH_IT_SETTINGS is unset: the test would use the real per-user settings file"
    named = Path(named)
    assert named.is_relative_to(tmp_path.parent)  # the pytest base folder, where every test's folder lives
    assert not named.exists()


def test_the_settings_file_of_a_test_is_not_the_real_per_user_one():
    assert settings.settings_path() == Path(os.environ["ORCH_IT_SETTINGS"])
    assert settings.settings_path() != settings.data_folder() / "settings.json"


def test_a_setting_written_by_a_test_lands_in_its_own_file():
    written = settings.save("isolation_probe", 1)
    assert written == Path(os.environ["ORCH_IT_SETTINGS"])
    assert settings.load()["isolation_probe"] == 1
    assert written != settings.data_folder() / "settings.json"


def test_a_test_that_sets_the_variable_itself_keeps_its_own_file(tmp_path, monkeypatch):
    own = tmp_path / "own.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(own))
    assert settings.settings_path() == own


def test_the_per_user_data_folder_of_a_test_is_a_folder_of_its_own(tmp_path):
    folder = settings.data_folder()
    assert folder.is_relative_to(tmp_path.parent)  # not the real per-user folder: store, locks, caches land here
