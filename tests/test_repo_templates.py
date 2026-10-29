"""The template cache of tests/repo_templates.py (#421 unit 10): a build that raises must not leave a half-made
template that the next test with the same key trips over."""

import pytest

from repo_templates import Templates


def test_a_build_that_raises_is_raised_again_by_the_next_copy_not_a_file_exists_error(tmp_path):
    templates = Templates(tmp_path / "base")

    def broken(folder):
        (folder / "half").write_text("x", "utf-8")
        raise RuntimeError("build failed")

    with pytest.raises(RuntimeError, match="build failed"):
        templates.copy("broken key", broken, tmp_path / "one", part="half")
    with pytest.raises(RuntimeError, match="build failed"):
        templates.copy("broken key", broken, tmp_path / "two", part="half")
