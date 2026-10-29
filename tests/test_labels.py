"""The label set shipped with orch-it and `labels sync` (#398 decision 6; acceptance row 6): a repo's labels
match the set of the new layer, a label that is not in the set is listed and kept. `gh` is a fake
that holds a repo's labels the way GitHub does (names compare without regard to case) and records
every call, so "changes nothing" is read off the call list."""

import json
from urllib.parse import quote

import pytest

from orch_it import cli, labels
from orch_it.cli import CommandError, main
from orch_it.labels import LabelError, label_set, sync
from orch_it.testruns import github
from orch_it.testruns.github import GithubError

REPO = "acme-x/widgets-y"
READ_URL = f"repos/{REPO}/labels?per_page=100"
NAMES = ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix", "parked",
         "priority", "brain-handoff"]
DEAD = ["autopilot", "autopilot-stuck", "provisional", "hard-push", "flow-fix"]


class FakeGh:
    """Serves and changes the labels of one repo as `gh api` would."""

    def __init__(self, labels=(), *, page_size=None):
        self.labels = [dict(label) for label in labels]
        self.page_size = page_size
        self.calls = []
        self.fail_create = None  # an exception to raise on a POST (of `fail_name` only, if set)
        self.fail_name = None
        self.create_first = None  # a label another sync creates just before our POST
        self.patch_failures = []  # exceptions to raise on the next PATCHes, one each
        self.before_write = None  # run once before the next write: another actor changes the repo

    def writes(self):
        return [c for c in self.calls if "-X" in c]

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["api", "-X"]:
            return self.write(args)
        assert args == ["api", READ_URL, "--paginate"], args
        size = self.page_size or max(len(self.labels), 1)
        pages = [self.labels[i:i + size] for i in range(0, len(self.labels), size)] or [[]]
        return "".join(json.dumps(page) for page in pages)  # concatenated, as --paginate prints them

    def find(self, name):
        return next((label for label in self.labels if label["name"].lower() == name.lower()), None)

    def write(self, args):
        method, url = args[2], args[3]
        if self.before_write:
            change, self.before_write = self.before_write, None
            change(self)
        fields = dict(part.split("=", 1) for part in args[4:] if part != "-f")
        assert args[4::2] == ["-f"] * len(args[4::2])
        if method == "POST":
            assert url == f"repos/{REPO}/labels"
            if self.fail_create and self.fail_name in (None, fields["name"]):
                if self.create_first:
                    self.labels.append(self.create_first)
                    self.create_first = None
                raise self.fail_create
            if self.find(fields["name"]):
                raise GithubError("gh_failed", "already_exists")
            self.labels.append({"name": fields["name"], "color": fields["color"],
                                "description": fields["description"]})
            return "{}"
        assert method == "PATCH", method
        if self.patch_failures:
            raise self.patch_failures.pop(0)
        label = next((held for held in self.labels
                      if url == f"repos/{REPO}/labels/{quote(held['name'], safe='')}"), None)
        if label is None:
            raise GithubError("gh_failed", "404")
        label["name"] = fields["new_name"]
        label["color"] = fields["color"]
        label["description"] = fields["description"]
        return "{}"


def state(gh):
    return {label["name"]: (label["color"], label["description"]) for label in gh.labels}


def rows(done):
    return dict(done)


def test_the_label_set_is_exactly_the_eight_names_in_order():
    assert list(label_set()) == NAMES


def test_the_label_set_holds_valid_colours_and_short_descriptions():
    for name, label in label_set().items():
        assert sorted(label) == ["color", "description"], name
        assert len(label["color"]) == 6 and label["color"] == label["color"].lower(), name
        int(label["color"], 16)
        assert 0 < len(label["description"]) <= 100, name


def test_the_colours_are_those_of_the_decision():
    assert {name: label["color"] for name, label in label_set().items()} == {
        "needs-triage": "fbca04", "needs-info": "d4c5f9", "ready-for-agent": "0e8a16",
        "ready-for-human": "1d76db", "wontfix": "ffffff", "parked": "c5def5", "priority": "d93f0b",
        "brain-handoff": "5319e7"}


