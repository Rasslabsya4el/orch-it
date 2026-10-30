"""GitHub off or out of minutes, and a private repo (#414 items 6 and 7, rows 7 and 8, on fakes).

GitHub's runs do not start when Actions is off for the repo, when the account's minutes are spent, or when a job has
waited in the account's queue past its bound: the `github` place is then left out (and the decision names which cause),
the required suite runs at the other places, and the line posts the result to the batch sha under the required check
names, so the wait for the names is satisfied and the batch merges. A repo whose base has no protection is held by the
line alone: every required name must be green. `guards status` says who holds the required checks. For a private repo GitHub's
minutes are a cost: the machine places go first."""

import contextlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fake_github import FAILED, PASSED, REPO, FakeActions
from line_world import line_world
from required_names import LINUX_CHECK, LINUX_OS, WINDOWS_CHECK, WINDOWS_OS
from test_merge_batch import FixturePlace, passes

from orch_it.events.store import Store
from orch_it.guards import status as guards_status
from orch_it.merge.batch import place_rule_suite
from orch_it.merge.follow import SHORT_SHA
from orch_it.merge.line import run_line
from orch_it.merge.settings import MergeSettings
from orch_it.testruns import bound_check, github, place_rule
from orch_it.testruns.config import TestSettings
from orch_it.testruns.github import WORKFLOW_FILE
from orch_it.testruns.github_place import GithubPlace, checks_on_os
from orch_it.testruns.places import PlaceEnd
from orch_it.testruns.runner import run_tests

OTHER = "owner-x/other-z"
QUEUE_BOUND_SECONDS = bound_check.default_bounds()["queue_minutes"] * 60
SPENT = {"total_minutes_used": 2000, "included_minutes": 2000, "total_paid_minutes_used": 0}
LEFT = {"total_minutes_used": 300, "included_minutes": 2000, "total_paid_minutes_used": 0}
JUNIT = "<testsuites><testsuite name='pytest'><testcase classname='t' name='test_a'/></testsuite></testsuites>"


def now():
    return datetime.now(UTC)


def actions_for(tmp_path):
    (tmp_path / "actions").mkdir(exist_ok=True)
    return FakeActions(tmp_path / "actions", REPO, now(), now=now())


def turn_off(actions, cause):
    """Make GitHub's runs not start for `cause`, a code of `places.RUNS_NOT_STARTING`."""

    if cause == "actions_off":
        actions.enabled = False
    elif cause == "minutes_spent":
        actions.private, actions.billing = True, SPENT
    elif cause == "queue_past_bound":
        actions.hold(OTHER, 20, queued=1, queued_for=QUEUE_BOUND_SECONDS + 60)
    else:
        raise AssertionError(cause)


CAUSES = ["actions_off", "minutes_spent", "queue_past_bound"]


def github_place(store, actions, **settings):
    return GithubPlace(REPO, store, TestSettings(github_tests=True, **settings), gh=actions, oses=(LINUX_OS,))


# --- the place rule leaves GitHub out, and says which cause (decision 6) ------------------------------------

@pytest.mark.parametrize("cause", CAUSES)
def test_each_cause_leaves_the_github_place_out_and_names_itself(tmp_path, cause):
    actions = actions_for(tmp_path)
    turn_off(actions, cause)
    room = github_place(Store.open(tmp_path / "store"), actions).availability(now())
    assert room.on is False and room.cause == cause and room.note


def test_a_public_repo_with_every_minute_spent_on_the_account_still_runs_on_github(tmp_path):
    actions = actions_for(tmp_path)  # public: GitHub's minutes are free there whatever the account has used
    actions.billing = SPENT
    room = github_place(Store.open(tmp_path / "store"), actions).availability(now())
    assert room.on is True and room.cause is None and room.metered is False


def test_a_private_repo_with_minutes_left_runs_on_github_and_pays_for_it(tmp_path):
    actions = actions_for(tmp_path)
    actions.private, actions.billing = True, LEFT
    room = github_place(Store.open(tmp_path / "store"), actions).availability(now())
    assert room.on is True and room.cause is None and room.metered is True


def test_minutes_paid_for_are_not_spent(tmp_path):
    actions = actions_for(tmp_path)
    actions.private = True
    actions.billing = SPENT | {"total_paid_minutes_used": 40}  # past the free ones, on a plan that pays on
    assert github_place(Store.open(tmp_path / "store"), actions).availability(now()).on is True


