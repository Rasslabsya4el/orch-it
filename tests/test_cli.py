"""`python -m orch_it events <command>` (#393 decisions 8 and 9; rows 6, 10): the commands print what
the named queries return, write what they should, and turn the store's errors into `error: <code>`
with exit code 2."""

import json
import subprocess
import sys
import types
from datetime import UTC, datetime

import pytest

from orch_it import settings
from orch_it.cli import main
from orch_it.events.store import Store

REPO = "acme-x/widgets-y"
DAY = "2026-10-25"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def envelope(**over):
    base = {
        "kind": "value.measured", "repo": REPO, "ticket": None,
        "role": "script", "session": None, "parent_session": None, "turn": None,
        "step": None, "run": None, "note": None, "detail": {},
    }
    base.update(over)
    return base


def turn(clock, usd, *, ticket=None, role="worker", model="claude-opus-5-5", held=None, session=None):
    detail = {"model": model, "usd": usd, "input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_write_tokens": 0}
    if held is not None:
        detail["tickets_held"] = held
    return envelope(kind="turn.used", at=f"{DAY}T{clock}Z", ticket=ticket, role=role,
                    session=session, detail=detail)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)
    (tmp_path / "settings.json").write_text(json.dumps({"repo": REPO}), "utf-8")  # the repo a command takes without --repo


@pytest.fixture
def store(tmp_path):
    return Store.open(tmp_path / "store")


def write(store, events):
    with store.writer() as writer:
        for event in events:
            writer.write(event)


