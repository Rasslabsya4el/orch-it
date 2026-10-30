"""A test that ran on GitHub on placeholder files does not settle the merge (#414 row 15, part 3 unit 11): it reruns
where the real files are, and the result names those tests. A test reads a placeholder when the run's report says so
(the `reads_placeholder` property the `orch_it_ids` plugin writes for a test that opens a file named in
`ORCH_IT_PLACEHOLDERS`), so a test the pull request itself adds is found in the same run that ran it."""

import json
import os
import subprocess
import sys
import textwrap
from xml.sax.saxutils import quoteattr

from orch_it.events.store import Store
from orch_it.testruns import junit
from orch_it.testruns.config import Machine, TestSettings
from orch_it.testruns.places import PlaceEnd
from orch_it.testruns.pytest_plugin import FOLDER
from orch_it.testruns.runner import NO_WINDOW, run_tests

REPO = "acme-x/widgets-y"
READS = ("tests.test_data::test_reads_a", "tests.test_data::test_reads_b", "tests.test_new::test_added_by_the_pr")
PLAIN = ("tests.test_data::test_plain_one", "tests.test_data::test_plain_two")
ADDED = READS[2]


def case(test_id, *, reads=False, red=False):
    classname, _, name = test_id.partition("::")
    props = "<properties><property name='reads_placeholder' value='1'/></properties>" if reads else ""
    child = "<failure message='the real file differs'/>" if red else ""
    return f"<testcase classname={quoteattr(classname)} name={quoteattr(name)}>{props}{child}</testcase>"


def report_text(cases):
    return f"<testsuites><testsuite name='pytest'>{''.join(cases)}</testsuite></testsuites>"


class FakeGithub:
    """The github place with the placeholders of a stub in the repo's workflow: its report flags the tests that read one."""

    id, os = "github", "linux"

    def __init__(self, *, reads=READS, plain=PLAIN, red=()):
        self.reads, self.plain, self.red, self.runs = reads, plain, red, 0

    def run(self, request, scratch):
        self.runs += 1
        report = scratch / "junit.xml"
        report.write_text(report_text([case(t, reads=True, red=t in self.red) for t in self.reads]
                                      + [case(t, red=t in self.red) for t in self.plain]), "utf-8")
        return PlaceEnd(report, False, False, 0, 0.0, 1.0)


class RealFiles:
    """A place that has the real files: it runs only the ids asked and fails the ones in `red`."""

    os = "linux"

    def __init__(self, red=()):
        self.id, self.red, self.asked = "machine:pc", red, []

    def run(self, request, scratch):
        self.asked.append(list(request.ids) if request.ids is not None else None)
        report = scratch / "junit.xml"
        ids = request.ids if request.ids is not None else [*READS, *PLAIN]
        report.write_text(report_text([case(t, red=t in self.red) for t in ids]), "utf-8")
        return PlaceEnd(report, False, False, 0, 0.0, 1.0)


SETTINGS = TestSettings(github_tests=True, machines=(Machine("pc", "pc", "linux", "/x"),))


def go(tmp_path, github, pc=None, *, store=None, places=None):
    store = store or Store.open(tmp_path / "store")
    work = tmp_path / "repo"
    work.mkdir(exist_ok=True)
    pc = pc or RealFiles()
    places = places if places is not None else {"github": github, "machine:pc": pc}
    done = run_tests(store, work_tree=work, repo=REPO, settings=SETTINGS, places=places, place="github")
    return done, store, pc


def runs_started(store):
    return [json.loads(d) for (d,) in store.query(
        "SELECT detail FROM events WHERE kind = 'step.started' AND step = 'test_run' ORDER BY at, id").rows]


def test_p1_green_on_placeholders_reruns_the_three_where_the_real_files_are_and_lands_naming_them(tmp_path):
    github = FakeGithub()
    done, store, pc = go(tmp_path, github)
    assert github.runs == 1
    assert [sorted(ids) for ids in pc.asked] == [sorted(READS)]  # the 3 that read a placeholder, not the 2 others
    assert (done.outcome, done.place) == ("passed", "machine:pc")
    assert sorted(done.placeholder_tests) == sorted(READS)
    assert done.failed_tests == ()
    assert [s["place"] for s in runs_started(store)] == ["github", "machine:pc"]


def test_p2_red_on_the_real_file_does_not_land_and_names_the_one_test(tmp_path):
    done, store, pc = go(tmp_path, FakeGithub(), RealFiles(red=(READS[1],)))
    assert (done.outcome, done.reason, done.failed) == ("failed", "red", 1)
    assert done.failed_tests == (READS[1],)
    assert sorted(done.placeholder_tests) == sorted(READS)
    failed = store.query("SELECT test_id FROM events WHERE kind = 'test.result' AND run = ?"
                         " AND outcome = 'failed'", (done.run,)).rows
    assert failed == [(READS[1],)]  # the line reads the failing ids of the run it is handed


def test_a_test_added_by_the_pull_request_reruns_with_no_earlier_record_of_what_it_reads(tmp_path):
    store = Store.open(tmp_path / "store")
    # the store knows an earlier green github run of the two old tests only
    go(tmp_path, FakeGithub(reads=READS[:2], plain=PLAIN), store=store)
    assert store.query("SELECT count(*) FROM events WHERE test_id = ?", (ADDED,)).rows == [(0,)]
    done, _, pc = go(tmp_path, FakeGithub(), RealFiles(red=(ADDED,)), store=store)
    assert ADDED in pc.asked[0]
    assert done.failed_tests == (ADDED,)


