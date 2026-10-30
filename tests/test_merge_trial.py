"""The live trial of the merge flow (#400 rows 2 to 6; `tests/merge_trial.py`) run on fixture GitHub: the
steps of each row, which only touch the `Trial`, the hub, the store and the merge flow's own
functions, give the numbers the result comment posts - so what the real run does with a real repo
is already proven here, and only the adapter's `gh` and git arguments are left for the real run."""

import json
import threading
from dataclasses import replace

import merge_trial
import pytest
from datetime import timedelta

from fake_github import FAILED, PASSED, REPO, FakeGitHub
from follow_world import NOW, Git
from line_world import CI

from orch_it import tickets
from orch_it.events.store import Store
from orch_it.testruns.github import GithubError
from required_names import LINUX_CHECK, WINDOWS_CHECK

OWNER = REPO.split("/")[0]
RED_TESTS = "tests/test_trial_pr_red_"
RED_MAIN = "tests/test_trial_main_red_"


class Locked:
    """A hub whose every call holds one lock: the fake is not made for the threads that `pr open` the pull requests."""

    def __init__(self, hub):
        self._hub, self._lock = hub, threading.RLock()

    def __getattr__(self, name):
        attr = getattr(self._hub, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            with self._lock:
                return attr(*args, **kwargs)

        return call


class FakeTrial:
    """The `Trial` over the fake: tickets and branches are made in it, and two branches that change a file
    differently conflict once the first of them merges, as git would say."""

    def __init__(self, hub, lock_root):
        self.hub, self.repo, self.base, self.lock_root = hub, REPO, "main", lock_root
        self.git = Git(hub)
        self.files: dict[str, dict] = {}  # branch -> {path: text, or None when removed}
        self.workflow_log: list[tuple[str, str]] = []
        self.off: set[str] = set()
        self.actions_log: list[bool] = []
        self.closed: list[int] = []
        self.adopted: list[int] = []
        self.cleaned = self.restored = False
        hub.on_merge.append(self._merged)

    def preflight(self):
        return {"fake": True}

    def make_ticket(self, title, body):
        number = self.hub._number()
        self.hub.add(number, body, ["ready-for-agent"], title=title)
        return number

    def make_branch(self, name, files):
        self.hub.branches[name] = [*self.hub.branches[self.base], self.hub.sha("b-")]
        self.files[name] = dict(files)
        return self.hub.branches[name][-1]

    def close_pull(self, pr):
        self.closed.append(pr)
        self.hub.close_pull(REPO, pr)

    def comment_as_person(self, pr, text):
        self.hub.note(pr, text, author=OWNER)

    def disable_workflow(self, file):
        self.workflow_log.append(("off", file))
        self.off.add(file)

    def enable_workflow(self, file):
        self.workflow_log.append(("on", file))
        self.off.discard(file)

    def set_actions(self, on):
        self.actions_log.append(on)
        self.hub.actions = on

    def dispatch_tests(self):
        self.dispatched = getattr(self, "dispatched", 0) + 1

    def adopt(self, ticket):
        self.adopted.append(ticket)

    def cleanup(self):
        self.cleaned = True
        return {"tickets": [], "pulls": [], "errors": []}

    def restore(self):
        self.restored = True

    def _merged(self, number):
        merged = self.files.get(self.hub.pulls[number].branch, {})
        for pull in self.hub.pulls.values():
            if pull.state == "open" and pull.number != number:
                same = [path for path, text in self.files.get(pull.branch, {}).items()
                        if path in merged and merged[path] != text]
                if same:
                    pull.conflict = tuple(sorted({*pull.conflict, *same}))


class RedByFiles(dict):
    """`CI.red`: a pull request whose branch adds a failing test file is red on it."""

    def __init__(self, hub, trial):
        super().__init__()
        self.hub, self.trial = hub, trial

    def get(self, number, default=None):
        pull = self.hub.pulls.get(number)
        files = self.trial.files.get(pull.branch, {}) if pull else {}
        ids = [f"{path}::test_red" for path, text in files.items() if text is not None and path.startswith(RED_TESTS)]
        return ids or default


class World:
    """Fixture GitHub with the CI of `line_world`, the trial's pushes to the base (a `tests` run per merge, red
    while a file `tests/test_trial_main_red_*` is on it) and a rerun of a run that comes back after a poll."""

    def __init__(self, tmp_path):
        self.hub = FakeGitHub(NOW)
        self.trial = FakeTrial(self.hub, tmp_path / "locks")
        self.ci = CI(self.hub)
        self.ci.red = RedByFiles(self.hub, self.trial)
        self.store = Store.open(tmp_path / "events")
        self.base_files: dict[str, str] = {}  # path -> text of the trial test files on the base
        self.hub.on_merge.append(self._pushed)
        self.rows = Locked(self.hub)

    def clock(self):
        return self.hub.now

    def sleep(self, seconds):
        self.ci.sleep(seconds)
        for i, run in enumerate(self.hub.main_runs):  # a run run again is at its next attempt
            attempt = self.hub.runs_by_id[run.run_id].attempt
            if attempt != run.attempt:
                self.hub.main_runs[i] = replace(run, attempt=attempt)

    def _pushed(self, number):
        pull = self.hub.pulls[number]
        for path, text in self.trial.files.get(pull.branch, {}).items():
            if text is None:
                self.base_files.pop(path, None)
            else:
                self.base_files[path] = text
        trial_tests = sorted(path for path in self.base_files if path.startswith(RED_MAIN))
        red = [path for path in trial_tests if self.base_files[path] == merge_trial.RED_ON_PUSH]
        jobs = {name: FAILED if red else PASSED for name in (LINUX_CHECK, WINDOWS_CHECK)}
        self.hub.main_run(pull.merge_sha, jobs, failing=[f"{path}::test_red_on_a_push" for path in red],
                          passed=[f"{path}::test_red_on_a_push" for path in trial_tests if path not in red])

    def run(self, row, **plan):
        return row(self.trial, self.rows, self.store, self.clock, self.sleep, merge_trial.Plan(**plan))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.mark.parametrize("workers", [1, 4])
def test_rows_2_and_3_four_merge_in_order_each_after_one_up_to_date_run_and_the_conflict_returns_its_ticket(
        world, workers):
    row2, row3 = world.run(merge_trial.line_rows, open_workers=workers)
    assert row2["passed"], row2
    assert (row2["tickets"], row2["merged"], row2["in_order"], row2["reason"]) == (4, 4, True, "empty")
    assert row2["merge_order"] == row2["expected_order"] == world.ci.merged[:4]
    for served in row2["prs"]:  # the head each one merged at had one run, on a head up to date with the base
        assert served["merged"] and served["line_result"] == "merged"
        assert (served["runs_on_merged_head"], served["attempts"], served["up_to_date_at_run_start"]) == (1, 1, True)
        assert sorted(served["checks"]) == [LINUX_CHECK, WINDOWS_CHECK]
    waited = [served["waited_minutes"] for served in row2["prs"]]
    assert waited == sorted(waited) and 0 < waited[0] and waited[-1] <= 5 * 6
    assert row2["ready_span_s"] <= 60

    assert row3["passed"], row3
    assert row3["line_result"] in (None, "left")  # the follow may take a waiting member out of the line between two turns
    assert row3["ticket_ready"] and not row3["ticket_parked"] and row3["last_comment_names_the_file"]
    assert row3["attempt_counted"] == [False] and not row3["in_line"]
    assert len(row3["behind"]) == 3 and row3["merged_behind"] == row3["behind"]  # nobody behind it was held
    assert row3["pr"] not in world.ci.merged and world.trial.closed == [row3["pr"]]  # and it is closed after


def test_rows_2_and_3_alone_return_their_own_numbers(world):
    row2 = world.run(merge_trial.row_2)
    assert row2["passed"] and row2["merged"] == 4
    other = World(world.trial.lock_root.parent / "other")
    assert other.run(merge_trial.row_3)["passed"]


def test_row_2_fails_when_a_head_merged_after_a_second_run(world):
    extra = []

    def a_second_run_on_every_head():  # a thrown away run: another run name on the same head
        for sha, checks in list(world.hub.check_runs.items()):
            if sha not in extra and all(c.state == PASSED for c in checks.values()):
                extra.append(sha)
                for name, check in checks.items():
                    checks[name] = replace(check, attempt=2)

    world.ci.on_tick.append(a_second_run_on_every_head)
    row2, _ = world.run(merge_trial.line_rows)
    assert row2["merged"] == 4 and not row2["passed"]
    assert {served["attempts"] for served in row2["prs"]} == {2}


def test_row_4_every_end_of_a_pull_request_moves_its_ticket_and_reads_back_from_github_and_the_store(world):
    row4 = world.run(merge_trial.row_4)
    assert row4["passed"], {end: row4[end] for end in row4 if isinstance(row4[end], dict) and not row4[end]["passed"]}
    assert row4["follow_workflow_disabled"] and world.trial.workflow_log == [("off", "follow.yml"),
                                                                               ("on", "follow.yml")]
    merged, later, red = row4["merged"], row4["after_merge"], row4["red"]
    assert (merged["open"], merged["state_reason"], merged["pr_state"]) == (False, "completed", "merged")
    assert "| 3 | pass |" in merged["comment"] and "ticket.closed" in merged["store"]
    assert (later["open"], later["ready"], later["parked"], later["follow"]) == (True, True, False, "after_merge")
    assert "ticket.closed" not in later["store"]
    assert (red["open"], red["ready"], red["follow"]) == (True, True, "red") and RED_TESTS in red["comment"]
    closed, said = row4["closed_unmerged"], row4["comment"]
    assert closed["needs_triage"] and not closed["ready"] and closed["pr_state"] == "closed"
    assert said["follow"] == "commented" and not said["in_line"] and said["needs_triage"]
    assert said["pr_state"] == "open" and "pr.commented" in said["store"]
    assert {said["pr"], red["pr"]} <= set(world.trial.closed)  # the ones still open are closed at the end


def test_row_4_fails_when_a_ticket_is_not_where_its_end_puts_it(world):
    real = world.trial.comment_as_person
    world.trial.comment_as_person = lambda pr, text: None  # nobody comments: the pull request stays in the line
    row4 = world.run(merge_trial.row_4)
    world.trial.comment_as_person = real
    assert not row4["passed"] and not row4["comment"]["passed"] and row4["merged"]["passed"]


def test_row_5_a_merge_turns_the_base_red_one_ticket_a_rerun_opens_none_and_green_closes_it(world):
    row5 = world.run(merge_trial.row_5)
    assert row5["passed"], row5
    first, second, third = row5["first_check"], row5["second_check"], row5["third_check"]
    assert (first["outcome"], len(first["opened"]), first["shape_violations"]) == ("red", 1, 0)
    assert set(first["labels"]) == {"ready-for-agent", "priority"} and first["names_the_pull_request"]
    assert row5["failing_ids"] and first["red_tickets"] == second["red_tickets"] == first["opened"]
    assert second["outcome"] == "red" and second["opened"] == [] and second["attempt"] == 2
    assert third["outcome"] == "green" and third["closed"] == first["opened"] == third["closed_by_this_check"]
    assert world.trial.workflow_log == [("off", "main-check.yml"), ("on", "main-check.yml")]
    [number] = first["opened"]
    issue = world.hub.issues[(REPO, number)]
    assert not issue.open and issue.state_reason == "completed"
    assert tickets.violations(tickets.parse(issue.body, title=issue.title, labels=issue.labels)) == []


def test_row_5_says_so_when_the_trial_test_does_not_turn_the_base_red(world):
    world.hub.on_merge.remove(world._pushed)
    world.hub.on_merge.append(lambda number: world.hub.main_run(world.hub.pulls[number].merge_sha,
                                                                {LINUX_CHECK: PASSED, WINDOWS_CHECK: PASSED}))
    with pytest.raises(merge_trial.TrialError, match="not red"):
        world.run(merge_trial.row_5)
    assert world.trial.workflow_log[-1] == ("on", "main-check.yml")  # the workflow is on again after a broken row


def test_row_6_reconcile_closes_the_ticket_of_a_pull_request_nobody_followed_with_actions_on_and_off(world):
    row6 = world.run(merge_trial.row_6)
    assert row6["passed"], row6
    on, off = row6["workflow_off"], row6["actions_off"]
    for half in (on, off):
        assert half["ticket_open_before"] and half["ticket_parked_before"] and not half["ticket_open_after"]
        assert ["closed", f"#{half['pr']}"] in half["repairs"] and half["state_reason"] == "completed"
    assert on["actions"] is True and off["actions"] is False
    assert world.trial.actions_log == [False, True] and world.hub.actions is True
    assert world.trial.workflow_log == [("off", "follow.yml"), ("on", "follow.yml")] * 2


def test_row_6_turns_actions_on_again_when_the_sweep_breaks(world):
    def broken(*args, **kwargs):
        raise RuntimeError("the sweep broke")

    world.hub.actions_enabled = broken  # read first by the sweep, before any guarded step
    with pytest.raises(RuntimeError):
        world.run(merge_trial.row_6)
    assert world.hub.actions is True and not world.trial.off


def test_the_script_runs_the_rows_asked_for_writes_their_numbers_and_prints_a_table(world, tmp_path, capsys):
    out = tmp_path / "numbers.json"
    code = merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "2,3,6",
                             "--out", str(out), "--store", str(tmp_path / "trial-store")],
                            world=lambda args: (world.trial, world.rows, world.clock, world.sleep))
    shown = capsys.readouterr().out
    report = json.loads(out.read_text("utf-8"))
    assert code == 0 and sorted(report["rows"]) == ["2", "3", "6"]
    assert report["rows"]["2"]["passed"] and report["rows"]["3"]["passed"] and report["preflight"] == {"fake": True}
    assert report["repo"] == REPO and report["base"] == "main" and report["plan"]["settings"]["line_minutes"] == 90
    assert "row  result  numbers" in shown and "PASS" in shown and "merged 4/4 in order=True" in shown
    assert world.trial.restored


