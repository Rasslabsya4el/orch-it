"""The human language is read from the repo the text is for (#464 rules 1-3, reader side).

An entry point names its target once (`settings.target`): a folder in a work tree on this machine, or another repo
read through the hub. The read takes it and never falls back to the process's own folder; a read with no target
named raises; a tree or repo settings file that cannot be read is a named bad setting carrying the file's name.
"""

import base64
import json
from pathlib import Path

import pytest
from orch_it import language, settings
from orch_it.testruns.github import GithubError
from orch_it.tickets.hub import GhHub

from language_tree import git_tree, write_language

REPO = "owner/other"
RU_KEY_SAMPLE = "ticket_not_read"


@pytest.fixture
def trees(tmp_path):
    """Two git trees: `ru` with a Russian settings file, `en` with none; the process's cwd is neither."""
    ru = git_tree(tmp_path / "ru")
    write_language(ru, "ru")
    return ru, git_tree(tmp_path / "en")


class HubStub:
    """Answers `file(repo, path)` from a dict; a value that is an exception is raised."""

    def __init__(self, files):
        self.files = files
        self.asked = []

    def file(self, repo, path):
        self.asked.append((repo, path))
        answer = self.files.get((repo, path))
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_read_with_no_target_named_fails(monkeypatch):
    monkeypatch.setattr(settings, "_target", None)  # what a new entry point that names none runs under
    with pytest.raises(settings.NoTargetError):
        settings.human_language()
    with pytest.raises(settings.NoTargetError):
        language.text(RU_KEY_SAMPLE, number=1)


def test_the_cwd_is_no_default_when_another_folder_is_named(trees, monkeypatch):
    ru, en = trees
    monkeypatch.chdir(ru)  # the process stands in the Russian tree ...
    with settings.target(folder=en):  # ... the entry point names the English one
        assert settings.human_language() == "en"
    monkeypatch.chdir(en)
    with settings.target(folder=ru):
        assert settings.human_language() == "ru"


def test_a_folder_inside_the_tree_reads_the_tree_file(trees):
    ru, _ = trees
    deep = ru / "src" / "deep"
    deep.mkdir(parents=True)
    with settings.target(folder=deep):
        assert settings.human_language() == "ru"


def test_a_named_target_is_left_when_its_block_ends(trees):
    ru, en = trees
    with settings.target(folder=ru):
        with settings.target(folder=en):
            assert settings.human_language() == "en"
        assert settings.human_language() == "ru"


def test_a_repo_target_reads_its_settings_file_through_the_hub(trees):
    ru, en = trees
    hub = HubStub({(REPO, "settings.json"): json.dumps({"human_language": "ru"})})
    with settings.target(repo=REPO, hub=hub):
        assert settings.human_language() == "ru"
    assert hub.asked == [(REPO, "settings.json")]


def test_a_repo_with_no_settings_file_is_english():
    with settings.target(repo=REPO, hub=HubStub({})):
        assert settings.human_language() == "en"


@pytest.mark.parametrize("raw", ["{", "[]", "7"])
def test_a_tree_settings_file_that_is_no_object_is_a_named_bad_setting(trees, raw):
    ru, _ = trees
    (ru / "settings.json").write_text(raw, "utf-8")
    with settings.target(folder=ru):
        with pytest.raises(settings.SettingsFileError) as refused:
            settings.human_language()
    assert refused.value.file == str(ru / "settings.json")
    assert refused.value.code == "settings_unreadable"
    assert str(ru / "settings.json") in str(refused.value)


@pytest.mark.parametrize("raw", ["{", "[]"])
def test_a_repo_settings_file_that_is_no_object_names_the_repo(raw):
    with settings.target(repo=REPO, hub=HubStub({(REPO, "settings.json"): raw})):
        with pytest.raises(settings.SettingsFileError) as refused:
            settings.human_language()
    assert REPO in refused.value.file
    assert REPO in str(refused.value)


def test_a_failed_repo_read_is_a_named_bad_setting_naming_the_repo():
    hub = HubStub({(REPO, "settings.json"): GithubError("gh_failed", "HTTP 500")})
    with settings.target(repo=REPO, hub=hub):
        with pytest.raises(settings.SettingsFileError) as refused:
            settings.human_language()
    assert refused.value.file == f"{REPO}:settings.json"
    assert "HTTP 500" in str(refused.value)


def test_the_hub_reads_a_file_on_the_default_branch_and_not_found_is_none():
    asked = []

    def gh(args):
        asked.append(args)
        if "missing.json" in args[1]:
            raise GithubError("gh_failed", "gh: Not Found (HTTP 404)")
        content = base64.b64encode(b'{"human_language": "ru"}').decode()
        return json.dumps({"encoding": "base64", "content": content})

    hub = GhHub(gh)
    assert hub.file(REPO, "settings.json") == '{"human_language": "ru"}'
    assert asked == [["api", f"repos/{REPO}/contents/settings.json"]]  # no ref: the default branch
    assert hub.file(REPO, "missing.json") is None


def test_the_hub_raises_a_failed_read_that_is_not_not_found():
    def gh(args):
        raise GithubError("gh_failed", "HTTP 500")

    with pytest.raises(GithubError):
        GhHub(gh).file(REPO, "settings.json")