def test_the_label_set_carries_none_of_the_dead_labels():
    assert not set(DEAD) & {name.lower() for name in label_set()}


def test_the_label_file_is_shipped_as_package_data():
    text = (labels.LABELS_FILE.parents[3] / "pyproject.toml").read_text("utf-8")
    assert "labels/*.json" in text


def test_a_first_sync_on_an_empty_repo_creates_all_eight():
    gh = FakeGh()
    done = sync(REPO, gh=gh)
    assert done == [(name, "created") for name in NAMES]
    assert state(gh) == {name: (want["color"], want["description"])
                          for name, want in label_set().items()}
    assert [c[3] for c in gh.writes()] == [f"repos/{REPO}/labels"] * len(NAMES)
    assert all(c[2] == "POST" for c in gh.writes())


def test_a_create_sends_name_colour_and_description():
    gh = FakeGh()
    sync(REPO, gh=gh)
    first = gh.writes()[0]
    want = label_set()["needs-triage"]
    assert first == ["api", "-X", "POST", f"repos/{REPO}/labels", "-f", "name=needs-triage",
                     "-f", f"color={want['color']}", "-f", f"description={want['description']}"]


def test_a_second_sync_changes_nothing():
    gh = FakeGh()
    sync(REPO, gh=gh)
    before = len(gh.writes())
    again = sync(REPO, gh=gh)
    assert again == [(name, "kept") for name in NAMES]
    assert len(gh.writes()) == before  # no POST and no PATCH


def test_a_foreign_label_is_listed_and_never_written():
    gh = FakeGh([{"name": "bug", "color": "d73a4a", "description": "Something is broken"}])
    done = sync(REPO, gh=gh)
    assert done[-1] == ("bug", "foreign") and len(done) == len(NAMES) + 1
    assert all("bug" not in " ".join(c) for c in gh.writes())
    assert state(gh)["bug"] == ("d73a4a", "Something is broken")
    assert not any(c[2] in ("PATCH", "DELETE") for c in gh.writes())


def test_foreign_labels_come_after_the_set_sorted_by_name():
    gh = FakeGh([{"name": n, "color": "000000", "description": ""} for n in ("zeta", "Alpha", "beta")])
    done = sync(REPO, gh=gh)
    assert [name for name, _ in done[len(NAMES):]] == ["Alpha", "beta", "zeta"]
    assert {action for _, action in done[len(NAMES):]} == {"foreign"}


@pytest.mark.parametrize("name", ["good first: issue", "type: bug", "help wanted", ":+1:", "a/b"])
def test_a_foreign_name_with_a_colon_or_space_is_listed_and_untouched(name):
    gh = FakeGh([{"name": name, "color": "ededed", "description": None}])
    done = sync(REPO, gh=gh)
    assert (name, "foreign") in done
    assert all(name not in " ".join(c) and quote(name, safe="") not in " ".join(c) for c in gh.writes())


def test_a_set_label_with_another_colour_is_updated():
    gh = FakeGh([{"name": "parked", "color": "000000", "description": label_set()["parked"]["description"]}])
    done = rows(sync(REPO, gh=gh))
    assert done["parked"] == "updated"
    assert state(gh)["parked"] == (label_set()["parked"]["color"], label_set()["parked"]["description"])


def test_a_set_label_with_another_description_is_updated():
    gh = FakeGh([{"name": "wontfix", "color": "ffffff", "description": "This will not be worked on"}])
    assert rows(sync(REPO, gh=gh))["wontfix"] == "updated"
    assert state(gh)["wontfix"][1] == label_set()["wontfix"]["description"]


def test_a_set_label_in_another_letter_case_is_renamed_not_created():
    old = label_set()["priority"]
    gh = FakeGh([{"name": "Priority", "color": old["color"], "description": old["description"]}])
    done = rows(sync(REPO, gh=gh))
    assert done["priority"] == "updated"
    assert "priority" in state(gh) and "Priority" not in state(gh)
    patch = [c for c in gh.writes() if c[2] == "PATCH"]
    assert len(patch) == 1 and patch[0][3] == f"repos/{REPO}/labels/Priority"
    assert "new_name=priority" in patch[0]
    assert not any(c[2] == "POST" and "name=priority" in c for c in gh.writes())


