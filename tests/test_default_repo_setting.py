"""The repo a command acts on is never a literal of the code (#413 unit 3): `--repo` wins, else the
top-level setting `repo`, else the work tree's GitHub origin, else the command exits 2 with a
message that names the three ways to give it. Seams: `cli.main(["--store", ..., "events", ...])` on
a temp work tree (the repo it writes into the event), `guards.hook._repo_of` (no fallback), and the
text of the modules that resolved the repo before. Invented values only: `acme-x/widgets-y`."""

import json
import subprocess
from pathlib import Path

import pytest

from orch_it import settings
from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.guards import hook

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FLAG_REPO = "acme-x/widgets-y"
SETTING_REPO = "acme-x/from-setting"
ORIGIN_REPO = "acme-x/from-origin"
MODULES = ("cli", "guards/hook", "guards/prepush", "merge/workflow_watch", "merge/workflow_watch_run",
           "settings")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=60,
                   creationflags=NO_WINDOW)


def work_tree(tmp_path, name, origin=None) -> Path:
    path = tmp_path / name
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    if origin is not None:
        git(path, "remote", "add", "origin", f"https://github.com/{origin}.git")
    return path


def write_settings(tmp_path, content: dict) -> None:
    (tmp_path / "settings.json").write_text(json.dumps(content), "utf-8")


def measured_repo(store) -> list[str]:
    return [row[0] for row in store.query("SELECT repo FROM events WHERE kind = 'value.measured'").rows]


def run_measure(store, capsys, *extra):
    code = main(["--store", str(store.root), "events", "measure-limit", "3", *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_repo_flag_wins_over_the_setting_and_the_origin(tmp_path, monkeypatch, store, capsys):
    write_settings(tmp_path,{"repo": SETTING_REPO})
    tree = work_tree(tmp_path, "tree", origin=ORIGIN_REPO)
    monkeypatch.chdir(tree)

    assert run_measure(store, capsys, "--repo", FLAG_REPO)[0] == 0
    assert measured_repo(store) == [FLAG_REPO]


def test_the_setting_wins_over_the_origin_when_no_flag_is_given(tmp_path, monkeypatch, store, capsys):
    write_settings(tmp_path,{"repo": SETTING_REPO})
    tree = work_tree(tmp_path, "tree", origin=ORIGIN_REPO)
    monkeypatch.chdir(tree)

    assert run_measure(store, capsys)[0] == 0
    assert measured_repo(store) == [SETTING_REPO]


def test_the_origin_of_the_work_tree_is_the_repo_when_no_flag_and_no_setting(tmp_path, monkeypatch, store, capsys):
    tree = work_tree(tmp_path, "tree", origin=FLAG_REPO)
    monkeypatch.chdir(tree)

    assert run_measure(store, capsys)[0] == 0
    assert measured_repo(store) == [FLAG_REPO]


def test_no_flag_no_setting_no_origin_exits_2_and_names_the_three_ways(tmp_path, monkeypatch, store, capsys):
    tree = work_tree(tmp_path, "tree")
    monkeypatch.chdir(tree)

    code, out, err = run_measure(store, capsys)
    assert code == 2
    assert out == ""
    assert "--repo" in err and "repo" in err and "origin" in err
    assert measured_repo(store) == []


def test_the_hook_reads_the_setting_then_the_origin_and_never_a_literal(tmp_path, monkeypatch):
    plain = work_tree(tmp_path, "plain")
    assert hook._repo_of(str(plain)) is None  # no fallback to any repo of the code

    write_settings(tmp_path,{"repo": SETTING_REPO})
    assert hook._repo_of(str(plain)) == SETTING_REPO


def test_a_setting_that_is_no_repo_is_refused(tmp_path, monkeypatch):
    write_settings(tmp_path,{"repo": "not-a-repo"})
    with pytest.raises(settings.SettingsError) as found:
        settings.default_repo()
    assert found.value.code == "bad_setting"


def test_no_module_that_resolves_the_repo_names_an_owner_or_a_default_constant():
    root = Path(__file__).resolve().parent.parent / "src" / "orch_it"
    for name in MODULES:
        text = (root / f"{name}.py").read_text("utf-8")
        assert "Rasslabsya4el" not in text, name
        assert "DEFAULT_REPO" not in text, name
