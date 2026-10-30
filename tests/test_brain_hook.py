"""The brain's session hook (orchestration-layer#403 decision 2; acceptance rows 4, 5 and 8), wired at
SessionStart and UserPromptSubmit. A session whose title is the repo's brain title gets the brain's
rules file (read from the default branch of `origin`) and the newest hand-off at its first prompt,
and again in a new session after a `/clear`. An interactive chat whose title looks like a brain's but
is not exactly this repo's gets one line asking the owner to rename it. Nothing it cannot read ever
stops a prompt: it lets it through and writes `error.raised` (`failed_open`)."""

import json
import subprocess
from pathlib import Path

import pytest

from fake_hub import FakeHub
from orch_it import settings
from orch_it.brain import hook
from orch_it.brain.hook import HEAD_LINE, READ_WHOLE
from orch_it.events.store import Store

REPO = "owner-x/orch-it"
RULES = "# The brain: the owner's chat\n\nThe brain is the chat the installation's settings name.\n"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    hub = FakeHub()
    hub.add(5, "", ["brain-handoff"], title="Brain hand-off", repo=REPO)
    hub.say(5, "Done: the queues. Next: the hook.", repo=REPO)
    hub.later(minutes=1)
    hub.say(5, "Done: the hook. Next: the table. Ask the owner: the TLDR wording.", repo=REPO)
    settings.save_in_repo(REPO, "brain_handoff_issue", 5)
    settings.save_in_repo("owner-x/uECP", "brain_chat_title", "Brain: uECP")
    return {"hub": hub, "store": Store.open(tmp_path / "store"), "marks": tmp_path / "marks", "tmp": tmp_path}


def transcript(path: Path, title: str | None, *, head: dict | None = None, context: int = 1000) -> Path:
    lines = [head] if head else []
    lines.append({"type": "user", "timestamp": "2026-10-26T09:00:00.000Z", "message": {"role": "user", "content": "hi"}})
    if title is not None:
        lines.append({"type": "custom-title", "customTitle": title})
    lines.append({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
                                                   "usage": {"input_tokens": 10, "cache_read_input_tokens": context}}})
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), "utf-8")
    return path


def payload(path: Path | None, *, session="s1", event="UserPromptSubmit", **extra) -> str:
    found = {"hook_event_name": event, "session_id": session, "cwd": ".", "prompt": "Что дальше?"}
    if path is not None:
        found["transcript_path"] = str(path)
    return json.dumps(found | extra)


def run(world, raw: str, rules: str | None = RULES):
    return hook.run(raw, store=world["store"], repo=REPO, hub=world["hub"], rules=lambda _cwd: rules,
                    marks=world["marks"])


