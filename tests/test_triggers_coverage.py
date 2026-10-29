"""Coverage gaps the brain's outside probe found in the trigger (#395 unit 2b). Each test asserts
the ACTION `triggers.actions` yields (targets, opaque, rule id), never the guard's verdict: row 1
cases yield an action naming the path ("/work/other", another worktree) or an opaque one; row 2
cases yield nothing, or a Delete whose only target is not a protected path ("/work/self" is the
session's own worktree)."""

import pytest

from orch_it.guards import shell
from orch_it.guards.triggers import Delete, Stop, actions

OTHER = "/work/other"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def act(command, tool="Bash", cwd="/work/self", env=None, windows=False):
    return actions(command, tool=tool, cwd=cwd, env=env or {}, windows=windows)


def deletes(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Delete)]


def stops(command, **kw):
    return [a for a in act(command, **kw) if isinstance(a, Stop)]


# --- 1. an unreadable head is opaque, never silent -------------------------------------------

def test_an_unreadable_head_becomes_a_delete_over_its_readable_path_arguments():
    # the guard judges the object, so an unreadable head is a delete over its readable path
    # arguments only; it does not refuse the own worktree itself, so roots_too is off and it
    # does not walk links (that would false-fire a non-delete over a folder holding a link)
    found = deletes(f"$(echo rm) -rf {OTHER}")
    assert len(found) == 1 and found[0].targets == (OTHER,)
    assert found[0].rule_id == "unreadable-command"
    assert found[0].recursive and not found[0].walk_links and not found[0].roots_too


def test_an_unreadable_head_with_no_readable_path_argument_yields_nothing():
    # `rm${IFS}...` (one unreadable word) and `$X $Y` (unreadable head and argument) have no
    # readable path to judge; refusing them would block ordinary variable-driven commands
    # (`$PY $f`), so they pass — an unreadable head over unreadable arguments is not a guess
    assert [a for a in deletes(f"rm${{IFS}}-rf${{IFS}}{OTHER}") if a.rule_id == "unreadable-command"] == []
    assert [a for a in deletes("$(pick) $OTHERDIR") if a.rule_id == "unreadable-command"] == []


def test_pure_option_flags_are_not_targets_of_an_unreadable_head():
    found = [a for a in deletes(f"$(echo rm) -rf -- {OTHER}") if a.rule_id == "unreadable-command"]
    assert found[0].targets == (OTHER,)


def test_a_computed_interpreter_running_a_script_passes_as_a_non_protected_target():
    found = [a for a in deletes("$(which python) script.py") if a.rule_id == "unreadable-command"]
    assert len(found) == 1 and found[0].targets == ("script.py",)


def test_a_readable_head_that_is_no_trigger_stays_silent():
    assert act("ls -la /work/other") == []
    assert [a for a in act("ls $(echo x)") if a.rule_id == "unreadable-command"] == []


def test_nesting_past_the_depth_bound_is_opaque_not_silent():
    deep = "echo " + "$(echo " * (shell.MAX_DEPTH + 5) + "rm -rf /work/other" + ")" * (shell.MAX_DEPTH + 5)
    found = [a for a in act(deep) if isinstance(a, Delete)]
    assert any(a.rule_id == "unreadable-command" or OTHER in a.targets for a in found)
    assert any(a.rule_id == "unreadable-command" for a in found)
    wrapped = "(" * (shell.MAX_DEPTH + 5) + "rm -rf /work/other" + ")" * (shell.MAX_DEPTH + 5)
    assert any(a.rule_id == "unreadable-command" for a in deletes(wrapped))


# --- 5. false fires --------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "rm --help", "mv --help", "pkill --help", "kill --help", "rmdir --help", "rm -h",
    "killall --help", "rsync --help", "shred --help", "git worktree remove --help",
])
def test_a_trigger_head_asking_for_help_yields_nothing(command):
    assert act(command) == []


@pytest.mark.parametrize("command", ["Remove-Item -?", "del /?", "taskkill /?", "rd /?"])
def test_a_windows_help_flag_yields_nothing(command):
    assert act(command, tool="PowerShell", windows=True) == []


