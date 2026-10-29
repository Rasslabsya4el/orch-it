"""The tickets hook under a bad `human_language` (#413 decision 6, brain decision on item 3, D4 amd 2).

A `human_language` with no table is a setting the hook cannot use: the hook refuses the `gh issue create`
or body edit, naming the setting and the missing table. The refusal is the hook's ordinary deny (stdout JSON,
exit 0). Any other error, a settings file that cannot be read included, still fails open with its event. Under
`ru` or unset the same command is judged as today. Invented values only: repo from `fake_hub`, a made-up `fr`
setting with no table."""

import json
import shlex
from pathlib import Path

import pytest
from fake_hub import REPO, FakeHub
from language_tree import git_tree, write_language
from ticket_fixtures import fixture

from orch_it.events import schema
from orch_it.guards.values import Values
from orch_it.tickets import hook

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"
WITH_SKILL = TRANSCRIPTS / "write_ticket_called.jsonl"
BODY_RU = fixture("good_basic")[1]  # a body with a Russian human block (the shipped fixture set)


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture(autouse=True)
def setting_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    monkeypatch.setenv("ORCH_IT_STORE", str(tmp_path / "events"))
    monkeypatch.chdir(git_tree(tmp_path / "tree"))  # the language is the tree file's, read from the cwd (#453 unit 1)
    monkeypatch.setattr("orch_it.guards.values.read_values", lambda **kwargs: Values())
    monkeypatch.setattr(hook, "make_hub", lambda: hub())
    monkeypatch.setattr(hook, "repo_of", lambda folder: REPO)
    monkeypatch.setattr(hook, "_record", lambda *args, **kwargs: None)
    return path


def hub():
    h = FakeHub()
    h.add(10, BODY_RU, ["ready-for-agent"], title="Cart shows the shipping cost before payment")
    return h


def configure(setting_file, **values):
    """`human_language` goes to the settings.json of the cwd's work tree; the rest to the installation file."""
    write_language(Path.cwd(), values.pop("human_language", None))
    setting_file.write_text(json.dumps(values), "utf-8")


def payload(command, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
            "cwd": str(cwd), "transcript_path": str(WITH_SKILL)}


def create_command(tmp_path, body=BODY_RU):
    path = tmp_path / "body.md"
    path.write_text(body, "utf-8")
    return (f"gh issue create --repo {REPO} --title 'Cart shows the shipping cost before payment'"
            f" --body-file {shlex.quote(str(path))}")


def edit_command(tmp_path, body=BODY_RU):
    path = tmp_path / "body.md"
    path.write_text(body, "utf-8")
    return f"gh issue edit 10 --repo {REPO} --body-file {shlex.quote(str(path))}"


def said(capsys):
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else None


def test_a_create_with_a_human_block_under_a_language_with_no_table_is_refused_naming_the_setting(
        tmp_path, capsys, setting_file):
    configure(setting_file, human_language="fr")
    assert hook.main(json.dumps(payload(create_command(tmp_path), tmp_path))) == 0
    reason = said(capsys)
    assert reason is not None, "a refusal prints a deny"
    assert "human_language" in reason
    assert "'fr'" in reason
    assert "fr.toml" in reason


def test_a_body_edit_under_a_language_with_no_table_is_refused_naming_the_setting(tmp_path, capsys, setting_file):
    configure(setting_file, human_language="fr")
    assert hook.main(json.dumps(payload(edit_command(tmp_path), tmp_path))) == 0
    reason = said(capsys)
    assert reason is not None, "a refusal prints a deny"
    assert "human_language" in reason
    assert "'fr'" in reason
    assert "fr.toml" in reason


def test_under_ru_the_same_create_is_judged_and_passes(tmp_path, capsys, setting_file):
    configure(setting_file, human_language="ru")
    assert hook.main(json.dumps(payload(create_command(tmp_path), tmp_path))) == 0
    assert said(capsys) is None


def test_unset_the_same_create_is_judged_as_today_english_refuses_the_russian_block(tmp_path, capsys, setting_file):
    configure(setting_file)
    assert hook.main(json.dumps(payload(create_command(tmp_path), tmp_path))) == 0
    reason = said(capsys)
    assert reason is not None
    assert "human_field_wrong_language" in reason
    assert "human_language" not in reason


def test_an_unexpected_error_still_fails_open_with_its_event(tmp_path, monkeypatch, capsys, setting_file):
    configure(setting_file, human_language="fr")
    recorded = []

    def broken(payload, **kwargs):
        raise RuntimeError("a bug in the hook")

    monkeypatch.setattr(hook, "judge", broken)
    monkeypatch.setattr(hook, "_record", lambda *args, **kwargs: recorded.append(kwargs))
    assert hook.main(json.dumps(payload(create_command(tmp_path), tmp_path))) == 0
    assert capsys.readouterr().out == ""
    assert recorded == [{"error": "RuntimeError"}]


def test_an_unreadable_settings_file_still_fails_open_with_its_event(tmp_path, monkeypatch, capsys, setting_file):
    setting_file.write_text("[]", "utf-8")  # no JSON object of settings: settings_unreadable, not a bad setting
    recorded = []
    monkeypatch.setattr(hook, "_record", lambda *args, **kwargs: recorded.append(kwargs))
    assert hook.main(json.dumps(payload(create_command(tmp_path), tmp_path))) == 0
    assert capsys.readouterr().out == ""
    assert recorded == [{"error": "SettingsError"}]


def test_a_named_bad_setting_keeps_the_codes_of_the_calls_before_it(tmp_path, setting_file):
    configure(setting_file, human_language="fr")
    too_long = f"gh issue edit 10 --repo {REPO} --title '{'x' * 300}'"  # a title-only call: no human block read
    refusal = hook.judge(payload(f"{too_long} && {edit_command(tmp_path)}", tmp_path), hub=hub(),
                         repo_of=lambda folder: REPO)
    assert "title_too_long" in refusal.codes
    assert refusal.codes[-1] == "bad_setting"


def test_the_refusal_reason_is_a_hook_code_in_the_closed_set(tmp_path, setting_file):
    assert "bad_setting" in hook.HOOK_CODES
    assert "bad_setting" in schema.codes_of("hook.ran", "reason")
