"""Test runs at the own place (#394 decisions 1-3, rows 1 and 3): the repo's test command runs in
a clean environment under its bound, and the run lands in the store as a `test_run` pair with one
`test.result` per test case. Every case runs real pytest over a small fixture suite."""

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from orch_it.events.store import Store
from orch_it.testruns import environment
from orch_it.testruns.config import TestSettings
from orch_it.testruns.places import PlaceRefused, PlaceVanished

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
from orch_it.testruns.runner import run_tests

REPO = "acme-x/widgets-y"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def suite(tmp_path, **files: str):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        (root / f"{name}.py").write_text(textwrap.dedent(text), "utf-8")
    return root


def ended(store):
    return store.query(
        "SELECT run, outcome, reason, collected, passed, failed FROM events"
        " WHERE kind = 'step.ended' AND step = 'test_run'").rows


def results(store):
    return sorted(store.query(
        "SELECT test_id, outcome FROM events WHERE kind = 'test.result'").rows)


def test_a_green_suite_lands_as_a_passed_run_with_its_rows(tmp_path):
    root = suite(tmp_path, test_math="""
        def test_adds():
            assert 1 + 1 == 2

        def test_subtracts():
            assert 2 - 1 == 1
        """)
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=root, repo=REPO, settings=TestSettings())
    assert (done.outcome, done.reason) == ("passed", None)
    [(run, outcome, reason, collected, passed, failed)] = ended(store)
    assert (run, outcome, reason, collected, passed, failed) == (done.run, "passed", None, 2, 2, 0)
    assert results(store) == [("test_math::test_adds", "passed"), ("test_math::test_subtracts", "passed")]
    [(place, settings_hash)] = store.query(
        "SELECT place, settings_hash FROM events WHERE kind = 'step.started' AND step = 'test_run'").rows
    assert place == "own" and settings_hash


def run_of(tmp_path, root, settings=None, **options):
    store = Store.open(tmp_path / "store")
    done = run_tests(store, work_tree=root, repo=REPO, settings=settings or TestSettings(), **options)
    return store, done


def test_one_red_test_is_a_failed_run_naming_that_test(tmp_path):
    root = suite(tmp_path, test_math="""
        def test_adds():
            assert 1 + 1 == 2

        def test_subtracts():
            assert 2 - 1 == 2
        """)
    store, done = run_of(tmp_path, root)
    assert (done.outcome, done.reason, done.failed) == ("failed", "red", 1)
    assert ("test_math::test_subtracts", "failed") in results(store)


def test_zero_collected_is_no_result_none_collected(tmp_path):
    root = suite(tmp_path, test_math="""
        def helper():
            pass
        """)
    store, done = run_of(tmp_path, root)
    assert (done.outcome, done.reason) == ("no_result", "none_collected")
    assert [row[1] for row in ended(store)] == ["no_result"]


def test_an_ids_file_naming_a_missing_test_is_no_result_ids_not_collected(tmp_path):
    root = suite(tmp_path, test_math="""
        def test_adds():
            pass

        def test_other():
            pass
        """)
    ids = tmp_path / "ids.txt"
    ids.write_text("test_math.py::test_adds\ntest_math.py::test_gone\n", "utf-8")
    store, done = run_of(tmp_path, root, ids_file=ids)
    assert (done.outcome, done.reason, done.missing_ids) == ("no_result", "ids_not_collected",
                                                             ("test_math.py::test_gone",))
    assert results(store) == [("test_math::test_adds", "passed")]  # the other test was left out
    [(selection,)] = store.query("SELECT selection FROM events WHERE kind = 'step.started'").rows
    assert selection.startswith("ids:2:")


def test_an_ids_file_naming_only_collected_tests_is_passed(tmp_path):
    root = suite(tmp_path, test_math="""
        import pytest

        @pytest.mark.parametrize("x", ["a b", "c[d]"])
        def test_each(x):
            pass
        """)
    ids = tmp_path / "ids.txt"
    ids.write_text("test_math.py::test_each[a b]\ntest_math::test_each[c[d]]\n", "utf-8")
    _, done = run_of(tmp_path, root, ids_file=ids)
    assert (done.outcome, done.collected) == ("passed", 2)


ID_FORMS = """
    import pytest

    class TestC:
        def test_a(self):
            pass

    @pytest.mark.parametrize("x", ["a", "a]b::c]"])
    def test_p(x):
        pass
    """