def test_help_beside_a_real_target_of_a_non_help_flag_still_fires():
    assert deletes(f"rsync -h --delete /e/ {OTHER}/")  # `-h` is human-readable there, not help


def test_kill_l_lists_signals_and_kill_0_probes_so_neither_stops():
    assert stops("kill -l") == []
    assert stops("kill -l 9") == []
    assert stops("kill -0 123") == []
    assert stops("kill -s 0 123") == []
    assert stops("kill -9 123")[0].pids == (123,)


def test_a_posix_command_word_is_case_sensitive():
    assert act(f"RM -rf {OTHER}") == []
    assert act(f"Rm -rf {OTHER}") == []
    assert act(f"KILL 123") == []
    assert deletes(f"rm -rf {OTHER}")[0].targets == (OTHER,)


def test_a_windows_command_word_is_case_insensitive():
    assert deletes(f"RM -rf {OTHER}", tool="PowerShell", windows=True)[0].rule_id == "rm"
    assert deletes(f"REMOVE-ITEM -Recurse {OTHER}", tool="PowerShell", windows=True)
    assert deletes(f"RM -rf {OTHER}", windows=True)  # Git Bash on Windows


# --- 3. case, select and function bodies are read --------------------------------------------

def test_every_case_arm_command_is_read():
    out = deletes(f"case $x in a) rm {OTHER}/y ;; b) rm {OTHER}/z ;; esac")
    assert [d.targets for d in out] == [(f"{OTHER}/y",), (f"{OTHER}/z",)]


def test_case_arms_on_their_own_lines_and_with_patterns_are_read():
    command = f"case \"$1\" in\n  start|go) echo hi ;;\n  (clean) rm -rf {OTHER} ;;\n  *) rm -rf /work/self/tmp ;;\nesac"
    assert [d.targets for d in deletes(command)] == [(OTHER,), ("/work/self/tmp",)]


def test_a_case_arm_with_a_stop_is_read():
    assert stops("case x in a) kill 123 ;; esac")[0].pids == (123,)


def test_a_case_that_runs_nothing_dangerous_yields_nothing():
    assert act("case $x in a) echo rm ;; *) ls ;; esac") == []


def test_a_select_loop_body_is_read():
    assert deletes(f"select f in a b; do rm -rf {OTHER}; done")[0].targets == (OTHER,)


def test_a_function_body_is_read():
    assert deletes(f"function clean {{ rm -rf {OTHER}; }}; clean")[0].targets == (OTHER,)
    assert deletes(f"clean() {{ rm -rf {OTHER}; }}")[0].targets == (OTHER,)
    assert deletes(f"clean () {{\n rm -rf {OTHER}\n}}")[0].targets == (OTHER,)


@pytest.mark.parametrize("command", [
    "case", "case x in", "case x in a)", "case x in a) ;; esac", "case ) in", "function", "function f",
    "function f {", "f() {", "select", "select x in", "case x in ( ) rm", "a) rm",
])
def test_a_malformed_case_select_or_function_never_raises(command):
    act(command)
    act(command, tool="PowerShell", windows=True)


# --- 2. heredoc and interpreter-string bodies that are run are parsed ------------------------

def test_a_python_heredoc_body_is_parsed_as_code():
    out = deletes(f"python - <<'EOF'\nimport shutil; shutil.rmtree('{OTHER}')\nEOF")
    assert out and out[0].targets == (OTHER,) and out[0].rule_id == "python-c"


@pytest.mark.parametrize("head", ["python", "python3", "python3.12", "python -", "python3 -u -"])
def test_a_python_reading_stdin_runs_its_heredoc(head):
    command = f"{head} <<'EOF'\nimport os\nos.remove('{OTHER}/f')\nEOF"
    assert deletes(command)[0].targets == (f"{OTHER}/f",)


def test_a_python_stop_in_a_heredoc_is_found():
    assert stops("python - <<'EOF'\nimport os\nos.kill(321, 9)\nEOF")[0].pids == (321,)


