"""GitHub minutes used and left (#406 decision 4, rows 4 and 9, as fixtures): the point `github.minutes`
{used, allowance, left, source} per GitHub pace, and the place rule's refusal of `github` with `out_of_minutes`
for a private repo whose `used` plus the part's median billed minutes passes the allowance.

Seams: `MinutesReader.poll` (the one reader of `used`), `place_rule.choose` and `place_rule.read_facts` (the refusal and
the medians it reads from the store), `GithubPlace.availability` (the budget a private repo's offer carries)."""

import json
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from fake_github import FakeActions

from orch_it.events.store import Store
from orch_it.testruns import place_rule
from orch_it.testruns.config import TestSettings
from orch_it.testruns.github import GithubError
from orch_it.testruns.github_minutes import MinutesReader, charged, next_month_start
from orch_it.testruns.github_place import GithubPlace
from orch_it.testruns.place_rule import Budget, Measured, Offer, choose

REPO = "acme-x/widgets-y"  # the repo under test: private
PUBLIC = "acme-x/open-z"  # another repo of the account: public
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
GH = Offer("github", ("linux", "windows"), on=True, capacity=20, metered=True)
OWN = Offer("own", ("linux",), on=True, capacity=1)


def budget(used, allowance=2000, resets=date(2026, 12, 1)):
    return Budget(used, allowance, resets)


def github_offer(used, allowance=2000):
    return Offer("github", ("linux", "windows"), on=True, capacity=20, metered=True, budget=budget(used, allowance))


FAST_GITHUB = {"own": Measured((), (10.0,)), "github": Measured((), (1.0,), billed=(70.0,))}


# --- row 4: the place rule refuses `github` over budget ---------------------------------------------------------

def test_used_1940_and_a_median_of_70_refuse_github_with_out_of_minutes_naming_the_numbers_and_the_first_of_next_month():
    decision = choose("any", [OWN, github_offer(1940)], {}, FAST_GITHUB)

    assert (decision.chosen, decision.reason) == ("own", "earliest_finish")  # 1940 + 70 > 2000: the next place
    github = next(o for o in decision.offered if o.place == "github")
    assert github.dropped == "out_of_minutes"
    for fact in ("1940", "70", "2000", "01.12"):
        assert fact in github.note


def dropped(decision):
    """Why the rule left `github` out, None when it is a candidate (a metered place is still chosen only when no free place
    is left, so a candidate here is `not dropped`, not `chosen`)."""

    return next(o for o in decision.offered if o.place == "github").dropped


def test_used_1920_and_a_median_of_70_leave_github_allowed_and_the_exact_allowance_is_allowed():
    assert dropped(choose("any", [OWN, github_offer(1920)], {}, FAST_GITHUB)) is None  # 1920 + 70 = 1990
    assert dropped(choose("any", [OWN, github_offer(1930)], {}, FAST_GITHUB)) is None  # 1930 + 70 = 2000 does not pass 2000
    assert choose("any", [github_offer(1930)], {}, FAST_GITHUB).chosen == "github"  # and with no free place it is chosen


def test_the_median_is_the_parts_then_every_github_runs_then_0():
    both = {"own": Measured((), (10.0,)), "github": Measured((), (1.0,), billed=(10.0,), billed_all=(500.0,))}
    assert dropped(choose("any", [OWN, github_offer(1995)], {}, both)) == "out_of_minutes"  # the part's 10 passes 2000
    smaller = both | {"github": Measured((), (1.0,), billed=(5.0,), billed_all=(500.0,))}
    assert dropped(choose("any", [OWN, github_offer(1995)], {}, smaller)) is None  # 1995 + 5 = 2000
    only_all = {"own": Measured((), (10.0,)), "github": Measured((), (1.0,), billed_all=(30.0, 50.0))}
    assert dropped(choose("any", [OWN, github_offer(1961)], {}, only_all)) == "out_of_minutes"  # none of the part: 1961 + 40
    assert dropped(choose("any", [OWN, github_offer(1960)], {}, only_all)) is None  # 1960 + 40 = 2000
    none = {"own": Measured((), (10.0,)), "github": Measured((), (1.0,))}
    assert dropped(choose("any", [OWN, github_offer(2000)], {}, none)) is None  # no run at all: median 0
    assert dropped(choose("any", [OWN, github_offer(2001)], {}, none)) == "out_of_minutes"


