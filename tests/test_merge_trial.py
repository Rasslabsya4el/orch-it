"""The live trial of the merge flow (#400 rows 2 to 6; `tests/merge_trial.py`) run on fixture GitHub: the
steps of each row, which only touch the `Trial`, the hub, the store and the merge flow's own
functions, give the numbers the result comment posts - so what the real run does with a real repo
is already proven here, and only the adapter's `gh` and git arguments are left for the real run."""

import json
import threading
from dataclasses import replace

import merge_trial
import pytest
from fake_github import FAILED, PASSED, REPO, FakeGitHub
from follow_world import NOW, Git
from line_world import CI

from orch_it import tickets
from orch_it.events.store import Store

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
        self.base_files: set[str] = set()
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
            (self.base_files.discard if text is None else self.base_files.add)(path)
        red = sorted(path for path in self.base_files if path.startswith(RED_MAIN))
        jobs = {name: FAILED if red else PASSED for name in ("linux", "windows")}
        self.hub.main_run(pull.merge_sha, jobs, failing=[f"{path}::test_red_on_a_push" for path in red])

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
        assert sorted(served["checks"]) == ["linux", "windows"]
    waited = [served["waited_minutes"] for served in row2["prs"]]
    assert waited == sorted(waited) and 0 < waited[0] and waited[-1] <= 5 * 6
    assert row2["ready_span_s"] <= 60

    assert row3["passed"], row3
    assert row3["line_result"] in (None, "left")  # the follow of a held member may take it out between two turns
    assert row3["ticket_ready"] and not row3["ticket_parked"] and row3["last_comment_names_the_file"]
    assert row3["attempt_counted"] == [False] and row3["auto_merge"] is None
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
    assert said["follow"] == "commented" and said["auto_merge"] is None and said["needs_triage"]
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
                                                                {"linux": PASSED, "windows": PASSED}))
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
        raise merge_trial.TrialError("auto-merge is not allowed")

    world.trial.preflight = refuse
    code = merge_trial.main(["--repo", REPO, "--base", "main", "--work-tree", str(tmp_path), "--rows", "2",
                             "--out", str(tmp_path / "n.json"), "--store", str(tmp_path / "s")],
                            world=lambda args: (world.trial, world.rows, world.clock, world.sleep))
    assert code == 1 and "auto-merge is not allowed" in capsys.readouterr().out and not world.hub.pulls


@pytest.mark.parametrize("args", [["--rows", "7"], ["--rows", "two"], ["--repo", "no-slash"]])
def test_the_arguments_are_checked(args, tmp_path):
    base = {"--repo": REPO, "--base": "main", "--work-tree": str(tmp_path), "--rows": "2"}
    given = {**base, **dict(zip(args[::2], args[1::2], strict=True))}
    with pytest.raises(SystemExit) as caught:
        merge_trial.parse_args([part for pair in given.items() for part in pair])
    assert caught.value.code == 2


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


def test_the_real_adapter_refuses_a_clone_of_another_repo_and_a_repo_without_auto_merge(tmp_path):
    answers = {"api repos/owner-x/repo-y": json.dumps({"allow_auto_merge": False, "default_branch": "main"})}
    trial = merge_trial.RealTrial(REPO, "main", tmp_path, FakeGitHub(NOW), gh=Recorder(answers))
    trial.git = Recorder({"remote": "https://github.com/someone/else.git\n"})
    with pytest.raises(merge_trial.TrialError, match="origin"):
        trial.preflight()
    trial.git = Recorder({"remote": f"https://github.com/{REPO}.git\n"})
    with pytest.raises(merge_trial.TrialError, match="auto-merge"):
        trial.preflight()
    answers["api repos/owner-x/repo-y"] = json.dumps({"allow_auto_merge": True, "default_branch": "main"})
    answers[f"api repos/{REPO}/branches/main/protection"] = json.dumps({"required_status_checks": {
        "strict": True, "checks": [{"context": "windows"}, {"context": "linux"}]}})
    trial.gh = Recorder(answers)
    found = trial.preflight()
    assert (found["strict"], found["required"], found["base_is_default"]) == (True, ["linux", "windows"], True)


def test_a_workflow_that_cannot_be_disabled_is_said_and_the_row_goes_on(world):
    def absent(file):
        raise merge_trial.TrialError(f"workflow {file}: HTTP 404")

    world.trial.disable_workflow = absent
    row6 = world.run(merge_trial.row_6)
    assert row6["workflow_off"]["workflow_off"] is False and world.trial.workflow_log == []