def test_a_row_that_breaks_is_a_failed_row_the_next_rows_still_run_and_everything_is_restored(
        world, tmp_path, monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise merge_trial.TrialTimeout("the checks: not within 45 minutes")

    monkeypatch.setitem(merge_trial.ROW_FUNCTIONS, 4, broken)
    out = tmp_path / "numbers.json"
    code = merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "4,6",
                             "--out", str(out), "--store", str(tmp_path / "trial-store")],
                            world=lambda args: (world.trial, world.rows, world.clock, world.sleep))
    report = json.loads(out.read_text("utf-8"))
    assert code == 1 and report["rows"]["6"]["passed"]
    assert report["rows"]["4"]["passed"] is False and "TrialTimeout" in report["rows"]["4"]["error"]
    assert "FAIL" in capsys.readouterr().out and world.trial.restored


def test_a_repo_the_trial_cannot_run_on_is_told_before_any_row_runs(world, tmp_path, capsys):
    def refuse():
        raise merge_trial.TrialError("the repo is not allowed")

    world.trial.preflight = refuse
    code = merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "2",
                             "--out", str(tmp_path / "n.json"), "--store", str(tmp_path / "s")],
                            world=lambda args: (world.trial, world.rows, world.clock, world.sleep))
    assert code == 1 and "the repo is not allowed" in capsys.readouterr().out and not world.hub.pulls


