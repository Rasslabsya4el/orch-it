"""The session hook on `gh issue create` and `gh issue edit` (#398 decision 3, acceptance rows 4
and 8): a body with shape violations is refused with every violation, a create in a session
that never called the `write-ticket` skill is refused, and a hand edit of a park's line or
label is refused (decision 4)."""

import json
import shlex
from pathlib import Path

import pytest
from fake_hub import REPO, FakeHub
from ticket_fixtures import EXPECTED, fixture

from orch_it import tickets
from orch_it.tickets import hook

pytestmark = [pytest.mark.usefixtures("ticket_language_ru"), pytest.mark.os_touching]  # Russian ticket bodies (#413 unit 10a); hooks (#414 unit 4)

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"
WITH_SKILL = TRANSCRIPTS / "write_ticket_called.jsonl"
WITHOUT_SKILL = TRANSCRIPTS / "write_ticket_not_called.jsonl"
BODY = fixture("good_basic")[1]
PARKED = "Parked: pr_ended #12 until 2026-11-30"


@pytest.fixture(autouse=True)
def no_values_of_this_machine(monkeypatch):
    """The bodies of these tests are judged as text: no value of this machine is read (#413 unit 6)."""
    from orch_it.guards.values import Values
    monkeypatch.setattr("orch_it.guards.values.read_values", lambda **kwargs: Values())


@pytest.fixture
def hub():
    hub = FakeHub()
    hub.add(10, BODY, ["ready-for-agent"], title="Cart shows the shipping cost before payment")
    hub.add(11, f"{PARKED}\n\n{BODY}", ["parked"], title="Cart shows the shipping cost before payment")
    hub.add(20, fixture("bad_weight_missing")[1], [], title="A ticket not yet ready")
    return hub


def payload(command, cwd, transcript=WITH_SKILL, tool="Bash"):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command},
            "cwd": str(cwd), "transcript_path": str(transcript) if transcript else None}


def judge(hub, command, cwd, **kwargs):
    return hook.judge(payload(command, cwd, **kwargs), hub=hub, repo_of=lambda folder: REPO)


def body_file(tmp_path, text, name="body.md"):
    path = tmp_path / name
    path.write_text(text, "utf-8")
    return path


def create(path, title="Cart shows the shipping cost before payment", extra=""):
    return f"gh issue create --repo {REPO} --title {shlex.quote(title)} --body-file {shlex.quote(str(path))} {extra}"


def test_a_command_that_writes_no_issue_is_not_judged(hub, tmp_path):
    for command in ("ls -la", "gh issue view 10", "gh issue comment 10 --body hi", "echo gh issue create",
                    "gh pr create --title x --body y", "git commit -m 'gh issue edit 10 --body x'"):
        assert judge(hub, command, tmp_path) is None, command


def test_a_good_ticket_created_after_the_skill_passes(hub, tmp_path):
    assert not judge(hub, create(body_file(tmp_path, BODY)), tmp_path)


def test_a_create_in_a_session_that_never_called_write_ticket_is_refused(hub, tmp_path):  # row 8
    refused = judge(hub, create(body_file(tmp_path, BODY)), tmp_path, transcript=WITHOUT_SKILL)
    assert refused.codes == ["write_ticket_missing"]
    assert "write-ticket" in refused.message
    assert judge(hub, create(body_file(tmp_path, BODY)), tmp_path, transcript=None).codes == ["write_ticket_missing"]


def test_every_violation_of_a_bad_body_is_printed(hub, tmp_path):
    title, body = fixture("bad_empty_body")
    refused = judge(hub, create(body_file(tmp_path, body), title=title), tmp_path)
    assert refused.codes == EXPECTED["bad_empty_body"]
    for code in EXPECTED["bad_empty_body"]:
        assert code in refused.message


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_hook_finds_the_fixture_set_s_violations(hub, tmp_path, name):  # row 2, the session side
    title, body = fixture(name)
    refused = judge(hub, create(body_file(tmp_path, body), title=title), tmp_path)
    assert [code for code in refused.codes if code in tickets.RULES] == EXPECTED[name]
    assert ("park_by_hand" in refused.codes) == (name == "good_parked")  # its line is the command's to write


