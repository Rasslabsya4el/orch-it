"""The named queries (#393 decisions 8 and 9; rows 6, 7 (session-cost half), 10): ticket cost per
role, session cost with subagents under their parent, the % of the weekly limit, the limit rate."""

import shutil

import pytest

from orch_it import settings
from orch_it.events import queries
from orch_it.events.store import Store

REPO = "acme-x/widgets-y"
OTHER = "acme-x/other"
DAY = "2026-10-25"


def at(clock):
    return f"{DAY}T{clock}Z"


def envelope(**over):
    base = {
        "kind": "value.measured", "repo": REPO, "ticket": None,
        "role": "script", "session": None, "parent_session": None, "turn": None,
        "step": None, "run": None, "note": None, "detail": {},
    }
    base.update(over)
    return base


def turn(clock, usd, *, ticket=None, role="worker", model="claude-opus-5-5", repo=REPO, held=None,
         session=None, parent=None, tokens=(0, 0, 0, 0), **over):
    detail = {"model": model, "usd": usd, "input_tokens": tokens[0], "output_tokens": tokens[1],
              "cache_read_tokens": tokens[2], "cache_write_tokens": tokens[3]}
    if held is not None:
        detail["tickets_held"] = held
    return envelope(kind="turn.used", at=at(clock), repo=repo, ticket=ticket, role=role,
                    session=session, parent_session=parent, detail=detail, **over)


def step(side, clock, step_id, session, *, parent=None, role="worker", **detail):
    if side == "ended":
        detail = {"outcome": "passed"} | detail
    return envelope(kind=f"step.{side}", at=at(clock), step=step_id, session=session,
                    parent_session=parent, role=role, detail=detail)


def measured(clock, pct):
    return envelope(at=at(clock), role="owner", detail={
        "name": "weekly_limit_pct", "value": pct, "unit": "%", "source": "usage_card"})


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


def store_with(tmp_path, events, name="store", **fixed):
    store = Store.open(tmp_path / name)
    with store.writer() as writer:
        for event in events:
            writer.write(event | fixed)
    return store


def set_rate(usd_per_pct=16.5):
    settings.save("weekly_limit_usd_per_pct", {
        "usd_per_pct": usd_per_pct, "plan": "max-20x", "measured": "2026-10-24"})


# --- % of the weekly limit (decision 9, row 10) ---------------------------------------------

def test_pct_week_is_the_dollars_over_the_dollars_per_percent():
    assert queries.pct_week(2.92, "claude-opus-5-5", 16.5) == "0.18 %"


def test_pct_week_without_the_setting_says_not_set_never_a_guess():
    assert queries.pct_week(2.92, "claude-opus-5-5", None) == "not set"


def test_pct_week_of_a_model_that_is_not_claude_is_not_applicable():
    assert queries.pct_week(2.92, "gpt-6", 16.5) == "n/a"
    assert queries.pct_week(2.92, "gpt-6", None) == "n/a"


def test_pct_week_of_a_turn_without_a_price_says_price_missing():
    assert queries.pct_week(None, "claude-opus-5-5", 16.5) == "price_missing"


def test_the_weekly_rate_is_the_setting_or_none():
    assert queries.weekly_rate() is None
    set_rate(16.5)
    assert queries.weekly_rate() == {"usd_per_pct": 16.5, "plan": "max-20x", "measured": "2026-10-24"}


def test_a_malformed_weekly_rate_setting_raises_with_a_code():
    settings.save("weekly_limit_usd_per_pct", "16.5")
    with pytest.raises(settings.SettingsError) as caught:
        queries.weekly_rate()
    assert caught.value.code == "bad_setting"


# --- ticket-cost (row 6) --------------------------------------------------------------------

def ticket_fixture(tmp_path):
    return store_with(tmp_path, [
        envelope(kind="ticket.edited", at=at("10:00:00"), ticket=393, role="orch", detail={
            "field": "labels", "before": ["bug"], "after": ["bug", "ready-for-agent"]}),
        envelope(kind="ticket.closed", at=at("12:00:00"), ticket=393, role="github", detail={}),
        turn("10:30:00", 1.20, ticket=393, role="worker"),
        turn("11:00:00", 0.90, role="orch", held=[393, 394]),
        # distractors
        turn("09:00:00", 5.0, ticket=393, role="worker"),                 # before ready
        turn("11:30:00", 2.0, ticket=394, role="worker"),                 # another ticket
        turn("10:45:00", 7.0, ticket=393, role="worker", repo=OTHER),     # another repo
        turn("12:30:00", 9.0, ticket=393, role="worker"),                 # after close
    ])