@pytest.mark.parametrize("args", [["--rows", "7"], ["--rows", "two"], ["--repo", "no-slash"]])
def test_the_arguments_are_checked(args, tmp_path):
    base = {"--repo": REPO, "--base": "main", "--work-tree": str(tmp_path), "--rows": "2"}
    given = {**base, **dict(zip(args[::2], args[1::2], strict=True))}
    with pytest.raises(SystemExit) as caught:
        merge_trial.parse_args([part for pair in given.items() for part in pair])
    assert caught.value.code == 2


@pytest.mark.usefixtures("ticket_language_ru")  # the trial bodies are Russian (#413 unit 10a)
def test_the_trial_tickets_are_in_the_ticket_shape_and_only_the_second_body_has_an_after_merge_row():
    for body in (merge_trial.BODY, merge_trial.FULL_BODY):
        found = tickets.parse(body, title=merge_trial.TITLE, labels=("ready-for-agent",))
        assert tickets.violations(found) == []
    rows = {body: [row.after_merge for row in tickets.parse(body).acceptance]
            for body in (merge_trial.BODY, merge_trial.FULL_BODY)}
    assert rows[merge_trial.BODY] == [False] * 3 and rows[merge_trial.FULL_BODY] == [False] * 3 + [True]
    assert [line.split("|")[1].strip() for line in merge_trial.ROWS.splitlines()[2:]] == ["1", "2", "3"]