def said(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def errors(world) -> list[tuple]:
    return [(json.loads(d)["component"], json.loads(d)["error_code"], json.loads(d)["failed_open"])
            for (d,) in world["store"].query("select detail from events where kind = 'error.raised'").rows]


def test_the_brain_s_first_prompt_gets_its_rules_and_the_newest_hand_off(world):
    out, code = run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")))
    text = said(out)
    assert code == 0 and json.loads(out)["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "# The brain: the owner's chat" in text
    assert "Done: the hook. Next: the table. Ask the owner: the TLDR wording." in text
    assert "Done: the queues." not in text
    [(detail,)] = world["store"].query("select detail from events where kind = 'handoff.read'").rows
    assert json.loads(detail) == {"entry_ref": f"{REPO}#5", "found": True, "source": "hand-off issue"}


def test_a_later_prompt_of_the_same_session_is_not_given_them_again_and_a_clear_is(world):
    first = transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")
    run(world, payload(first))
    assert said(run(world, payload(first))[0]) == ""
    cleared = transcript(world["tmp"] / "b.jsonl", "Brain: orch-it")  # /clear: a new session, same title
    assert "# The brain: the owner's chat" in said(run(world, payload(cleared, session="s2"))[0])


def test_session_start_after_a_clear_or_a_compaction_injects_too(world):
    path = transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")
    for source in ("startup", "clear", "compact"):
        out, _ = run(world, payload(path, event="SessionStart", source=source))
        assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "# The brain: the owner's chat" in said(out)


def test_rules_not_yet_on_the_default_branch_still_give_the_hand_off_and_say_so(world):
    text = said(run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")), rules=None)[0])
    assert "Done: the hook." in text and "docs/agents/brain.md" in text
    assert ("brain_hook", "rules_missing", True) in errors(world)


def test_no_hand_off_issue_set_says_how_to_make_one(world, tmp_path):
    settings.save("repos", {})
    text = said(run(world, payload(transcript(tmp_path / "a.jsonl", "Brain: orch-it")))[0])
    assert "brain handoff ensure" in text
    [(detail,)] = world["store"].query("select detail from events where kind = 'handoff.read'").rows
    assert json.loads(detail)["found"] is False


@pytest.mark.parametrize("title", ["brain: orch it", "Brain:orch-it", "BRAIN - orch-it", "Brain: uECP",
                                   "Brainstorm the pricing page"])
def test_a_brain_like_title_that_is_not_this_repo_s_asks_for_the_rename(world, title):
    out, code = run(world, payload(transcript(world["tmp"] / "a.jsonl", title)))
    text = said(out)
    assert code == 0 and "`Brain: orch-it`" in text and "Russian" in text
    assert len(text.splitlines()) == 1 and "# The brain" not in text


@pytest.mark.parametrize("title, extra, head", [
    ("Fix the parser", {}, None),
    ("brain: orch it", {"agent_id": "a1", "agent_type": "worker"}, None),
    ("brain: orch it", {}, {"type": "summary", "sessionKind": "bg"}),
    ("Brain: orch-it", {"agent_id": "a1", "agent_type": "worker"}, None),
])
def test_another_title_a_subagent_or_a_background_session_gets_nothing(world, title, extra, head):
    path = transcript(world["tmp"] / "a.jsonl", title, head=head)
    assert run(world, payload(path, **extra)) == ("", 0)
    assert errors(world) == []


@pytest.mark.parametrize("raw, code", [
    ("", "payload_unreadable"),
    ("[]", "payload_unreadable"),
    (json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1"}), "transcript_unreadable"),
    (json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "transcript_path": "/no/such.jsonl"}),
     "transcript_unreadable"),
])
def test_what_cannot_be_read_lets_the_prompt_through_and_says_so(world, raw, code):
    assert run(world, raw) == ("", 0)
    assert errors(world) == [("brain_hook", code, True)]


def test_a_hand_off_that_cannot_be_read_still_gives_the_rules(world):
    world["hub"].failing.add((REPO, 5))
    text = said(run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")))[0])
    assert "# The brain: the owner's chat" in text
    assert ("brain_hook", "handoff_unreadable", True) in errors(world)


def test_the_rules_are_read_from_origin_s_default_branch_not_the_work_tree(tmp_path):
    def git(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("clone", str(origin), str(work), cwd=tmp_path)
    (work / "docs" / "agents").mkdir(parents=True)
    (work / "docs" / "agents" / "brain.md").write_bytes(RULES.encode("utf-8"))  # bytes: no newline conversion
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=work)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "rules", cwd=work)
    git("push", "origin", "main", cwd=work)
    (work / "docs" / "agents" / "brain.md").write_text("# A local edit not pushed\n", "utf-8")
    assert hook.rules_from_origin(str(work)) == RULES
    assert hook.rules_from_origin(str(tmp_path)) is None


def test_the_repo_s_settings_run_one_hook_at_session_start_and_on_every_prompt():
    wired = json.loads((Path(__file__).resolve().parents[1] / ".claude" / "settings.json").read_text("utf-8"))["hooks"]
    commands = {event: [h["command"] for entry in wired[event] for h in entry["hooks"]]
                for event in ("SessionStart", "UserPromptSubmit")}
    shim = 'sh "$CLAUDE_PROJECT_DIR"/hooks/orch-it -m orch_it hook brain'
    assert commands["UserPromptSubmit"] == [shim]
    assert shim in commands["SessionStart"]


def test_two_prompts_of_one_session_at_once_give_the_rules_once(world):
    import threading

    path = transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")
    outs = []
    threads = [threading.Thread(target=lambda: outs.append(said(run(world, payload(path))[0]))) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sum("# The brain: the owner's chat" in out for out in outs) == 1


def test_a_hand_off_that_could_not_be_read_is_tried_again_at_the_next_prompt(world):
    path = transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")
    world["hub"].failing.add((REPO, 5))
    run(world, payload(path))
    world["hub"].failing.clear()
    assert "Done: the hook." in said(run(world, payload(path))[0])


def test_broken_settings_still_find_the_brain_by_the_default_title_and_say_so(world, tmp_path):
    (tmp_path / "settings.json").write_text("{not json", "utf-8")
    text = said(run(world, payload(transcript(tmp_path / "a.jsonl", "Brain: orch-it")))[0])
    assert "# The brain: the owner's chat" in text and "settings cannot be read" in text
    assert ("brain_hook", "settings_unreadable", True) in errors(world)


def test_a_start_before_the_transcript_is_written_is_no_error(world, tmp_path):
    assert run(world, payload(tmp_path / "not-yet.jsonl", event="SessionStart", source="startup")) == ("", 0)
    assert errors(world) == []


def test_a_huge_title_is_cut_when_quoted_back(world):
    text = said(run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain " + "x" * 100_000)))[0])
    assert len(text) < 1000 and "`Brain: orch-it`" in text


def test_an_empty_session_id_still_writes_the_events(world):
    run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it"), session=""))
    assert world["store"].query("select count(*) from events where kind = 'handoff.read'").rows == [(1,)]


def test_a_broken_context_check_keeps_the_rules_already_given(world, monkeypatch):
    from orch_it.roles import context

    def broken(*_args):
        raise RuntimeError("broken")

    monkeypatch.setattr(context, "reminder", broken)
    text = said(run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")))[0])
    assert "# The brain: the owner's chat" in text and ("brain_hook", "RuntimeError", True) in errors(world)


def test_the_file_missing_at_origin_head_gives_the_read_it_whole_line_and_one_fallback(world):
    out, code = run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")), rules=None)
    text = said(out)
    assert code == 0 and READ_WHOLE in text and f"{HEAD_LINE}\n\n" not in text
    assert "work from the role file and say so" in text and "not on the default branch" not in text


def test_the_root_claude_md_carries_the_line_the_hook_gives():
    root = Path(__file__).resolve().parents[1]
    assert READ_WHOLE in (root / "CLAUDE.md").read_text("utf-8")


def test_rules_over_the_cap_give_the_read_it_whole_line(world):
    text = "# The brain\n\n" + "x" * hook.RULES_CHARS
    out, code = run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")), rules=text)
    assert code == 0 and "[cut at" in said(out) and READ_WHOLE in said(out)


def test_git_failing_gives_the_read_it_whole_line(world, monkeypatch):
    def no_git(*_args, **_kwargs):
        raise OSError("git is not installed")

    monkeypatch.setattr(hook.subprocess, "run", no_git)
    path = transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")
    out, code = hook.run(payload(path), store=world["store"], repo=REPO, hub=world["hub"], marks=world["marks"])
    assert code == 0 and READ_WHOLE in said(out)


def test_a_hook_that_raises_for_the_brain_still_gives_the_read_it_whole_line(world, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(hook, "_brain", broken)
    out, code = run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")))
    assert code == 0 and READ_WHOLE in said(out) and ("brain_hook", "RuntimeError", True) in errors(world)


@pytest.mark.parametrize("owner, name", [(hook.session, "is_brain"), (hook, "_subagent")])
def test_a_raise_before_the_session_is_known_to_be_the_brain_gives_no_line(world, monkeypatch, owner, name):
    def broken(*_args):
        raise RuntimeError("broken")

    monkeypatch.setattr(owner, name, broken)
    out, code = run(world, payload(transcript(world["tmp"] / "a.jsonl", "Fix the parser")))
    assert (out, code) == ("", 0) and ("brain_hook", "RuntimeError", True) in errors(world)


def test_the_repo_s_brain_rules_fit_the_cap_or_the_read_it_whole_line_is_given(world):
    text = (Path(__file__).resolve().parents[1] / hook.RULES_FILE).read_text("utf-8")
    got = said(run(world, payload(transcript(world["tmp"] / "a.jsonl", "Brain: orch-it")), rules=text)[0])
    if len(text) <= hook.RULES_CHARS:
        assert text in got and "[cut at" not in got
    else:
        assert READ_WHOLE in got