def test_ticket_cost_splits_a_shared_turn_and_marks_it_shared(tmp_path):
    result = queries.ticket_cost(ticket_fixture(tmp_path), REPO, 393)
    assert result.columns[:3] == ["role", "how", "usd"]
    assert [row[:3] for row in result.rows] == [("worker", "direct", 1.2), ("orch", "shared", 0.45)]


def test_ticket_cost_shows_the_share_of_the_weekly_limit_per_row(tmp_path):
    set_rate(16.5)
    result = queries.ticket_cost(ticket_fixture(tmp_path), REPO, 393)
    assert result.columns[3] == "% week"
    assert [row[3] for row in result.rows] == ["0.07 %", "0.03 %"]


def test_ticket_cost_share_is_not_set_while_the_setting_is_unset(tmp_path):
    result = queries.ticket_cost(ticket_fixture(tmp_path), REPO, 393)
    assert [row[3] for row in result.rows] == ["not set", "not set"]


def test_ticket_cost_share_counts_only_the_claude_part_of_a_row(tmp_path):
    set_rate(16.5)
    store = store_with(tmp_path, [
        envelope(kind="ticket.edited", at=at("10:00:00"), ticket=7, role="orch", detail={
            "field": "labels", "before": [], "after": ["ready-for-agent"]}),
        envelope(kind="ticket.closed", at=at("12:00:00"), ticket=7, role="github"),
        turn("10:30:00", 1.65, ticket=7, role="worker", model="claude-opus-5-5"),
        turn("10:31:00", 1.00, ticket=7, role="worker", model="gpt-6"),
        turn("10:32:00", 3.00, ticket=7, role="reviewer-spec", model="gpt-6"),
    ])
    result = queries.ticket_cost(store, REPO, 7)
    assert [row for row in result.rows] == [
        ("reviewer-spec", "direct", 3.0, "n/a"), ("worker", "direct", 2.65, "0.10 %")]


def test_ticket_cost_of_a_turn_without_a_price_is_price_missing(tmp_path):
    set_rate(16.5)
    store = store_with(tmp_path, [
        envelope(kind="ticket.edited", at=at("10:00:00"), ticket=7, role="orch", detail={
            "field": "labels", "before": [], "after": ["ready-for-agent"]}),
        envelope(kind="ticket.closed", at=at("12:00:00"), ticket=7, role="github"),
        turn("10:30:00", None, ticket=7, role="worker", model="claude-future-9"),
    ])
    [row] = queries.ticket_cost(store, REPO, 7).rows
    assert row == ("worker", "direct", None, "price_missing")


def test_the_ticket_cost_sql_file_is_the_event_list_s_query():
    sql = queries.load_sql("ticket_cost")
    assert sql.startswith("-- t_ready: the edit that put ready-for-agent on ticket N")
    assert sql.rstrip().endswith("GROUP BY role, how ORDER BY usd DESC;")


# --- session-cost (decision 8; row 7, the session-cost half; row 10) -----------------------------

SESSION_COLUMNS = ["session", "machine", "model", "turns", "input", "output", "cache_read",
                   "cache_write", "usd", "% week", "minutes"]


def parent_with_subagent_fixture(tmp_path):
    return store_with(tmp_path, [
        step("started", "10:00:00", "session", "s-parent"),
        turn("10:01:00", 1.00, session="s-parent", tokens=(100, 50, 1000, 200)),
        turn("10:02:00", 1.50, session="s-parent", tokens=(10, 5, 100, 20)),
        turn("10:03:00", 0.50, session="s-parent", model="gpt-6", tokens=(1, 1, 1, 1)),
        step("started", "10:05:00", "subagent", "s-sub", parent="s-parent"),
        turn("10:06:00", 0.40, session="s-sub", parent="s-parent", tokens=(20, 10, 0, 0)),
        step("ended", "10:08:30", "subagent", "s-sub", parent="s-parent"),
        step("ended", "10:22:00", "session", "s-parent"),
        step("started", "11:00:00", "session", "s-other"),
        turn("11:01:00", 9.00, session="s-other", repo=OTHER),
    ], machine="machine-a")


def test_session_cost_lists_subagents_under_their_parent_with_totals(tmp_path):
    set_rate(16.5)
    store = parent_with_subagent_fixture(tmp_path)
    result = queries.session_cost(store, session="s-parent")
    assert result.columns == SESSION_COLUMNS
    assert result.rows == [
        ("s-parent", "machine-a", "claude-opus-5-5", 2, 110, 55, 1100, 220, "2.50", "0.15 %", "22.0"),
        ("s-parent", "machine-a", "gpt-6", 1, 1, 1, 1, 1, "0.50", "n/a", ""),
        ("  s-sub", "machine-a", "claude-opus-5-5", 1, 20, 10, 0, 0, "0.40", "0.02 %", "3.5"),
        ("total s-parent", "", "", 4, 131, 66, 1101, 221, "3.40", "0.18 %", "25.5"),
        ("total all", "", "", 4, 131, 66, 1101, 221, "3.40", "0.18 %", "25.5"),
    ]


