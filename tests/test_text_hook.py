"""The value check on text for another reader (#415 unit 4): `guards check-text` and the PreToolUse hook
`orch_it.guards.text_hook`, which judges the text of a call by its input, not by its tool name.

Rows of #415 held here: 3 (a prompt and a message each holding each kind are refused; another session's
scratchpad file passes), 7 (a tool no list names is judged by its input), 8 (a check that cannot run is
refused, naming the install step) and 9 (a write into the repo's records holding a kind is refused; the
`<scratchpad of session id>/<file>` form passes).

Every value is invented: the account `acct-x`, the repo `acct-x/proj-y`, the host `testbox-17`, the home folder
`C:\\Users\\tester9`, the projects folder `/srv/ws`, the git name `Tess Tester` and the e-mail `t9@example.invalid`.
The values come from `read_values` with a fake `run`, so the test touches no real machine.
"""

import io
import json
import re
import sys
from pathlib import Path

import pytest

from orch_it import cli, language
from orch_it.guards import kinds, text_hook
from orch_it.guards.values import Values, read_values

HOME_WIN = "C:\\Users\\tester9"
REPO_ROOT = "/srv/ws/proj-y"
REPO_ROOTS = (REPO_ROOT,)
SESSION_SCRATCH = ("C:\\Users\\tester9\\AppData\\Local\\Temp\\claude\\proj-y\\"
                   "3f2a9c1e-8b4d-4e6f-9a1b-2c3d4e5f6a7b\\scratchpad")
OTHER_SESSION_FILE = SESSION_SCRATCH.replace("3f2a9c1e", "7d1e0c2b") + "\\plan.md"
RULE = "no value that differs per repo, account, machine or OS; such a value is a setting with its default"

MACHINE = {
    "user.name": "Tess Tester",
    "user.email": "t9@example.invalid",
    "remote get-url": "https://github.com/acct-x/proj-y.git\n",
    "git-common-dir": "/srv/ws/proj-y/.git\n",
    "api user": "acct-x\n",
}


def fake_run(answers):
    def run(cmd):
        line = " ".join(cmd)
        for key, answer in answers.items():
            if key in line:
                return answer
        raise FileNotFoundError(cmd[0])

    return run


VALUES = read_values(env={"USERNAME": "tester9"}, run=fake_run(MACHINE), home=HOME_WIN, host="testbox-17")
JOB_NAMES = frozenset({"lint-and-types"})


def context(roots=()):
    return text_hook.Context(values=VALUES, job_names=JOB_NAMES, roots=tuple(roots))


CTX = context(REPO_ROOTS)

# one sample per kind the check knows: the 8 value kinds of values.py and the 3 pattern kinds of kinds.py
KIND_SAMPLES = {
    "account": "the owner is acct-x here",
    "repo": "clone acct-x/proj-y now",
    "host": "ssh testbox-17 -p 22",
    "home": "cd C:\\Users\\tester9\\src",
    "user": "USER=tester9",
    "projects_folder": "cd /srv/ws/other",
    "git_name": "Author: Tess Tester",
    "git_email": "mail to T9@Example.Invalid please",
    "bare_python": "run `python -m pytest -q` first",
    "one_os_call": "stop it with taskkill /F",
    "check_name_literal": 'the check "lint-and-types" failed',
}


def call(tool, tool_input, cwd=REPO_ROOT):
    return json.dumps({"session_id": "s1", "tool_name": tool, "tool_input": tool_input, "cwd": cwd})


def kinds_of(hits):
    return {hit.kind for hit in hits}


def reason_of(output):
    return json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]


def judge(payload_text, *, context_=None):
    payload = json.loads(payload_text)
    hits, _ = text_hook.judge_call(payload, lambda folder: context_ or CTX)
    return hits