def test_a_node_and_deno_heredoc_body_is_parsed_as_code():
    out = deletes(f"node - <<'EOF'\nrequire('fs').rmSync('{OTHER}', {{recursive: true}})\nEOF")
    assert out[0].targets == (OTHER,) and out[0].rule_id == "node-e"
    out = deletes(f"deno run - <<'EOF'\nDeno.removeSync('{OTHER}', {{recursive: true}})\nEOF")
    assert out[0].targets == (OTHER,)


def test_a_python_given_a_script_file_does_not_run_the_heredoc_as_code():
    # the heredoc is the script's stdin data, not code
    assert deletes(f"python script.py <<'EOF'\nshutil.rmtree('{OTHER}')\nEOF") == []


@pytest.mark.parametrize("head", ["bash", "sh", "zsh", "bash -s", "sh -", "sudo bash", "bash -e"])
def test_a_shell_heredoc_body_is_parsed_as_commands(head):
    out = deletes(f"{head} <<'EOF'\nrm -rf {OTHER}\nEOF")
    assert out and out[0].targets == (OTHER,) and out[0].rule_id == "rm"


def test_a_heredoc_piped_into_a_shell_is_parsed_as_commands():
    assert deletes(f"cat <<EOF | bash\nrm -rf {OTHER}\nEOF")[0].targets == (OTHER,)
    assert deletes(f"cat <<'EOF' | sh\nrm -rf {OTHER}\nEOF")[0].targets == (OTHER,)
    assert deletes(f"cat <<'EOF' | /bin/bash -s\nrm -rf {OTHER}\nEOF")[0].targets == (OTHER,)


def test_a_bash_heredoc_may_hold_several_commands_and_control_flow():
    body = f"cd /work/self\nfor d in a b; do\n  rm -rf {OTHER}/$d\ndone\nkill 77"
    out = deletes(f"bash <<'EOF'\n{body}\nEOF")
    assert out and out[0].rule_id == "rm" and out[0].cwd == "/work/self"
    assert stops(f"bash <<'EOF'\n{body}\nEOF")[0].pids == (77,)


def test_a_heredoc_that_is_only_data_yields_nothing():
    assert act("cat <<EOF > note\nrm -rf x\nEOF") == []
    assert act(f"cat <<'EOF'\nrm -rf {OTHER}\nEOF") == []
    assert act(f"tee notes.txt <<EOF\nrm -rf {OTHER}\nEOF") == []
    assert act(f"python script.py <<EOF\nrm -rf {OTHER}\nEOF") == []
    assert act(f"grep rm <<EOF | wc -l\nrm -rf {OTHER}\nEOF") == []


def test_commands_after_a_heredoc_are_still_read():
    out = deletes(f"cat <<'EOF'\ndata\nEOF\nrm -rf {OTHER}")
    assert [d.targets for d in out] == [(OTHER,)]


def test_an_unquoted_heredoc_runs_its_command_substitution():
    out = deletes(f"cat <<EOF\nhello $(rm -rf {OTHER})\nEOF")
    assert out and out[0].targets == (OTHER,)
    out = deletes(f"cat <<EOF > note\n`rm -rf {OTHER}`\nEOF")
    assert out and out[0].targets == (OTHER,)


def test_a_quoted_heredoc_does_not_run_its_command_substitution():
    assert act(f"cat <<'EOF'\n$(rm -rf {OTHER})\nEOF") == []
    assert act("cat <<\\EOF\n$(rm -rf " + OTHER + ")\nEOF") == []
    assert act(f'cat <<"EOF"\n$(rm -rf {OTHER})\nEOF') == []


def test_heredoc_nesting_is_bounded():
    body = f"rm -rf {OTHER}"
    for _ in range(shell.MAX_DEPTH + 5):
        body = "bash <<'EOF'\n" + body + "\nEOF"
    out = act(body)  # never raises, never silent
    assert any(isinstance(a, Delete) for a in out)


def test_an_arithmetic_shift_is_no_heredoc():
    out = deletes(f"echo $((1<<2))\nrm -rf {OTHER}")
    assert [d.targets for d in out] == [(OTHER,)]