class Recorder:
    def __init__(self, answers=()):
        self.calls, self.answers = [], dict(answers)

    def __call__(self, args):
        self.calls.append(args)
        said = " ".join(args)
        starts = [start for start in self.answers if said.startswith(start)]
        return self.answers[max(starts, key=len)] if starts else ""


def test_the_real_adapter_makes_branches_and_turns_things_off_with_the_arguments_gh_and_git_take(tmp_path):
    hub, gh, git = FakeGitHub(NOW), Recorder(), Recorder({"rev-parse": "abc123\n"})
    trial = merge_trial.RealTrial(REPO, "trial-main", tmp_path, hub, gh=gh, identity=("Trial", "trial@example.org"))
    trial.git = git
    assert trial.make_branch("trial-1-a", {"trial/x/a.txt": "text\n", "tests/gone.py": None}) == "abc123"
    assert (tmp_path / "trial" / "x" / "a.txt").read_text("utf-8") == "text\n"
    assert git.calls == [
        ["fetch", "--quiet", "origin", "trial-main"], ["checkout", "--quiet", "-B", "trial-1-a", "origin/trial-main"],
        ["add", "--", "trial/x/a.txt"], ["rm", "--quiet", "--ignore-unmatch", "--", "tests/gone.py"],
        ["-c", "user.name=Trial", "-c", "user.email=trial@example.org", "commit", "--quiet", "-m", "trial: trial-1-a"],
        ["push", "--quiet", "origin", "trial-1-a:refs/heads/trial-1-a"], ["rev-parse", "HEAD"]]
    number = trial.make_ticket("A ticket", merge_trial.BODY)
    assert hub.issues[(REPO, number)].labels == ("ready-for-agent",)
    trial.close_pull(7)
    trial.comment_as_person(7, "wait")
    trial.disable_workflow("follow.yml")
    trial.set_actions(False)
    assert gh.calls == [["pr", "close", "7", "--repo", REPO], ["pr", "comment", "7", "--repo", REPO, "--body", "wait"],
                        ["workflow", "disable", "follow.yml", "--repo", REPO],
                        ["api", "-X", "PUT", f"repos/{REPO}/actions/permissions", "-F", "enabled=false"]]
    gh.calls.clear()
    trial.restore()  # whatever the trial turned off comes back on, whichever way the script ends
    assert gh.calls == [["api", "-X", "PUT", f"repos/{REPO}/actions/permissions", "-F", "enabled=true"],
                        ["workflow", "enable", "follow.yml", "--repo", REPO]]
    gh.calls.clear()
    trial.restore()
    assert gh.calls == []


