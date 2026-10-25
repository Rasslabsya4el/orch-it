import os


def test_path_separator_is_slash():
    # throwaway probe: fails only on Windows
    assert os.sep == "/"
