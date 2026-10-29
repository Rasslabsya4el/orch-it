"""The pattern kinds of #413 unit 2, in `orch_it.guards.kinds`: `bare_python`, `one_os_call` and
`check_name_literal`. Each kind is found once in a short invented text, and each has a negative case:
`sys.executable`, a `"$PY" -m` line, an OS-guarded windll call, a `python` word in markdown prose.
Job names are invented (`alpha`, `beta`); the workflow reader is checked on an invented workflow file.
"""

import pytest

from orch_it.guards import kinds
from orch_it.guards.kinds import TICKET_PATH, Finding, find_in_ticket_text, find_pattern_kinds, read_job_names

JOBS = frozenset({"alpha", "beta"})


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def found(path, text, job_names=JOBS):
    return [(f.kind, f.line) for f in find_pattern_kinds(path, text, job_names)]


def test_kinds_are_the_three_of_the_issue():
    assert kinds.KINDS == ("bare_python", "one_os_call", "check_name_literal")


def test_finding_has_kind_line_text_and_path():
    [finding] = find_pattern_kinds("scripts/run.sh", "set -e\npython -m tool\n", JOBS)
    assert finding == Finding(kind="bare_python", line=2, text="python", path="scripts/run.sh")


# (a) bare_python: a `python` or `python3` in command position

def test_bare_python_in_shell_script_is_found_once():
    assert found("scripts/run.sh", "set -e\npython -m tool --check\n") == [("bare_python", 2)]


def test_bare_python_in_json_command_string_is_found_once():
    text = '{\n  "command": "python",\n  "args": ["-m", "tool"]\n}\n'
    assert found(".mcp.json", text) == [("bare_python", 2)]


def test_bare_python_in_workflow_run_line_is_found_once():
    text = "jobs:\n  alpha:\n    steps:\n      - run: python3 -m pytest -q\n"
    assert found(".github/workflows/x.yml", text) == [("bare_python", 4)]


def test_bare_python_as_subprocess_list_first_element_is_found_once():
    text = 'import subprocess\nsubprocess.run(["python", "-m", "pytest"])\n'
    assert found("src/tool.py", text) == [("bare_python", 2)]


def test_bare_python_as_subprocess_command_string_is_found_once():
    text = 'import subprocess\nsubprocess.run("python3 -m pytest", shell=True)\n'
    assert found("src/tool.py", text) == [("bare_python", 2)]


def test_bare_python_inside_a_markdown_fence_is_found_once():
    text = "Run it like this:\n\n```bash\npython -m pytest\n```\n"
    assert found("docs/guide.md", text) == [("bare_python", 4)]


def test_sys_executable_is_not_bare_python():
    assert found("src/tool.py", 'subprocess.run([sys.executable, "-m", "pytest"])\n') == []


def test_variable_or_path_before_python_is_not_bare_python():
    text = 'PY=python3\n"$PY" -m pytest -q\n/usr/bin/python3 -m tool\n'
    assert found("scripts/run.sh", text) == []


def test_python_word_in_markdown_prose_is_not_bare_python():
    text = "Run python -m pytest to test the tree.\n\nSee `python3` in the notes.\n"
    assert found("docs/guide.md", text) == []


# (b) one_os_call: an OS-only call outside a branch on the OS

def test_unguarded_windll_call_is_found_once():
    text = (
        "import ctypes\n"
        "\n"
        "\n"
        "def long_name(path):\n"
        "    return ctypes.windll.kernel32.GetLongPathNameW(path)\n"
    )
    assert found("src/names.py", text) == [("one_os_call", 5)]


def test_os_guarded_windll_call_is_not_found():
    text = (
        "import sys\n"
        "\n"
        "\n"
        "def long_name(path):\n"
        '    if sys.platform != "win32":\n'
        "        return None\n"
        "    import ctypes\n"
        "    return ctypes.windll.kernel32.GetLongPathNameW(path)\n"
    )
    assert found("src/names.py", text) == []


def test_import_inside_try_except_importerror_is_not_found():
    text = "try:\n    import msvcrt\nexcept ImportError:\n    msvcrt = None\n"
    assert found("src/console.py", text) == []


def test_unguarded_os_only_import_is_found_once():
    assert found("src/console.py", "import winreg\n") == [("one_os_call", 1)]


def test_unguarded_os_only_os_call_is_found_once():
    text = "import os\n\n\ndef me():\n    return os.getuid()\n"
    assert found("src/me.py", text) == [("one_os_call", 5)]