def test_a_workflow_that_cannot_be_disabled_is_said_and_the_row_goes_on(world):
    def absent(file):
        raise merge_trial.TrialError(f"workflow {file}: HTTP 404")

    world.trial.disable_workflow = absent
    row6 = world.run(merge_trial.row_6)
    assert row6["workflow_off"]["workflow_off"] is False and world.trial.workflow_log == []


# --- the preflight, the cleanup and the restore of the real adapter ---------------------------------------------

GOOD = {
    f"api repos/{REPO}": json.dumps({"default_branch": "main", "private": False}),
    f"api repos/{REPO}/branches/trial-main": json.dumps({"protected": True}),
    f"api repos/{REPO}/actions/workflows/tests.yml": json.dumps({"id": 1, "path": ".github/workflows/tests.yml"}),
    f"api repos/{REPO}/branches/trial-main/protection": json.dumps({"required_status_checks": {
        "strict": True, "checks": [{"context": WINDOWS_CHECK}, {"context": LINUX_CHECK}]}}),
}


class Refusing(Recorder):
    """`gh` that answers with `GithubError` for the calls whose start is in `fail`."""

    def __init__(self, answers, fail=()):
        super().__init__(answers)
        self.fail = tuple(fail)

    def __call__(self, args):
        if any(" ".join(args).startswith(start) for start in self.fail):
            self.calls.append(args)
            raise GithubError("gh_failed", f"{' '.join(args[:3])}: HTTP 404")
        return super().__call__(args)


def real(tmp_path, answers=None, *, base="trial-main", remote=None, status="", fail=(), **kwargs):
    gh = Refusing(GOOD if answers is None else answers, fail)
    trial = merge_trial.RealTrial(REPO, base, tmp_path, FakeGitHub(NOW), gh=gh, **kwargs)
    trial.git = Recorder({"remote": f"{remote or f'https://github.com/{REPO}.git'}\n", "status": status})
    return trial


def test_the_preflight_of_a_clone_and_a_repo_it_can_run_on_says_what_it_stands_on(tmp_path):
    found = real(tmp_path).preflight()
    assert found == {"default_branch": "main", "private": False, "base_is_default": False, "base_protected": True,
                     "strict": True, "required": [LINUX_CHECK, WINDOWS_CHECK]}


@pytest.mark.parametrize("remote", [f"https://github.com/{REPO}.git", f"https://github.com/{REPO}",
                                    f"https://github.com/{REPO}/", f"https://git@github.com/{REPO}.git",
                                    f"git@github.com:{REPO}.git", f"ssh://git@github.com/{REPO}.git",
                                    "https://github.com/OWNER-X/Repo-Y.git"])
def test_the_origin_of_the_clone_names_exactly_the_repo(tmp_path, remote):
    assert real(tmp_path, remote=remote).preflight()["required"] == [LINUX_CHECK, WINDOWS_CHECK]


@pytest.mark.parametrize("remote", [f"https://github.com/{REPO}-fork.git", f"https://github.com/x{REPO}.git",
                                    f"https://github.com/someone/{REPO}.git", f"https://example.org/{REPO}.git",
                                    f"https://github.com/{REPO}.git/more", "C:/clones/repo-y"])
def test_an_origin_that_only_contains_the_repo_is_refused(tmp_path, remote):
    with pytest.raises(merge_trial.TrialError, match="origin"):
        real(tmp_path, remote=remote).preflight()