def ended_details(store, place):
    rows = store.query("SELECT e.detail FROM events e JOIN events s ON s.run = e.run AND s.kind = 'step.started'"
                       " WHERE e.kind = 'step.ended' AND e.step = 'test_run' AND s.detail LIKE ?"
                       " ORDER BY e.at, e.id", (f'{{"place":"{place}"%',)).rows
    return [json.loads(d) for (d,) in rows]


def test_the_rerun_s_end_carries_the_tests_it_reran_and_the_ones_that_failed(tmp_path):
    _, store, _ = go(tmp_path, FakeGithub(), RealFiles(red=(READS[1],)))
    [github] = ended_details(store, "github")
    [rerun] = ended_details(store, "machine:pc")
    assert "placeholder_tests" not in github
    assert sorted(rerun["placeholder_tests"]) == sorted(READS)
    assert rerun["failed_tests"] == [READS[1]]


def test_a_green_rerun_names_the_tests_it_reran_and_no_failed_ones(tmp_path):
    _, store, _ = go(tmp_path, FakeGithub())
    [rerun] = ended_details(store, "machine:pc")
    assert sorted(rerun["placeholder_tests"]) == sorted(READS)
    assert "failed_tests" not in rerun


def test_a_run_that_is_no_rerun_carries_neither_field(tmp_path):
    _, store, _ = go(tmp_path, FakeGithub(reads=(), plain=(*READS, *PLAIN)))
    [github] = ended_details(store, "github")
    assert "placeholder_tests" not in github and "failed_tests" not in github


def test_the_property_name_is_the_same_in_the_reader_and_the_standalone_plugin():
    from orch_it.testruns.pytest_plugin import orch_it_ids

    assert orch_it_ids.PLACEHOLDER_PROPERTY == junit.PLACEHOLDER_PROPERTY


def test_a_run_with_no_placeholder_reads_is_unchanged(tmp_path):
    github = FakeGithub(reads=(), plain=(*READS, *PLAIN))
    done, store, pc = go(tmp_path, github)
    assert (done.outcome, done.place, done.placeholder_tests) == ("passed", "github", ())
    assert pc.asked == []
    assert [s["place"] for s in runs_started(store)] == ["github"]


def test_a_red_github_run_is_red_and_is_not_rerun(tmp_path):
    done, _, pc = go(tmp_path, FakeGithub(red=(PLAIN[0],)))
    assert (done.outcome, done.place) == ("failed", "github")
    assert pc.asked == []


def test_no_place_with_the_real_files_leaves_the_placeholder_green_unsettled(tmp_path):
    done, store, _ = go(tmp_path, FakeGithub(), places={"github": FakeGithub()})
    assert done.outcome != "passed"
    assert sorted(done.placeholder_tests) == sorted(READS)
    assert [s["place"] for s in runs_started(store)][0] == "github"


def test_the_rerun_is_never_placed_on_github_again(tmp_path):
    github = FakeGithub()
    go(tmp_path, github)
    assert github.runs == 1


def test_a_report_case_with_the_placeholder_property_reads_back_flagged(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(report_text([case("a.b::t1", reads=True), case("a.b::t2")]), "utf-8")
    one, two = junit.read(path)
    assert (one.placeholder, two.placeholder) == (True, False)


def test_the_plugin_flags_each_test_that_opens_a_placeholder_file_and_no_other(tmp_path):
    work = tmp_path / "work"
    (work / "data").mkdir(parents=True)
    stub = "data/" + "private" + ".json"
    (work / stub).write_text("{}", "utf-8")
    (work / "data" / "public.json").write_text("{}", "utf-8")
    (work / "test_files.py").write_text(textwrap.dedent(f"""
        import json
        from pathlib import Path
        import pytest

        @pytest.fixture
        def loaded():
            return json.load(open({stub!r}))

        def test_reads_with_open():
            open({stub!r}).read()

        def test_reads_with_path():
            Path({stub!r}).read_text()

        def test_reads_in_a_fixture(loaded):
            pass

        def test_reads_another_file():
            Path("data/public.json").read_text()

        def test_reads_nothing():
            pass
        """), "utf-8")
    env = {**os.environ, "PYTHONPATH": str(FOLDER), "ORCH_IT_PLACEHOLDERS": stub}
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "orch_it_ids", "-p", "no:cacheprovider",
                    f"--junitxml={work / 'junit.xml'}"], cwd=work, env=env, check=True, capture_output=True,
                   creationflags=NO_WINDOW)
    flagged = {c.name: c.placeholder for c in junit.read(work / "junit.xml")}
    assert flagged == {"test_reads_with_open": True, "test_reads_with_path": True, "test_reads_in_a_fixture": True,
                       "test_reads_another_file": False, "test_reads_nothing": False}


def test_with_no_placeholder_list_the_plugin_flags_nothing(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "test_x.py").write_text("def test_x():\n    open(__file__).read()\n", "utf-8")
    env = {**os.environ, "PYTHONPATH": str(FOLDER)}
    env.pop("ORCH_IT_PLACEHOLDERS", None)
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "orch_it_ids", "-p", "no:cacheprovider",
                    f"--junitxml={work / 'junit.xml'}"], cwd=work, env=env, check=True, capture_output=True,
                   creationflags=NO_WINDOW)
    assert [c.placeholder for c in junit.read(work / "junit.xml")] == [False]