def test_a_persons_own_label_in_another_case_and_colour_is_reported_updated():
    gh = FakeGh([{"name": "Priority", "color": "000000", "description": "mine"}])
    done = sync(REPO, gh=gh)
    assert done[list(label_set()).index("priority")] == ("priority", "updated")
    assert state(gh)["priority"] == (label_set()["priority"]["color"], label_set()["priority"]["description"])
    assert "Priority" not in state(gh) and ("Priority", "foreign") not in done


def test_a_colour_with_a_hash_or_capitals_equals_the_set_colour():
    want = label_set()["needs-info"]
    gh = FakeGh([{"name": "needs-info", "color": "#" + want["color"].upper(),
                  "description": want["description"]}])
    assert rows(sync(REPO, gh=gh))["needs-info"] == "kept"
    assert all("needs-info" not in " ".join(c) for c in gh.writes())


def test_a_null_description_from_github_equals_an_empty_one_and_differs_from_text():
    gh = FakeGh([{"name": "needs-info", "color": "d4c5f9", "description": None}])
    assert rows(sync(REPO, gh=gh))["needs-info"] == "updated"  # the set has text; null is ""
    assert state(gh)["needs-info"][1] == label_set()["needs-info"]["description"]


def test_a_missing_description_key_is_read_as_empty():
    gh = FakeGh([{"name": "bug", "color": "d73a4a"}])
    assert sync(REPO, gh=gh)[-1] == ("bug", "foreign")


def test_an_empty_label_list_creates_everything_and_lists_nothing_foreign():
    gh = FakeGh()
    done = sync(REPO, gh=gh)
    assert [action for _, action in done] == ["created"] * len(NAMES)


def test_concatenated_pages_are_all_read():
    existing = [{"name": f"extra-{i}", "color": "ededed", "description": ""} for i in range(5)]
    set_labels = [{"name": name, "color": want["color"], "description": want["description"]}
                  for name, want in label_set().items()]
    gh = FakeGh(existing + set_labels, page_size=2)
    done = sync(REPO, gh=gh)
    assert [a for _, a in done[:len(NAMES)]] == ["kept"] * len(NAMES)
    assert [n for n, _ in done[len(NAMES):]] == [f"extra-{i}" for i in range(5)]
    assert gh.writes() == []


def test_a_create_that_failed_because_the_label_appeared_is_kept_or_updated():
    want = label_set()["parked"]
    gh = FakeGh()
    gh.create_first = {"name": "parked", "color": want["color"], "description": want["description"]}
    gh.fail_create, gh.fail_name = GithubError("gh_failed", "422"), "parked"
    done = rows(sync(REPO, gh=gh))
    assert done["parked"] == "kept"  # the other sync made it right
    assert [c for c in gh.calls if c[:3] == ["api", "-X", "POST"]]


def test_a_create_that_failed_because_the_label_appeared_with_other_text_is_updated():
    gh = FakeGh()
    gh.create_first = {"name": "parked", "color": "000000", "description": "mine"}
    gh.fail_create, gh.fail_name = GithubError("gh_failed", "422"), "parked"
    assert rows(sync(REPO, gh=gh))["parked"] == "updated"
    assert state(gh)["parked"] == (label_set()["parked"]["color"], label_set()["parked"]["description"])


def test_a_create_that_failed_and_left_no_label_raises_the_original_error():
    gh = FakeGh()
    gh.fail_create = GithubError("gh_failed", "boom")
    with pytest.raises(GithubError) as error:
        sync(REPO, gh=gh)
    assert error.value is gh.fail_create


def test_a_patch_that_failed_because_the_label_was_renamed_creates_it_from_what_is_there_now():
    gh = FakeGh([{"name": "parked", "color": "000000", "description": "mine"}])

    def rename(fake):
        fake.labels[0]["name"] = "parked-old"

    gh.before_write = rename
    gh.patch_failures = [GithubError("gh_failed", "404")]
    done = sync(REPO, gh=gh)
    assert rows(done)["parked"] == "created" and "parked-old" in state(gh)  # nothing is deleted
    assert state(gh)["parked"] == (label_set()["parked"]["color"], label_set()["parked"]["description"])


