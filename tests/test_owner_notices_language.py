"""The owner-facing texts of the red-on-main tickets, the workflow watch ticket and the skills update
issue take their words from the table of `human_language` (#413 decision 6, unit 10b).

Seams: the builders `red_main.build`, `red_main.build_no_result`, `workflow_watch.build` and `update.issue`,
each called with a small invented input. Languages: unset (English), `ru` (the owner's), a planted `de`
table in a tmp `language_tables_dir` (copies every key of en.toml), and `fr` (no table: refused when the
setting is read). Invented values only: repo `acme/example`, workflow `example-flow.yml`, run 4117.

The two builders of the red-on-main ticket and the watch ticket run the shape check (`violations`) after
they write; that check reads its language in unit 10a and is measured in its own tests. This file measures
the words, so the shape check is replaced by an empty one in its fixture.
"""

import json
import re
import tomllib
from pathlib import Path

import pytest

from language_tree import git_tree, write_language
from orch_it import settings
from orch_it.merge import red_main
from orch_it.merge import workflow_watch as watch
from orch_it.skills import update
from orch_it.skills.classify import Change
from orch_it.skills.copies import Manifest

CYRILLIC = re.compile("[Ѐ-ӿ]")
SRC = Path(settings.__file__).parent  # src/orch_it
PACKAGE_TABLES = SRC / "language_tables"
# the one file that still names Russian: unit 10a's (`tickets/shape.py`), allowed here by name until 10a lands
ALLOWED_CYRILLIC_FILES = {"tickets/shape.py"}

