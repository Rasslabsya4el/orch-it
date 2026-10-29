"""The brain's hand-off home (orchestration-layer#403 decision 3; acceptance row 7): one pinned issue
per repo, title `Brain hand-off`, label `brain-handoff`, its number a setting; `brain handoff ensure`
creates and pins it when missing and changes nothing on a second run; a hand-off is a comment on it,
the newest one is what the next chat reads; the ready and sweep queues never return it."""

import json

import pytest

from fake_hub import REPO, FakeHub
from orch_it import settings
from orch_it.brain import BrainSettings, handoff
from orch_it.events.store import Store


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    return path


def test_the_brain_chat_title_defaults_to_the_repo_s_name_and_a_repo_may_set_its_own():
    assert BrainSettings.of({}, "owner-x/orch-it").chat_title == "Brain: orch-it"
    raw = {"repos": {"Owner-X/Orch-It": {"brain_chat_title": "Brain: orch layer"}}}
    assert BrainSettings.of(raw, "owner-x/orch-it").chat_title == "Brain: orch layer"
    assert BrainSettings.of({}, "owner-x/orch-it").handoff_issue is None


@pytest.mark.parametrize("raw", [
    {"repos": {"owner-x/orch-it": {"brain_chat_title": ""}}},
    {"repos": {"owner-x/orch-it": {"brain_chat_title": 7}}},
    {"repos": {"owner-x/orch-it": {"brain_chat_title": "Brain:\norch-it"}}},
    {"repos": {"owner-x/orch-it": {"brain_handoff_issue": 0}}},
    {"repos": {"owner-x/orch-it": {"brain_handoff_issue": True}}},
    {"repos": {"owner-x/orch-it": {"brain_handoff_issue": "12"}}},
    {"repos": {"owner-x/orch-it": {}, "OWNER-X/orch-it": {}}},
    {"repos": []},
])
def test_a_bad_brain_setting_is_refused_by_name(raw):
    with pytest.raises(settings.SettingsError) as refused:
        BrainSettings.of(raw, "owner-x/orch-it")
    assert refused.value.code == "bad_setting"


def test_ensure_creates_one_pinned_labelled_issue_and_writes_the_setting(settings_file):
    hub = FakeHub()
    hub.add(1, "", ["needs-triage"])
    done = handoff.ensure(hub, REPO)
    [made] = [i for i in hub.issues.values() if i.title == handoff.TITLE]
    assert made.labels == (handoff.LABEL,) and made.open
    assert hub.pins[REPO] == [made.number]
    assert BrainSettings.of(settings.load(), REPO).handoff_issue == made.number
    assert done.number == made.number and done.actions == ("created", "pinned", "setting_written")


def test_a_second_ensure_changes_nothing(settings_file):
    hub = FakeHub()
    handoff.ensure(hub, REPO)
    before = (dict(hub.issues), dict(hub.pins), settings_file.read_text("utf-8"), list(hub.calls))
    done = handoff.ensure(hub, REPO)
    assert done.actions == ()
    assert (dict(hub.issues), dict(hub.pins), settings_file.read_text("utf-8")) == before[:3]
    assert [c for c in hub.calls[len(before[3]):] if c[0] in hub.WRITES] == []


def test_ensure_finds_the_labelled_issue_when_the_setting_is_lost(settings_file):
    hub = FakeHub()
    hub.add(9, "", [handoff.LABEL], title=handoff.TITLE)
    done = handoff.ensure(hub, REPO)
    assert done.number == 9 and done.actions == ("pinned", "setting_written")
    assert len(hub.issues) == 1


def test_ensure_mends_a_set_issue_that_lost_its_label_or_pin(settings_file):
    hub = FakeHub()
    hub.add(4, "", [], title=handoff.TITLE)
    settings.save("repos", {REPO: {"brain_handoff_issue": 4}})
    done = handoff.ensure(hub, REPO)
    assert done.number == 4 and done.actions == ("labelled", "pinned")
    assert hub.issues[(REPO, 4)].labels == (handoff.LABEL,)


def test_ensure_replaces_a_set_issue_that_is_closed_or_gone(settings_file):
    hub = FakeHub()
    hub.add(4, "", [handoff.LABEL], title=handoff.TITLE)
    hub.close(4)
    settings.save("repos", {REPO: {"brain_handoff_issue": 4, "brain_chat_title": "Brain: x"}})
    done = handoff.ensure(hub, REPO)
    assert done.number != 4 and "created" in done.actions
    kept = settings.load()["repos"][REPO]
    assert kept == {"brain_handoff_issue": done.number, "brain_chat_title": "Brain: x"}


def test_two_open_labelled_issues_take_the_oldest_and_say_so(settings_file):
    hub = FakeHub()
    hub.add(7, "", [handoff.LABEL], title=handoff.TITLE)
    hub.add(3, "", [handoff.LABEL], title=handoff.TITLE)
    done = handoff.ensure(hub, REPO)
    assert done.number == 3 and done.others == (7,)


def test_the_newest_hand_off_comment_is_the_one_read():
    hub = FakeHub()
    hub.add(5, "", [handoff.LABEL], title=handoff.TITLE)
    assert handoff.latest(hub, REPO, 5) is None
    hub.say(5, "first hand-off")
    hub.later(minutes=5)
    hub.say(5, "second hand-off")
    hub.later(minutes=5)
    assert handoff.latest(hub, REPO, 5).body == "second hand-off"