def test_a_repo_with_no_budget_a_public_one_is_never_refused_on_minutes():
    public = Offer("github", ("linux", "windows"), on=True, capacity=20)
    assert choose("any", [OWN, public], {}, FAST_GITHUB).chosen == "github"


def test_the_only_place_left_over_budget_leaves_no_choice_and_the_drop_is_out_of_minutes():
    decision = choose("any", [github_offer(1999)], {}, FAST_GITHUB)
    assert decision.chosen is None and decision.offered[0].dropped == "out_of_minutes"
    assert place_rule.OUT_OF_MINUTES == "out_of_minutes" and "out_of_minutes" in place_rule.DROP_CODES


# --- the store fixtures ------------------------------------------------------------------------------------------

def seed(store, run, ended, billed, *, os_name="linux", repo=REPO, place="github", selection="full",
         outcome="passed", started=None):
    """One ended `test_run` of `place` that ended at `ended` (an ISO stamp, any offset)."""

    envelope = {"repo": repo, "ticket": None, "role": "github", "session": None, "parent_session": None,
                "turn": None, "step": "test_run", "run": run, "note": None}
    with store.writer() as writer:
        writer.write(envelope | {"kind": "step.started", "at": started or ended, "detail": {
            "place": place, "selection": selection, "run_os": os_name, "bound_minutes": 15}})
        writer.write(envelope | {"kind": "step.ended", "at": ended, "detail": {
            "outcome": outcome, "reason": None, "run_minutes": 4.0, "start_minutes": 0.5, "billed_minutes": billed}})


def zone(hours):
    return timezone(timedelta(hours=hours))


def points(store):
    return store.query("SELECT used, allowance, \"left\", source FROM events WHERE kind = 'github.minutes'"
                       " ORDER BY at, id").rows


class Billing:
    """GitHub as `gh` sees it for the minutes: the billing of the account (or unreadable) and the `private` flag of each
    repo; everything else answers like `FakeActions`."""

    def __init__(self, actions, *, readable=True, private=(REPO,)):
        self.actions, self.readable, self.private = actions, readable, set(private)
        self.billing_calls = 0

    def __call__(self, args):
        text = " ".join(args)
        if "settings/billing" in text:
            self.billing_calls += 1
            if not self.readable:
                raise GithubError("gh_failed", "403 Resource not accessible")
            return self.actions(args)
        endpoint = next((a for a in args[1:] if a.startswith("repos/")), "")
        if endpoint.count("/") == 2:  # `repos/<owner>/<name>`: the repo itself
            return json.dumps({"full_name": endpoint[6:], "private": endpoint[6:] in self.private})
        return self.actions(args)


def world(tmp_path, *, readable=True, used=None):
    store = Store.open(tmp_path / "store")
    actions = FakeActions(tmp_path, REPO, NOW, now=NOW)
    actions.private = True
    if used is not None:
        actions.billing = {"total_minutes_used": used, "included_minutes": 2000, "total_paid_minutes_used": 0}
    gh = Billing(actions, readable=readable)
    return store, actions, gh


def reader(store, gh, **settings):
    return MinutesReader(REPO, store, TestSettings(**settings), gh)


def test_github_billing_readable_gives_used_from_github_and_one_point(tmp_path):
    store, _, gh = world(tmp_path, used=1940)
    seed(store, "r1", "2026-11-02T10:00:00+00:00", 5)  # the store is not asked when GitHub's billing reads

    minutes = reader(store, gh).poll(NOW)

    assert (minutes.used, minutes.allowance, minutes.left, minutes.source) == (1940, 2000, 60, "github")
    assert points(store) == [(1940, 2000, 60, "github")]