# strings that are shell commands inside python and node bodies

@pytest.mark.parametrize("command", [
    f"python -c \"import os; os.system('rm -rf {OTHER}')\"",
    f"python3 -c \"import subprocess; subprocess.run('rm -rf {OTHER}', shell=True)\"",
    f"python -c \"import subprocess; subprocess.call('rm -rf {OTHER}', shell=True)\"",
    f"python -c \"import subprocess; subprocess.Popen('rm -rf {OTHER}', shell=True)\"",
    f"python -c \"import subprocess; subprocess.run(['rm','-rf','{OTHER}'])\"",
    f"python -c \"import subprocess; subprocess.check_call(['rm', '-rf', '{OTHER}'], cwd='/')\"",
    f"python -c \"import subprocess; subprocess.run(['bash','-c','rm -rf {OTHER}'])\"",
    f"python -c \"import os; os.popen('rm -rf {OTHER}')\"",
    f"node -e \"require('child_process').execSync('rm -rf {OTHER}')\"",
    f"node -e \"require('child_process').exec('rm -rf {OTHER}')\"",
    f"node -e \"const cp=require('child_process'); cp.spawnSync('rm',['-rf','{OTHER}'])\"",
    f"node -e \"const {{execSync}}=require('child_process'); execSync('rm -rf {OTHER}')\"",
    f"node -e \"require('child_process').spawn(['rm','-rf','{OTHER}'])\"",
    f"node -e \"require('child_process').spawn('rm -rf {OTHER}', {{shell: true}})\"",
])
def test_a_shell_string_inside_an_interpreter_body_is_parsed(command):
    out = deletes(command)
    assert any(d.rule_id == "rm" and OTHER in d.targets for d in out), (command, out)


def test_a_stop_inside_an_interpreter_string_is_found():
    assert stops("python -c \"import os; os.system('kill -9 456')\"")[0].pids == (456,)
    assert stops("node -e \"require('child_process').execSync('pkill -f runserver')\"")[0].matchers


def test_a_python_f_string_command_keeps_the_unreadable_part_unreadable():
    out = deletes("python -c \"import os; p='x'; os.system(f'rm -rf {p}')\"")
    assert any(d.rule_id == "rm" and None in d.targets for d in out)


def test_a_non_literal_shell_command_is_opaque():
    out = deletes("python -c \"import os; os.system(cmd)\"")
    assert any(None in d.targets for d in out)
    out = deletes("python -c \"import subprocess; subprocess.run(cmd, shell=True)\"")
    assert any(None in d.targets for d in out)


@pytest.mark.parametrize("command", [
    "python -c \"print('rm -rf /work/other')\"",
    "python -c \"x = 'os.system(\\\"rm -rf /work/other\\\")'\"",
    "python -c \"import os; os.system('ls /work/other')\"",
    "python -c \"import subprocess; subprocess.run('rm -rf /work/other')\"",  # no shell=True: not a shell
    "python -c \"import subprocess; subprocess.run(['ls', '/work/other'])\"",
    "python -c \"import subprocess; subprocess.run(cmd)\"",
    "node -e \"console.log('execSync(rm -rf /work/other)')\"",
    "node -e \"/rm -rf x/.exec('rm -rf /work/other')\"",
    "node -e \"require('child_process').execSync('ls')\"",
    "python -c \"# os.system('rm -rf /work/other')\"",
])
def test_a_delete_word_in_a_plain_string_without_a_shell_call_does_not_fire(command):
    assert not any(isinstance(a, (Delete, Stop)) for a in act(command)), act(command)


# --- a substitution in an assignment runs too -------------------------------------------------

def test_a_command_substitution_in_an_assignment_is_read():
    assert deletes(f"x=$(rm -rf {OTHER})")[0].targets == (OTHER,)
    assert deletes(f"FOO=$(rm -rf {OTHER}) env")[0].targets == (OTHER,)
    out = deletes(f"x=$(cat <<EOF\n$(rm -rf {OTHER})\nEOF\n)")
    assert out and out[0].targets == (OTHER,)