def test_a_clone_with_changes_to_tracked_files_is_refused_and_untracked_files_are_not_asked_about(tmp_path):
    trial = real(tmp_path, status=" M src/orch_it/cli.py\n")
    with pytest.raises(merge_trial.TrialError, match="changes to tracked files"):
        trial.preflight()
    assert ["status", "--porcelain", "--untracked-files=no"] in trial.git.calls  # git is not shown untracked files
    assert real(tmp_path, status="").preflight()


def test_a_base_branch_that_is_not_there_is_refused(tmp_path):
    with pytest.raises(merge_trial.TrialError, match="base branch trial-main is not in"):
        real(tmp_path, fail=[f"api repos/{REPO}/branches/trial-main"]).preflight()


def test_a_repo_without_the_tests_workflow_is_refused(tmp_path):
    with pytest.raises(merge_trial.TrialError, match="no `tests.yml` workflow"):
        real(tmp_path, fail=[f"api repos/{REPO}/actions/workflows/tests.yml"]).preflight()


def test_a_base_without_branch_protection_or_with_protection_that_is_not_what_orch_it_has_is_refused(tmp_path):
    protection = f"api repos/{REPO}/branches/trial-main/protection"
    with pytest.raises(merge_trial.TrialError, match="no readable branch protection"):
        real(tmp_path, fail=[protection]).preflight()

    def with_checks(checks):
        return {**GOOD, protection: json.dumps({"required_status_checks": checks})}

    for checks, said in [({"strict": False, "checks": [{"context": LINUX_CHECK}, {"context": WINDOWS_CHECK}]}, "strict"),
                         ({"strict": True, "checks": [{"context": LINUX_CHECK}]}, rf"\['{LINUX_CHECK}'\]"),
                         ({"strict": True, "checks": [{"context": LINUX_CHECK}, {"context": WINDOWS_CHECK},
                                                       {"context": "lint"}]}, "lint"),
                         ({"strict": True, "contexts": [LINUX_CHECK, WINDOWS_CHECK]}, None),
                         ({}, "strict")]:
        trial = real(tmp_path, with_checks(checks))
        if said is None:
            assert trial.preflight()["required"] == [LINUX_CHECK, WINDOWS_CHECK]  # the older form of the same rule
        else:
            with pytest.raises(merge_trial.TrialError, match=said):
                trial.preflight()


def test_the_default_branch_is_refused_as_the_base_unless_it_is_allowed(tmp_path):
    answers = {k.replace("trial-main", "main"): v for k, v in GOOD.items()}
    with pytest.raises(merge_trial.TrialError, match="default branch.*--allow-default-base"):
        real(tmp_path, answers, base="main").preflight()
    assert real(tmp_path, answers, base="main", allow_default_base=True).preflight()["base_is_default"] is True


def test_the_preflight_keeps_the_default_branch_it_read_so_a_row_needs_no_second_read(tmp_path):
    answers = {k.replace("trial-main", "main"): v for k, v in GOOD.items()}
    trial = real(tmp_path, answers, base="main", allow_default_base=True)
    trial.preflight()

    assert trial.default_branch == "main"


def test_the_new_arguments_default_to_off_and_allow_default_base_reaches_the_adapter(tmp_path):
    args = merge_trial.parse_args(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "5"])
    assert args.allow_default_base is False and args.keep is False and args.store is None
    on = merge_trial.parse_args(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--keep",
                                 "--allow-default-base"])
    assert merge_trial._real_world(on)[0].allow_default_base is True and on.keep


def test_the_cleanup_takes_ready_off_closes_every_open_trial_ticket_and_pull_request_and_nothing_else(tmp_path):
    hub = FakeGitHub(NOW)
    trial = merge_trial.RealTrial(REPO, "main", tmp_path, hub, gh=Recorder())
    trial.git = Recorder({"rev-parse": "abc\n"})
    mine, done, adopted = (trial.make_ticket(f"t{n}", merge_trial.BODY) for n in range(3))
    hub.close(done)  # a ticket the flow closed itself
    red = hub._number()
    hub.add(red, "red on main", ["ready-for-agent", "priority"])
    trial.adopt(red)
    trial.adopt(red)
    strange = hub._number()
    hub.add(strange, "someone else's", ["ready-for-agent"])
    trial.make_branch("trial-1-a", {"a.txt": "a\n"})
    ours = hub.open_pull(branch="trial-1-a", body=f"Refs #{mine}")
    other = hub.open_pull(branch="somebody-elses")
    report = trial.cleanup()
    assert report == {"tickets": [mine, adopted, red], "pulls": [ours], "errors": []}
    for number in (mine, adopted, red):
        issue = hub.issues[(REPO, number)]
        assert not issue.open and issue.state_reason == "not_planned" and "ready-for-agent" not in issue.labels
        assert "cleanup" in hub.comments(REPO, number)[-1].body
    assert hub.issues[(REPO, done)].state_reason == "completed"  # a ticket already closed is left as it was
    assert hub.issues[(REPO, strange)].open and "ready-for-agent" in hub.issues[(REPO, strange)].labels
    assert hub.pulls[ours].state == "closed" and hub.pulls[other].state == "open"
    assert [c[1] for c in hub.calls if c[0] == "close_issue"] == [mine, adopted, red]
    assert trial.cleanup() == {"tickets": [], "pulls": [], "errors": []}