def test_billing_unreadable_sums_the_stores_ended_runs_with_a_windows_job_counted_twice(tmp_path):
    store, _, gh = world(tmp_path, readable=False)
    seed(store, "linux", "2026-11-03T10:00:00+00:00", 10)
    seed(store, "windows", "2026-11-03T11:00:00+00:00", 30, os_name="windows")
    seed(store, "own", "2026-11-03T12:00:00+00:00", 99, place="own")  # not a github run

    minutes = reader(store, gh).poll(NOW)

    assert (minutes.used, minutes.source) == (10 + 2 * 30, "store")  # the Windows job's 30 minutes count 60, once
    assert points(store) == [(70, 2000, 1930, "store")]


def test_the_multiplier_is_applied_once_to_a_stored_billed_minutes():
    assert (charged(30, "windows"), charged(30, "linux"), charged(30, None), charged(3, "macos")) == (60, 30, 30, 30)


def test_a_public_repos_runs_add_nothing_to_the_store_sum(tmp_path):
    store, _, gh = world(tmp_path, readable=False)
    seed(store, "private", "2026-11-03T10:00:00+00:00", 10)
    seed(store, "public", "2026-11-03T11:00:00+00:00", 500, repo=PUBLIC)

    assert reader(store, gh).poll(NOW).used == 10


def test_the_sum_is_the_accounts_not_another_owners(tmp_path):
    store, _, gh = world(tmp_path, readable=False)
    seed(store, "mine", "2026-11-03T10:00:00+00:00", 10)
    seed(store, "foreign", "2026-11-03T10:00:00+00:00", 700, repo="other-owner/thing")
    gh.private.add("other-owner/thing")

    assert reader(store, gh).poll(NOW).used == 10


# --- row 9: the month is the UTC month -----------------------------------------------------------------------------

@pytest.mark.parametrize("pc", [zone(3), zone(-5)], ids=["UTC+3", "UTC-5"])
def test_runs_ended_31_10_2350_and_01_11_0010_utc_read_at_01_11_0020_utc_count_only_the_second(tmp_path, pc):
    store, _, gh = world(tmp_path, readable=False)
    seed(store, "october", datetime(2026, 10, 31, 23, 50, tzinfo=UTC).astimezone(pc).isoformat(), 40)
    seed(store, "november", datetime(2026, 11, 1, 0, 10, tzinfo=UTC).astimezone(pc).isoformat(), 25)
    read_at = datetime(2026, 11, 1, 0, 20, tzinfo=UTC).astimezone(pc)  # the PC's clock, in its own zone

    minutes = reader(store, gh).poll(read_at)

    assert (minutes.used, minutes.source) == (25, "store")


def test_used_starts_at_0_on_the_first_at_00_00_utc_and_a_run_counts_in_the_month_it_ended(tmp_path):
    store, _, gh = world(tmp_path, readable=False)
    seed(store, "started-in-october", "2026-11-01T00:00:00+00:00", 8, started="2026-10-31T23:58:00+00:00")
    seed(store, "last-second", "2026-10-31T23:59:59+00:00", 40)

    assert reader(store, gh).poll(datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)).used == 40
    assert reader(store, gh).poll(datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)).used == 8  # the run that ended 00:00 counts
    assert reader(store, gh).poll(datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC)).used == 0  # December starts at 0


def test_a_reading_of_one_month_does_not_stand_in_the_next(tmp_path):
    store, _, gh = world(tmp_path, readable=False)
    seed(store, "november", "2026-11-30T23:50:00+00:00", 40)
    both = reader(store, gh)
    assert both.poll(datetime(2026, 11, 30, 23, 55, tzinfo=UTC)).used == 40
    assert both.poll(datetime(2026, 12, 1, 0, 0, 5, tzinfo=UTC)).used == 0  # 10 s later: the month changed, so it is read


def test_the_first_of_next_month_in_utc_and_the_reason_names_it_as_dd_mm():
    assert next_month_start(datetime(2026, 11, 30, 23, 59, tzinfo=UTC)) == date(2026, 12, 1)
    assert next_month_start(datetime(2026, 12, 15, 9, 0, tzinfo=UTC)) == date(2027, 1, 1)
    assert next_month_start(datetime(2026, 12, 31, 20, 0, tzinfo=zone(-5))) == date(2027, 2, 1)  # 01:00 UTC on 1 Jan: January
    assert next_month_start(datetime(2026, 11, 30, 23, 0, tzinfo=zone(-5))) == date(2027, 1, 1)  # 04:00 UTC on 1 Dec
    assert next_month_start(datetime(2026, 11, 1, 2, 0, tzinfo=zone(3))) == date(2026, 11, 1)  # 23:00 UTC on 31 Oct
    refused = choose("any", [OWN, Offer("github", ("linux",), True, 20, True, Budget(1990, 2000, date(2026, 12, 1)))],
                     {}, FAST_GITHUB)
    assert "01.12" in refused.offered[1].note