def test_os_only_name_in_a_markdown_file_is_not_one_os_call():
    assert found("docs/notes.md", "The call ctypes.windll is Windows only.\n") == []


# the Windows-only process tools: taskkill, tskill, Stop-Process, wmic (CODING_STANDARDS.md kind 4)
WINDOWS_PROCESS_TOOLS = ("taskkill", "tskill", "Stop-Process", "wmic")
# each tool in three casings: Windows reads these names case-insensitively
WINDOWS_PROCESS_CASINGS = [case(name) for name in WINDOWS_PROCESS_TOOLS for case in (str.upper, str.lower, str.capitalize)]

WINDOWS_PROCESS_CALLS = [
    'subprocess.run(["taskkill", "/F", "/PID", str(pid)])',
    'subprocess.run(["tskill", str(pid)])',
    'subprocess.run(["powershell", "-Command", "Stop-Process -Id " + str(pid)])',
    'subprocess.run(["wmic", "process", "where", "ProcessId=" + str(pid), "delete"])',
]


@pytest.mark.parametrize("call", WINDOWS_PROCESS_CALLS)
def test_unguarded_windows_process_tool_in_py_is_found_once(call):
    text = f"import subprocess\n\n\ndef kill(pid):\n    {call}\n"
    assert found("src/proc.py", text) == [("one_os_call", 5)]


@pytest.mark.parametrize("call", WINDOWS_PROCESS_CALLS)
def test_os_guarded_windows_process_tool_is_not_found(call):
    text = (
        "import subprocess\n"
        "import sys\n"
        "\n"
        "\n"
        "def kill(pid):\n"
        '    if sys.platform != "win32":\n'
        "        return\n"
        f"    {call}\n"
    )
    assert found("src/proc.py", text) == []


@pytest.mark.parametrize("name", WINDOWS_PROCESS_CASINGS)
def test_windows_process_tool_in_any_casing_in_py_is_found_once(name):
    text = f'import subprocess\n\n\ndef kill(pid):\n    subprocess.run(["{name}", str(pid)])\n'
    assert found("src/proc.py", text) == [("one_os_call", 5)]


def test_cross_os_process_kill_through_psutil_is_not_found():
    text = "import psutil\n\n\ndef kill(pid):\n    psutil.Process(pid).kill()\n"
    assert found("src/proc.py", text) == []


@pytest.mark.parametrize("name", WINDOWS_PROCESS_TOOLS)
def test_windows_process_tool_name_in_markdown_prose_is_not_one_os_call(name):
    assert found("docs/notes.md", f"The stuck process is ended with {name} on Windows.\n") == []


# (c) check_name_literal: a job name written as a string literal in a .py file

def test_job_name_literal_in_py_is_found_once():
    assert found("src/orch_it/checks.py", 'CHECK = Check("alpha", "tests")\n') == [("check_name_literal", 1)]


def test_job_name_literal_in_workflow_yml_is_not_checked():
    text = "jobs:\n  alpha:\n    name: alpha\n"
    assert found(".github/workflows/tests.yml", text) == []


def test_string_that_is_not_a_job_name_is_not_found():
    assert found("src/labels.py", 'label = "gamma"\n') == []


def test_job_name_inside_a_longer_string_is_not_found():
    assert found("src/labels.py", 'label = "alpha-tests"\n') == []


def test_no_job_names_means_no_check_name_literal():
    assert found("src/orch_it/checks.py", 'CHECK = Check("alpha", "tests")\n', frozenset()) == []


def test_job_names_are_read_from_workflow_jobs_keys(tmp_path):
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "tests.yml").write_text(
        "name: tests\n"
        "on: push\n"
        "jobs:\n"
        "  alpha:\n"
        "    runs-on: box-one\n"
        "    steps:\n"
        "      - run: echo one\n"
        "  beta:\n"
        "    runs-on: box-two\n"
        "env:\n"
        "  X: 1\n",
        encoding="utf-8",
    )
    assert read_job_names(str(workflows)) == frozenset({"alpha", "beta"})


def workflow(tmp_path, name, text):
    """Write one workflow file in a folder of its own under tmp_path; return the folder's path."""

    folder = tmp_path / "workflows"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")
    return str(folder)


def test_job_name_field_is_read_next_to_its_key(tmp_path):
    folder = workflow(tmp_path, "tests.yml",
                      "jobs:\n"
                      "  alpha:\n"
                      "    name: Alpha Gate\n"
                      "    runs-on: box-one\n"
                      "  beta:\n"
                      "    runs-on: box-two\n")
    assert read_job_names(folder) == frozenset({"alpha", "Alpha Gate", "beta"})