def test_a_queue_wait_inside_its_bound_leaves_github_on(tmp_path):
    actions = actions_for(tmp_path)
    actions.hold(OTHER, 20, queued=1, queued_for=QUEUE_BOUND_SECONDS - 120)  # `hold` adds a minute: it waited bound - 1 min
    assert github_place(Store.open(tmp_path / "store"), actions).availability(now()).on is True


def test_the_queue_bound_is_the_setting_place_bounds_queue_minutes(tmp_path):
    raw = {"place_bounds": {"queue_minutes": 1}}
    assert TestSettings.from_settings(raw, REPO).queue_bound_minutes == 1
    actions = actions_for(tmp_path)
    actions.hold(OTHER, 20, queued=1, queued_for=120)  # past 1 minute, inside the default bound
    assert 120 + 60 < QUEUE_BOUND_SECONDS
    room = github_place(Store.open(tmp_path / "store"), actions, queue_bound_minutes=1).availability(now())
    assert room.on is False and room.cause == "queue_past_bound"


def seed(store, place, minutes, repo=REPO):
    with store.writer() as writer:
        envelope = {"repo": repo, "ticket": None, "role": "script", "session": None, "parent_session": None,
                    "turn": None, "step": "test_run", "run": f"seed-{place}", "note": None}
        writer.write(envelope | {"kind": "step.started", "at": "2026-10-29T10:00:00+00:00", "detail": {
            "place": place, "selection": "full", "run_os": None, "bound_minutes": 15}})
        writer.write(envelope | {"kind": "step.ended", "at": "2026-10-29T10:01:00+00:00", "detail": {
            "outcome": "passed", "reason": None, "run_minutes": minutes, "start_minutes": 0.0}})


class StubOwn:
    id, os = "own", LINUX_OS

    def run(self, request, scratch):
        report = scratch / "junit.xml"
        report.write_text(JUNIT, "utf-8")
        return PlaceEnd(report, False, False, 0, 0.0, 1.0)


def run_here(tmp_path, actions, store, **kwargs):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return run_tests(store, work_tree=work, repo=REPO, settings=TestSettings(github_tests=True),
                     places={"own": StubOwn(), "github": github_place(store, actions)}, sleep=lambda s: None, **kwargs)


@pytest.mark.parametrize("cause", CAUSES)
def test_a_run_with_github_out_goes_to_own_and_the_decision_carries_the_cause(tmp_path, cause):
    store = Store.open(tmp_path / "store")
    seed(store, "own", 5.0)
    seed(store, "github", 1.0)  # GitHub is measured fastest: only the cause keeps the run off it
    actions = actions_for(tmp_path)
    turn_off(actions, cause)
    done = run_here(tmp_path, actions, store)
    assert done.place == "own" and done.outcome == "passed" and done.github_out == cause
    [(detail,)] = store.query("SELECT detail FROM events WHERE kind = 'step.started' AND step = 'test_run'"
                              " AND run != 'seed-own' AND run != 'seed-github'").rows
    entry = next(p for p in json.loads(detail)["places_offered"] if p["place"] == "github")
    assert entry["dropped"] == "place_off" and entry["note"]


def test_a_run_with_github_on_names_no_cause(tmp_path):
    store = Store.open(tmp_path / "store")
    seed(store, "own", 5.0)
    seed(store, "github", 1.0)
    done = run_here(tmp_path, actions_for(tmp_path), store)
    # the work tree has no commit, so GitHub refuses the run it was chosen for and the run ends at own: no cause is named
    [(first,)] = store.query("SELECT json_extract(detail, '$.place') FROM events WHERE kind = 'step.started'"
                             " AND step = 'test_run' AND run NOT LIKE 'seed-%' ORDER BY id LIMIT 1").rows
    assert first == "github" and done.github_out is None


# --- the line merges on a result of another place, posted under the required names ------------------------

SCAN_CHECK = "scan-of-another-kind"  # a required name tied to no OS (no job of the workflow): a scan of the pull request
SUITE_JOBS = {LINUX_CHECK: LINUX_OS, WINDOWS_CHECK: WINDOWS_OS}  # the test jobs of the repo's `tests` workflow, by OS


def workflow_of(jobs):
    """The text of a `tests` workflow with one job per name of `jobs` (name -> the OS it runs on)."""

    return "name: tests\njobs:\n" + "".join(
        f"  {name}:\n    name: {name}\n    runs-on: {os_name}-latest\n" for name, os_name in jobs.items())


