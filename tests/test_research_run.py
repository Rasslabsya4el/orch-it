"""One research run against the opencode stand-in (`opencode_stub.py`), start to end (#396 rows
2-6): the report goes to the path the brief names, the events land in the store, a bound ends
the run `no_result` with the partial report beside the path and no process left, and what the
settings cannot serve is refused before any server starts."""

import subprocess
import threading
import time
from pathlib import Path

import pytest

from orch_it.research import opencode, run
from orch_it.research.opencode import OpencodeError

from orch_it.research.settings import ResearchRefused
from research_bridge import BRIEF, KEY_ENV, PROMPT_ONLY, Bridge, report_messages


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def test_a_passed_run_writes_the_report_where_the_brief_names_it_and_both_events(tmp_path):
    bridge = Bridge(tmp_path, messages=report_messages("lost-report-380"))
    done = bridge.run(model="tiny")
    target = bridge.work_tree / "docs" / "research" / "2026-10-26-test.md"
    assert (done.outcome, done.reason, done.report_from) == ("passed", None, "earlier")
    assert done.report_path == str(target) and len(target.read_text("utf-8")) == 38973
    [started] = bridge.events("step.started")
    [ended] = bridge.events("step.ended")
    assert (started["model_alias"], started["model"]) == ("tiny", "model-x-9")
    assert (ended["outcome"], ended["report_from"], ended["report_chars"], ended["compactions"]) == (
        "passed", "earlier", 38973, 1)
    # the run's own tokens are those of the #380 session as opencode's database totals it
    assert (ended["tokens_in"], ended["tokens_out"], ended["tokens_reasoning"], ended["tokens_cache_read"]) == (
        425926, 16936, 37382, 3914240)
    bridge.assert_tree_gone()


def test_an_alias_of_the_settings_reaches_opencode_as_its_id(tmp_path):
    bridge = Bridge(tmp_path)
    bridge.run(model="tiny")
    [prompt] = bridge.seen()["prompts"]
    assert prompt["model"]["modelID"] == "model-x-9" and prompt["agent"] == "orch-it-research"
    assert prompt["model"]["providerID"] == "stub-provider"
    assert bridge.seen()["key_env"] == KEY_ENV and bridge.seen()["key_present"] is True


def test_the_helpers_of_a_run_are_metered_in_its_ended_event(tmp_path):
    bridge = Bridge(tmp_path, tree="depth-3", messages=report_messages("report-alone"))
    bridge.run(model="tiny")
    [ended] = bridge.events("step.ended")
    assert (ended["helper_tokens_in"], ended["helper_tokens_out"], ended["helper_tokens_reasoning"],
            ended["helper_tokens_cache_read"], ended["helper_tokens_cache_write"]) == (
        320500, 12050, 1600, 2070000, 4103)
    assert (ended["helper_sessions"], ended["helper_depth"]) == (3, 2)


def test_a_run_past_its_time_bound_ends_no_result_with_the_partial_report_and_no_process(tmp_path):
    bridge = Bridge(tmp_path, mode="hang", messages=PROMPT_ONLY, minutes=0.03, stall_minutes=5)
    done = bridge.run()
    target = bridge.work_tree / "docs" / "research" / "2026-10-26-test.partial.md"
    assert (done.outcome, done.reason) == ("no_result", "time_bound")
    assert done.partial_path == str(target) and "of the work" in target.read_text("utf-8")
    assert done.report_path is None and not target.with_name("2026-10-26-test.md").exists()
    progressed = bridge.events("step.progressed")
    assert progressed and progressed[-1]["steps"] > progressed[0]["steps"]
    assert "run" in bridge.seen()["aborted"]
    bridge.assert_tree_gone()


def test_a_stalled_run_ends_no_result_with_the_partial_report_and_no_process(tmp_path):
    bridge = Bridge(tmp_path, mode="stall", messages=PROMPT_ONLY, minutes=5, stall_minutes=0.03)
    done = bridge.run()
    assert (done.outcome, done.reason) == ("no_result", "stalled")
    assert done.partial_path.endswith("2026-10-26-test.partial.md")
    [ended] = bridge.events("step.ended")
    assert (ended["outcome"], ended["reason"]) == ("no_result", "stalled")
    bridge.assert_tree_gone()