def test_a_patch_that_failed_because_another_sync_made_the_label_right_keeps_it():
    want = label_set()["parked"]
    gh = FakeGh([{"name": "parked", "color": "000000", "description": "mine"}])

    def fix(fake):
        fake.labels[0].update(color=want["color"], description=want["description"])

    gh.before_write = fix
    gh.patch_failures = [GithubError("gh_failed", "422")]
    assert rows(sync(REPO, gh=gh))["parked"] == "kept"
    assert len([c for c in gh.writes() if c[2] == "PATCH"]) == 1  # the failed one; no second


def test_a_patch_that_failed_on_a_label_still_wrong_is_tried_once_more():
    gh = FakeGh([{"name": "parked", "color": "000000", "description": "mine"}])
    gh.patch_failures = [GithubError("gh_failed", "502")]
    assert rows(sync(REPO, gh=gh))["parked"] == "updated"
    assert len([c for c in gh.calls if c[:3] == ["api", "-X", "PATCH"]]) == 2


def test_a_second_patch_failure_raises():
    gh = FakeGh([{"name": "parked", "color": "000000", "description": "mine"}])
    second = GithubError("gh_failed", "second")
    gh.patch_failures = [GithubError("gh_failed", "first"), second]
    with pytest.raises(GithubError) as error:
        sync(REPO, gh=gh)
    assert error.value is second
    assert len([c for c in gh.calls if c[:3] == ["api", "-X", "PATCH"]]) == 2


def test_a_gh_failure_while_reading_gives_its_code():
    def broken(args):
        raise GithubError("gh_failed", "no network")

    with pytest.raises(GithubError) as error:
        sync(REPO, gh=broken)
    assert error.value.code == "gh_failed"


@pytest.mark.parametrize("text", ["", " \n", "not json", '{"a": 1}', '[1, 2]', '[{"color": "ffffff"}]'])
def test_an_answer_that_is_no_list_of_labels_gives_gh_bad_output(text):
    with pytest.raises(GithubError) as error:
        sync(REPO, gh=lambda args: text)
    assert error.value.code == "gh_bad_output"


@pytest.mark.parametrize("repo", ["orch-it", "a/b/c", "", "a b/c"])
def test_a_bad_repo_is_refused_before_gh_is_called(repo):
    gh = FakeGh()
    with pytest.raises(GithubError) as error:
        sync(repo, gh=gh)
    assert error.value.code == "bad_repo" and gh.calls == []


def test_the_default_runner_is_the_github_one(monkeypatch):
    gh = FakeGh()
    monkeypatch.setattr(github, "run_gh", gh)
    assert len(sync(REPO)) == len(NAMES)


# a bad label file

GOOD = {"a": {"color": "ffffff", "description": "x"}}


@pytest.mark.parametrize("text", [
    "", "not json", "[]", "{}", '"x"',
    json.dumps({"a": "ffffff"}),
    json.dumps({"a": {"color": "ffffff"}}),
    json.dumps({"a": {"color": "FFFFFF", "description": "x"}}),
    json.dumps({"a": {"color": "#ffffff", "description": "x"}}),
    json.dumps({"a": {"color": "fff", "description": "x"}}),
    json.dumps({"a": {"color": "ffffff", "description": "x" * 101}}),
    json.dumps({"a": {"color": "ffffff", "description": 3}}),
    json.dumps({"a": {"color": "ffffff", "description": "x", "extra": 1}}),
    json.dumps({"": {"color": "ffffff", "description": "x"}}),
    json.dumps({"a": GOOD["a"], "A": GOOD["a"]}),
    json.dumps({"n" * 51: GOOD["a"]}),
    json.dumps({"a,b": GOOD["a"]}),
    json.dumps({" a": GOOD["a"]}),
    json.dumps({"a ": GOOD["a"]}),
    json.dumps({"a": {"color": "ffffff", "description": "two\nlines"}}),
    json.dumps({"a": {"color": "ffffff", "description": "two\rlines"}}),
    '{"a": {"color": "ffffff", "description": "x"}, "a": {"color": "000000", "description": "y"}}',
    '{"a": {"color": "ffffff", "color": "000000", "description": "x"}}',
])
def test_a_bad_label_file_gives_labels_unreadable(tmp_path, monkeypatch, text):
    bad = tmp_path / "labels.json"
    bad.write_text(text, encoding="utf-8")
    monkeypatch.setattr(labels, "LABELS_FILE", bad)
    with pytest.raises(LabelError) as error:
        label_set()
    assert error.value.code == "labels_unreadable"