# the English the tables carry (written by this unit) and the Russian the code wrote before (the literals)
EN = {
    "red": {"title": "Main is red: 2 tests fail",
            "What": "main is red: 2 tests fail after the merge of pull request #12.",
            "Goal": "main is green again, and the pull requests that are red only on these tests go on by themselves.",
            "Plan": "fix or revert what broke these tests; which one to pick is decided by this ticket.",
            "Result": "the full test run on main is green; the ticket closes by itself on the first green run.",
            "Undo": "two-way door (one revert); touches each pull request that waits for this ticket."},
    "no_result": {"title": "Main's tests gave no result twice",
                  "What": "the tests on main gave no result twice in a row: the run was cut, cancelled or had no report.",
                  "Goal": "it is visible again whether main is green or red, and the pull requests that are red only on it are clear.",
                  "Plan": "the brain looks at why the run gave no answer (place, limit, report) and decides what to fix.",
                  "Result": "the next test run on main gives a result on both systems.",
                  "Undo": "two-way door (one revert); touches only the main check."},
    "watch": {"title": "Workflow example-flow did not end green",
              "What": "workflow `example-flow` did not end green: its latest run 4117 ended without a green result.",
              "Goal": "the latest run of `example-flow` is green again, and the ticket closes by itself on the first such run.",
              "Plan": "find why the run is not green and fix the cause; which one to fix is decided by this ticket.",
              "Result": "the latest run of `example-flow` is green.",
              "Undo": "two-way door (one revert); touches only the run of `example-flow`."},
    "skills": {"What": "Pocock's upstream of skills moved from `111111111111` to `222222222222`: changed skills: 1, of them copied into the repo: ",
               "Goal": "the copies of the skills in the repo match the new version of its upstream, and no role loses a callable skill silently.",
               "Plan": "ORCH under the owner's `gh` runs `python -m orch_it skills update`; it opens one pull request with the new copies, and the repo's tests check the copies and the references to the skills.",
               "Result": "the pull request is green on both systems, 100 % of the copies have hashes equal to the manifest of the new version, and every skill in it is assigned its class.",
               "Undo": "two-way door (one revert of the pull request brings back the previous copies); touches all roles that call these skills."},
    "skills_brain": {"Plan": "ORCH opens no pull request for this ticket: the brain decides each class from the table below, and `python -m orch_it skills update` prints `waits_for_brain` and moves nothing.",
                     "Result": "the brain wrote its decision for each class in this ticket, and no copy is moved without it."},
}
RU = {
    "red": {"title": "main красный: 2 теста падают",
            "What": "main красный: 2 теста падают после слияния пулл-реквеста #12.",
            "Goal": "main снова зелёный, и пулл-реквесты, красные только на этих тестах, идут дальше сами.",
            "Plan": "починить или откатить то, что сломало эти тесты; что выбрать, решает этот тикет.",
            "Result": "полный прогон тестов на main зелёный; тикет закрывается сам на первом зелёном прогоне.",
            "Undo": "two-way door (один откат); touches каждый пулл-реквест, который ждёт этот тикет."},
    "no_result": {"title": "тесты на main дважды не дали результата",
                  "What": "тесты на main дважды подряд не дали результата: прогон обрезан, отменён или без отчёта.",
                  "Goal": "снова видно, зелёный main или красный, и пулл-реквесты, красные только на нём, понятны.",
                  "Plan": "мозг смотрит, почему прогон не дал ответа (место, лимит, отчёт), и решает, что чинить.",
                  "Result": "следующий прогон тестов на main даёт результат на обеих системах.",
                  "Undo": "two-way door (один откат); touches только проверку main."},
    "watch": {"title": "Workflow example-flow не зелёный",
              "What": "workflow `example-flow` не завершился зелёным: последний прогон 4117 закончился без зелёного результата.",
              "Goal": "последний прогон `example-flow` снова зелёный, и тикет закрывается сам на первом таком прогоне.",
              "Plan": "разобрать, почему прогон не зелёный, и починить причину; что именно чинить, решает этот тикет.",
              "Result": "последний прогон `example-flow` зелёный.",
              "Undo": "two-way door (один откат); touches только запуск `example-flow`."},
    "skills": {"What": "апстрим навыков Покока перешёл с `111111111111` на `222222222222`: изменилось навыков: 1, из них скопировано в репо: ",
               "Goal": "копии навыков в репо совпадают с новой версией его апстрима, и ни одна роль не теряет вызываемый навык молча.",
               "Plan": "ORCH под `gh` владельца запускает `python -m orch_it skills update`; тот открывает один пулл-реквест с новыми копиями, а тесты репо проверяют копии и ссылки на навыки.",
               "Result": "пулл-реквест зелёный на обеих ОС, у 100 % копий хэши совпадают с манифестом новой версии, и каждый навык в нём отнесён к своему классу.",
               "Undo": "two-way door (один откат пулл-реквеста возвращает прежние копии); touches все роли, которые вызывают эти навыки."},
    "skills_brain": {"Plan": "ORCH не открывает пулл-реквест по этому тикету: мозг решает по каждому классу из таблицы ниже, а `python -m orch_it skills update` печатает `waits_for_brain` и ничего не переносит.",
                     "Result": "мозг записал своё решение по каждому классу в этом тикете, и ни одна копия не перенесена без него."},
}
# the planted German: every key of en.toml that this unit moved (the other keys are copied from en.toml)
DE = {
    "red_main_title": "Main ist rot: {count} {tests}",
    "red_main_tests_one": "Test schlägt fehl",
    "red_main_tests_few": "Tests schlagen fehl",
    "red_main_tests_many": "Tests schlagen fehl",
    "red_main_by_pr": "Pull-Request #{pr}",
    "red_main_by_commit": "einem Commit ohne Pull-Request",
    "red_main_what": "main ist rot: {count} {tests} nach dem Merge von {by}.",
    "red_main_goal": "main ist wieder grün, und die Pull-Requests, die nur auf diesen Tests rot sind, laufen von selbst weiter.",
    "red_main_plan": "reparieren oder zurücknehmen, was diese Tests gebrochen hat; welches, entscheidet dieses Ticket.",
    "red_main_result": "der vollständige Testlauf auf main ist grün; das Ticket schließt sich beim ersten grünen Lauf von selbst.",
    "red_main_undo": "two-way door (ein Rollback); touches jeden Pull-Request, der auf dieses Ticket wartet.",
    "no_result_title": "Tests auf main haben zweimal kein Ergebnis geliefert",
    "no_result_what": "die Tests auf main haben zweimal hintereinander kein Ergebnis geliefert: der Lauf wurde abgebrochen, abgesagt oder hatte keinen Bericht.",
    "no_result_goal": "wieder sichtbar, ob main grün oder rot ist, und die Pull-Requests, die nur auf main rot sind, sind klar.",
    "no_result_plan": "der Brain prüft, warum der Lauf kein Ergebnis gab (Ort, Limit, Bericht), und entscheidet, was zu reparieren ist.",
    "no_result_result": "der nächste Testlauf auf main liefert auf beiden Systemen ein Ergebnis.",
    "no_result_undo": "two-way door (ein Rollback); touches nur die main-Prüfung.",
    "watch_title": "Workflow {shown} nicht grün",
    "watch_what": "Workflow `{shown}` ist nicht grün beendet worden: der letzte Lauf {run_id} endete ohne grünes Ergebnis.",
    "watch_goal": "der letzte Lauf von `{shown}` ist wieder grün, und das Ticket schließt sich beim ersten solchen Lauf von selbst.",
    "watch_plan": "herausfinden, warum der Lauf nicht grün ist, und die Ursache beheben; was genau, entscheidet dieses Ticket.",
    "watch_result": "der letzte Lauf von `{shown}` ist grün.",
    "watch_undo": "two-way door (ein Rollback); touches nur den Lauf von `{shown}`.",
    "skills_what": "Pocock Upstream der Skills wechselte von `{old}` zu `{new}`: geänderte Skills: {changed}, davon in das Repo kopiert: {copies}.",
    "skills_goal": "die Kopien der Skills im Repo entsprechen der neuen Version seines Upstreams, und keine Rolle verliert einen aufrufbaren Skill stillschweigend.",
    "skills_plan": "ORCH startet unter dem `gh` des Owners `python -m orch_it skills update`; das öffnet einen Pull-Request mit den neuen Kopien, und die Tests des Repos prüfen die Kopien und die Verweise auf die Skills.",
    "skills_result": "der Pull-Request ist auf beiden Systemen grün, 100 % der Kopien haben Hashes gleich dem Manifest der neuen Version, und jeder Skill darin ist seiner Klasse zugeordnet.",
    "skills_undo": "two-way door (ein Rollback des Pull-Requests bringt die früheren Kopien zurück); touches alle Rollen, die diese Skills aufrufen.",
    "skills_plan_brain": "ORCH öffnet für dieses Ticket keinen Pull-Request: der Brain entscheidet jede Klasse aus der Tabelle unten, und `python -m orch_it skills update` gibt `waits_for_brain` aus und verschiebt nichts.",
    "skills_result_brain": "der Brain hat seine Entscheidung zu jeder Klasse in diesem Ticket geschrieben, und keine Kopie wird ohne sie verschoben.",
}
DE_EXPECT = {
    "red": {"title": "Main ist rot: 2 Tests schlagen fehl",
            "What": "main ist rot: 2 Tests schlagen fehl nach dem Merge von Pull-Request #12.",
            "Goal": DE["red_main_goal"], "Plan": DE["red_main_plan"], "Result": DE["red_main_result"],
            "Undo": DE["red_main_undo"]},
    "no_result": {"title": DE["no_result_title"], "What": DE["no_result_what"], "Goal": DE["no_result_goal"],
                  "Plan": DE["no_result_plan"], "Result": DE["no_result_result"], "Undo": DE["no_result_undo"]},
    "watch": {"title": "Workflow example-flow nicht grün",
              "What": "Workflow `example-flow` ist nicht grün beendet worden: der letzte Lauf 4117 endete ohne grünes Ergebnis.",
              "Goal": "der letzte Lauf von `example-flow` ist wieder grün, und das Ticket schließt sich beim ersten solchen Lauf von selbst.",
              "Plan": DE["watch_plan"], "Result": "der letzte Lauf von `example-flow` ist grün.",
              "Undo": "two-way door (ein Rollback); touches nur den Lauf von `example-flow`."},
    "skills": {"What": "Pocock Upstream der Skills wechselte von `111111111111` zu `222222222222`: geänderte Skills: 1, davon in das Repo kopiert: ",
               "Goal": DE["skills_goal"], "Plan": DE["skills_plan"], "Result": DE["skills_result"], "Undo": DE["skills_undo"]},
    "skills_brain": {"Plan": DE["skills_plan_brain"], "Result": DE["skills_result_brain"]},
}
LANGUAGES = {"unset": EN, "ru": RU, "de": DE_EXPECT}


