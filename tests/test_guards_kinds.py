"""The pattern kinds of #413 unit 2, in `orch_it.guards.kinds`: `bare_python`, `one_os_call` and
`check_name_literal`. Each kind is found once in a short invented text, and each has a negative case:
`sys.executable`, a `"$PY" -m` line, an OS-guarded windll call, a `python` word in markdown prose.
Job names are invented (`alpha`, `beta`); the workflow reader is checked on an invented workflow file.
"""

from orch_it.guards import kinds
from orch_it.guards.kinds import Finding, find_pattern_kinds, read_job_names

JOBS = frozenset({"alpha", "beta"})


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