def batch_world(tmp_path, cause, *, tickets=(10, 11, 12, 13), required=(LINUX_CHECK,), jobs=None, workflow=None):
    """The line with the suite at `own` (which runs on the Linux word) and GitHub out for `cause`. `required`: the
    names the repo requires (the setting, and what the base's protection requires on the fake); `jobs`: the test jobs
    of its workflow, name -> OS; `workflow`: its text, instead of one built from `jobs`."""

    world = line_world(tmp_path, tickets=tickets)
    hub, store, prs, ci = world
    hub.required_by_base = {"main": set(required)}
    actions = actions_for(tmp_path)
    turn_off(actions, cause)
    settings = TestSettings(github_tests=True, required_checks=tuple(required))
    workflow = workflow or workflow_of(SUITE_JOBS if jobs is None else jobs)
    place = FixturePlace(hub, set())
    place.posted = []  # the pass at this place runs the suite and posts nothing

    @contextlib.contextmanager
    def checkout(sha):
        tree = tmp_path / "trees" / sha
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "tip.txt").write_text(sha, "utf-8")
        (tree / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (tree / ".github" / "workflows" / WORKFLOW_FILE).write_text(workflow, "utf-8")
        yield tree

    suite = place_rule_suite(store, REPO, settings, checkout,
                             places={"own": place, "github": GithubPlace(REPO, store, settings, gh=actions,
                                                                         oses=(LINUX_OS,))})
    return SimpleNamespace(world=world, suite=suite, place=place, actions=actions, required=tuple(required))


def line(w, tmp_path, **settings):
    hub, store, _, ci = w.world
    return run_line(hub, store, REPO, "main", settings=MergeSettings(**settings), clock=lambda: hub.now,
                    sleep=ci.sleep, root=tmp_path / "locks", holder="test line", suite=w.suite, required=w.required)


@pytest.mark.parametrize("cause", CAUSES)
def test_with_github_out_the_suite_runs_at_own_the_names_are_posted_on_the_sha_and_the_batch_merges(tmp_path, cause):
    w = batch_world(tmp_path, cause)
    hub, store, prs, ci = w.world
    done = line(w, tmp_path)
    order = [prs[n] for n in (10, 11, 12, 13)]
    assert passes(store) == 1
    assert {s.result for s in done.served} == {"merged"} and ci.merged == order and done.reason == "empty"
    tip = hub.branches["main"][-1]
    assert hub.advances == [tip] and w.place.tested == [(tip, "passed")]
    assert {c.name: c.state for c in hub.checks(REPO, tip)} == {LINUX_CHECK: PASSED}
    assert not any("dispatches" in " ".join(c) for c in w.actions.calls)  # nothing was asked of GitHub's runners


@pytest.mark.parametrize("cause", CAUSES)
def test_a_name_of_another_os_stays_unposted_and_holds_the_batch_naming_it(tmp_path, cause):
    # the pass ran the test suite at a Linux place: it covers the Linux job only, not the Windows job (a name tied to no
    # OS, like SCAN_CHECK, is posted with it: tests/test_line_os_free_names.py)
    uncovered = WINDOWS_CHECK
    w = batch_world(tmp_path, cause, tickets=(10,), required=(LINUX_CHECK, uncovered))
    hub, store, prs, ci = w.world
    done = line(w, tmp_path)
    tip = w.place.tested[0][0]
    assert ci.merged == [] and hub.advances == [] and hub.branches["main"] == ["m0"]
    assert {c.name: c.state for c in hub.checks(REPO, tip)} == {LINUX_CHECK: PASSED}
    assert done.notes == (f"required checks not green on {tip[:SHORT_SHA]}: {uncovered}",)
    assert [s.result for s in done.served] == ["bound"] and hub.pull(REPO, prs[10]).state == "open"


def test_every_required_name_a_machine_place_covers_is_posted_and_one_it_does_not_cover_is_not(tmp_path):
    jobs = {LINUX_CHECK: LINUX_OS, WINDOWS_CHECK: LINUX_OS, SCAN_CHECK: WINDOWS_OS}  # two jobs on Linux, one elsewhere
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=(LINUX_CHECK, WINDOWS_CHECK, SCAN_CHECK), jobs=jobs)
    hub, store, prs, ci = w.world
    done = line(w, tmp_path)
    assert {c.name: c.state for c in hub.checks(REPO, w.place.tested[0][0])} == {LINUX_CHECK: PASSED,
                                                                                   WINDOWS_CHECK: PASSED}
    assert ci.merged == [] and SCAN_CHECK in done.notes[0]


def test_the_names_of_a_workflow_that_run_on_an_os_are_the_checks_a_place_of_that_os_covers():
    text = workflow_of(SUITE_JOBS)
    required = (WINDOWS_CHECK, SCAN_CHECK, LINUX_CHECK)
    assert checks_on_os(text, required, WINDOWS_OS) == (WINDOWS_CHECK,)
    assert checks_on_os(text, required, LINUX_OS) == (LINUX_CHECK,)
    assert checks_on_os(text, required, None) == () and checks_on_os("", required, LINUX_OS) == ()