@pytest.fixture
def language_of(tmp_path, monkeypatch):
    """Sets the settings file: `unset` removes the setting, `de` plants the German table."""

    def set_language(language: str):
        values: dict = {}
        if language == "de":
            folder = tmp_path / "tables"
            folder.mkdir()
            en = tomllib.loads((PACKAGE_TABLES / "en.toml").read_text("utf-8"))
            lines = [f"{key} = {json.dumps(DE.get(key, value), ensure_ascii=False)}\n" for key, value in en.items()]
            (folder / "de.toml").write_text("".join(lines), "utf-8")
            values["language_tables_dir"] = str(folder)
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(values), "utf-8")
        monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
        tree = git_tree(tmp_path / "tree")  # the language is the tree file's, read from the cwd (#453 unit 1)
        monkeypatch.chdir(tree)
        write_language(tree, None if language == "unset" else language)

    return set_language


@pytest.fixture
def words_only(monkeypatch):
    """The shape check of the two builders that run it is unit 10a's; this file measures the words."""
    monkeypatch.setattr(red_main, "violations", lambda ticket: [])
    monkeypatch.setattr(watch, "violations", lambda ticket: [])


def human_fields(body: str) -> dict:
    head = body.split("## For the agent")[0]
    return dict(re.findall(r"^- \*\*(\w+):\*\* (.*)$", head, re.M))


def build_red(count: int = 2):
    return red_main.build([f"t::{n}" for n in range(count)], sha="m0", run_id="7", pr=12)


def build_no_result():
    return red_main.build_no_result(sha="m0", run_id="7",
                                    jobs={"tests (ubuntu-latest)": "no result", "tests (windows-latest)": "no result"})


