"""Every event passes `validate` before it is stored: a closed-set breach raises with its own
code (#393 row 2), an unknown detail field passes."""

import pytest

from orch_it.events.schema import EventError, validate


def event(**over):
    base = {
        "id": "e1", "kind": "turn.used", "at": "2026-10-25T14:40:36+02:00",
        "repo": "acme-x/widgets-y", "ticket": None, "role": "worker", "session": "s1",
        "parent_session": None, "turn": "m1", "step": None, "run": None, "machine": "m",
        "os": "Windows 10", "layer_version": "0.0.0", "note": None,
        "detail": {"model": "claude-opus-5-5", "usd": 0.5},
    }
    base.update(over)
    return base


def refused(e) -> str:
    with pytest.raises(EventError) as caught:
        validate(e)
    return caught.value.code


def test_a_whole_event_passes_and_comes_back_unchanged():
    assert validate(event()) == event()


def test_an_unknown_kind_is_refused():
    assert refused(event(kind="turn.spent")) == "unknown_kind"


def test_an_outcome_outside_the_set_is_refused():
    e = event(kind="step.ended", step="session", detail={"outcome": "done"})
    assert refused(e) == "bad_outcome"


def test_a_step_ended_without_outcome_is_refused():
    assert refused(event(kind="step.ended", step="session", detail={})) == "missing_outcome"


def test_a_reason_code_outside_its_list_is_refused():
    e = event(kind="step.ended", step="session", detail={"outcome": "passed", "reason": "bored"})
    assert refused(e) == "bad_code"


def test_a_reason_of_another_step_s_list_is_refused():
    e = event(kind="step.ended", step="session", detail={"outcome": "passed", "reason": "merged"})
    assert refused(e) == "bad_code"


def test_the_generic_ended_reasons_pass_on_any_step():
    e = event(kind="step.ended", step="test_run", detail={"outcome": "no_result", "reason": "suspended"})
    assert validate(e) == e


def test_a_code_inside_a_list_value_is_checked_item_by_item():
    e = event(kind="step.ended", step="reconcile",
              detail={"outcome": "passed", "repairs": {"closed": 1, "exploded": 2}})
    assert refused(e) == "bad_code"


def test_a_placeholder_code_takes_any_value_after_its_prefix():
    ok = event(kind="step.started", step="test_run", detail={"place": "machine:ab12"})
    assert validate(ok) == ok
    assert refused(event(kind="step.started", step="test_run", detail={"place": "cloud"})) == "bad_code"


def test_a_role_outside_the_set_is_refused():
    assert refused(event(role="intern")) == "bad_code"


def test_a_missing_envelope_field_is_refused():
    e = event()
    del e["repo"]
    assert refused(e) == "missing_field"


def test_a_missing_nullable_envelope_field_is_still_refused():
    e = event()
    del e["note"]
    assert refused(e) == "missing_field"


def test_a_null_in_a_required_field_is_refused():
    assert refused(event(machine=None)) == "null_field"


def test_an_unknown_top_level_field_is_refused():
    assert refused(event(colour="red")) == "unknown_field"


def test_an_at_without_a_zone_is_refused():
    assert refused(event(at="2026-10-25T14:40:36")) == "at_without_zone"


def test_an_at_that_is_no_timestamp_is_refused():
    assert refused(event(at="yesterday")) == "bad_at"


def test_a_utc_z_at_passes():
    assert validate(event(at="2026-10-25T12:40:36.5Z"))["at"] == "2026-10-25T12:40:36.5Z"


def test_a_repo_that_is_a_folder_name_is_refused():
    assert refused(event(repo="orch-it")) == "bad_repo"


def test_a_ticket_that_is_no_integer_is_refused():
    assert refused(event(ticket="393")) == "bad_type"
    assert refused(event(ticket=True)) == "bad_type"


def test_an_unknown_step_is_refused():
    assert refused(event(step="lunch")) == "unknown_step"


def test_a_pair_event_needs_its_step():
    assert refused(event(kind="step.started", step=None, detail={})) == "step_missing"


def test_an_unknown_detail_field_passes():
    e = event(detail={"model": "x", "flavour": "salty"})
    assert validate(e) == e


def test_a_detail_that_is_no_object_is_refused():
    assert refused(event(detail=["model"])) == "bad_type"


def test_an_args_head_past_120_characters_is_refused():
    assert refused(event(kind="tool.called", detail={"args_head": "x" * 121})) == "too_long"


def test_something_not_an_object_is_refused():
    assert refused(["kind"]) == "not_an_object"


def test_every_code_is_distinct_and_named():
    from orch_it.events import schema
    assert len(set(schema.ERROR_CODES)) == len(schema.ERROR_CODES)


def test_a_generic_reason_goes_only_with_its_outcome():
    e = event(kind="step.ended", step="wait", detail={"outcome": "passed", "reason": "lease_lost"})
    assert refused(e) == "bad_code"
    ok = event(kind="step.ended", step="wait", detail={"outcome": "lost", "reason": "lease_lost"})
    assert validate(ok) == ok


def test_a_reason_with_no_list_written_takes_no_code_yet():
    assert refused(event(kind="hook.ran", detail={"reason": "anything"})) == "bad_code"
    e = event(kind="step.ended", step="launch", detail={"outcome": "failed", "reason": "timed_out"})
    assert refused(e) == "bad_code"


def test_progress_belongs_to_a_step():
    assert refused(event(kind="step.progressed", step=None, detail={})) == "step_missing"
    ok = event(kind="step.progressed", step="test_run", run="r1", detail={"parts_done": 3})
    assert validate(ok) == ok


def test_detail_must_be_plain_json():
    for bad in (float("nan"), float("inf"), b"bytes", {1: 2}, {"a": {None: 1}}, {"x"}, "\ud800"):
        assert refused(event(detail={"value": bad})) == "bad_type", bad


def test_a_deeply_nested_detail_is_refused_or_passed_without_crashing():
    deep = []
    for _ in range(100_000):
        deep = [deep]
    assert refused(event(kind="hook.ran", detail={"action": deep})) == "bad_code"
    assert validate(event(detail={"tree": deep}))["detail"]["tree"] is deep


def test_a_bounded_field_must_be_text():
    assert refused(event(kind="tool.called", detail={"args_head": ["x" * 1000] * 9})) == "bad_type"


def test_the_error_names_the_field_never_the_value():
    with pytest.raises(EventError) as caught:
        validate(event(kind="tool.called", detail={"args_head": "secret " * 100}))
    assert "secret" not in str(caught.value) and caught.value.field == "detail.args_head"


def test_an_error_survives_a_trip_between_processes():
    import pickle
    error = pickle.loads(pickle.dumps(EventError("unknown_kind", "kind")))
    assert (error.code, error.field) == ("unknown_kind", "kind")


def test_odd_repos_and_tickets_are_refused():
    for repo in ("a/b\n", "./.", "../..", "a/b/c", "/b"):
        assert refused(event(repo=repo)) == "bad_repo", repo
    for ticket in (0, -5, 10**30):
        assert refused(event(ticket=ticket)) == "bad_type", ticket


def test_ats_that_are_not_iso_with_a_zone_are_refused():
    for at in ("2026-10-25X14:40:36Z", "2026-10-25T14:40:36\x00Z", "0001-01-01T00:00:00+14:00",
               "2016-12-31T23:59:60Z", "2026-10-25T14:40:36z", "2026-10-25"):
        assert refused(event(at=at)) == "bad_at", at