def test_a_ticket_the_cleanup_cannot_close_is_told_and_the_others_still_close(tmp_path):
    hub = FakeGitHub(NOW)
    trial = merge_trial.RealTrial(REPO, "main", tmp_path, hub, gh=Recorder())
    first, second = trial.make_ticket("a", merge_trial.BODY), trial.make_ticket("b", merge_trial.BODY)
    hub.failing.add((REPO, first))
    report = trial.cleanup()
    assert report["tickets"] == [second] and len(report["errors"]) == 1 and f"#{first}" in report["errors"][0]


def test_the_script_cleans_up_unless_told_to_keep_and_does_it_when_a_row_broke(world, tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("a row broke")

    monkeypatch.setitem(merge_trial.ROW_FUNCTIONS, 6, broken)
    argv = ["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "6",
            "--store", str(tmp_path / "trial-store")]
    factory = lambda args: (world.trial, world.rows, world.clock, world.sleep)  # noqa: E731
    out = tmp_path / "kept.json"
    merge_trial.main([*argv, "--keep", "--out", str(out)], world=factory)
    assert not world.trial.cleaned and "cleanup" not in json.loads(out.read_text("utf-8"))
    out = tmp_path / "cleaned.json"
    code = merge_trial.main([*argv, "--out", str(out)], world=factory)
    assert world.trial.cleaned and json.loads(out.read_text("utf-8"))["cleanup"]["errors"] == [] and code == 1


def test_a_cleanup_that_fails_is_told_and_the_restore_still_runs(world, tmp_path, capsys):
    def refuse():
        raise RuntimeError("gh is gone")

    world.trial.cleanup = refuse
    out = tmp_path / "n.json"
    code = merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "6",
                             "--out", str(out), "--store", str(tmp_path / "s")],
                            world=lambda args: (world.trial, world.rows, world.clock, world.sleep))
    assert code == 1 and world.trial.restored
    assert "gh is gone" in json.loads(out.read_text("utf-8"))["cleanup_error"]
    assert "CLEANUP FAILED" in capsys.readouterr().out


def test_row_5_gives_the_tickets_a_main_check_opened_to_the_cleanup(world):
    world.run(merge_trial.row_5)
    assert len(world.trial.adopted) >= 1  # the red-on-main ticket, closed on green, is still the trial's


def test_the_restore_turns_back_on_everything_it_can_and_then_says_what_stayed_off(tmp_path):
    gh = Refusing({}, fail=["api -X PUT", "workflow enable follow.yml"])
    trial = merge_trial.RealTrial(REPO, "main", tmp_path, FakeGitHub(NOW), gh=gh)
    for file in ("follow.yml", "main-check.yml"):
        trial.disable_workflow(file)
    trial._actions_off = True
    with pytest.raises(merge_trial.TrialError) as caught:
        trial.restore()
    assert ["workflow", "enable", "main-check.yml", "--repo", REPO] in gh.calls  # tried after the two that failed
    said = str(caught.value)
    assert "Actions" in said and "follow.yml" in said and "main-check" not in said
    assert trial._workflows_off == {"follow.yml"} and trial._actions_off


def test_a_branch_that_half_failed_leaves_the_work_tree_at_the_base_and_the_files_it_wrote_gone(tmp_path):
    class FailsAtCommit(Recorder):
        def __call__(self, args):
            super().__call__(args)
            if "commit" in args:
                raise merge_trial.merge_git.GitError("git_failed", "commit: nothing to commit")
            return ""

    (tmp_path / "mine.txt").write_text("a person's untracked file\n", "utf-8")
    trial = merge_trial.RealTrial(REPO, "trial-main", tmp_path, FakeGitHub(NOW), gh=Recorder())
    trial.git = git = FailsAtCommit()
    with pytest.raises(merge_trial.merge_git.GitError, match="nothing to commit"):
        trial.make_branch("trial-1-a", {"trial/a.txt": "a\n"})
    assert git.calls[-2:] == [["checkout", "--quiet", "--force", "--detach", "origin/trial-main"],
                              ["ls-files", "--", "trial/a.txt"]]
    assert not (tmp_path / "trial" / "a.txt").exists()  # written by the script, untracked: gone
    assert (tmp_path / "mine.txt").exists()  # nobody else's file is touched