def test_an_inline_body_and_a_relative_body_file_after_cd_are_read(hub, tmp_path):
    (tmp_path / "drafts").mkdir()
    body_file(tmp_path / "drafts", fixture("bad_weight_missing")[1], "t.md")
    command = f"cd drafts && gh issue create -R {REPO} -t 'Fine title' -F t.md"
    assert judge(hub, command, tmp_path).codes == ["weight_missing"]
    inline = f"gh issue create --title 'Fine title' --body {shlex.quote(fixture('bad_weight_missing')[1])}"
    assert judge(hub, inline, tmp_path).codes == ["weight_missing"]


@pytest.mark.parametrize("wrapped", [
    "bash -c {cmd}", "sh -c {cmd}", "eval {cmd}", "timeout 5 {raw}", "env GH_PAGER= {raw}", "command {raw}",
    "sudo -u x {raw}", "nohup {raw}", "/usr/bin/gh issue edit 10 --add-label parked", "gh.exe issue edit 10 --add-label parked",
    "cmd /c {raw}", "pwsh -NoProfile -Command {cmd}", "echo 10 | xargs gh issue edit --add-label parked",
    "if true; then {raw}; fi", "x=$(gh issue edit 10 --add-label parked)",
])
def test_a_wrapped_command_is_judged_like_the_plain_one(hub, tmp_path, wrapped):
    raw = "gh issue edit 10 --add-label parked"
    command = wrapped.format(raw=raw, cmd=shlex.quote(raw))
    refused = judge(hub, command, tmp_path)
    assert refused and refused.codes[0] in ("park_by_hand", "command_unreadable"), command


@pytest.mark.parametrize("command", [
    "$url = gh issue edit 10 --add-label parked", "$u = (gh issue edit 10 --add-label parked)",
    "try { gh issue edit 10 --add-label parked } catch { }", "& { gh issue edit 10 --add-label parked }",
    "1 | ForEach-Object { gh issue edit 10 --add-label parked }",
])
def test_powershell_s_own_forms_are_judged_too(hub, tmp_path, command):
    refused = judge(hub, command, tmp_path, tool="PowerShell")
    assert refused and refused.codes[0] == "park_by_hand", command


@pytest.mark.parametrize("target", [
    f"https://github.com/{REPO}/issues/20#issuecomment-5", f"https://github.com/{REPO}/issues/20?x=1",
    f"http://www.github.com/{REPO}/issues/20", f"HTTPS://GITHUB.COM/{REPO}/issues/20", "#20"])
def test_an_edit_target_in_any_form_gh_takes_is_read(hub, tmp_path, target):
    assert judge(hub, f"gh issue edit {target} --add-label ready-for-agent", tmp_path).codes == ["weight_missing"]


def test_the_repo_flag_before_the_subcommand_and_a_short_flag_with_equals_are_read(hub, tmp_path):
    path = shlex.quote(str(body_file(tmp_path, fixture("bad_weight_missing")[1])))
    assert judge(hub, f"gh issue -R {REPO} create -t T -F={path}", tmp_path).codes == ["weight_missing"]
    assert judge(hub, f"gh issue --repo={REPO} edit 20 --add-label ready-for-agent", tmp_path).codes == [
        "weight_missing"]


def test_a_transcript_line_of_any_json_shape_is_read_without_failing(hub, tmp_path):
    transcript = tmp_path / "t.jsonl"
    lines = ['"write-ticket"', '["write-ticket"]', '{"type": "user", "message": ["write-ticket"]}',
             '{"type": "assistant", "message": {"content": ["write-ticket", {"type": "tool_use", "name": "Skill", '
             '"input": "write-ticket"}]}}',
             '{"type": "user", "message": {"content": "<command-name>/some-plugin:write-ticket</command-name>"}}']
    transcript.write_text("\n".join(lines), "utf-8")
    assert not judge(hub, create(body_file(tmp_path, BODY)), tmp_path, transcript=transcript)
    transcript.write_text("\n".join(lines[:-1]), "utf-8")
    assert judge(hub, create(body_file(tmp_path, BODY)), tmp_path, transcript=transcript).codes == [
        "write_ticket_missing"]


def test_gh_s_own_alias_new_is_a_create(hub, tmp_path):
    refused = judge(hub, f"gh issue new --title T --body-file {shlex.quote(str(body_file(tmp_path, BODY)))}",
                    tmp_path, transcript=WITHOUT_SKILL)
    assert refused.codes == ["write_ticket_missing"]