def test_the_check_knows_eleven_kinds_and_each_has_a_sample():
    value_kinds = [f for f in Values.__dataclass_fields__ if f != "not_checked"]
    assert len(value_kinds) == 8
    assert len(kinds.KINDS) == 3
    assert set(KIND_SAMPLES) == {*value_kinds, *kinds.KINDS}
    assert len(KIND_SAMPLES) == 11


@pytest.mark.parametrize("kind", sorted(KIND_SAMPLES))
def test_a_prompt_holding_a_kind_is_refused(kind):
    hits = judge(call("Agent", {"description": "brief", "prompt": KIND_SAMPLES[kind]}))
    assert kind in kinds_of(hits)


@pytest.mark.parametrize("kind", sorted(KIND_SAMPLES))
def test_a_message_holding_a_kind_is_refused(kind):
    hits = judge(call("SendMessage", {"to": "helper", "message": KIND_SAMPLES[kind]}))
    assert kind in kinds_of(hits)


@pytest.mark.parametrize("kind", sorted(KIND_SAMPLES))
def test_a_write_of_a_repo_record_holding_a_kind_is_refused(kind):
    hits = judge(call("Write", {"file_path": f"{REPO_ROOT}/docs/STATE.md", "content": KIND_SAMPLES[kind]}))
    assert kind in kinds_of(hits)


@pytest.mark.parametrize("kind", sorted(KIND_SAMPLES))
def test_an_edit_of_a_grill_record_holding_a_kind_is_refused(kind):
    hits = judge(call("Edit", {"file_path": f"{REPO_ROOT}/docs/grill/2026-10-29.md",
                               "old_string": "x", "new_string": KIND_SAMPLES[kind]}))
    assert kind in kinds_of(hits)


def test_the_refusal_names_the_rule_and_the_kind():
    reason, _ = text_hook.decide(call("Agent", {"prompt": KIND_SAMPLES["git_email"]}), context=lambda f: CTX)
    assert RULE in reason
    assert "git_email" in reason
    assert "line 1" in reason


def test_the_refusal_is_in_the_human_language_of_the_setting(ticket_language_ru):
    reason, _ = text_hook.decide(call("Agent", {"prompt": KIND_SAMPLES["git_email"]}), context=lambda f: CTX)
    assert re.search("[Ѐ-ӿ]", reason)


def test_another_sessions_scratchpad_file_passes_in_a_prompt_and_a_message():
    prompt = f"read the other session's plan at {OTHER_SESSION_FILE} and continue"
    assert judge(call("Agent", {"prompt": prompt})) == []
    assert judge(call("SendMessage", {"message": f"the notes are at {OTHER_SESSION_FILE}"})) == []


def test_a_home_path_outside_the_scratchpad_form_is_refused():
    hits = judge(call("Agent", {"prompt": "read C:\\Users\\tester9\\notes\\plan.md"}))
    assert "home" in kinds_of(hits)


def test_the_run_time_locations_pass_in_a_prompt_only_when_they_are_inside_the_repo_or_a_worktree():
    worktree = f"{REPO_ROOT}/.claude/worktrees/wt-415-u4/src/orch_it/guards/text_hook.py"
    assert judge(call("Agent", {"prompt": f"edit {worktree}"}), context_=context()) == []
    assert judge(call("Agent", {"prompt": f"read {REPO_ROOT}/docs/STATE.md"}), context_=context(REPO_ROOTS)) == []
    assert "projects_folder" in kinds_of(judge(call("Agent", {"prompt": "read /srv/ws/other/x.md"}),
                                               context_=context(REPO_ROOTS)))
    assert "projects_folder" in kinds_of(judge(call("Agent", {"prompt": "read /srv/ws/proj-y-old/x.md"}),
                                               context_=context(REPO_ROOTS)))


def test_leaves_the_machine_allows_no_run_time_location():
    tool_input = {"file_path": f"{REPO_ROOT}/docs/STATE.md", "content": f"scratch is {SESSION_SCRATCH}\\plan.md"}
    assert "home" in kinds_of(judge(call("Write", tool_input)))