def test_a_thin_reply_fails_naming_the_first_failing_test_and_keeps_the_text(tmp_path):
    bridge = Bridge(tmp_path, messages=report_messages("thin"))
    done = bridge.run()
    assert (done.outcome, done.reason) == ("failed", "too_short")
    assert Path(done.partial_path).read_text("utf-8")


def test_without_a_report_path_in_the_brief_the_report_goes_to_the_run_folder(tmp_path):
    bridge = Bridge(tmp_path)
    done = bridge.run(brief="# Brief with no report path\n")
    assert Path(done.report_path) == tmp_path / "state" / "runs" / done.run / "report.md"


def test_the_default_model_follows_the_brief_size(tmp_path):
    bridge = Bridge(tmp_path, default_model=[{"up_to_chars": 10, "model": "tiny"}, {"model": "other"}])
    done = bridge.run()
    assert (done.model_alias, done.model) == ("other", "model-y-1")


@pytest.mark.parametrize("change,code", [
    ({"model": "luna"}, "unknown_model"),
    ({"env": {}}, "key_missing"),
    ({"report_to": "../outside.md"}, "report_path_outside"),
])
def test_a_run_the_settings_cannot_serve_is_refused_before_any_server_or_event(tmp_path, change, code):
    bridge = Bridge(tmp_path)
    with pytest.raises(ResearchRefused) as refused:
        bridge.run(**change)
    assert refused.value.code == code
    assert not bridge.record.exists() and bridge.events() == []


def test_a_missing_opencode_is_refused_before_any_event(tmp_path):
    bridge = Bridge(tmp_path, opencode="no-such-opencode-orch-it")
    with pytest.raises(ResearchRefused) as refused:
        bridge.run()
    assert refused.value.code == "opencode_missing" and bridge.events() == []


def test_a_server_that_dies_at_start_ends_the_run_no_result(tmp_path):
    bridge = Bridge(tmp_path, mode="exit")
    done = bridge.run()
    assert (done.outcome, done.reason) == ("no_result", "server_failed")
    [ended] = bridge.events("step.ended")
    assert ended["server_error"] == "server_start_failed"


