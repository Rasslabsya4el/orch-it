"""Who a session is (orchestration-layer#403 decision 2): the session's title is read the way the old
layer read it (the payload's `session_title`, else the last `custom-title` record of the transcript,
the end of the file first; a rename writes a new record), and a session is the brain when its title
equals the repo's brain title; a session started with `--agent brain` (the payload's `agent_type`,
or the `agentSetting` its transcript head records) is the brain too. Acceptance row 11 is the
store's side: a brain chat with no `agentType` is imported as role `brain`, not `worker`."""

import json
from pathlib import Path

import pytest

from orch_it.brain import session
from orch_it.events.store import Store
from orch_it.events.transcripts import import_transcripts

SESSION = "22222222-aaaa-bbbb-cccc-000000000002"


def transcript(path: Path, *titles: str, filler: int = 0, head: dict | None = None) -> Path:
    lines = [head] if head else []
    lines.append({"type": "user", "sessionId": SESSION, "timestamp": "2026-10-26T09:00:00.000Z",
                  "message": {"role": "user", "content": "привет"}})
    for title in titles:
        lines.append({"type": "custom-title", "sessionId": SESSION, "customTitle": title})
        lines += [{"type": "user", "sessionId": SESSION, "timestamp": "2026-10-26T09:00:01.000Z",
                   "message": {"role": "user", "content": "x" * 100}}] * filler
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), "utf-8")
    return path


def test_the_last_title_record_wins(tmp_path):
    path = transcript(tmp_path / "s.jsonl", "New chat", "Brain: orch-it")
    assert session.session_title({"transcript_path": str(path)}) == "Brain: orch-it"


def test_the_payload_s_title_comes_before_the_transcript(tmp_path):
    path = transcript(tmp_path / "s.jsonl", "Brain: orch-it")
    assert session.session_title({"session_title": "Fix the parser", "transcript_path": str(path)}) == "Fix the parser"


def test_a_title_far_from_the_end_is_still_found(tmp_path):
    path = transcript(tmp_path / "s.jsonl", "Brain: orch-it", filler=5000)  # about 700 KB after the title
    assert session.session_title({"transcript_path": str(path)}) == "Brain: orch-it"


def test_a_transcript_with_no_title_has_none_and_a_broken_line_is_skipped(tmp_path):
    path = transcript(tmp_path / "s.jsonl")
    with path.open("a", encoding="utf-8") as out:
        out.write('{"type": "custom-title", "customTitle": "half')
    assert session.session_title({"transcript_path": str(path)}) is None


@pytest.mark.parametrize("payload", [{}, {"transcript_path": ""}, {"transcript_path": 7},
                                     {"transcript_path": "/no/such/file.jsonl"}])
def test_a_transcript_that_cannot_be_read_is_said_so(payload):
    with pytest.raises(session.TranscriptUnreadable):
        session.session_title(payload)


@pytest.mark.parametrize("payload, brain", [
    ({"session_title": "Brain: orch-it"}, True),
    ({"session_title": "Brain: orch-it ", "agent_type": None}, False),
    ({"session_title": "brain: orch it"}, False),
    ({"session_title": "Fix the parser", "agent_type": "brain"}, True),
    ({"session_title": "Fix the parser", "agent_type": "plugin:brain"}, True),
    ({"session_title": "Fix the parser", "agent_type": "orch"}, False),
])
def test_the_brain_is_its_exact_title_first_and_agent_brain_second(payload, brain):
    assert session.is_brain(payload, "Brain: orch-it") is brain


def test_a_session_started_as_the_brain_needs_no_readable_transcript():
    assert session.is_brain({"agent_type": "brain", "transcript_path": "/no/such/file.jsonl"}, "Brain: orch-it")


def test_an_empty_title_record_never_wipes_the_title_before_it(tmp_path):
    path = transcript(tmp_path / "s.jsonl", "Brain: orch-it", "")
    assert session.session_title({"transcript_path": str(path)}) == "Brain: orch-it"


def test_the_importer_reads_the_title_and_the_agent_as_the_hook_does(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    folder = tmp_path / "projects" / "a-project"
    folder.mkdir(parents=True)
    transcript(folder / f"{SESSION}.jsonl", "Brain: orch-it", "")
    import_transcripts(folder, Store.open(tmp_path / "s1"), repo="owner-x/orch-it")
    assert Store.open(tmp_path / "s1").query("select distinct role from events").rows == [("brain",)]
    transcript(folder / f"{SESSION}.jsonl", "Fix the parser", head={"type": "agent-setting", "agentSetting": "brain"})
    import_transcripts(folder, Store.open(tmp_path / "s2"), repo="owner-x/orch-it", role="orch")
    assert Store.open(tmp_path / "s2").query("select distinct role from events").rows == [("brain",)]


def test_a_session_started_with_agent_brain_is_the_brain_by_its_transcript_head(tmp_path):
    path = transcript(tmp_path / "s.jsonl", head={"type": "agent-setting", "agentSetting": "brain"})
    assert session.is_brain({"transcript_path": str(path)}, "Brain: orch-it")


def test_a_brain_chat_without_agent_type_is_imported_as_the_brain(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    folder = tmp_path / "projects" / "a-project"
    folder.mkdir(parents=True)
    transcript(folder / f"{SESSION}.jsonl", "Brain: orch-it")
    store = Store.open(tmp_path / "store")
    import_transcripts(folder, store, repo="owner-x/orch-it")
    assert store.query("select distinct role from events").rows == [("brain",)]


def test_another_title_keeps_the_role_given_and_a_set_brain_title_is_read(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"repos": {"owner-x/orch-it": {"brain_chat_title": "Brain: orch layer"}}}),
                             "utf-8")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(settings_file))
    folder = tmp_path / "projects" / "a-project"
    folder.mkdir(parents=True)
    transcript(folder / f"{SESSION}.jsonl", "Brain: orch-it")
    store = Store.open(tmp_path / "store")
    import_transcripts(folder, store, repo="owner-x/orch-it")
    assert store.query("select distinct role from events").rows == [("worker",)]
    transcript(folder / f"{SESSION}.jsonl", "Brain: orch layer")
    import_transcripts(folder, Store.open(tmp_path / "store2"), repo="owner-x/orch-it")
    assert Store.open(tmp_path / "store2").query("select distinct role from events").rows == [("brain",)]


def test_a_long_title_that_merely_starts_with_the_brain_title_is_no_brain(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    folder = tmp_path / "projects" / "a-project"
    folder.mkdir(parents=True)
    transcript(folder / f"{SESSION}.jsonl", "B" * 200 + "y" * 500)
    import_transcripts(folder, Store.open(tmp_path / "s"), repo="owner-x/orch-it", brain_title="B" * 200)
    assert Store.open(tmp_path / "s").query("select distinct role from events").rows == [("worker",)]
