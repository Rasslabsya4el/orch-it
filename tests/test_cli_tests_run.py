"""`python -m orch_it tests run` (#394 decision 9): one run of the repo's tests, a line saying how
it ended, and an exit code a script reads without parsing it (0 passed, 1 failed, 3 any other
outcome, 2 an error)."""

import sys
import textwrap

import pytest

from orch_it import settings
from orch_it.cli import main
from orch_it.events.store import Store

REPO = "acme-x/widgets-y"


@pytest.fixture(autouse=True)
def no_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))


def suite(tmp_path, body: str):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "test_one.py").write_text(textwrap.dedent(body), "utf-8")
    return root


def run(tmp_path, root, *extra):
    return main(["--store", str(tmp_path / "store"), "tests", "run", "--work-tree", str(root),
                 "--repo", REPO, *extra])


def test_a_green_run_exits_0_and_says_passed(tmp_path, capsys):
    root = suite(tmp_path, "def test_one():\n    pass\n")
    assert run(tmp_path, root) == 0
    line = capsys.readouterr().out
    assert "place=own outcome=passed reason=- collected=1 failed=0" in line
    [(n,)] = Store.open(tmp_path / "store").query(
        "SELECT count(*) FROM events WHERE kind = 'step.ended' AND step = 'test_run'").rows
    assert n == 1


def test_a_red_run_exits_1(tmp_path, capsys):
    root = suite(tmp_path, "def test_one():\n    assert False\n")
    assert run(tmp_path, root) == 1
    assert "outcome=failed reason=red" in capsys.readouterr().out


def test_a_run_with_no_result_exits_3(tmp_path, capsys):
    root = suite(tmp_path, "def helper():\n    pass\n")
    assert run(tmp_path, root) == 3
    assert "outcome=no_result reason=none_collected" in capsys.readouterr().out


def test_the_test_command_setting_is_used_for_its_repo(tmp_path, capsys):
    settings.save("repos", {REPO: {"test_command": ["{python}", "-c", "pass"]}})
    root = suite(tmp_path, "def test_one():\n    pass\n")
    assert run(tmp_path, root) == 3
    assert "reason=summary_missing" in capsys.readouterr().out


def test_a_work_tree_with_no_github_origin_and_no_repo_is_an_error(tmp_path, capsys):
    root = suite(tmp_path, "def test_one():\n    pass\n")
    code = main(["--store", str(tmp_path / "store"), "tests", "run", "--work-tree", str(root)])
    assert code == 2
    assert "error: repo_unknown" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "darwin", reason="the own place has macOS here")
def test_a_run_needing_an_os_no_place_has_is_refused_with_exit_3(tmp_path, capsys):
    root = suite(tmp_path, "def test_one():\n    pass\n")
    assert run(tmp_path, root, "--os", "macos") == 3
    assert "outcome=refused reason=os_missing" in capsys.readouterr().out


def test_an_unreadable_ids_file_is_an_error(tmp_path, capsys):
    root = suite(tmp_path, "def test_one():\n    pass\n")
    assert run(tmp_path, root, "--ids-file", str(tmp_path / "missing.txt")) == 2
    assert "error: ids_file_unreadable" in capsys.readouterr().err
