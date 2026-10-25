"""Helpers shared by the worktree-guard tests (#395): a directory link that is a symlink, or a
junction on Windows where a symlink needs a privilege the runner may lack, and a path compare
that reads a path as the OS does."""

import os
import sys


def make_link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        make_junction(link, target)


def make_junction(link, target):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