def test_session_cost_without_a_filter_lists_every_top_level_session_and_a_grand_total(tmp_path):
    store = parent_with_subagent_fixture(tmp_path)
    rows = queries.session_cost(store).rows
    assert [row[0] for row in rows] == [
        "s-parent", "s-parent", "  s-sub", "total s-parent", "s-other", "total s-other", "total all"]
    assert rows[-1] == ("total all", "", "", 5, 131, 66, 1101, 221, "12.40", "not set", "26.5")


def test_session_cost_filters_by_repo(tmp_path):
    store = parent_with_subagent_fixture(tmp_path)
    rows = queries.session_cost(store, repo=OTHER).rows
    assert [row[0] for row in rows] == ["s-other", "total s-other", "total all"]
    assert rows[0][8] == "9.00"


def test_session_cost_filters_by_time_inclusive_at_both_ends(tmp_path):
    store = store_with(tmp_path, [
        turn("09:00:00", 1.0, session="s1"),
        turn("10:00:00", 2.0, session="s1"),
        turn("11:00:00", 4.0, session="s1"),
    ])
    rows = queries.session_cost(store, since="2026-10-25T09:30:00Z", until="2026-10-25T12:00:00+02:00").rows
    assert rows[0][3] == 1 and rows[0][8] == "2.00"
    both = queries.session_cost(store, since="2026-10-25T10:00:00Z", until="2026-10-25T11:00:00Z").rows
    assert both[0][3] == 2 and both[0][8] == "6.00"


def test_session_cost_with_the_setting_unset_says_not_set_and_a_non_claude_model_n_a(tmp_path):
    store = store_with(tmp_path, [
        turn("10:00:00", 2.92, session="s1"),
        turn("10:01:00", 1.0, session="s1", model="gpt-6"),
    ])
    rows = queries.session_cost(store).rows
    assert [(row[2], row[9]) for row in rows[:2]] == [("claude-opus-5-5", "not set"), ("gpt-6", "n/a")]
    set_rate(16.5)
    assert queries.session_cost(store).rows[0][9] == "0.18 %"


def test_session_cost_leaves_a_turn_without_a_price_out_of_the_sums_and_says_so(tmp_path):
    set_rate(16.5)
    store = store_with(tmp_path, [
        turn("10:00:00", 1.65, session="s1"),
        turn("10:01:00", None, session="s1", model="claude-future-9"),
        turn("10:02:00", 1.0, session="s1", model="claude-future-9"),
    ])
    rows = queries.session_cost(store).rows
    assert [(row[2], row[8], row[9]) for row in rows[:2]] == [
        ("claude-future-9", "1.00 (1 price_missing left out)", "0.06 %"),
        ("claude-opus-5-5", "1.65", "0.10 %")]
    assert rows[2][8:10] == ("2.65 (1 price_missing left out)", "0.16 %")
    only_missing = store_with(
        tmp_path, [turn("10:00:00", None, session="s2", model="claude-future-9")], name="b")
    [row, total, _] = queries.session_cost(only_missing).rows
    assert row[8:10] == ("price_missing", "price_missing")
    assert total[8] == "0.00 (1 price_missing left out)"


def test_session_minutes_come_from_the_step_pair_else_the_last_event_else_the_first_event(tmp_path):
    store = store_with(tmp_path, [
        step("started", "10:00:00", "session", "paired"), step("ended", "10:22:00", "session", "paired"),
        turn("10:40:00", 1.0, session="paired"),
        step("started", "10:00:00", "session", "open"), turn("10:10:30", 1.0, session="open"),
        turn("09:00:00", 1.0, session="bare"), turn("09:30:00", 1.0, session="bare"),
    ])
    minutes = {row[0]: row[10] for row in queries.session_cost(store).rows if row[2]}
    assert minutes == {"bare": "30.0", "open": "10.5", "paired": "22.0"}


def test_a_filter_by_a_subagent_id_lists_just_it_under_its_parent(tmp_path):
    store = parent_with_subagent_fixture(tmp_path)
    rows = queries.session_cost(store, session="s-sub").rows
    assert [row[0] for row in rows] == ["  s-sub", "total s-parent", "total all"]