def test_github_on_and_the_pass_posts_no_name_merges_nothing_as_before(tmp_path):
    w = batch_world(tmp_path, "actions_off", required=(LINUX_CHECK, WINDOWS_CHECK))
    w.actions.enabled = True  # Actions is on and the github place is offered, but the suite ran at own
    hub, store, prs, ci = w.world
    w.place.id = "own"
    seed(store, "own", 5.0)
    seed(store, "github", 9.0)  # own measured faster: github is not chosen, names are GitHub's to post
    done = line(w, tmp_path)
    assert ci.merged == [] and hub.advances == [] and done.reason == "next_run"
    assert hub.checks(REPO, hub.branches["main"][-1]) == []


def test_the_line_posts_only_the_names_nobody_posted(tmp_path):
    w = batch_world(tmp_path, "actions_off", tickets=(10,), required=(LINUX_CHECK, WINDOWS_CHECK),
                    jobs={LINUX_CHECK: LINUX_OS, WINDOWS_CHECK: LINUX_OS})  # both jobs run on the place's OS
    hub, store, prs, ci = w.world
    w.place.posted = [LINUX_CHECK]
    w.place.red_names = {LINUX_CHECK: {prs[10]}}  # the pass itself posted this name red
    done = line(w, tmp_path)
    assert ci.merged == [] and hub.advances == [] and [s.result for s in done.served] == ["left"]
    assert {c.name: c.state for c in hub.checks(REPO, w.place.tested[0][0])} == {LINUX_CHECK: FAILED,
                                                                                   WINDOWS_CHECK: PASSED}


def test_a_red_suite_with_github_out_posts_nothing_and_merges_nothing(tmp_path):
    w = batch_world(tmp_path, "actions_off", tickets=(10,))
    hub, store, prs, ci = w.world
    w.place.red = {prs[10]}
    done = line(w, tmp_path)
    assert ci.merged == [] and hub.advances == [] and [s.result for s in done.served] == ["left"]
    assert hub.checks(REPO, w.place.tested[0][0]) == []


@pytest.mark.parametrize("protected", [True, False])
def test_a_red_name_merges_nothing_on_a_protected_and_on_an_unprotected_base(tmp_path, protected):
    w = batch_world(tmp_path, "minutes_spent", tickets=(10,), required=(LINUX_CHECK, WINDOWS_CHECK))
    hub, store, prs, ci = w.world
    if not protected:
        hub.required_by_base = {}  # nothing on GitHub holds the required checks: only the line does
    w.place.posted = [WINDOWS_CHECK]
    w.place.red_names = {WINDOWS_CHECK: {prs[10]}}
    line(w, tmp_path)
    assert ci.merged == [] and hub.advances == [] and hub.branches["main"] == ["m0"]
    assert hub.pull(REPO, prs[10]).state == "open"


@pytest.mark.parametrize("protected", [True, False])
def test_every_name_green_merges_on_a_protected_and_on_an_unprotected_base(tmp_path, protected):
    w = batch_world(tmp_path, "actions_off", tickets=(10,))
    hub, store, prs, ci = w.world
    if not protected:
        hub.required_by_base = {}
    line(w, tmp_path)
    assert ci.merged == [prs[10]]


# --- guards status: who holds the required checks (decision 7) -----------------------------------------------------

