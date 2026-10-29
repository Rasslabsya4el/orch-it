"""The value check on text for another reader (#415 unit 4): `guards check-text` and the PreToolUse hook
`orch_it.guards.text_hook`, which judges the text of a call by its input, not by its tool name.

Rows of #415 held here: 3 (a prompt and a message each holding each kind are refused; another session's
scratchpad file passes), 7 (a tool no list names is judged by its input), 8 (a check that cannot run is
refused, naming the install step) and 9 (a write into the repo's records holding a kind is refused; the
`<scratchpad of session id>/<file>` form passes).

Every value is invented: the account `acct-x`, the repo `acct-x/proj-y`, the host `testbox-17`, the Windows home folder
of the invented user `tester9`, the projects folder `/srv/ws`, the git name `Tess Tester` and the e-mail `t9@example.invalid`.
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

# The samples below are built when the tests run from these parts: the guard scan reads raw source text, so a
# flagged form (a home path, a one-OS call, a bare interpreter call) must not stand in this file as a literal.
BS = "\\"  # one backslash, the Windows path separator
PY_WORD = "py" + "thon"  # the interpreter's name, in two parts
ONE_OS_KILL = "task" + "kill"  # a Windows-only process tool's name, in two parts (CODING_STANDARDS.md kind 4)


def _win(*parts):
    """A Windows path of `parts`, joined by backslashes."""

    return BS.join(parts)


HOME_WIN = _win("C:", "Users", "tester9")
REPO_ROOT = "/srv/ws/proj-y"
REPO_ROOTS = (REPO_ROOT,)
REPO_SLUG = "-srv-ws-proj-y"  # REPO_ROOT with every non-alphanumeric character turned into '-' (the project's slug)
SESSION_SCRATCH = (_win(HOME_WIN, "AppData", "Local", "Temp", "claude") + BS + REPO_SLUG + BS
                   + "3f2a9c1e-8b4d-4e6f-9a1b-2c3d4e5f6a7b" + BS + "scratchpad")
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
    """The setup of the repo at REPO_ROOT (its roots and its project's slug) when `roots` is given, else a setup
    outside any repo."""

    return text_hook.Context(values=VALUES, job_names=JOB_NAMES, roots=tuple(roots),
                             repo=REPO_ROOT if roots else "")


CTX = context(REPO_ROOTS)

# one sample per kind the check knows: the 8 value kinds of values.py and the 3 pattern kinds of kinds.py
KIND_SAMPLES = {
    "account": "the owner is acct-x here",
    "repo": "clone acct-x/proj-y now",
    "host": "ssh testbox-17 -p 22",
    "home": "cd " + _win(HOME_WIN, "src"),
    "user": "USER=tester9",
    "projects_folder": "cd /srv/ws/other",
    "git_name": "Author: Tess Tester",
    "git_email": "mail to T9@Example.Invalid please",
    "bare_python": "run `python -m pytest -q` first",
    "one_os_call": "stop it with " + ONE_OS_KILL + " /F",
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


MACHINE_VALUE_KINDS = sorted(k for k in KIND_SAMPLES if k not in kinds.KINDS)


@pytest.mark.parametrize("kind", MACHINE_VALUE_KINDS)
def test_a_write_of_a_repo_record_holding_a_machine_value_is_refused(kind):
    hits = judge(call("Write", {"file_path": f"{REPO_ROOT}/docs/STATE.md", "content": KIND_SAMPLES[kind]}))
    assert kind in kinds_of(hits)


@pytest.mark.parametrize("kind", MACHINE_VALUE_KINDS)
def test_an_edit_of_a_grill_record_holding_a_machine_value_is_refused(kind):
    hits = judge(call("Edit", {"file_path": f"{REPO_ROOT}/docs/grill/2026-10-29.md",
                               "old_string": "x", "new_string": KIND_SAMPLES[kind]}))
    assert kind in kinds_of(hits)


def test_a_file_write_is_judged_by_the_file_it_writes_for_the_pattern_kinds():
    # a record (.md) holds a bare python only inside a code fence, where a prose mention is no command
    fenced = "step:\n```\npython -m pytest -q\n```\n"
    assert "bare_python" in kinds_of(judge(call("Write", {"file_path": f"{REPO_ROOT}/docs/STATE.md",
                                                          "content": fenced})))
    prose = "run `python -m pytest -q` first\n"
    assert judge(call("Write", {"file_path": f"{REPO_ROOT}/docs/STATE.md", "content": prose})) == []


def test_a_python_source_file_is_not_judged_by_its_call_forms_or_its_job_names():
    # a source file's pattern kinds are not judged (they flag legitimate code, #415 unit 4 item 3); the same text in a
    # record is refused (see the record tests below)
    call_form = f'import subprocess\nsubprocess.run(["{PY_WORD}", "-m", "pytest", "-q"])\n'
    assert judge(call("Write", {"file_path": f"{REPO_ROOT}/tests/t_x.py", "content": call_form})) == []
    job = 'check = "lint-and-types"\n'
    assert judge(call("Write", {"file_path": f"{REPO_ROOT}/tests/t_x.py", "content": job})) == []


@pytest.mark.parametrize("key", ["pattern", "glob", "notebook_path", "cell_id", "edit_mode", "old_string"])
def test_a_structural_key_holding_a_machine_value_is_not_judged(key):
    # Grep and Glob only search: their pattern and glob name what to look for; an Edit's old_string is the text it
    # finds, not the text it writes (#415 unit 4 items 1 and 2)
    assert judge(call("Grep", {key: "t9@example.invalid"})) == []


def test_an_edit_whose_old_string_holds_a_value_passes_when_its_new_string_is_clean():
    tool_input = {"file_path": f"{REPO_ROOT}/docs/STATE.md", "old_string": "e-mail t9@example.invalid",
                  "new_string": "e-mail of this machine"}
    assert judge(call("Edit", tool_input)) == []


def test_an_edit_whose_new_string_holds_a_value_is_refused():
    tool_input = {"file_path": f"{REPO_ROOT}/docs/STATE.md", "old_string": "e-mail of this machine",
                  "new_string": "e-mail t9@example.invalid"}
    assert "git_email" in kinds_of(judge(call("Edit", tool_input)))


SOURCE_FILES = ["src/orch_it/x.py", "scripts/run.sh", "scripts/run.ps1", "web/app.js", "web/app.ts",
                ".github/ci.yml", "ci/job.yaml", "pyproject.toml"]
RECORD_FILES = ["docs/STATE.md", "README.md", "docs/notes.txt", "docs/guide.rst", "docs/research/x.json",
                "docs/config.toml", "data/x.json"]  # a suffix no source list names is judged in full too
ONE_OS_TEXT = f"subprocess.run(['{ONE_OS_KILL}', '/F', '/PID', '1'])\n"


@pytest.mark.parametrize("path", SOURCE_FILES)
def test_a_source_file_write_with_a_one_os_word_is_not_refused(path):
    assert judge(call("Write", {"file_path": f"{REPO_ROOT}/{path}", "content": ONE_OS_TEXT})) == []


@pytest.mark.parametrize("path", SOURCE_FILES)
def test_a_source_file_write_with_a_machine_value_is_refused(path):
    hits = judge(call("Write", {"file_path": f"{REPO_ROOT}/{path}", "content": "author t9@example.invalid\n"}))
    assert "git_email" in kinds_of(hits)


@pytest.mark.parametrize("path", RECORD_FILES)
def test_a_record_write_with_a_one_os_word_is_refused(path):
    hits = judge(call("Write", {"file_path": f"{REPO_ROOT}/{path}", "content": "stop it with " + ONE_OS_KILL + " /F\n"}))
    assert "one_os_call" in kinds_of(hits)


@pytest.mark.parametrize("path", RECORD_FILES)
def test_a_record_write_with_a_machine_value_is_refused(path):
    hits = judge(call("Write", {"file_path": f"{REPO_ROOT}/{path}", "content": "author t9@example.invalid\n"}))
    assert "git_email" in kinds_of(hits)


def test_a_record_write_with_a_job_name_literal_is_refused():
    tool_input = {"file_path": f"{REPO_ROOT}/docs/STATE.md", "content": KIND_SAMPLES["check_name_literal"] + "\n"}
    assert "check_name_literal" in kinds_of(judge(call("Write", tool_input)))


def test_legitimate_python_code_with_no_machine_value_or_call_form_is_not_refused():
    code = ('import re\n'
            'PYTHON_WORD = re.compile(r"^python3?$")\n'
            '# a comment that says python -m pytest in prose\n'
            'def run(): return None\n')
    assert judge(call("Write", {"file_path": f"{REPO_ROOT}/src/orch_it/x.py", "content": code})) == []


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


def test_another_projects_scratchpad_is_refused_even_under_the_same_temp_folder():
    other = SESSION_SCRATCH.replace(REPO_SLUG, "-srv-ws-proj-z")
    assert "home" in kinds_of(judge(call("Agent", {"prompt": f"read {other}\\plan.md"})))


def test_a_scratchpad_whose_slug_only_starts_with_this_projects_is_refused():
    longer = SESSION_SCRATCH.replace(REPO_SLUG, REPO_SLUG + "-old")
    assert "home" in kinds_of(judge(call("Agent", {"prompt": f"read {longer}\\plan.md"})))


def test_a_scratchpad_is_no_run_time_location_outside_a_repo():
    assert "home" in kinds_of(judge(call("Agent", {"prompt": f"read {SESSION_SCRATCH}\\plan.md"}),
                                    context_=context()))


def test_this_projects_worktree_form_passes_even_when_git_does_not_list_it():
    unlisted = f"{REPO_ROOT}/.claude/worktrees/wt-9/src/x.md"
    bare = text_hook.Context(values=VALUES, job_names=JOB_NAMES, roots=(), repo=REPO_ROOT)
    assert judge(call("Agent", {"prompt": f"edit {unlisted}"}), context_=bare) == []


def test_another_repos_worktree_form_is_refused():
    prompt = "edit " + _win(HOME_WIN, "proj-z", ".claude", "worktrees", "wt-1", "x.md")
    assert "home" in kinds_of(judge(call("Agent", {"prompt": prompt}), context_=context(REPO_ROOTS)))


def test_a_comma_or_a_bracket_ends_an_allowed_path_so_the_value_after_it_is_still_found():
    comma = f"read {SESSION_SCRATCH}\\plan.md,t9@example.invalid"
    assert "git_email" in kinds_of(judge(call("Agent", {"prompt": comma}), context_=context(REPO_ROOTS)))
    bracket = f"read ({SESSION_SCRATCH}\\plan.md)t9@example.invalid"
    assert "git_email" in kinds_of(judge(call("Agent", {"prompt": bracket}), context_=context(REPO_ROOTS)))
    repo_path = f"{REPO_ROOT}/docs/x.md,t9@example.invalid"
    assert "git_email" in kinds_of(judge(call("Agent", {"prompt": repo_path}), context_=context(REPO_ROOTS)))


def test_a_home_path_outside_the_scratchpad_form_is_refused():
    hits = judge(call("Agent", {"prompt": "read " + _win(HOME_WIN, "notes", "plan.md")}))
    assert "home" in kinds_of(hits)


def test_the_run_time_locations_pass_in_a_prompt_only_when_they_are_inside_the_repo_or_a_worktree():
    worktree = f"{REPO_ROOT}/.claude/worktrees/wt-415-u4/src/orch_it/guards/text_hook.py"
    assert judge(call("Agent", {"prompt": f"edit {worktree}"}), context_=context(REPO_ROOTS)) == []
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


@pytest.mark.parametrize("key", ["text", "summary", "payload"])
def test_an_invented_input_key_carrying_the_git_e_mail_is_refused(key):
    # a tool no list names: its text is found by the input's shape, every string leaf but the structural keys
    assert "git_email" in kinds_of(judge(call("SendToTeammate", {key: "author t9@example.invalid"})))


def test_a_string_nested_under_an_invented_key_is_judged_at_any_depth():
    tool_input = {"items": [{"text": "fine"}, {"note": ["see t9@example.invalid"]}]}
    assert "git_email" in kinds_of(judge(call("SendToTeammate", tool_input)))


def test_a_url_is_judged_and_a_structural_key_is_not():
    assert "repo" in kinds_of(judge(call("WebFetch", {"url": "https://github.com/acct-x/proj-y"})))
    assert judge(call("Agent", {"subagent_type": "t9@example.invalid", "prompt": "fix the parser"})) == []
    assert judge(call("Glob", {"pattern": "**/*.md", "path": "/srv/ws/proj-y"}), context_=context(REPO_ROOTS)) == []


@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_a_shell_call_is_not_judged_by_the_text_hook(tool):
    tool_input = {"command": "echo t9@example.invalid", "description": KIND_SAMPLES["git_email"]}
    assert judge(call(tool, tool_input)) == []


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


def test_the_hook_bounds_the_gh_login_read_to_five_seconds_and_the_git_reads_to_the_default(monkeypatch):
    import subprocess

    from orch_it.guards import values

    seconds = []

    def fake_run(cmd, **kwargs):
        seconds.append(kwargs["timeout"])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(values.subprocess, "run", fake_run)
    text_hook._bounded_run(["gh", "api", "user", "--jq", ".login"])
    text_hook._bounded_run(["git", "config", "user.email"])
    assert seconds == [5, values.COMMAND_SECONDS]
    assert text_hook.GH_SECONDS == 5


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
    code, out, err = run_cli(monkeypatch, capsys, ["guards", "check-text", "--leaves-machine"], "fix the parser\n")
    assert code == 0
    assert json.loads(out) == {"ok": True, "findings": [], "not_checked": []}
    assert err == ""


def test_check_text_pass_names_the_kinds_it_could_not_read_and_prints_the_line(monkeypatch, capsys):
    # no git e-mail on this setup: a clean result still says which kind was not checked
    no_email = read_values(env={"USERNAME": "tester9"}, home=HOME_WIN, host="testbox-17",
                           run=fake_run({k: v for k, v in MACHINE.items() if k != "user.email"}))
    bare = text_hook.Context(values=no_email, job_names=JOB_NAMES, roots=REPO_ROOTS, repo=REPO_ROOT)
    monkeypatch.setattr(sys, "stdin", io.StringIO("fix the parser\n"))
    monkeypatch.setattr(text_hook, "read_context", lambda folder: bare)
    code = cli.main(["guards", "check-text", "--leaves-machine"])
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["not_checked"] == ["git_email"]
    assert "not checked" in captured.err and "git_email" in captured.err


def test_check_text_with_a_finding_also_names_the_kinds_not_read(monkeypatch, capsys):
    no_email = read_values(env={"USERNAME": "tester9"}, home=HOME_WIN, host="testbox-17",
                           run=fake_run({k: v for k, v in MACHINE.items() if k != "user.email"}))
    bare = text_hook.Context(values=no_email, job_names=JOB_NAMES, roots=REPO_ROOTS, repo=REPO_ROOT)
    monkeypatch.setattr(sys, "stdin", io.StringIO(KIND_SAMPLES["account"] + "\n"))
    monkeypatch.setattr(text_hook, "read_context", lambda folder: bare)
    code = cli.main(["guards", "check-text", "--leaves-machine"])
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["not_checked"] == ["git_email"]


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


# #415 unit 7: a reference to one GitHub object is no hit; a bare account name beside it still is
LINK_OWNER = "acct" + "-x"  # the account of VALUES (MACHINE's `api user`)
LINK_REPO = "proj" + "-y"


def test_an_allowed_link_passes_the_text_hook_and_a_bare_account_beside_it_is_refused():
    link = f"https://github.com/{LINK_OWNER}/{LINK_REPO}/pull/415"
    assert judge(call("Agent", {"prompt": f"Refs {link}"})) == []
    hits = judge(call("SendMessage", {"message": f"Refs {link}\nthe owner {LINK_OWNER} wrote it"}))
    assert [(hit.kind, hit.line) for hit in hits] == [("account", 2)]