def test_the_session_id_form_passes_in_a_record_and_in_a_prompt():
    form = "the notes are in <scratchpad of session id>/plan.md"
    assert judge(call("Write", {"file_path": f"{REPO_ROOT}/docs/STATE.md", "content": form})) == []
    assert judge(call("Agent", {"prompt": form})) == []


def test_an_invented_tool_name_with_the_git_e_mail_in_a_message_is_refused():
    hits = judge(call("SendToTeammate", {"message": "author t9@example.invalid"}))
    assert "git_email" in kinds_of(hits)
    reason, _ = text_hook.decide(call("SendToTeammate", {"message": "author t9@example.invalid"}),
                                 context=lambda f: CTX)
    assert reason is not None and "git_email" in reason


def test_a_tool_input_key_no_text_field_names_is_not_judged():
    assert judge(call("SendToTeammate", {"payload": "author t9@example.invalid"})) == []


def test_a_write_outside_the_repo_is_not_judged():
    hits = judge(call("Write", {"file_path": "/tmp/notes/x.md", "content": KIND_SAMPLES["home"]}))
    assert hits == []


def test_a_multiedit_new_string_inside_the_repo_is_judged():
    tool_input = {"file_path": f"{REPO_ROOT}/docs/STATE.md",
                  "edits": [{"old_string": "a", "new_string": "fine"},
                            {"old_string": "b", "new_string": KIND_SAMPLES["git_name"]}]}
    assert "git_name" in kinds_of(judge(call("MultiEdit", tool_input)))


def test_a_clean_prompt_passes_with_no_output():
    reason, notice = text_hook.decide(call("Agent", {"prompt": "fix the parser at docs/x.md"}),
                                      context=lambda f: CTX)
    assert reason is None


def test_a_check_that_cannot_run_is_refused_and_names_the_install_step():
    def boom(folder):
        raise RuntimeError("no module named orch_it")

    reason, _ = text_hook.decide(call("Agent", {"prompt": "hello"}), context=boom)
    assert reason is not None
    assert "pip install" in reason
    assert "no module named orch_it" in reason


def test_a_call_with_no_text_field_does_not_read_the_check():
    def boom(folder):
        raise RuntimeError("must not be read")

    assert text_hook.decide(call("Agent", {"description": "", "prompt_id": 3}), context=boom) == (None, "")


def test_an_import_failure_of_the_check_is_refused_and_names_the_install_step(monkeypatch):
    monkeypatch.setitem(sys.modules, "orch_it.guards.values", None)
    reason, _ = text_hook.decide(call("Agent", {"prompt": "hello"}), context=text_hook.read_context)
    assert reason is not None and "pip install" in reason


def test_the_hook_entry_point_prints_the_deny_when_the_check_cannot_run(monkeypatch, capsys):
    def gone(folder):
        raise OSError("gone")

    monkeypatch.setattr(text_hook, "read_context", gone)
    assert text_hook.main(call("Agent", {"prompt": "hello"})) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "pip install" in out["permissionDecisionReason"]


def test_the_hook_entry_point_prints_nothing_for_a_clean_call(capsys):
    assert text_hook.main(call("Agent", {"prompt": "fix the parser"})) == 0
    assert capsys.readouterr().out == ""


