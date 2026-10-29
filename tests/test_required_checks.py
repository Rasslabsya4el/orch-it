"""The required check names (the setting `required_checks`, read by one reader in `testruns/config.py`): `guards
status`, the merge line, `follow` and the importer of the tests workflow take the names from the setting. The
settings file names the checks `unit` and `lint`; each consumer is driven through its public function. A grep of
`src/orch_it` fails when a check name is written as a literal in code (the setting's default lives in its defaults
file, not in code)."""

import json
import re
from pathlib import Path

import pytest

from fake_github import FAILED, PASSED, FakeGitHub
from follow_world import NO_AFTER_MERGE, NOW, ROWS_NO_AFTER, TITLE

from orch_it.cli import main
from orch_it.events.store import Store
from orch_it.guards import status
from orch_it.merge import commands, lock
from orch_it.settings import SettingsError
from orch_it.testruns import config, github

REPO = "owner-x/repo-y"
NAMES = ["unit", "lint"]
SRC = Path(__file__).resolve().parents[1] / "src" / "orch_it"


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"required_checks": NAMES, "merge": {"check_minutes": 0.02, "poll_seconds": 1}}),
                    "utf-8")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    return path


@pytest.fixture
def hub(tmp_path, monkeypatch, settings_file):
    hub = FakeGitHub(NOW)
    hub.add(10, NO_AFTER_MERGE, ["ready-for-agent"], title=TITLE)
    hub.branches["ticket-10"] = [*hub.branches["main"], "t1"]
    hub.required = tuple(NAMES)  # the checks the fake's branch protection holds a merge to
    monkeypatch.setattr(commands, "GhPullHub", lambda: hub)
    monkeypatch.setattr(lock.installation, "data_folder", lambda: tmp_path / "data")

    def git(args):
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "ticket-10\n"
        return f"{hub.branches[args[-1].removesuffix('^{commit}')][-1]}\n" if args[:1] == ["rev-parse"] else ""

    monkeypatch.setattr(commands.merge_git, "work_tree", lambda work_tree: git)
    return hub


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def open_pull(hub, tmp_path, capsys):
    rows = tmp_path / "rows.md"
    rows.write_text(ROWS_NO_AFTER, "utf-8")
    run(capsys, "--store", str(tmp_path / "events"), "pr", "open", "10", "--acceptance", str(rows), "--repo", REPO)
    return max(hub.pulls)


def protection(names):
    return {"required_status_checks": {"strict": True, "contexts": list(names), "checks": []},
            "enforce_admins": {"enabled": True}, "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False}}


def test_the_setting_names_the_checks_and_the_default_is_the_defaults_file(settings_file):
    assert config.required_checks(json.loads(settings_file.read_text("utf-8")), REPO) == tuple(NAMES)
    assert config.required_checks({}, REPO) == config.DEFAULT_REQUIRED_CHECKS
    raw = {"required_checks": NAMES, "repos": {"Owner-X/Repo-Y": {"required_checks": ["lint"]}}}
    assert config.required_checks(raw, REPO) == ("lint",)
    with pytest.raises(SettingsError) as error:
        config.required_checks({"required_checks": []}, REPO)
    assert error.value.code == "bad_setting"


def test_the_old_setting_name_is_refused_and_names_its_replacement():
    with pytest.raises(SettingsError) as error:
        config.required_checks({"github_required_jobs": list(config.DEFAULT_REQUIRED_CHECKS)}, REPO)
    assert error.value.code == "bad_setting"
    assert str(error.value) == "bad_setting: github_required_jobs replaced by required_checks"


def test_guards_status_holds_the_protection_to_the_setting(settings_file, tmp_path):
    store = Store.open(tmp_path / "store")

    def required_fact(names):
        report = status.status(store, REPO, tmp_path, protection=lambda repo: protection(names))
        return [fact.ok for fact in report.facts if fact.name == "required_checks"]

    assert required_fact(NAMES) == [True]
    assert required_fact(config.DEFAULT_REQUIRED_CHECKS) == [False]


def test_the_merge_line_merges_on_the_setting_checks(hub, tmp_path, capsys):
    pr = open_pull(hub, tmp_path, capsys)
    hub.checks_on(hub.head(pr), PASSED, names=NAMES)
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "merge", "line", "--repo", REPO)
    assert (code, err) == (0, "")
    assert out.splitlines()[-1].startswith("line of owner-x/repo-y into main: merged=1 left=0")


def test_follow_reads_the_setting_checks(hub, tmp_path, capsys):
    pr = open_pull(hub, tmp_path, capsys)
    hub.checks_on(hub.head(pr), FAILED, names=NAMES, failing=["t::a"])
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": pr}}), "utf-8")
    code, out, err = run(capsys, "--store", str(tmp_path / "events"), "follow", "--event", str(event), "--repo", REPO)
    assert (code, out, err) == (0, f"#{pr} red ticket=#10 1 tests\n", "")


class RunsGh:
    """What `gh` answers for one green run of the tests workflow with one job per name given."""

    def __init__(self, names):
        self.names = names

    def __call__(self, args):
        path = args[-1].partition("?")[0]
        if path.endswith(f"/workflows/{github.WORKFLOW_FILE}/runs"):
            run_row = {"id": 100, "status": "completed", "conclusion": "success", "event": "pull_request",
                       "head_branch": "feat/x", "head_sha": "sha100", "run_attempt": 1,
                       "created_at": "2026-10-26T10:00:00Z", "updated_at": "2026-10-26T10:05:00Z",
                       "pull_requests": [{"number": 7}]}
            return json.dumps({"total_count": 1, "workflow_runs": [run_row]})
        last = path.split("/")[-1]
        if last == "jobs":
            jobs = [{"id": 1000 + n, "name": name, "status": "completed", "conclusion": "success",
                     "run_attempt": 1, "labels": [], "created_at": "2026-10-26T10:00:00Z",
                     "started_at": "2026-10-26T10:00:30Z", "completed_at": "2026-10-26T10:03:00Z", "steps": []}
                    for n, name in enumerate(self.names)]
            return json.dumps({"total_count": len(jobs), "jobs": jobs})
        if last == "artifacts":
            return json.dumps({"total_count": 0, "artifacts": []})
        if last == "timing":
            return json.dumps({"billable": {}})
        raise AssertionError(args)


def test_the_importer_writes_a_pair_per_setting_check(settings_file, tmp_path):
    store = Store.open(tmp_path / "store")
    github.import_github(store, REPO, gh=RunsGh(NAMES), now=NOW)
    outcomes = sorted(json.loads(detail)["outcome"] for (detail,) in store.query(
        "SELECT detail FROM events WHERE kind = 'step.ended' AND step = 'test_run'").rows)
    assert outcomes == ["passed", "passed"]


SOURCE_LITERAL = re.compile(r"""["'](linux|windows)["']""")
CHECK_WORD = re.compile(r"check|job|required|workflow", re.IGNORECASE)


def test_no_src_line_writes_an_os_name_as_a_check_name():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            if SOURCE_LITERAL.search(line) and CHECK_WORD.search(line):
                found.append(f"{path.relative_to(SRC)}:{number}")
    assert found == []