def test_two_runs_at_once_have_their_own_servers_and_files(tmp_path):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    bridges = [Bridge(tmp_path / "a"), Bridge(tmp_path / "b")]
    results = {}
    threads = [threading.Thread(target=lambda n=n: results.__setitem__(n, bridges[n].run())) for n in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a run did not end in 60 s"
    assert [results[n].outcome for n in (0, 1)] == ["passed", "passed"]
    assert bridges[0].seen()["pid"] != bridges[1].seen()["pid"]


def test_a_named_report_path_that_cannot_be_written_keeps_the_report_in_the_run_folder(tmp_path):
    bridge = Bridge(tmp_path)
    (bridge.work_tree / "docs" / "research" / "2026-10-26-test.md").mkdir(parents=True)  # a folder in the way
    done = bridge.run()
    assert done.outcome == "passed"
    assert Path(done.report_path) == tmp_path / "state" / "runs" / done.run / "report.md"
    [ended] = bridge.events("step.ended")
    assert ended["named_path_unwritable"] is True


def test_a_status_call_that_fails_now_and_then_does_not_end_the_run(tmp_path, monkeypatch):
    real = opencode.Client.busy
    calls = {"n": 0}

    def flaky(self, session):
        calls["n"] += 1
        if calls["n"] in (2, 3):
            raise OpencodeError("unreachable", "a moment's hiccup")
        return real(self, session)

    monkeypatch.setattr(opencode.Client, "busy", flaky)
    done = Bridge(tmp_path).run()
    assert done.outcome == "passed"


def test_a_wall_clock_jumping_forward_does_not_stall_a_working_run(tmp_path):
    calls = {"n": 0}

    def wall():  # an hour forward from the 20th reading on, well inside the wait
        calls["n"] += 1
        return time.time() + (3600 if calls["n"] >= 20 else 0)

    bridge = Bridge(tmp_path, mode="hang", messages=PROMPT_ONLY, minutes=30, stall_minutes=0.05)
    done = bridge.run(wall=wall)
    # the forward jump trips the time bound (a sleeping machine still cuts the run), never the stall
    assert (done.outcome, done.reason) == ("no_result", "time_bound")


def test_an_error_of_the_bridge_itself_still_closes_the_pair(tmp_path, monkeypatch):
    def broken(messages, *, require_reddit):
        raise RuntimeError("a bug")

    monkeypatch.setattr(run.report, "choose", broken)
    bridge = Bridge(tmp_path)
    with pytest.raises(RuntimeError):
        bridge.run()
    [ended] = bridge.events("step.ended")
    assert (ended["outcome"], ended["reason"], ended["error"]) == ("no_result", "bridge_error", "RuntimeError")
    bridge.assert_tree_gone()


@pytest.mark.parametrize("brief", ["", "   \n", "x" * (run.MAX_BRIEF_CHARS + 1)], ids=["empty", "blank", "huge"])
def test_an_empty_or_huge_brief_is_refused_before_any_server(tmp_path, brief):
    bridge = Bridge(tmp_path)
    with pytest.raises(ResearchRefused) as refused:
        bridge.run(brief=brief)
    assert refused.value.code == "brief_unreadable" and not bridge.record.exists()


def test_a_state_folder_inside_a_git_work_tree_is_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    bridge = Bridge(tmp_path, state=str(repo / "state"))
    with pytest.raises(ResearchRefused) as refused:
        bridge.run()
    assert refused.value.code == "state_in_git_tree" and bridge.events() == []


def test_a_failed_model_call_carries_the_provider_s_error_without_its_headers(tmp_path):
    bridge = Bridge(tmp_path, messages=report_messages("model-error"))
    done = bridge.run()
    assert (done.outcome, done.reason, done.model_error) == ("no_result", "model_error", "APIError: 429 Too Many Requests")
    [ended] = bridge.events("step.ended")
    assert ended["model_error"] == "APIError: 429 Too Many Requests"


def test_a_failed_abort_is_recorded_and_the_partial_report_still_kept(tmp_path, monkeypatch):
    def refuse(self, session):
        raise OpencodeError("http_error", "POST abort: HTTP 500")

    monkeypatch.setattr(opencode.Client, "abort", refuse)
    bridge = Bridge(tmp_path, mode="stall", messages=PROMPT_ONLY, minutes=5, stall_minutes=0.03)
    done = bridge.run()
    assert (done.outcome, done.reason) == ("no_result", "stalled") and done.partial_path
    [ended] = bridge.events("step.ended")
    assert ended["abort_failed"] is True
    bridge.assert_tree_gone()


def fetch(url: str, status: str) -> dict:
    return {"type": "tool", "tool": "webfetch", "state": {"status": status, "input": {"url": url}}}


def test_progress_counts_completed_fetches_and_reddit_only_when_a_reddit_fetch_completed(tmp_path):
    """#333 carried: sources are completed fetches; Reddit is reached by a completed fetch only."""

    def run_with(parts):
        folder = tmp_path / str(len(list(tmp_path.iterdir())))
        folder.mkdir()
        messages = PROMPT_ONLY + [{"info": {"role": "assistant", "time": {"created": 1}},
                                   "parts": [{"type": "step-start"}, *parts]}]
        bridge = Bridge(folder, mode="stall", messages=messages, minutes=5, stall_minutes=0.03)
        bridge.run()
        return bridge.events("step.progressed")[-1]

    failed = run_with([fetch("https://www.reddit.com/r/a/1", "error"), fetch("https://example.org/a", "completed")])
    assert (failed["sources"], failed["reddit_reached"]) == (1, False)
    reached = run_with([fetch("https://old.reddit.com/r/a/2", "completed"), fetch("https://example.org/a", "completed"),
                        fetch("https://example.org/a", "completed")])
    assert (reached["sources"], reached["reddit_reached"]) == (2, True)


def test_a_message_read_that_fails_once_skips_that_progress_read_only(tmp_path, monkeypatch):
    real = opencode.Client.messages
    calls = {"n": 0}

    def flaky(self, session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OpencodeError("unreachable", "a moment's hiccup")
        return real(self, session)

    monkeypatch.setattr(opencode.Client, "messages", flaky)
    bridge = Bridge(tmp_path, mode="hang", messages=PROMPT_ONLY, minutes=0.03, stall_minutes=5)
    done = bridge.run()
    assert (done.outcome, done.reason) == ("no_result", "time_bound") and bridge.events("step.progressed")