def run(capsys, store, *argv):
    code = main(["--store", str(store.root), "events", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def cells(out):
    return [line.split() for line in out.splitlines()]


# --- events query ---------------------------------------------------------------------------

def test_query_prints_an_aligned_table_with_a_header(store, capsys):
    write(store, [envelope(detail={"name": "ram", "value": 16}),
                  envelope(detail={"name": "cpu_cores", "value": 8})])
    code, out, err = run(capsys, store, "query", "SELECT name, value FROM events ORDER BY value")
    assert (code, err) == (0, "")
    assert out == "name       value\ncpu_cores  8\nram        16\n"


def test_query_binds_named_parameters(store, capsys):
    write(store, [envelope(detail={"name": "ram", "value": 16}),
                  envelope(detail={"name": "cpu_cores", "value": 8})])
    code, out, _ = run(capsys, store, "query", "SELECT value FROM events WHERE name = :name AND value > :least",
                       "--param", "name=ram", "--param", "least=10")
    assert (code, out) == (0, "value\n16\n")


def test_query_of_no_rows_prints_just_the_header(store, capsys):
    code, out, _ = run(capsys, store, "query", "SELECT id, kind FROM events")
    assert (code, out) == (0, "id  kind\n")


def test_a_query_that_fails_is_an_error_with_exit_code_2(store, capsys):
    code, out, err = run(capsys, store, "query", "DELETE FROM events")
    assert (code, out) == (2, "")
    assert err.startswith("error: query_failed")


def test_a_param_without_an_equals_sign_is_refused_with_exit_code_2(store, capsys):
    code, _, err = run(capsys, store, "query", "SELECT 1", "--param", "ram")
    assert code == 2
    assert "--param" in err


def test_the_store_option_is_also_taken_after_the_command(store, capsys):
    write(store, [envelope()])
    code = main(["events", "query", "SELECT count(*) FROM events", "--store", str(store.root)])
    assert (code, capsys.readouterr().out) == (0, "count(*)\n1\n")


def test_without_the_store_option_the_store_is_the_setting(tmp_path, monkeypatch, capsys):
    chosen = Store.open(tmp_path / "chosen")
    write(chosen, [envelope(), envelope()])
    settings.save("store", str(chosen.root))
    assert main(["events", "query", "SELECT count(*) FROM events"]) == 0
    assert capsys.readouterr().out == "count(*)\n2\n"


# --- events ticket-cost -----------------------------------------------------------------------

def test_ticket_cost_prints_the_per_role_sums_with_the_share_of_the_week(store, capsys):
    settings.save("weekly_limit_usd_per_pct", {"usd_per_pct": 16.5, "plan": "max-20x", "measured": "2026-10-24"})
    write(store, [
        envelope(kind="ticket.edited", at=f"{DAY}T10:00:00Z", ticket=393, role="orch", detail={
            "field": "labels", "before": ["bug"], "after": ["bug", "ready-for-agent"]}),
        envelope(kind="ticket.closed", at=f"{DAY}T12:00:00Z", ticket=393, role="github"),
        turn("10:30:00", 1.20, ticket=393),
        turn("11:00:00", 0.90, role="orch", held=[393, 394]),
        turn("11:30:00", 2.0, ticket=394),
    ])
    code, out, err = run(capsys, store, "ticket-cost", "--repo", REPO, "--ticket", "393")
    assert (code, err) == (0, "")
    assert cells(out) == [["role", "how", "usd", "%", "week"],
                          ["worker", "direct", "1.2", "0.07", "%"],
                          ["orch", "shared", "0.45", "0.03", "%"]]


# --- events measure-limit, limit-rate (row 10) -----------------------------------------------------

def test_two_readings_and_the_turns_between_them_give_the_dollars_per_percent(store, capsys, monkeypatch):
    now = [datetime(2026, 10, 25, 10, 0, tzinfo=UTC)]
    monkeypatch.setattr("orch_it.events.store.datetime", types.SimpleNamespace(now=lambda: now[0]))
    assert run(capsys, store, "measure-limit", "10", "--note", "usage card, morning")[0] == 0
    now[0] = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    assert run(capsys, store, "measure-limit", "12")[0] == 0
    write(store, [turn("10:30:00", 20.0), turn("11:30:00", 13.0), turn("12:30:00", 99.0)])

    code, out, err = run(capsys, store, "limit-rate")
    assert (code, err) == (0, "")
    assert cells(out) == [
        ["from", "to", "pct_before", "pct_after", "usd", "usd_per_pct"],
        ["2026-10-25T10:00:00.000000Z", "2026-10-25T12:00:00.000000Z", "10.0", "12.0", "33.0", "16.50"]]


def test_measure_limit_writes_a_value_measured_event_with_every_envelope_field_given(store, capsys):
    assert run(capsys, store, "measure-limit", "26.5", "--role", "brain", "--repo", "acme-x/other",
               "--note", "card")[0] == 0
    [(raw,)] = store.query("SELECT json_object('kind', kind, 'repo', repo, 'ticket', ticket, 'role', role,"
                           " 'session', session, 'parent_session', parent_session, 'turn', turn,"
                           " 'step', step, 'run', run, 'note', note, 'detail', json(detail)) FROM events").rows
    assert json.loads(raw) == {
        "kind": "value.measured", "repo": "acme-x/other", "ticket": None, "role": "brain",
        "session": None, "parent_session": None, "turn": None, "step": None, "run": None,
        "note": "card",
        "detail": {"name": "weekly_limit_pct", "value": 26.5, "unit": "%", "source": "usage_card"}}


def test_measure_limit_takes_the_repo_setting_when_no_repo_is_given(store, capsys, tmp_path, monkeypatch):
    plain = tmp_path / "plain"  # a work tree with no GitHub origin: the origin comes before the setting
    plain.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=plain, check=True, capture_output=True)
    monkeypatch.chdir(plain)
    assert run(capsys, store, "measure-limit", "3")[0] == 0
    assert store.query("SELECT repo, role, note FROM events").rows == [(REPO, "owner", None)]


def test_a_role_outside_the_set_is_an_error_with_its_code(store, capsys):
    code, out, err = run(capsys, store, "measure-limit", "3", "--role", "janitor")
    assert (code, out) == (2, "")
    assert err.startswith("error: bad_code")
    assert store.query("SELECT count(*) FROM events").rows == [(0,)]


# --- events set-limit-rate, session-cost (row 10) ------------------------------------------------

def test_set_limit_rate_saves_the_setting_and_session_cost_reads_it(store, capsys):
    write(store, [turn("10:00:00", 2.92, session="s1"), turn("10:01:00", 1.0, session="s1", model="gpt-6")])
    before = run(capsys, store, "session-cost")[1]
    assert "not set" in before and "n/a" in before and "0.18 %" not in before

    code, out, err = run(capsys, store, "set-limit-rate", "16.5", "--plan", "max-20x", "--measured", "2026-10-24")
    assert (code, err) == (0, "")
    assert settings.load()["weekly_limit_usd_per_pct"] == {
        "usd_per_pct": 16.5, "plan": "max-20x", "measured": "2026-10-24"}

    code, out, err = run(capsys, store, "session-cost")
    assert (code, err) == (0, "")
    rows = cells(out)
    assert rows[0][:3] == ["session", "machine", "model"]
    assert rows[1][2] == "claude-opus-5-5" and rows[1][8:11] == ["2.92", "0.18", "%"]
    assert rows[2][2] == "gpt-6" and rows[2][9] == "n/a"


def test_session_cost_options_filter_the_rows(store, capsys):
    write(store, [turn("09:00:00", 1.0, session="early"), turn("11:00:00", 2.0, session="late")])
    code, out, _ = run(capsys, store, "session-cost", "--since", "2026-10-25T10:00:00Z")
    assert code == 0
    assert "late" in out and "early" not in out
    code, out, _ = run(capsys, store, "session-cost", "--session", "early", "--repo", REPO)
    assert "early" in out and "late" not in out


def test_set_limit_rate_refuses_a_rate_that_is_not_positive(store, capsys):
    code, _, err = run(capsys, store, "set-limit-rate", "0", "--plan", "max-20x", "--measured", "2026-10-24")
    assert code == 2 and err.startswith("error: bad_setting")
    assert settings.load() == {"repo": REPO}  # the refusal wrote nothing: only the fixture's setting is there


def test_a_settings_file_that_is_not_json_is_an_error_with_its_code(store, capsys, tmp_path):
    (tmp_path / "settings.json").write_text("{not json", "utf-8")
    code, _, err = run(capsys, store, "set-limit-rate", "16.5", "--plan", "p", "--measured", "2026-10-24")
    assert code == 2 and err.startswith("error: settings_unreadable")


# --- errors -----------------------------------------------------------------------------------

def test_a_store_inside_a_git_work_tree_is_refused_with_exit_code_2(tmp_path, capsys):
    (tmp_path / "work" / ".git").mkdir(parents=True)
    code = main(["--store", str(tmp_path / "work" / "events"), "events", "limit-rate"])
    captured = capsys.readouterr()
    assert (code, captured.out) == (2, "")
    assert captured.err.startswith("error: store_in_git_tree")
    assert not (tmp_path / "work" / "events").exists()


# --- events import ------------------------------------------------------------------------------

def fake_transcripts(monkeypatch, calls):
    from orch_it.events import transcripts

    def import_transcripts(folder, store, **kwargs):
        calls.append((folder, store, kwargs))
        return types.SimpleNamespace(written=12, already_stored=3, sessions=2, bad_lines=1,
                                     pending_lines=4, refused_events=0, unreadable_files=0)

    monkeypatch.setattr(transcripts, "import_transcripts", import_transcripts)


def test_import_hands_the_options_to_the_importer_and_prints_its_counts(store, capsys, monkeypatch, tmp_path):
    calls = []
    fake_transcripts(monkeypatch, calls)
    code, out, err = run(capsys, store, "import", str(tmp_path / "chat"), "--repo", REPO, "--role", "orch",
                         "--subagent-role", "researcher-find", "--ticket", "393", "--session", "abc",
                         "--quiet-minutes", "45")
    assert (code, err) == (0, "")
    assert out == ("written=12 already_stored=3 sessions=2 bad_lines=1 pending_lines=4"
                   " refused_events=0 unreadable_files=0\n")
    [(folder, given, kwargs)] = calls
    assert folder == tmp_path / "chat" and given.root == store.root
    assert kwargs == {"repo": REPO, "role": "orch", "subagent_role": "researcher-find", "ticket": 393,
                      "session": "abc", "quiet_minutes": 45}


def test_import_defaults_to_the_worker_role_and_30_quiet_minutes(store, capsys, monkeypatch, tmp_path):
    calls = []
    fake_transcripts(monkeypatch, calls)
    assert run(capsys, store, "import", str(tmp_path), "--repo", REPO)[0] == 0
    assert calls[0][2] == {"repo": REPO, "role": "worker", "subagent_role": None, "ticket": None,
                           "session": None, "quiet_minutes": 30}


def test_import_then_session_cost_reads_a_real_transcript(store, capsys, tmp_path):
    import json
    folder = tmp_path / "projects" / "p"
    folder.mkdir(parents=True)
    first = {"type": "user", "sessionId": "s-1", "timestamp": "2026-10-25T17:50:00.000Z",
             "message": {"role": "user", "content": "go"}}
    turn = {"type": "assistant", "sessionId": "s-1", "timestamp": "2026-10-25T18:00:00.000Z",
            "message": {"id": "msg_1", "model": "claude-opus-5-5", "stop_reason": "end_turn",
                        "usage": {"input_tokens": 10000, "output_tokens": 100000}, "content": []}}
    (folder / "s-1.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(turn) + "\n", "utf-8")
    code, out, err = run(capsys, store, "import", str(folder), "--repo", REPO, "--role", "worker-strong")
    assert (code, err) == (0, "") and out.startswith("written=3 already_stored=0 sessions=1")
    code, out, _ = run(capsys, store, "session-cost", "--session", "s-1")
    # (10000 in x 4 + 100000 out x 20) per million = 2.04 USD; 17:50 -> 18:00 is 10.0 minutes
    assert code == 0 and "2.04" in out and "10.0" in out


def test_import_of_a_missing_folder_is_an_error_with_its_code(store, capsys, tmp_path):
    code, _, err = run(capsys, store, "import", str(tmp_path / "nowhere"), "--repo", REPO)
    assert code == 2 and err.startswith("error: no_transcript_folder")


def test_import_needs_its_repo(store, capsys, tmp_path):
    code, _, err = run(capsys, store, "import", str(tmp_path))
    assert code == 2 and "--repo" in err


# --- python -m orch_it --------------------------------------------------------------------------

def test_the_module_runs_as_python_dash_m_orch_it(tmp_path):
    env = {**__import__("os").environ, "ORCH_IT_SETTINGS": str(tmp_path / "settings.json"),
           "ORCH_IT_STORE": str(tmp_path / "store")}
    done = subprocess.run(
        [sys.executable, "-m", "orch_it", "events", "query", "SELECT 41 + 1 AS answer"],
        capture_output=True, text=True, env=env, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert (done.returncode, done.stdout, done.stderr) == (0, "answer\n42\n", "")


def test_help_prints_and_exits_0(store, capsys):
    assert run(capsys, store, "--help")[0] == 0
    assert main(["events", "session-cost", "--help"]) == 0


def test_a_weekly_percent_outside_0_to_100_is_bad_pct(store, capsys):
    for pct in ("-5", "1000"):
        code, _, err = run(capsys, store, "measure-limit", pct)
        assert code == 2 and err.startswith("error: bad_pct")


def test_params_bind_as_numbers_only_when_written_plainly(store, capsys):
    code, out, _ = run(capsys, store, "query", "SELECT typeof(:a), typeof(:b), typeof(:c), :b",
                       "--param", "a=7", "--param", "b=007", "--param", "c=-2.5")
    assert code == 0 and cells(out)[1] == ["integer", "text", "real", "007"]


def test_an_empty_query_is_an_error_and_the_code_is_said_once(store, capsys):
    code, _, err = run(capsys, store, "query", "  -- nothing")
    assert code == 2 and err.startswith("error: query_failed:") and err.count("query_failed") == 1


def test_a_recursive_query_runs(store, capsys):
    code, out, _ = run(capsys, store, "query",
                       "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3) SELECT sum(x) FROM n")
    assert code == 0 and cells(out)[1] == ["6"]