def protection(names=(LINUX_CHECK, WINDOWS_CHECK)):
    return {"required_status_checks": {"strict": True, "contexts": list(names)},
            "enforce_admins": {"enabled": True}, "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False}}


def detail_of(report, name):
    return next(fact.detail for fact in report.facts if fact.name == name)


def test_guards_status_says_the_line_holds_the_required_checks_where_the_base_has_no_protection(tmp_path):
    store = Store.open(tmp_path / "store")

    def reader(repo):
        raise github.GithubError("gh_failed", "api: Branch not protected (HTTP 404)")

    report = guards_status.status(store, "o/n", tmp_path, protection=reader)
    assert report.required_holder == "line"
    assert "held by the merge line, not by GitHub" in detail_of(report, "required_checks")


def test_guards_status_says_github_holds_the_required_checks_where_the_base_is_protected(tmp_path):
    store = Store.open(tmp_path / "store")
    report = guards_status.status(store, "o/n", tmp_path, protection=lambda repo: protection())
    assert report.required_holder == "github"
    assert "held by GitHub's branch protection" in detail_of(report, "required_checks")
    assert "merge line" not in detail_of(report, "required_checks")


def test_guards_status_says_the_line_holds_the_required_checks_where_the_plan_has_no_protection_to_give(tmp_path):
    store = Store.open(tmp_path / "store")

    def reader(repo):  # a private repo on a plan without protection: GitHub answers 403 and asks to upgrade
        raise github.GithubError("gh_failed", "gh: Upgrade to GitHub Pro or make this repository public to enable"
                                              " this feature. (HTTP 403)")

    report = guards_status.status(store, "o/n", tmp_path, protection=reader)
    assert report.required_holder == "line"
    assert "held by the merge line, not by GitHub" in detail_of(report, "required_checks")


def test_guards_status_claims_no_holder_when_the_answer_is_a_403_that_is_no_plan_limit(tmp_path):
    store = Store.open(tmp_path / "store")

    def reader(repo):
        raise github.GithubError("gh_failed", "gh: Resource not accessible by personal access token (HTTP 403)")

    assert guards_status.status(store, "o/n", tmp_path, protection=reader).required_holder is None


def test_guards_status_says_github_holds_the_required_checks_only_when_it_requires_every_required_name(tmp_path):
    store = Store.open(tmp_path / "store")
    held = {}
    for label, names in {"all": (LINUX_CHECK, WINDOWS_CHECK), "all_and_more": (LINUX_CHECK, WINDOWS_CHECK, SCAN_CHECK),
                         "subset": (LINUX_CHECK,), "none": ()}.items():
        report = guards_status.status(store, "o/n", tmp_path, protection=lambda repo, names=names: protection(names))
        held[label] = report.required_holder
        if report.required_holder == "line":
            assert "held by the merge line, not by GitHub" in detail_of(report, "required_checks")
    assert held == {"all": "github", "all_and_more": "github", "subset": "line", "none": "line"}


def test_guards_status_claims_no_holder_when_the_protection_cannot_be_read(tmp_path):
    store = Store.open(tmp_path / "store")

    def reader(repo):
        raise github.GithubError("gh_timeout", "api")

    assert guards_status.status(store, "o/n", tmp_path, protection=reader).required_holder is None


# --- a private repo: GitHub's minutes are a cost, the machine places go first (decision 7) -------------------

def offers(*, own_on=True, github_metered=True):
    return [place_rule.Offer("own", (LINUX_OS,), own_on, 1),
            place_rule.Offer("github", (LINUX_OS,), True, 20, metered=github_metered)]


MEASURED = {"own": place_rule.Measured(runs=(5.0,)), "github": place_rule.Measured(runs=(1.0,))}


def test_for_a_private_repo_github_is_ranked_after_the_machine_places_though_it_is_faster():
    decision = place_rule.choose(LINUX_OS, offers(), {}, MEASURED)
    assert decision.chosen == "own" and decision.reason == place_rule.EARLIEST
    github_entry = next(o for o in decision.offered if o.place == "github")
    assert github_entry.dropped is None and github_entry.expected_minutes == 1.0  # still offered, still measured


def test_for_a_public_repo_the_faster_github_is_chosen():
    assert place_rule.choose(LINUX_OS, offers(github_metered=False), {}, MEASURED).chosen == "github"


def test_for_a_private_repo_github_is_chosen_when_no_machine_place_is_left():
    assert place_rule.choose(LINUX_OS, offers(own_on=False), {}, MEASURED).chosen == "github"
    assert place_rule.choose(LINUX_OS, offers(), {"own": 1}, MEASURED).chosen == "github"  # own is at capacity


def test_for_a_private_repo_with_nothing_measured_the_machine_place_still_goes_first():
    assert place_rule.choose(LINUX_OS, offers(), {}, {}).chosen == "own"
    assert place_rule.choose(LINUX_OS, offers(own_on=False), {}, {}).chosen == "github"


def test_a_run_for_a_private_repo_goes_to_own_though_github_is_measured_fastest(tmp_path):
    store = Store.open(tmp_path / "store")
    seed(store, "own", 5.0)
    seed(store, "github", 1.0)
    actions = actions_for(tmp_path)
    actions.private, actions.billing = True, LEFT
    done = run_here(tmp_path, actions, store)
    assert done.place == "own" and done.github_out is None
    [(detail,)] = store.query("SELECT detail FROM events WHERE kind = 'step.started' AND step = 'test_run'"
                              " AND run != 'seed-own' AND run != 'seed-github'").rows
    entry = next(p for p in json.loads(detail)["places_offered"] if p["place"] == "github")
    assert entry["dropped"] is None and entry["expected_minutes"] == 1.0