def test_session_cost_reads_both_machines_after_a_copy_and_counts_a_shared_event_once(tmp_path):
    a = Store.open(tmp_path / "a")
    b = Store.open(tmp_path / "b")
    shared = turn("08:30:00", 1.0, session="sa") | {"id": "h-shared", "machine": "machine-a"}
    with a.writer() as wa, b.writer() as wb:
        wa.write(turn("08:00:00", 2.0, session="sa") | {"machine": "machine-a"})
        wa.write(dict(shared))
        wb.write(turn("09:00:00", 4.0, session="sb") | {"machine": "machine-b"})
        wb.write(dict(shared))
    for day in (tmp_path / "b").iterdir():
        if day.is_dir():
            shutil.copytree(day, tmp_path / "a" / day.name, dirs_exist_ok=True)
    rows = queries.session_cost(a).rows
    assert [(row[0], row[1], row[3], row[8]) for row in rows] == [
        ("sa", "machine-a", 2, "3.00"), ("total sa", "", 2, "3.00"),
        ("sb", "machine-b", 1, "4.00"), ("total sb", "", 1, "4.00"),
        ("total all", "", 3, "7.00")]


# --- limit-rate (decision 9) ----------------------------------------------------------------------

def test_limit_rate_is_the_dollars_of_claude_turns_between_two_readings_over_the_rise(tmp_path):
    store = store_with(tmp_path, [
        measured("10:00:00", 10), measured("12:00:00", 12),
        turn("10:30:00", 20.0), turn("12:00:00", 13.0),           # inside (from, to]
        turn("10:00:00", 5.0), turn("09:00:00", 50.0), turn("12:00:01", 9.0),  # outside
        turn("11:15:00", 100.0, model="gpt-6"),                    # not Claude
    ])
    result = queries.limit_rate(store)
    assert result.columns == ["from", "to", "pct_before", "pct_after", "usd", "usd_per_pct"]
    assert result.rows == [("2026-10-25T10:00:00.000000Z", "2026-10-25T12:00:00.000000Z",
                            10, 12, 33.0, "16.50")]


def test_limit_rate_shows_reset_for_a_pair_whose_percent_did_not_rise(tmp_path):
    store = store_with(tmp_path, [
        measured("12:00:00", 12), measured("13:00:00", 3), measured("14:00:00", 4),
        turn("12:30:00", 2.0), turn("13:30:00", 8.0),
    ])
    rows = queries.limit_rate(store).rows
    assert [(row[2], row[3], row[4], row[5]) for row in rows] == [(12, 3, 2.0, "reset"), (3, 4, 8.0, "8.00")]


def test_limit_rate_with_fewer_than_two_readings_has_no_rows(tmp_path):
    store = store_with(tmp_path, [measured("12:00:00", 12), turn("12:30:00", 2.0)])
    assert queries.limit_rate(store).rows == []


# --- review fixes: answers instead of silence ----------------------------------------------

def test_ticket_cost_of_a_ticket_never_ready_or_never_closed_says_so(tmp_path):
    edited = envelope(kind="ticket.edited", at=at("10:00:00"), ticket=7, role="brain",
                      detail={"field": "labels", "before": [], "after": ["ready-for-agent"]})
    for events, code in (([], "ticket_not_ready"), ([edited], "ticket_not_closed")):
        store = store_with(tmp_path, events, name=code)
        with pytest.raises(queries.QueryError) as caught:
            queries.ticket_cost(store, REPO, 7)
        assert caught.value.code == code


def test_limit_rate_tells_no_change_and_no_turns_apart_from_a_reset(tmp_path):
    store = store_with(tmp_path, [measured("10:00:00", 10), measured("11:00:00", 10),
                                  measured("12:00:00", 12), measured("13:00:00", 3)])
    assert [row[-1] for row in queries.limit_rate(store).rows] == ["no_change", "no_turns", "reset"]


def test_an_until_date_keeps_the_whole_day(tmp_path):
    store = store_with(tmp_path, [turn("18:00:00", 1.0, session="s1")])
    rows = queries.session_cost(store, until=DAY).rows
    assert rows and rows[0][0] == "s1"


def test_an_end_before_the_start_falls_back_to_first_and_last_event(tmp_path):
    store = store_with(tmp_path, [step("ended", "10:00:00", "session", "s1"),
                                  step("started", "10:10:00", "session", "s1"),
                                  turn("10:11:00", 1.0, session="s1")])
    assert queries.session_cost(store).rows[0][-1] == "11.0"


def test_a_rate_setting_out_of_bounds_or_of_another_shape_is_bad_setting():
    for rate, measured_at in ((10 ** 400, "2026-10-24"), (1e-320, "2026-10-24"), (float("inf"), "2026-10-24"),
                              (16.5, "not a date")):
        with pytest.raises(settings.SettingsError) as caught:
            queries.checked_rate(rate, "max", measured_at)
        assert caught.value.code == "bad_setting"