def test_write_posts_the_hand_off_points_the_ticket_back_and_counts_it(settings_file, tmp_path):
    hub = FakeHub()
    hub.add(5, "", [handoff.LABEL], title=handoff.TITLE)
    hub.add(40, "", ["ready-for-agent"])
    settings.save("repos", {REPO: {"brain_handoff_issue": 5}})
    store = Store.open(tmp_path / "store")
    handoff.write(hub, store, REPO, "Done: A. Next: B.", ticket=40)
    [said] = hub.notes[(REPO, 5)]
    assert said.body.startswith("Done: A. Next: B.") and "#40" in said.body
    [pointer] = hub.notes[(REPO, 40)]
    assert "#5" in pointer.body and len(pointer.body.splitlines()) == 1
    handoff.write(hub, store, REPO, "Done: B.", ticket=40)
    assert len(hub.notes[(REPO, 40)]) == 1 and len(hub.notes[(REPO, 5)]) == 2  # one pointer per ticket
    rows = store.query("select role, detail from events where kind = 'handoff.written'").rows
    assert [(role, json.loads(detail)["entry_ref"]) for role, detail in rows] == [("brain", f"{REPO}#5")] * 2


def test_two_ensures_that_each_made_an_issue_converge_on_the_oldest(settings_file):
    hub = FakeHub()
    hub.add(1, "", [handoff.LABEL], title=handoff.TITLE)
    hub.add(2, "", [handoff.LABEL], title=handoff.TITLE)
    hub.pins[REPO] = [1, 2]
    settings.save_in_repo(REPO, "brain_handoff_issue", 2)
    done = handoff.ensure(hub, REPO)
    assert (done.number, done.actions, done.others) == (1, ("setting_written",), (2,))


def test_the_label_in_another_case_is_the_label(settings_file):
    hub = FakeHub()
    hub.add(1, "", ["Brain-Handoff"], title=handoff.TITLE)
    hub.pins[REPO] = [1]
    settings.save_in_repo(REPO, "brain_handoff_issue", 1)
    assert handoff.ensure(hub, REPO).actions == ()


def test_of_two_hand_offs_in_one_second_the_later_one_is_read():
    hub = FakeHub()
    hub.add(5, "", [handoff.LABEL], title=handoff.TITLE)
    hub.say(5, "first")
    hub.say(5, "second")
    assert handoff.latest(hub, REPO, 5).body == "second"


def test_two_writers_of_the_settings_at_once_both_land(settings_file):
    import threading

    def write(repo):
        for n in range(1, 21):
            settings.save_in_repo(repo, "brain_handoff_issue", n)

    writers = [threading.Thread(target=write, args=(f"owner-x/r{i}",)) for i in range(4)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=60)
    assert {repo: own["brain_handoff_issue"] for repo, own in settings.load()["repos"].items()} == {
        f"owner-x/r{i}": 20 for i in range(4)}


def test_write_refuses_with_no_hand_off_issue_set_and_an_empty_text(settings_file, tmp_path):
    hub = FakeHub()
    store = Store.open(tmp_path / "store")
    with pytest.raises(handoff.HandoffError) as refused:
        handoff.write(hub, store, REPO, "text")
    assert refused.value.code == "handoff_issue_unset"
    settings.save("repos", {REPO: {"brain_handoff_issue": 5}})
    hub.add(5, "", [handoff.LABEL], title=handoff.TITLE)
    with pytest.raises(handoff.HandoffError) as refused:
        handoff.write(hub, store, REPO, "  \n")
    assert refused.value.code == "handoff_empty"
    assert hub.notes == {}


def test_a_repo_without_the_label_gets_it_and_a_full_pin_row_is_refused_by_name(settings_file):
    hub = FakeHub()
    hub.repo_labels[REPO] = {"needs-triage"}
    hub.pins[REPO] = [101, 102, 103]
    with pytest.raises(handoff.HandoffError) as refused:
        handoff.ensure(hub, REPO)
    assert refused.value.code == "pin_refused"
    assert handoff.LABEL in hub.repo_labels[REPO]


def test_a_ticket_of_another_title_is_never_taken_for_the_hand_off_issue(settings_file):
    hub = FakeHub()
    hub.add(2, "", [handoff.LABEL, "needs-triage"], title="Fix the parser")
    settings.save_in_repo(REPO, "brain_handoff_issue", 2)
    done = handoff.ensure(hub, REPO)
    assert done.number != 2 and "created" in done.actions and done.others == (2,)
    assert hub.issues[(REPO, 2)].labels == (handoff.LABEL, "needs-triage")


@pytest.mark.parametrize("ticket", [5, 77, 6])
def test_a_ticket_that_is_no_ticket_is_refused_before_anything_is_posted(settings_file, tmp_path, ticket):
    hub = FakeHub()
    hub.add(5, "", [handoff.LABEL], title=handoff.TITLE)
    hub.add(6, "", [], is_pr=True)
    settings.save_in_repo(REPO, "brain_handoff_issue", 5)
    with pytest.raises(handoff.HandoffError) as refused:
        handoff.write(hub, Store.open(tmp_path / "store"), REPO, "text", ticket=ticket)
    assert refused.value.code == "ticket_bad" and hub.notes == {}


def test_a_hand_off_over_github_s_comment_size_is_refused(settings_file, tmp_path):
    hub = FakeHub()
    hub.add(5, "", [handoff.LABEL], title=handoff.TITLE)
    settings.save_in_repo(REPO, "brain_handoff_issue", 5)
    with pytest.raises(handoff.HandoffError) as refused:
        handoff.write(hub, Store.open(tmp_path / "store"), REPO, "x" * 70_000)
    assert refused.value.code == "handoff_too_long" and hub.notes == {}