def test_quoted_name_value_is_read_without_its_quotes(tmp_path):
    folder = workflow(tmp_path, "tests.yml", 'jobs:\n  gamma:\n    name: "Gamma Gate"\n')
    assert read_job_names(folder) == frozenset({"gamma", "Gamma Gate"})


def test_matrix_suffixed_name_gives_its_bare_part_too(tmp_path):
    folder = workflow(tmp_path, "tests.yml", "jobs:\n  delta:\n    name: tests (os)\n")
    assert read_job_names(folder) == frozenset({"delta", "tests (os)", "tests"})


def test_matrix_expression_name_gives_only_its_bare_part(tmp_path):
    folder = workflow(tmp_path, "tests.yml", "jobs:\n  eps:\n    name: tests (${{ matrix.os }})\n")
    assert read_job_names(folder) == frozenset({"eps", "tests"})


def test_yaml_extension_is_read_too(tmp_path):
    folder = workflow(tmp_path, "gate.yaml", "jobs:\n  zeta:\n    name: zeta gate\n")
    assert read_job_names(folder) == frozenset({"zeta", "zeta gate"})


def test_top_level_workflow_name_is_not_a_job_name(tmp_path):
    folder = workflow(tmp_path, "tests.yml", "name: tests\non: push\njobs:\n  eta:\n    runs-on: box-one\n")
    assert read_job_names(folder) == frozenset({"eta"})


# ticket bodies: `find_in_ticket_text`, for unit 6's ticket checks

@pytest.mark.parametrize("line", [
    "python -m pytest -q",  # the word at a line start, with an option after it
    "$ python3 -m tool --check",  # after a shell prompt
    "Run `python -m tool` first.",  # after a backtick
    "- run: python -m tool",  # after a workflow `run:` key
])
def test_ticket_bare_python_in_command_position_is_found_once(line):
    findings = find_in_ticket_text(f"Steps:\n\n{line}\n")
    assert [(f.kind, f.line) for f in findings] == [("bare_python", 3)]


def test_ticket_bare_python_inside_a_fence_is_found_once():
    text = "## Steps\n\n```bash\npython -m pytest -q\n```\n"
    assert [(f.kind, f.line) for f in find_in_ticket_text(text)] == [("bare_python", 4)]


def test_ticket_one_os_name_is_found_once():
    text = "The fix calls ctypes.windll here.\n"
    assert [(f.kind, f.line, f.text) for f in find_in_ticket_text(text)] == [("one_os_call", 1, "ctypes.windll")]


@pytest.mark.parametrize("name", WINDOWS_PROCESS_CASINGS)
def test_ticket_windows_process_tool_name_is_found_once(name):
    text = f"The stuck process is ended with {name} here.\n"
    assert [(f.kind, f.line, f.text) for f in find_in_ticket_text(text)] == [("one_os_call", 1, name)]


@pytest.mark.parametrize("line", ['Wait for the "alpha" check.', "Wait for the `alpha` check."])
def test_ticket_job_name_as_quoted_or_backticked_literal_is_found_once(line):
    findings = find_in_ticket_text(line, JOBS)
    assert [(f.kind, f.line, f.text) for f in findings] == [("check_name_literal", 1, "alpha")]


def test_ticket_job_name_without_names_given_is_not_found():
    assert find_in_ticket_text('Wait for the "alpha" check.') == []


def test_ticket_body_with_all_three_kinds_is_found_in_order():
    text = (
        "## Steps\n"
        "\n"
        "```bash\n"
        "python3 -m tool\n"
        "```\n"
        "\n"
        "The fix calls `ctypes.windll` for the path.\n"
        "\n"
        'Wait for the "alpha" check.\n'
    )
    findings = find_in_ticket_text(text, JOBS)
    assert [(f.kind, f.line, f.text) for f in findings] == [
        ("bare_python", 4, "python3"),
        ("one_os_call", 7, "ctypes.windll"),
        ("check_name_literal", 9, "alpha"),
    ]
    assert {f.path for f in findings} == {TICKET_PATH}


def test_ticket_prose_with_python_in_a_sentence_is_not_found():
    text = (
        "We chose python for the tool, and the stack keeps python in its tests.\n"
        "python is the language of the tool we chose for the stack.\n"
        "The checks alpha and beta are named in the workflow, unquoted.\n"
    )
    assert find_in_ticket_text(text, JOBS) == []
