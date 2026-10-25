"""Path resolution for the worktree-delete guard (#395 decision 2): the real path on disk, links
followed, names compared by the case rule measured on the folder that holds them.

The runners' file systems are known: the `linux` job's is case-sensitive, the `windows` job's
(NTFS) is not, so a test may state the expected rule per OS; the guard itself never asks."""

import sys

from orch_it.guards import paths

CASE_INSENSITIVE_HERE = sys.platform in ("win32", "darwin")


def test_the_case_rule_is_measured_on_the_folder(tmp_path):
    (tmp_path / "Sample").mkdir()
    assert paths.measure_case_rule(str(tmp_path)) is CASE_INSENSITIVE_HERE


def make_link(link, target):
    """A directory link: a symlink, or on Windows a junction where symlinks need a privilege
    this machine does not grant."""

    import os
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


def same(a, b):
    import os
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


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
    assert paths.resolve("/c/Coding/x", "/", windows=True).lower() == r"c:\coding\x"
    assert paths.resolve("/cygdrive/d/x", "/", windows=True).lower() == r"d:\x"
    assert paths.resolve("/mnt/c/Coding/x", "/", windows=True).lower() == r"c:\coding\x"
    if sys.platform != "win32":
        assert paths.resolve("/c/Coding/x", "/", windows=False) == "/c/Coding/x"
        assert paths.resolve("/mnt/c/x", "/", windows=False) == "/mnt/c/x"


def test_a_link_loop_ends(tmp_path):
    if sys.platform == "win32":
        return  # a junction loop needs a symlink privilege the windows job may lack; POSIX shows the rule
    import os
    os.symlink(tmp_path / "b", tmp_path / "a")
    os.symlink(tmp_path / "a", tmp_path / "b")
    assert paths.resolve(str(tmp_path / "a" / "x"), str(tmp_path), windows=False)