def test_the_repo_of_an_edit_is_the_one_gh_would_use(hub, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_REPO", "someone/else")
    hub.add(10, fixture("bad_weight_missing")[1], ["ready-for-agent"], repo="someone/else")
    refused = hook.judge(payload("gh issue edit 10 --add-label ready-for-agent", tmp_path), hub=hub,
                         repo_of=lambda folder: REPO)
    assert refused.codes == ["weight_missing"] and refused.repo == "someone/else"


def test_powershell_writes_the_same_command(hub, tmp_path):
    path = body_file(tmp_path, fixture("bad_weight_missing")[1])
    command = f'gh issue create --repo {REPO} --title "Fine title" --body-file "{path}"'
    assert judge(hub, command, tmp_path, tool="PowerShell").codes == ["weight_missing"]


def test_a_body_the_hook_cannot_read_is_refused_with_how_to_write_it(hub, tmp_path):
    for command in (f"gh issue create --title T --body-file - <<'EOF'\n{BODY}\nEOF",
                    "gh issue create --title T --body-file \"$BODY_FILE\"",
                    f"gh issue create --title T --body-file {shlex.quote(str(tmp_path / 'absent.md'))}",
                    "gh issue create --title T --body \"$(cat body.md)\""):
        refused = judge(hub, command, tmp_path)
        assert refused.codes == ["body_unreadable"], command


def test_a_park_line_or_label_written_by_hand_is_refused(hub, tmp_path):  # row 4, decision 4
    with_line = body_file(tmp_path, f"{PARKED}\n\n{BODY}", "with_line.md")
    assert judge(hub, create(with_line), tmp_path).codes == ["park_by_hand"]
    assert judge(hub, create(body_file(tmp_path, BODY), extra="--label parked"), tmp_path).codes == ["park_by_hand"]
    assert judge(hub, f"gh issue edit 10 --repo {REPO} --body-file {shlex.quote(str(with_line))}",
                 tmp_path).codes == ["park_by_hand"]                                    # adds the line
    changed = body_file(tmp_path, f"Parked: pr_ended #13 until 2026-11-30\n\n{BODY}", "changed.md")
    assert judge(hub, f"gh issue edit 11 --body-file {shlex.quote(str(changed))}", tmp_path).codes == ["park_by_hand"]
    removed = body_file(tmp_path, BODY, "removed.md")
    assert judge(hub, f"gh issue edit 11 --body-file {shlex.quote(str(removed))}", tmp_path).codes == ["park_by_hand"]
    for flags in ("--add-label parked", "--remove-label parked", "--add-label ready-for-agent,Parked"):
        assert judge(hub, f"gh issue edit 10 {flags}", tmp_path).codes == ["park_by_hand"], flags


def test_a_rewrite_of_a_parked_ticket_that_keeps_its_line_passes(hub, tmp_path):
    kept = body_file(tmp_path, f"{PARKED}\n\n{BODY.replace('Weight: 3', 'Weight: 4')}")
    assert not judge(hub, f"gh issue edit 11 --body-file {shlex.quote(str(kept))}", tmp_path)


def test_an_edit_is_judged_on_the_ticket_it_leaves(hub, tmp_path):
    bad = body_file(tmp_path, fixture("bad_weight_missing")[1])
    assert judge(hub, f"gh issue edit 10 --body-file {shlex.quote(str(bad))}", tmp_path).codes == ["weight_missing"]
    assert judge(hub, "gh issue edit 10 --title 'A title holding #12'", tmp_path).codes == ["title_issue_number"]
    assert judge(hub, "gh issue edit 20 --add-label ready-for-agent", tmp_path).codes == ["weight_missing"]
    assert judge(hub, "gh issue edit 20 --add-label needs-info", tmp_path).codes == []  # not ready, body not rewritten
    assert judge(hub, f"gh issue edit 10 20 --body-file {shlex.quote(str(bad))}", tmp_path).codes == ["weight_missing"]
    url = f"https://github.com/{REPO}/issues/20"
    assert judge(hub, f"gh issue edit {url} --add-label ready-for-agent", tmp_path).codes == ["weight_missing"]


def test_an_edit_whose_ticket_cannot_be_read_is_refused(hub, tmp_path):
    hub.failing.add((REPO, 10))
    assert judge(hub, "gh issue edit 10 --add-label ready-for-agent", tmp_path).codes == ["issue_unreadable"]
    assert judge(hub, "gh issue edit 99 --add-label ready-for-agent", tmp_path).codes == ["issue_unreadable"]


def test_the_hook_prints_a_deny_with_every_violation_and_fails_open_on_its_own_error(hub, tmp_path, monkeypatch,
                                                                                      capsys):
    store = tmp_path / "events"
    monkeypatch.setenv("ORCH_IT_STORE", str(store))
    title, body = fixture("bad_empty_body")
    monkeypatch.setattr(hook, "make_hub", lambda: hub)
    monkeypatch.setattr(hook, "repo_of", lambda folder: REPO)
    data = json.dumps(payload(create(body_file(tmp_path, body), title=title), tmp_path))
    assert hook.main(data) == 0
    said = json.loads(capsys.readouterr().out)
    assert said["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "title_parked" in said["hookSpecificOutput"]["permissionDecisionReason"]

    def broken(*args, **kwargs):
        raise RuntimeError("a bug in the hook")

    monkeypatch.setattr(hook, "judge", broken)
    assert hook.main(data) == 0
    out, err = capsys.readouterr()
    from orch_it import language  # the words are the table's, in this module's language (ru, unit 10a)

    assert out == "" and language.text("ticket_hook_internal", error="RuntimeError: a bug in the hook") in err
    assert hook.main("not json") == 0


def test_a_refusal_and_a_pass_are_written_to_the_store(hub, tmp_path, monkeypatch):
    from orch_it.events.store import Store

    monkeypatch.setenv("ORCH_IT_STORE", str(tmp_path / "events"))
    monkeypatch.setattr(hook, "make_hub", lambda: hub)
    monkeypatch.setattr(hook, "repo_of", lambda folder: REPO)
    hook.main(json.dumps(payload(create(body_file(tmp_path, BODY)), tmp_path, transcript=WITHOUT_SKILL)))
    hook.main(json.dumps(payload(create(body_file(tmp_path, BODY)), tmp_path)))
    hook.main(json.dumps(payload("ls", tmp_path)))
    rows = Store.open(tmp_path / "events").query(
        "SELECT hook, action, reason, rules FROM events WHERE kind = 'hook.ran' ORDER BY at").rows
    assert [tuple(row) for row in rows] == [("tickets", "acted", "write_ticket_missing", '["write_ticket_missing"]'),
                                            ("tickets", "skipped", "passed", "[]")]


def test_every_code_the_hook_writes_is_in_the_closed_set():
    from orch_it.events import schema

    assert schema.codes_of("hook.ran", "rules") == set(tickets.RULES) | set(hook.HOOK_CODES)
    assert schema.codes_of("hook.ran", "reason") == set(hook.HOOK_CODES) | {"shape", "passed"}


def test_the_repo_s_claude_settings_run_the_hook_before_every_shell_command():
    def no_repeats(pairs):  # two `PreToolUse` keys would drop one hook without a word
        keys = [key for key, _ in pairs]
        assert len(keys) == len(set(keys)), keys
        return dict(pairs)

    settings = json.loads((Path(__file__).parents[1] / ".claude" / "settings.json").read_text("utf-8"),
                          object_pairs_hook=no_repeats)
    entries = [entry for entry in settings["hooks"]["PreToolUse"]
               if any(h.get("command") == 'sh "$CLAUDE_PROJECT_DIR"/hooks/orch-it -m orch_it.tickets.hook'
                      for h in entry["hooks"])]
    assert len(entries) == 1
    assert set(entries[0]["matcher"].split("|")) >= {"Bash", "PowerShell"}


def test_the_hook_runs_as_its_own_module_and_answers_a_plain_command_fast(tmp_path):
    import subprocess
    import sys
    import time

    started = time.monotonic()
    done = subprocess.run([sys.executable, "-m", "orch_it.tickets.hook"], input=json.dumps(payload("ls", tmp_path)),
                          capture_output=True, text=True, timeout=60,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert (done.returncode, done.stdout) == (0, "")
    assert time.monotonic() - started < 10  # one Python start; nothing of the store or of gh is loaded
