"""Path resolution for the worktree-delete guard (#395 decision 2): the real path on disk, links
followed, names compared by the case rule measured on the folder that holds them.

The runners' file systems are known: the `linux` job's is case-sensitive, the `windows` job's
(NTFS) is not, so a test may state the expected rule per OS; the guard itself never asks."""

import sys

from orch_it.guards import paths
from guard_links import make_link, same_path as same

CASE_INSENSITIVE_HERE = sys.platform in ("win32", "darwin")


def test_the_case_rule_is_measured_on_the_folder(tmp_path):
    (tmp_path / "Sample").mkdir()
    assert paths.measure_case_rule(str(tmp_path)) is CASE_INSENSITIVE_HERE


def test_a_relative_path_is_joined_to_the_command_directory(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    real = paths.resolve("b", str(tmp_path / "a"), windows=sys.platform == "win32")
    assert same(real, tmp_path / "a" / "b")


def test_a_link_on_the_way_is_followed(tmp_path):
    (tmp_path / "wt" / "src").mkdir(parents=True)
    make_link(tmp_path / "door", tmp_path / "wt")
    real = paths.resolve(str(tmp_path / "door" / "src"), str(tmp_path), windows=sys.platform == "win32")
    assert same(real, tmp_path / "wt" / "src")


def test_a_name_keeps_its_spelled_case(tmp_path):
    (tmp_path / "wt").mkdir()
    real = paths.resolve(str(tmp_path / "WT"), str(tmp_path), windows=sys.platform == "win32")
    assert real.endswith("WT")


def test_msys_and_wsl_drive_forms_are_drive_paths_only_on_windows():
    assert paths.resolve("/c/Projects/x", "/", windows=True).lower() == r"c:\projects\x"
    assert paths.resolve("/cygdrive/d/x", "/", windows=True).lower() == r"d:\x"
    assert paths.resolve("/mnt/c/Projects/x", "/", windows=True).lower() == r"c:\projects\x"
    if sys.platform != "win32":
        assert paths.resolve("/c/Projects/x", "/", windows=False) == "/c/Projects/x"
        assert paths.resolve("/mnt/c/x", "/", windows=False) == "/mnt/c/x"




def test_a_link_loop_resolves_to_nothing(tmp_path):
    if sys.platform == "win32":
        import pytest
        pytest.skip("a junction loop needs a symlink privilege the windows job may lack")
    import os
    os.symlink(tmp_path / "b", tmp_path / "a")
    os.symlink(tmp_path / "a", tmp_path / "b")
    # it must not come back as a plain folder named after the link: that would be a silent pass
    assert paths.resolve(str(tmp_path / "a" / "x"), str(tmp_path), windows=False) is None


def test_a_nul_byte_never_crashes_resolution():
    assert paths.measure_case_rule("a\x00b") in (True, False)
    assert paths.resolve("a\x00b", None, windows=sys.platform == "win32") is None


def test_the_error_codes_are_a_closed_set():
    import pytest
    with pytest.raises(ValueError):
        paths.GuardError("made_up")
    assert paths.FINDING_RULES == ("protected_root", "ancestor_of_root", "inside_other_root",
                                   "link_outward")