def test_a_file_the_base_tracks_is_not_deleted_by_the_reset(tmp_path):
    class Tracks(Recorder):
        def __call__(self, args):
            super().__call__(args)
            if "commit" in args:
                raise merge_trial.merge_git.GitError("git_failed", "no")
            return "tracked\n" if args[0] == "ls-files" else ""

    trial = merge_trial.RealTrial(REPO, "trial-main", tmp_path, FakeGitHub(NOW), gh=Recorder())
    trial.git = Tracks()
    with pytest.raises(merge_trial.merge_git.GitError):
        trial.make_branch("trial-1-a", {"tracked.txt": "changed\n"})
    assert (tmp_path / "tracked.txt").exists()  # git restored it, the script does not remove what the base holds


def test_the_events_of_a_ticket_are_read_for_this_repo_only(world):
    from orch_it.merge import record

    record.write(world.store, REPO, 7, "github", None, None, [("comment.written", {"comment_kind": "closing"})])
    record.write(world.store, "other-x/other-y", 7, "github", None, None, [("attempt.counted", {
        "reason": "red", "counted": True, "in_a_row": 1})])
    run = merge_trial._Run(world.trial, world.rows, world.store, world.clock, world.sleep, None, "r9")
    assert [kind for kind, _ in run.events(7)] == ["comment.written"]


def test_each_run_without_a_store_argument_gets_a_new_store_folder(world, tmp_path, monkeypatch):
    made = []

    def fresh(prefix):
        made.append(tmp_path / f"store-{len(made)}")
        made[-1].mkdir()
        return str(made[-1])

    monkeypatch.setattr(merge_trial.tempfile, "mkdtemp", fresh)
    for n in range(2):
        merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "6",
                          "--out", str(tmp_path / f"{n}.json")],
                         world=lambda args: (world.trial, world.rows, world.clock, world.sleep))
    assert len(made) == 2 and made[0] != made[1]
    assert json.loads((tmp_path / "1.json").read_text("utf-8"))["store"] == str(made[1])


def test_checks_that_never_come_end_the_row_at_its_bound_with_a_timeout_and_everything_is_restored(world, tmp_path):
    world.sleep = lambda seconds: world.hub.later(seconds=seconds)  # no CI: no check run ever starts
    started = world.hub.now
    out = tmp_path / "numbers.json"
    code = merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "6",
                             "--wait-minutes", "3", "--out", str(out), "--store", str(tmp_path / "s")],
                            world=lambda args: (world.trial, world.rows, world.clock, world.sleep))
    report = json.loads(out.read_text("utf-8"))
    assert code == 1 and report["rows"]["6"]["passed"] is False
    assert "TrialTimeout" in report["rows"]["6"]["error"] and "not within 3 minutes" in report["rows"]["6"]["error"]
    assert (world.hub.now - started) <= timedelta(minutes=4)  # bounded: the bound and one poll
    assert not world.trial.off and world.hub.actions is True and world.trial.restored
    with pytest.raises(merge_trial.TrialTimeout):
        world.run(merge_trial.row_6, wait_minutes=1)
    assert not world.trial.off


def test_a_wait_that_gh_fails_in_is_tried_again_and_then_raised(world):
    calls = []

    def flaky():
        calls.append(1)
        raise GithubError("gh_failed", "HTTP 502")

    run = merge_trial._Run(world.trial, world.rows, world.store, world.clock, world.sleep, None, "r9")
    with pytest.raises(GithubError):
        run.wait("a thing", flaky)
    assert len(calls) == 4


def test_a_real_trial_asks_for_the_base_s_run_only_when_told_to(tmp_path):
    calls = []
    for dispatch in (False, True):
        trial = merge_trial.RealTrial(REPO, "trial-main", tmp_path, hub=None, gh=lambda args: calls.append(args) or "",
                                      dispatch_base_runs=dispatch)
        trial.dispatch_tests()
    assert calls == [["workflow", "run", "tests.yml", "--repo", REPO, "--ref", "trial-main", "-f", "slow_and_live=false"]]


def test_the_trial_s_red_test_is_red_on_a_dispatched_run_of_the_base_too(tmp_path, monkeypatch):
    namespace = {}
    exec(merge_trial.RED_ON_PUSH, namespace)
    for event, red in (("push", True), ("workflow_dispatch", True), ("pull_request", False)):
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        try:
            namespace["test_red_on_a_push"]()
            failed = False
        except AssertionError:
            failed = True
        assert failed is red, event