def test_the_hook_denies_when_the_module_itself_cannot_be_imported():
    # a broken import of the language module (a failure at module load, before main) must still deny, exit 0
    import os
    import subprocess

    src = str(Path(__file__).parents[1] / "src")
    code = ("import sys; sys.modules['orch_it.language'] = None; "
            "from orch_it.guards import text_hook; raise SystemExit(text_hook.main())")
    payload = call("Agent", {"prompt": "hello"})
    env = {**os.environ, "PYTHONPATH": src}
    done = subprocess.run([sys.executable, "-c", code], input=payload, capture_output=True, text=True,
                          env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "pip install" in out["permissionDecisionReason"]


def test_a_broken_language_module_at_refusal_time_still_denies_with_the_install_step(monkeypatch, capsys):
    import orch_it

    monkeypatch.setattr(orch_it, "language", None)
    monkeypatch.setattr(text_hook, "read_context", lambda folder: CTX)
    reason, _ = text_hook.decide(call("Agent", {"prompt": KIND_SAMPLES["git_email"]}), context=lambda f: CTX)
    assert reason is not None and "pip install" in reason
    assert text_hook.main(call("Agent", {"prompt": KIND_SAMPLES["git_email"]})) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"


def test_the_wiring_in_settings_names_the_module_under_a_catch_all_matcher():
    # a catch-all matcher (empty): the hook judges each call by its input, so a tool no list names is judged too
    settings = json.loads((Path(__file__).parents[1] / ".claude" / "settings.json").read_text("utf-8"))
    entries = [e for e in settings["hooks"]["PreToolUse"] if "text_hook" in e["hooks"][0]["command"]]
    assert len(entries) == 1
    assert entries[0]["matcher"] == ""
    command = entries[0]["hooks"][0]["command"]
    assert "-m orch_it.guards.text_hook" in command
    assert entries[0]["hooks"][0]["timeout"] == 120
    # fail closed: a non-zero exit of the shim or the hook (no Python, a crash) becomes exit 2, a block, with the
    # install step on stderr; Claude Code blocks a PreToolUse call on exit 2
    assert re.search(r"\|\|\s*\{[^}]*>&2;\s*exit 2;\s*\}\s*$", command), command


# --- guards check-text (the CLI) -------------------------------------------------------------------------


def run_cli(monkeypatch, capsys, argv, stdin_text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(text_hook, "read_context", lambda folder: CTX)
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_check_text_leaves_the_machine_is_clean_with_exit_0(monkeypatch, capsys):
    code, out, _ = run_cli(monkeypatch, capsys, ["guards", "check-text", "--leaves-machine"], "fix the parser\n")
    assert code == 0
    assert json.loads(out) == {"ok": True, "findings": []}


def test_check_text_leaves_the_machine_refuses_a_kind_with_exit_1(monkeypatch, capsys):
    code, out, _ = run_cli(monkeypatch, capsys, ["guards", "check-text", "--leaves-machine"],
                           KIND_SAMPLES["git_email"] + "\n")
    assert code == 1
    result = json.loads(out)
    assert result["ok"] is False
    assert result["findings"] == [{"kind": "git_email", "rule": language.english("text_rule")}]
    assert result["findings"][0]["rule"] == RULE


def test_check_text_stays_on_the_machine_passes_a_scratchpad_path_and_leaves_refuses_it(monkeypatch, capsys):
    stdin_text = f"read {OTHER_SESSION_FILE}\n"
    code, _, _ = run_cli(monkeypatch, capsys, ["guards", "check-text", "--stays-on-machine"], stdin_text)
    assert code == 0
    code, _, _ = run_cli(monkeypatch, capsys, ["guards", "check-text", "--leaves-machine"], stdin_text)
    assert code == 1


def test_check_text_needs_exactly_one_of_the_two_places(monkeypatch, capsys):
    code, _, _ = run_cli(monkeypatch, capsys, ["guards", "check-text"], "x")
    assert code == 2
    code, _, _ = run_cli(monkeypatch, capsys, ["guards", "check-text", "--leaves-machine", "--stays-on-machine"], "x")
    assert code == 2


def test_check_text_that_cannot_run_is_an_error_with_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("x"))

    def boom(folder):
        raise ImportError("no orch_it values")

    monkeypatch.setattr(text_hook, "read_context", boom)
    code = cli.main(["guards", "check-text", "--leaves-machine"])
    assert code == 2
    assert "check_unreachable" in capsys.readouterr().err