def test_a_name_of_fifty_characters_and_a_description_of_a_hundred_are_accepted(tmp_path, monkeypatch):
    edge = tmp_path / "labels.json"
    edge.write_text(json.dumps({"n" * 50: {"color": "ffffff", "description": "d" * 100}}), encoding="utf-8")
    monkeypatch.setattr(labels, "LABELS_FILE", edge)
    assert list(label_set()) == ["n" * 50]


def test_a_missing_label_file_gives_labels_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "LABELS_FILE", tmp_path / "nope.json")
    with pytest.raises(LabelError) as error:
        label_set()
    assert error.value.code == "labels_unreadable"


def test_a_sync_with_a_bad_label_file_calls_gh_never(tmp_path, monkeypatch):
    bad = tmp_path / "labels.json"
    bad.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(labels, "LABELS_FILE", bad)
    gh = FakeGh()
    with pytest.raises(LabelError):
        sync(REPO, gh=gh)
    assert gh.calls == []


def test_label_error_refuses_an_unknown_code():
    with pytest.raises(ValueError):
        LabelError("something_else")


# the command


def test_the_command_prints_the_table(monkeypatch, capsys):
    gh = FakeGh([{"name": "bug", "color": "d73a4a", "description": "x"}])
    monkeypatch.setattr(github, "run_gh", gh)
    assert main(["labels", "sync", "--repo", REPO]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["label", "action"]
    assert [line.split() for line in lines[1:]] == [[n, "created"] for n in NAMES] + [["bug", "foreign"]]
    assert lines[1].index("created") == lines[0].index("action")  # columns are aligned


def test_the_command_defaults_to_the_origin_of_the_work_tree(monkeypatch, capsys):
    gh = FakeGh()
    monkeypatch.setattr(github, "run_gh", gh)
    monkeypatch.setattr(cli, "_repo_of", lambda work_tree: REPO if work_tree == "." else "x/y")
    assert main(["labels", "sync"]) == 0
    assert gh.calls[0] == ["api", READ_URL, "--paginate"]


def test_the_command_without_a_repo_and_origin_says_repo_unknown(monkeypatch, capsys):
    def none(work_tree):
        raise CommandError("repo_unknown", work_tree)

    monkeypatch.setattr(cli, "_repo_of", none)
    assert main(["labels", "sync"]) == 2
    assert capsys.readouterr().err.startswith("error: repo_unknown")


def test_the_command_prints_a_gh_failure_as_its_code(monkeypatch, capsys):
    def broken(args):
        raise GithubError("gh_failed", "no network")

    monkeypatch.setattr(github, "run_gh", broken)
    assert main(["labels", "sync", "--repo", REPO]) == 2
    assert capsys.readouterr().err.startswith("error: gh_failed")


def test_the_command_prints_a_bad_label_file_as_its_code(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "labels.json"
    bad.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(labels, "LABELS_FILE", bad)
    monkeypatch.setattr(github, "run_gh", FakeGh())
    assert main(["labels", "sync", "--repo", REPO]) == 2
    assert capsys.readouterr().err.startswith("error: labels_unreadable")


def test_the_command_refuses_a_bad_repo(monkeypatch, capsys):
    monkeypatch.setattr(github, "run_gh", FakeGh())
    assert main(["labels", "sync", "--repo", "nope"]) == 2
    assert capsys.readouterr().err.startswith("error: bad_repo")