def build_watch():
    run = watch.WorkflowRun("example-flow.yml", 4117, "https://github.com/acme/example/actions/runs/4117",
                            "2026-10-28T09:00:00Z", "failure", 3, 0)
    return watch.build(run)


def skills_found():
    manifest = Manifest("u", "1" * 40, {}, {})
    change = Change("tdd", "text_changed", "skills/x/tdd/SKILL.md", "skills/x/tdd/SKILL.md", ["SKILL.md"])
    return update.Check(manifest, "2" * 40, {}, [change])


def check_skills(expect: dict, body: str):
    fields = human_fields(body)
    assert fields["What"].startswith(expect["What"])
    for name in ("Goal", "Plan", "Result", "Undo"):
        if name in expect:
            assert fields[name] == expect[name], name


@pytest.mark.parametrize("language", ["unset", "ru", "de"])
def test_red_main_ticket_words_follow_the_setting(language, language_of, words_only):
    language_of(language)
    title, body = build_red()
    expect = LANGUAGES[language]["red"]
    assert title == expect["title"]
    fields = human_fields(body)
    for name in ("What", "Goal", "Plan", "Result", "Undo"):
        assert fields[name] == expect[name], name
    if language != "ru":
        assert not CYRILLIC.search(body)


@pytest.mark.parametrize("language", ["unset", "ru", "de"])
def test_no_result_ticket_words_follow_the_setting(language, language_of, words_only):
    language_of(language)
    title, body = build_no_result()
    expect = LANGUAGES[language]["no_result"]
    assert title == expect["title"]
    fields = human_fields(body)
    for name in ("What", "Goal", "Plan", "Result", "Undo"):
        assert fields[name] == expect[name], name
    if language != "ru":
        assert not CYRILLIC.search(body)


@pytest.mark.parametrize("language", ["unset", "ru", "de"])
def test_watch_ticket_words_follow_the_setting(language, language_of, words_only):
    language_of(language)
    title, body = build_watch()
    expect = LANGUAGES[language]["watch"]
    assert title == expect["title"]
    fields = human_fields(body)
    for name in ("What", "Goal", "Plan", "Result", "Undo"):
        assert fields[name] == expect[name], name
    if language != "ru":
        assert not CYRILLIC.search(body)


@pytest.mark.parametrize("language", ["unset", "ru", "de"])
def test_skills_issue_words_follow_the_setting(language, language_of):
    language_of(language)
    _, body = update.issue(skills_found())
    check_skills(LANGUAGES[language]["skills"], body)
    if language != "ru":
        assert not CYRILLIC.search(body.split("## For the agent")[0])


@pytest.mark.parametrize("language", ["unset", "ru", "de"])
def test_skills_issue_on_the_brain_path_follows_the_setting(language, language_of):
    language_of(language)
    _, body = update.issue(skills_found(), waits=True)
    expect = LANGUAGES[language]["skills_brain"]
    fields = human_fields(body)
    assert fields["Plan"] == expect["Plan"]
    assert fields["Result"] == expect["Result"]


@pytest.mark.parametrize("count, form", [
    (1, "тест падает"), (2, "теста падают"), (5, "тестов падают"), (11, "тестов падают"), (22, "теста падают"),
])
def test_russian_count_forms_of_the_red_main_title(count, form, language_of, words_only):
    language_of("ru")
    title, _ = build_red(count)
    assert title == f"main красный: {count} {form}"


@pytest.mark.parametrize("language", ["unset", "ru", "de"])
def test_the_longest_watch_title_stays_within_the_title_limit(language, language_of, words_only):
    language_of(language)
    shown = "x" * watch.NAME_MAX  # the longest name `display_name` gives
    run = watch.WorkflowRun(f"{shown}.yml", 4117, "https://github.com/acme/example/actions/runs/4117",
                            "2026-10-28T09:00:00Z", "failure", 3, 0)
    title, _ = watch.build(run)
    assert len(title) <= 80


def test_a_language_with_no_table_is_refused_and_no_english_is_written(language_of, words_only):
    language_of("fr")
    for build in (build_red, build_no_result, build_watch, lambda: update.issue(skills_found())):
        with pytest.raises(settings.SettingsError) as refused:
            build()
        assert "human_language: no table for 'fr'" in str(refused.value)


def test_scan_no_cyrillic_outside_the_tables_and_the_allowed_file():
    found = []
    for path in sorted(SRC.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("language_tables/") or rel in ALLOWED_CYRILLIC_FILES:
            continue
        for number, line in enumerate(path.read_bytes().decode("utf-8", "ignore").splitlines(), 1):
            if CYRILLIC.search(line):
                found.append(f"{rel}:{number}")
    assert found == []