def test_the_tickets_hook_names_the_folder_of_the_payload_as_its_target(trees, monkeypatch, capsys):
    from orch_it.tickets import hook

    ru, en = trees
    monkeypatch.chdir(en)  # the process stands in the English tree ...
    monkeypatch.setattr(hook, "make_hub", lambda: HubStub({}))
    monkeypatch.setattr(hook, "repo_of", lambda folder: REPO)
    monkeypatch.setattr(hook, "_record", lambda *args, **kwargs: None)
    call = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(ru),  # ... the call runs in the Russian one
            "tool_input": {"command": f"gh issue create --repo {REPO} --title t --body b"}}
    assert hook.main(json.dumps(call)) == 0
    said = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert any("Ѐ" <= ch <= "ӿ" for ch in said), said


def test_the_text_hook_and_the_command_line_name_a_target_of_their_own(trees, monkeypatch):
    from orch_it import cli
    from orch_it.guards import text_hook

    ru, en = trees
    seen = []
    monkeypatch.chdir(ru)
    monkeypatch.setattr(cli, "_main", lambda argv: seen.append(settings.human_language()) or 0)
    monkeypatch.setattr(text_hook, "_check", lambda raw: seen.append(settings.human_language()) or 0)
    assert cli.main([]) == 0
    assert text_hook.main(json.dumps({"cwd": str(en)})) == 0
    assert seen == ["ru", "en"]


def test_no_target_is_no_settings_error_and_no_refusal_text(monkeypatch):
    """Row 6: the read fails in `text` and in `text_or_refusal` alike; a refusal helper never turns it into words."""
    monkeypatch.setattr(settings, "_target", None)
    assert not issubclass(settings.NoTargetError, settings.SettingsError)
    with pytest.raises(settings.NoTargetError):
        language.text_or_refusal(RU_KEY_SAMPLE, number=1)
    with pytest.raises(settings.NoTargetError):
        language.text(RU_KEY_SAMPLE, number=1)


def test_the_hub_raises_a_file_it_cannot_read_as_text_naming_the_repo():
    """A file GitHub returns with no base64 content (over 1 MB) is no `en`: the read failed, and says whose."""

    def gh(args):
        return json.dumps({"encoding": "none", "content": "", "size": 2_000_000})

    with pytest.raises(GithubError) as refused:
        GhHub(gh).file(REPO, "settings.json")
    assert REPO in str(refused.value)
    with settings.target(repo=REPO, hub=GhHub(gh)):
        with pytest.raises(settings.SettingsFileError) as named:
            settings.human_language()
    assert REPO in named.value.file


def test_a_hook_that_cannot_find_its_own_folder_still_fails_open(monkeypatch, capsys):
    from orch_it.tickets import hook

    def gone():
        raise OSError("the folder is gone")

    monkeypatch.setattr("os.getcwd", gone)
    assert hook.main("not json") == 0  # no payload folder, no process folder: the hook passes the call, not crashes


def _module_names_with_an_entry_point():
    """Every module under src/orch_it with a top-level `main` or an `if __name__ == "__main__"` block."""
    import ast

    root = Path(settings.__file__).parent
    found = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        mains = any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body)
        guarded = any(isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                      and getattr(n.test.left, "id", "") == "__name__" for n in tree.body)
        if mains or guarded:
            found.add(".".join(path.relative_to(root.parent).with_suffix("").parts))
    return found


# the entry points that reach a language read, which name their target: each is run below; the one that writes no
# human text is listed with why. A new entry point fails the first test until it is named here and run below.
NAMING = {"orch_it.cli", "orch_it.tickets.hook", "orch_it.guards.text_hook"}
WRITES_NO_HUMAN_TEXT = {"orch_it.__main__": "calls cli.main", "orch_it.testruns.remote": "the remote worker's JSON only"}


def test_every_entry_point_is_listed_as_naming_its_target_or_as_writing_no_human_text():
    assert _module_names_with_an_entry_point() == NAMING | set(WRITES_NO_HUMAN_TEXT)
    source = (Path(settings.__file__).parent / "testruns" / "remote.py").read_text("utf-8")
    assert "language" not in source


def test_each_entry_point_names_a_target_before_a_language_read(trees, monkeypatch):
    """Row 6, the drift guard: with the suite's target cleared, the inner work of each entry point reads the language;
    the entry point must have named one, or `NoTargetError` escapes."""
    from orch_it import cli
    from orch_it.guards import text_hook
    from orch_it.tickets import hook

    ru, _ = trees
    monkeypatch.setattr(settings, "_target", None)
    monkeypatch.chdir(ru)
    read = []

    def reads_the_language(*args):
        read.append(settings.human_language())
        return 0

    monkeypatch.setattr(cli, "_main", reads_the_language)
    monkeypatch.setattr(text_hook, "_check", reads_the_language)
    monkeypatch.setattr(hook, "_main", reads_the_language)
    assert cli.main([]) == 0
    assert text_hook.main("{}") == 0
    assert hook.main("{}") == 0
    assert read == ["ru", "ru", "ru"]
    assert settings._target is None  # each left the process as it found it