@pytest.mark.parametrize("wanted, collected", [
    ("./tests/test_x.py", 3),
    ("tests\\test_x.py", 3),
    ("tests", 3),
    ("./tests/test_x.py::TestC::test_a", 1),
    ("tests\\test_x.py::TestC::test_a", 1),
    ("tests/test_x.py::TestC", 1),
    ("tests/test_x.py::test_p", 2),
    ("tests/test_x.py::test_p[a]b::c]]", 1),
    ("tests.test_x::test_p[a]", 1),
])
def test_the_plugin_and_the_judge_agree_on_every_id_form(tmp_path, wanted, collected):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text(textwrap.dedent(ID_FORMS), "utf-8")
    ids = tmp_path / "ids.txt"
    ids.write_text(wanted + "\n", "utf-8")
    _, done = run_of(tmp_path, root, ids_file=ids)
    assert (done.outcome, done.collected, done.missing_ids) == ("passed", collected, ())


class Interrupted:
    id, os = "own", "linux"

    def run(self, request, scratch):
        raise KeyboardInterrupt


def test_a_run_interrupted_by_a_person_ends_stopped_and_is_not_left_live(tmp_path):
    root = suite(tmp_path)
    store = Store.open(tmp_path / "store")
    with pytest.raises(KeyboardInterrupt):
        run_tests(store, work_tree=root, repo=REPO, settings=TestSettings(), places={"own": Interrupted()})
    assert [row[1:3] for row in ended(store)] == [("stopped", None)]


def test_the_work_tree_placeholder_is_an_absolute_path(tmp_path, monkeypatch):
    root = suite(tmp_path)
    monkeypatch.chdir(tmp_path)
    write = 'import os, sys; open(sys.argv[2], "w").write(str(os.path.isabs(sys.argv[1])))'
    seen = tmp_path / "seen.txt"
    settings = TestSettings(command=("{python}", "-c", write, "{work_tree}", str(seen)))
    run_of(tmp_path, root.relative_to(tmp_path), settings)
    assert seen.read_text() == "True"


def test_a_command_that_writes_no_report_is_summary_missing_never_failed(tmp_path):
    root = suite(tmp_path)
    settings = TestSettings(command=("{python}", "-c", "raise SystemExit(1)"))
    store, done = run_of(tmp_path, root, settings)
    assert (done.outcome, done.reason) == ("no_result", "summary_missing")
    assert [row[1] for row in ended(store)] == ["no_result"]


def test_a_truncated_report_is_summary_missing_never_failed(tmp_path):
    root = suite(tmp_path)
    half = ('import sys; open(sys.argv[1], "w").write(\'<testsuite><testcase name="a">'
            '<failure message="x"/></testcase><testcase name="b"\')')
    settings = TestSettings(command=("{python}", "-c", half, "{report}"))
    store, done = run_of(tmp_path, root, settings)
    assert (done.outcome, done.reason) == ("no_result", "summary_missing")
    assert results(store) == []


class Failing:
    """A place that cannot finish: it raises what a real place raises."""

    def __init__(self, error):
        self.id, self.os, self.error = "own", "linux", error

    def run(self, request, scratch):
        raise self.error


def test_a_place_that_vanishes_mid_run_ends_it_lost_never_failed(tmp_path):
    root = suite(tmp_path)
    store, done = run_of(tmp_path, root, places={"own": Failing(PlaceVanished("ssh dropped"))})
    assert (done.outcome, done.reason) == ("lost", "place_vanished")
    assert [row[1:3] for row in ended(store)] == [("lost", "place_vanished")]
    assert results(store) == []


def test_a_place_that_refuses_ends_the_run_refused_with_its_code(tmp_path):
    root = suite(tmp_path)
    store, done = run_of(tmp_path, root, places={"own": Failing(PlaceRefused("out_of_minutes"))})
    assert (done.outcome, done.reason) == ("refused", "out_of_minutes")
    assert [row[1:3] for row in ended(store)] == [("refused", "out_of_minutes")]


def test_a_refusal_code_outside_the_list_is_no_refusal():
    with pytest.raises(ValueError):
        PlaceRefused("tired")


