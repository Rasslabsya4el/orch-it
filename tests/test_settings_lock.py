"""The settings lock reads a PermissionError on its folder's create as busy (#418): Windows refuses
the create while another writer removes the same folder, and that refusal waits inside the lock's own
wait and ends in its own timeout, as a FileExistsError does. Platform independent: the refusal is
monkeypatched on the settings module's `os`."""

import os

import pytest

from orch_it import settings


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    return path


def _refuse_lock_creates(monkeypatch, settings_file, times):
    """`os.mkdir` of the lock folder raises PermissionError the first `times` calls, then succeeds.
    Returns the counter of the calls made on the lock folder."""

    real_mkdir = os.mkdir
    calls = {"lock": 0}

    def mkdir(path, *args, **kwargs):
        if os.fspath(path).startswith(os.fspath(settings.settings_path())):  # the lock sits beside the file
            calls["lock"] += 1
            if calls["lock"] <= times:
                raise PermissionError(13, "refused while another writer removes the folder", os.fspath(path))
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(settings.os, "mkdir", mkdir)
    return calls


def test_a_refused_lock_create_is_waited_out_and_the_save_lands(settings_file, monkeypatch):
    calls = _refuse_lock_creates(monkeypatch, settings_file, times=3)
    settings.save_in_repo("owner-x/r1", "brain_handoff_issue", 4)
    assert calls["lock"] == 4  # three refusals, then the create that takes the lock
    assert settings.load()["repos"] == {"owner-x/r1": {"brain_handoff_issue": 4}}
    assert [entry.name for entry in settings_file.parent.iterdir()] == [settings_file.name]  # lock released


def test_a_refusal_that_never_clears_ends_in_the_locks_busy_error(settings_file, monkeypatch):
    monkeypatch.setattr(settings, "LOCK_WAIT_SECONDS", 0.2)  # the lock's own timeout, shortened for the test
    _refuse_lock_creates(monkeypatch, settings_file, times=float("inf"))
    with pytest.raises(settings.SettingsError) as busy:
        settings.save("repos", {})
    assert busy.value.code == "settings_busy"