# --- the pace and the point ------------------------------------------------------------------------------------------

def test_one_read_per_github_poll_seconds_and_the_billing_is_asked_once(tmp_path):
    store, _, gh = world(tmp_path, used=100)
    minutes = reader(store, gh, github_poll_seconds=30)
    assert minutes.poll(NOW) is not None and minutes.poll(NOW + timedelta(seconds=10)) is None
    assert minutes.poll(NOW + timedelta(seconds=31)) is not None
    assert gh.billing_calls == 2 and len(points(store)) == 2


def test_under_the_reserve_the_next_read_waits_longer_as_the_busy_reader_does(tmp_path):
    store, actions, gh = world(tmp_path, used=100)
    minutes = reader(store, gh)
    assert minutes.poll(NOW) is not None
    actions.core = (900, 5000)  # under the 1000 reserve: the 30 s base doubles
    assert minutes.poll(NOW + timedelta(seconds=31)) is None
    assert minutes.poll(NOW + timedelta(seconds=61)) is not None
    actions.core = (0, 5000)
    assert minutes.poll(NOW + timedelta(hours=2)) is None


def test_the_allowance_is_the_setting_and_left_never_goes_below_0(tmp_path):
    store, _, gh = world(tmp_path, used=2500)
    minutes = reader(store, gh, github_minutes_allowance=3000).poll(NOW)
    assert (minutes.used, minutes.allowance, minutes.left) == (2500, 3000, 500)
    over = reader(store, gh).poll(NOW)
    assert (over.allowance, over.left) == (2000, 0)


# --- the store's facts for the rule -----------------------------------------------------------------------------------

def facts(store, selection="full", need_os="any"):
    return place_rule.read_facts(store, repo=REPO, selection=selection, need_os=need_os, now=NOW)[1]


def test_read_facts_gives_the_parts_billed_medians_with_the_windows_multiplier_and_every_github_runs_beside_them(tmp_path):
    store = Store.open(tmp_path / "store")
    seed(store, "a", "2026-11-10T10:00:00+00:00", 30, os_name="windows")  # charged 60
    seed(store, "b", "2026-11-10T11:00:00+00:00", 10)
    seed(store, "c", "2026-11-10T12:00:00+00:00", 90, selection="nightly")  # another part
    seed(store, "d", "2026-11-10T13:00:00+00:00", 7, place="own")
    github = facts(store)["github"]
    assert sorted(github.billed) == [10, 60]
    assert sorted(github.billed_all) == [10, 60, 90]
    assert facts(store)["own"].billed == ()


# --- a private repo's offer carries the budget --------------------------------------------------------------------------

def place_of(store, gh, **settings):
    return GithubPlace(REPO, store, TestSettings(github_tests=True, **settings), gh=gh, oses=("linux",))


def test_availability_of_a_private_repo_carries_the_budget_and_of_a_public_one_none(tmp_path):
    store, actions, gh = world(tmp_path, used=1940)
    room = place_of(store, gh).availability(NOW)
    assert room.on is True and room.metered is True
    assert room.budget == Budget(1940, 2000, date(2026, 12, 1))

    actions.private = False
    gh.private.clear()
    public = place_of(Store.open(tmp_path / "store2"), gh).availability(NOW)
    assert public.on is True and public.metered is False and public.budget is None


def test_a_billing_that_is_not_a_whole_reading_is_unreadable_and_the_store_answers(tmp_path):
    store, actions, gh = world(tmp_path)
    actions.billing = {"total_minutes_used": "many"}
    seed(store, "r", "2026-11-03T10:00:00+00:00", 12)
    minutes = reader(store, gh).poll(NOW)
    assert (minutes.used, minutes.source) == (12, "store")