def test_the_start_says_whether_the_work_tree_had_changes(tmp_path):
    root = suite(tmp_path, test_one="def test_one():\n    pass\n")
    git = ["git", "-C", str(root)]
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t",
                                                 "commit", "-qm", "one"]):
        subprocess.run(git + args, check=True, capture_output=True, timeout=60, creationflags=NO_WINDOW)
    store = Store.open(tmp_path / "store")
    run_tests(store, work_tree=root, repo=REPO, settings=TestSettings())
    (root / "test_one.py").write_text("def test_one():\n    assert True\n", "utf-8")
    run_tests(store, work_tree=root, repo=REPO, settings=TestSettings())
    rows = store.query("SELECT \"commit\", json_extract(detail, '$.dirty') FROM events"
                       " WHERE kind = 'step.started' ORDER BY at").rows
    assert [dirty for _, dirty in rows] == [0, 1]
    assert rows[0][0] and rows[0][0] == rows[1][0]


def test_a_program_that_does_not_exist_is_summary_missing(tmp_path):
    root = suite(tmp_path)
    settings = TestSettings(command=("no-such-program-orch-it",))
    _, done = run_of(tmp_path, root, settings)
    assert (done.outcome, done.reason) == ("no_result", "summary_missing")


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie no one has reaped yet is no process of the run
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(") ")[-1][:1] != "Z"
    except OSError:
        return True


def test_a_test_sleeping_past_a_one_minute_bound_is_cut_and_leaves_no_process(tmp_path):
    pids = tmp_path / "pids.txt"
    root = suite(tmp_path, test_slow="""
        import os, subprocess, sys, time

        def test_sleeps_past_the_bound():
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with open(os.environ["PIDS_FILE"], "w") as f:
                f.write(f"{os.getpid()} {child.pid}")
            time.sleep(600)
        """)
    settings = TestSettings(bound_minutes=1, test_env={"PIDS_FILE": str(pids)})
    started = time.monotonic()
    store, done = run_of(tmp_path, root, settings)
    assert 60 <= time.monotonic() - started < 120
    assert (done.outcome, done.reason) == ("no_result", "cut")
    assert [row[1:3] for row in ended(store)] == [("no_result", "cut")]
    left = [int(pid) for pid in pids.read_text().split()]
    assert len(left) == 2
    deadline = time.monotonic() + 10
    while any(alive(pid) for pid in left) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert [pid for pid in left if alive(pid)] == []


def test_the_test_process_sees_test_env_and_none_of_the_caller_s_variables(tmp_path, monkeypatch):
    seen = tmp_path / "seen.json"
    root = suite(tmp_path, test_env="""
        import json, os

        def test_dumps_its_environment():
            with open(os.environ["SEEN_FILE"], "w") as f:
                json.dump(dict(os.environ), f)
        """)
    monkeypatch.setenv("ORCH_IT_CALLER_ONLY", "leaked")
    settings = TestSettings(test_env={"SEEN_FILE": str(seen), "FROM_TEST_ENV": "yes"})
    _, done = run_of(tmp_path, root, settings)
    assert done.outcome == "passed"
    env = {name.upper(): value for name, value in json.loads(seen.read_text("utf-8")).items()}
    assert "ORCH_IT_CALLER_ONLY" not in env
    assert env["FROM_TEST_ENV"] == "yes"
    allowed = set(environment.BASE_NAMES[environment.this_os()]) | {"SEEN_FILE", "FROM_TEST_ENV", "PYTHONPATH"}
    # what pytest sets for itself while it runs, and what Python sets in a C locale (PEP 538
    # coerces it to UTF-8 through LC_CTYPE): never the caller's
    own = {"PYTEST_CURRENT_TEST", "PYTEST_VERSION", "LC_CTYPE"}
    assert set(env) - allowed - own == set()


def test_settings_hash_changes_with_test_env(tmp_path):
    root = suite(tmp_path, test_one="""
        def test_one():
            pass
        """)
    store = Store.open(tmp_path / "store")
    for test_env in ({}, {"A": "1"}, {"A": "2"}, {}):
        run_tests(store, work_tree=root, repo=REPO, settings=TestSettings(test_env=test_env))
    hashes = [h for (h,) in store.query(
        "SELECT settings_hash FROM events WHERE kind = 'step.started' ORDER BY at").rows]
    assert len(set(hashes[:3])) == 3 and hashes[0] == hashes[3]
