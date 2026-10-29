"""The repo a command acts on is never a literal of the code (#413 unit 3): `--repo` wins, else the
work tree's GitHub origin, else the top-level setting `repo` (the last fallback), else the command
exits 2 with a message that names the three ways to give it. Seams: `cli.main(["--store", ..., "events",
...])` on a temp work tree (the repo it writes into the event), `guards.hook._repo_of` (no fallback),
`cli.main(["guards", "scan", "--range", ...])` (a bad setting is read there and exits 2), and the text
of the modules that resolved the repo before. Invented values only: `acme-x/widgets-y`."""

import json
import re
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


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


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


def test_the_origin_wins_over_the_setting_when_no_flag_is_given(tmp_path, monkeypatch, store, capsys):
    write_settings(tmp_path,{"repo": SETTING_REPO})
    tree = work_tree(tmp_path, "tree", origin=ORIGIN_REPO)
    monkeypatch.chdir(tree)

    assert run_measure(store, capsys)[0] == 0
    assert measured_repo(store) == [ORIGIN_REPO]


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


def test_the_hook_reads_the_origin_then_the_setting_and_never_a_literal(tmp_path, monkeypatch):
    plain = work_tree(tmp_path, "plain")
    assert hook._repo_of(str(plain)) is None  # no fallback to any repo of the code

    write_settings(tmp_path,{"repo": SETTING_REPO})
    assert hook._repo_of(str(plain)) == SETTING_REPO
    origin = work_tree(tmp_path, "origin-tree", origin=ORIGIN_REPO)
    assert hook._repo_of(str(origin)) == ORIGIN_REPO  # the origin first, even with the setting


def test_the_hook_reads_a_bad_setting_as_no_repo_and_says_so_on_stderr(tmp_path, capsys):
    plain = work_tree(tmp_path, "plain")
    write_settings(tmp_path, {"repo": "not-a-repo"})
    assert hook._repo_of(str(plain)) is None
    assert "bad_setting" in capsys.readouterr().err


def test_a_bad_setting_exits_2_through_guards_scan_range_when_no_origin_exists(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    git(plain, "init", "-q", "-b", "main")
    for message in ("first", "second"):
        git(plain, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
            "--allow-empty", "-m", message)
    write_settings(tmp_path, {"repo": "not-a-repo"})
    code = main(["guards", "scan", "--range", "HEAD~1..HEAD", "--work-tree", str(plain),
                 "--machine-independent"])
    assert code == 2
    assert capsys.readouterr().err.startswith("error: bad_setting")


def test_a_setting_that_is_no_repo_is_refused(tmp_path, monkeypatch):
    write_settings(tmp_path,{"repo": "not-a-repo"})
    with pytest.raises(settings.SettingsError) as found:
        settings.default_repo()
    assert found.value.code == "bad_setting"


def test_no_module_that_resolves_the_repo_names_a_literal_repo_or_a_default_constant():
    root = Path(__file__).resolve().parent.parent / "src" / "orch_it"
    literal = re.compile(r"github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
    for name in MODULES:
        text = (root / f"{name}.py").read_text("utf-8")
        assert "DEFAULT_REPO" not in text, name
        assert not literal.search(text), name
