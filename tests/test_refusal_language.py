"""The refusals and notices a person reads take their words from the table of `human_language` (#413
decision 6, unit 10c): the push refusal and its closing refusal, the guard's refusals and notices, the
check event's notice, the tickets hook's refusal and its reasons, the check's comment on a ticket, and
the watch ticket's comment and close.

Seams: each moved function, called with invented values. Four settings per site: unset (English, the words
the code wrote before, to the character), `ru` (the shipped Russian table), a planted `de` table in a tmp
`language_tables_dir` (copies every key of en.toml, the moved ones in German), and `fr` (no table: the
refusal names the missing table and no English is written instead). Invented values only: commit `ab...`,
file `notes.md`, repo `acme/example`, workflow `example-flow.yml`, run 4117.
"""

import json
import re
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_hub import REPO, FakeHub
from language_tree import git_tree, write_language
from test_hook_decide import FakeWorld
from ticket_fixtures import fixture

from orch_it import language, settings
from orch_it.cli import main
from orch_it.guards import hook, install, prepush, scanner
from orch_it.guards.exceptions import ExceptionsError, FILE_NAME, load_exceptions
from orch_it.guards.paths import Finding
from orch_it.guards.values import Values
from orch_it.merge import workflow_watch as watch
from orch_it.tickets import gh_check
from orch_it.tickets import hook as ticket_hook

CYRILLIC = re.compile("[Ѐ-ӿ]")
EN_TABLE = Path(language.__file__).parent / "language_tables" / "en.toml"
SETTINGS = ("unset", "ru", "de", "fr")
WAY_EN = ("the guard reads the object, not the command; to stop an own process with its tree use"
         f" `{sys.executable} -m orch_it stop <pid>`")  # the interpreter running the code, not a bare `python`

# the German of every key this unit moved; the other keys of the planted table are copied from en.toml
DE = {
    "push_refused_private": "orch-it: Push abgelehnt, die Commits enthalten private Daten und das Remote ist öffentlich (oder seine Sichtbarkeit war nicht lesbar):",
    "push_more": "  ... und {count} weitere",
    "push_take_out": "Werte aus den Commits entfernen (amend oder rebase) und erneut pushen; `{python} -m orch_it guards scan --range <remote>..<local>` listet dasselbe auf",
    "push_refused_closing": "orch-it: Push abgelehnt, eine Commit-Nachricht enthält ein Schlüsselwort zum Schließen vor einer Ticket-Referenz (GitHub schließt das Ticket, wenn der Commit den Standardbranch erreicht):",
    "push_closing_hit": '  {sha}, Zeile {number}, "{text}", {hit}',
    "push_closing_way": "`Refs <URL>` oder `Ticket: <URL>` an deren Stelle schreiben (amend oder rebase) und erneut pushen",
    "push_unreadable": "orch-it: Push abgelehnt, {file}: {reason}",
    "guard_refusal_way": "der Guard liest das Objekt, nicht den Befehl; um einen eigenen Prozess mit seinem Baum zu stoppen, `{python} -m orch_it stop <pid>` verwenden",
    "guard_refused": "vom orch-it-Guard abgelehnt",
    "guard_no_repo": "orch-it guard: kein Repo bekannt (die Einstellung `repo` oder ein GitHub-Origin): {what} nicht geschrieben",
    "guard_no_repo_events": "die Ereignisse des Guards",
    "guard_no_repo_error": "das Fehlerereignis",
    "guard_repo_setting": "orch-it guard: die Einstellung `repo` ist kein Repo, gelesen als keines: {error}",
    "guard_event_not_written": "orch-it guard: Ereignis nicht geschrieben: {error}",
    "check_not_written": "orch-it guards: Check-Ereignis nicht geschrieben: {error}",
    "check_took_label": "Der Shape-Check hat `{label}` von diesem Ticket genommen. Schreibe es mit dem Skill `write-ticket` neu und setze dann das Label erneut:",
    "ticket_hook_refused": "Der Tickets-Hook hat diesen Befehl abgelehnt:",
    "ticket_hook_internal": "tickets hook: interner Fehler, lässt durch: {error}",
    "ticket_unreadable_word": "ein Wort, das eine Ersetzung oder eine Variable baut",
    "ticket_unreadable_value": "der Wert von {name}",
    "ticket_command_unreadable": "command_unreadable: {reason}: als Literal schreiben",
    "ticket_body_stdin": "der Body kommt von stdin, einem Editor, dem Browser oder einem Wort, das die Shell baut",
    "ticket_body_file_unreadable": "die Body-Datei {name} kann nicht gelesen werden ({error})",
    "ticket_body_way": "{problem}; den Body in eine Datei schreiben und mit --body-file deren Pfad übergeben",
    "ticket_write_ticket_missing": "den Skill-Tool mit `{skill}` aufrufen, bevor `gh issue create` läuft, dann das Ticket neu schreiben",
    "ticket_park_by_hand": "ein Park wird nur von `{python} -m orch_it tickets park` geschrieben",
    "ticket_parked_label": "das Label `parked` wird nur von `tickets park` und `unpark` gesetzt und entfernt",
    "ticket_no_ticket": "die Bearbeitung nennt kein Ticket, das der Hook lesen kann",
    "ticket_not_read": "#{number} konnte für die Prüfung der Bearbeitung nicht von GitHub gelesen werden",
    "ticket_parked_line": "die Zeile `Parked:` wird nur von `tickets park` und `unpark` geschrieben und entfernt; lass sie, wie sie ist",
    "coding_rule": "CODING_STANDARDS.md: ein Wert, der sich je Repo, Konto, Rechner oder OS unterscheidet, ist eine Einstellung mit ihrem Standardwert, nie ein Literal in Code, Tests, Regeln oder Tickets.",
    "not_checked_line": "nicht geprüft: {items}",
    "not_checked_item": "{kind} ({reason})",
    "not_checked_not_set": "nicht gesetzt",
    "not_checked_too_short": "zu kurz zum Prüfen",
    "watch_title": "Workflow {shown} nicht grün",
    "watch_what": "Workflow `{shown}` nicht grün beendet: der letzte Lauf {run_id} endete ohne grünes Ergebnis.",
    "watch_comment": "Workflow `{workflow}` ist wieder nicht grün beendet worden: Lauf {run_id} endete als {how} bei {url}, beendet {ended_at}.",
    "watch_close": "Workflow `{workflow}` endet grün: Lauf {run_id} bei {url}, beendet {ended_at}. Diese Beobachtung wird geschlossen.",
    "watch_how_failure": "Fehler",
    "watch_how_cancelled": "abgebrochen",
    "watch_how_timed_out": "Zeitüberschreitung",
    "watch_how_no_job": "kein Job gestartet",
    "exc_not_toml": "{file}: nicht als TOML lesbar",
    "exc_not_tables": "{file}: die Einträge sind [[exception]]-Tabellen",
    "exc_entry": "[[exception]]-Eintrag {number} (Datei {file})",
    "exc_no_file": "{name}: keine file: ein Glob oder ein Repo-Pfad (oder eine Liste davon) ist nötig",
    "exc_no_pattern": "{name}: kein pattern: ein regulärer Ausdruck über die Zeile ist nötig",
    "exc_kinds": "{name}: kinds: eine nicht leere Liste von {kinds} ist nötig",
    "exc_no_reason": "{name}: reason leer oder fehlend: jeder Eintrag sagt, warum er erlaubt ist",
    "exc_bad_pattern": "{name}: das Muster lässt sich nicht kompilieren",
}

# English words no ru or de sentence of a refusal may hold (the TOML key names are data, not listed)
ENGLISH_WORDS = ("refused", "required", "missing", "readable", "compile", "allowed", "each", "entry", "entries", "says")


def set_language(tmp_path, monkeypatch, setting: str, extra: dict) -> None:
    """The settings file: `unset` takes no `human_language`; `ru` takes the shipped table; `de` plants the German
    table in a folder of `language_tables_dir`; `fr` names a language with no table."""

    values = dict(extra)
    if setting == "de":
        folder = tmp_path / "tables"
        folder.mkdir(exist_ok=True)
        en = tomllib.loads(EN_TABLE.read_text("utf-8"))
        lines = [f"{key} = {json.dumps(DE.get(key, value), ensure_ascii=False)}\n" for key, value in en.items()]
        (folder / "de.toml").write_text("".join(lines), "utf-8")
        values["language_tables_dir"] = str(folder)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(values), "utf-8")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    tree = git_tree(tmp_path / "tree")  # the language is the tree file's, read from the cwd (#453 unit 1)
    monkeypatch.chdir(tree)
    write_language(tree, None if setting == "unset" else setting)


def in_each_language(tmp_path, monkeypatch, make, extra: dict | None = None) -> dict[str, str]:
    """What `make()` gives under each setting. A setting with no table raises the settings refusal, which is
    what a site gives too (the refusal names the missing table)."""

    out = {}
    for setting in SETTINGS:
        set_language(tmp_path, monkeypatch, setting, extra or {})
        try:
            out[setting] = make()
        except (settings.SettingsError, language.LanguageError) as e:
            out[setting] = str(e)
    return out


def check(texts: dict, english: str, *, exact: bool = True, sentences=(), german=()) -> None:
    """`english`: what the English setting gave before this unit, whole (`exact`) or as a part of the text;
    `sentences`: English sentences that no other setting may hold; `german`: parts the planted German table gives."""

    en = texts["unset"]
    assert (en == english) if exact else (english in en), en
    assert not CYRILLIC.search(en)
    assert CYRILLIC.search(texts["ru"]), texts["ru"]
    assert not CYRILLIC.search(texts["de"]), texts["de"]
    assert not CYRILLIC.search(texts["fr"])
    for sentence in sentences:
        for setting in ("ru", "de", "fr"):
            assert sentence not in texts[setting], (setting, sentence)
    for part in german:
        assert part in texts["de"], part
    assert "no table for 'fr'" in texts["fr"], texts["fr"]


@pytest.fixture
def no_machine_values(monkeypatch):
    """The ticket bodies here are judged as text: no value of this machine and no workflow is read."""
    monkeypatch.setattr("orch_it.guards.values.read_values", lambda **kwargs: Values())
    monkeypatch.setattr("orch_it.guards.kinds.read_job_names", lambda folder: frozenset())


def hit() -> scanner.Hit:
    return scanner.Hit(kind="email", where="content", commit="ab" * 20, file="notes.md", line=3)


def rule_hit() -> scanner.Hit:
    """A hit of a kind the coding standard judges (`email` is not one: it is a kind of #395, cited by no rule)."""
    return scanner.Hit(kind="account", where="content", commit="ab" * 20, file="notes.md", line=3)


def test_the_push_refusal_takes_its_words_from_the_table(tmp_path, monkeypatch):
    texts = in_each_language(tmp_path, monkeypatch, lambda: prepush._refusal([hit()]))
    english = ("orch-it: push refused, the commits hold private data and the remote is public"
               " (or its visibility could not be read):\n"
               f"  {'ab' * 6} content notes.md:3 email{scanner.cited('email')}\n"
               "take the values out of the commits (amend or rebase) and push again;"
               f" `{sys.executable} -m orch_it guards scan --range <remote>..<local>` lists the same\n")
    check(texts, english, sentences=["the commits hold private data", "take the values out of the commits"],
          german=[DE["push_refused_private"], DE["push_take_out"].format(python=sys.executable)])


def test_the_push_refusal_cites_the_rule_in_the_table_words(tmp_path, monkeypatch):
    texts = in_each_language(tmp_path, monkeypatch, lambda: prepush._refusal([rule_hit()]))
    english = ("orch-it: push refused, the commits hold private data and the remote is public"
               " (or its visibility could not be read):\n"
               f"  {'ab' * 6} content notes.md:3 account - {scanner.RULE}\n"
               "take the values out of the commits (amend or rebase) and push again;"
               f" `{sys.executable} -m orch_it guards scan --range <remote>..<local>` lists the same\n")
    check(texts, english, sentences=["the commits hold private data", "take the values out of the commits", scanner.RULE],
          german=[DE["push_refused_private"], DE["push_take_out"].format(python=sys.executable), DE["coding_rule"]])


def test_the_push_names_what_it_did_not_check_in_the_table_words(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hook, "_write_events", lambda *args, **kwargs: None)
    monkeypatch.setattr(prepush, "_new_commits", lambda *args: ["c" * 40])
    monkeypatch.setattr(prepush, "_closing_hits", lambda *args: [])
    monkeypatch.setattr(scanner, "scan_commits", lambda *args: [])

    def make():
        patterns = scanner.Patterns(values=Values(not_checked=(("git_email", "not set"),)))
        prepush.run_pre_push(f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n", None,
                             remote_url="https://example.invalid/o/n.git", remote_name="origin",
                             work_tree=tmp_path, visibility=lambda url: "public", patterns=patterns)
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, "not checked: git_email (not set)\n", sentences=["not checked: ", "(not set)"],
          german=[DE["not_checked_line"].format(items="git_email (" + DE["not_checked_not_set"] + ")")])


def test_guards_scan_takes_the_rule_and_the_not_checked_line_from_the_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scanner, "scan_tree", lambda *args: [rule_hit()])
    monkeypatch.setattr(scanner.Patterns, "of_machine",
                        lambda **kwargs: scanner.Patterns(values=Values(not_checked=(("git_email", "not set"),))))

    def make():
        main(["guards", "scan", "--work-tree", str(tmp_path), "--tree"])
        return capsys.readouterr().out

    texts = in_each_language(tmp_path, monkeypatch, make)
    english = (f"{'ab' * 6} content notes.md:3 account - {scanner.RULE}\n"
               "hits=1 commits=1\n"
               "not checked: git_email (not set)\n")
    check(texts, english, sentences=[scanner.RULE, "not checked: ", "(not set)"],
          german=[DE["coding_rule"], DE["not_checked_line"].format(items="git_email (" + DE["not_checked_not_set"] + ")")])


def count_rule_reads(monkeypatch) -> list[str]:
    """Each read of the table's `coding_rule` (the language it is read in), in the order made."""
    reads: list[str] = []
    original = language._lookup

    def spy(lang, key, fields):
        if key == "coding_rule":
            reads.append(lang)
        return original(lang, key, fields)

    monkeypatch.setattr(language, "_lookup", spy)
    return reads


def test_a_push_refusal_with_many_hits_reads_the_rule_sentence_once(monkeypatch, tmp_path):
    reads = count_rule_reads(monkeypatch)
    refusal = prepush._refusal([rule_hit()] * (prepush.SHOWN_HITS + 10))
    assert len(reads) == 1, reads
    assert refusal.count(scanner.RULE) == prepush.SHOWN_HITS


def test_guards_scan_with_many_hits_reads_the_rule_sentence_once(tmp_path, monkeypatch, capsys):
    reads = count_rule_reads(monkeypatch)
    monkeypatch.setattr(scanner, "scan_tree", lambda *args: [rule_hit()] * 500)
    monkeypatch.setattr(scanner.Patterns, "of_machine", lambda **kwargs: scanner.Patterns(values=Values()))
    main(["guards", "scan", "--work-tree", str(tmp_path), "--tree"])
    out = capsys.readouterr().out
    assert len(reads) == 1, reads
    assert out.count(f" - {scanner.RULE}\n") == 500


def test_the_push_refusal_says_how_many_hits_it_does_not_show_in_the_table_words(tmp_path, monkeypatch):
    texts = in_each_language(tmp_path, monkeypatch, lambda: prepush._refusal([hit()] * 51))
    check(texts, "  ... and 1 more\n", exact=False, sentences=["... and 1 more"],
          german=[DE["push_more"].format(count=1)])


def test_the_closing_refusal_takes_its_words_from_the_table(tmp_path, monkeypatch):
    texts = in_each_language(tmp_path, monkeypatch,
                             lambda: prepush._closing_refusal([("abc123", 2, "Fixes #5", "Fixes")]))
    english = ("orch-it: push refused, a commit message holds a closing keyword before a ticket reference"
               " (GitHub closes the ticket when the commit reaches its default branch):\n"
               '  abc123, line 2, "Fixes #5", Fixes\n'
               "write `Refs <URL>` or `Ticket: <URL>` in its place (amend or rebase) and push again\n")
    check(texts, english, sentences=["a commit message holds a closing keyword", "in its place (amend or rebase)"],
          german=[DE["push_refused_closing"], DE["push_closing_way"],
                  DE["push_closing_hit"].format(sha="abc123", number=2, text="Fixes #5", hit="Fixes")])


def test_a_push_with_a_list_nobody_can_read_says_so_in_the_table_words(tmp_path, monkeypatch, capsys):
    def make():
        monkeypatch.setattr(prepush, "_new_commits", lambda *args: ["c" * 40])
        monkeypatch.setattr(prepush, "_closing_hits", lambda *args: [])
        monkeypatch.setattr(prepush, "_profile_emails", lambda repo: ())
        monkeypatch.setattr(prepush, "_machine_patterns",
                            lambda *args: (SimpleNamespace(not_checked_line=lambda: ""), ExceptionsError("bad entry 3")))
        monkeypatch.setattr(scanner, "scan_commits", lambda *args: [])
        prepush.run_pre_push(f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n", None,
                             remote_url="https://example.invalid/o/n.git", remote_name="origin",
                             work_tree=tmp_path, visibility=lambda url: "public")
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, f"orch-it: push refused, {FILE_NAME}: bad entry 3\n", exact=False,
          sentences=["push refused, "], german=[DE["push_unreadable"].format(file=FILE_NAME, reason="bad entry 3")])


def malformed_list(tmp_path, body: str) -> ExceptionsError:
    """The `ExceptionsError` the loader raises for an exceptions list of `body`, read in the setting in force."""

    path = tmp_path / FILE_NAME
    path.write_text(body, "utf-8")
    with pytest.raises(ExceptionsError) as err:
        load_exceptions(path)
    return err.value


# (the list, its stable code, the English words of the sentence the English setting gives)
MALFORMED_LISTS = {
    "no_reason": ('[[exception]]\nfile = "tools/other.py"\npattern = "probe"\nkinds = ["one_os_call"]\nreason = "   "\n',
                  "no_reason"),
    "not_toml": ('[[exception]\nfile = "tools/other.py"\n', "not_toml"),
}


@pytest.mark.parametrize("case", list(MALFORMED_LISTS), ids=list(MALFORMED_LISTS))
def test_a_push_with_a_malformed_exceptions_list_says_so_in_the_table_words(tmp_path, monkeypatch, capsys, case):
    body, code = MALFORMED_LISTS[case]

    def make():
        broken = malformed_list(tmp_path, body)
        assert broken.code == code
        monkeypatch.setattr(prepush, "_new_commits", lambda *args: ["c" * 40])
        monkeypatch.setattr(prepush, "_closing_hits", lambda *args: [])
        monkeypatch.setattr(prepush, "_profile_emails", lambda repo: ())
        monkeypatch.setattr(prepush, "_machine_patterns",
                            lambda *args: (SimpleNamespace(not_checked_line=lambda: ""), broken))
        monkeypatch.setattr(scanner, "scan_commits", lambda *args: [])
        prepush.run_pre_push(f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}\n", None,
                             remote_url="https://example.invalid/o/n.git", remote_name="origin",
                             work_tree=tmp_path, visibility=lambda url: "public")
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make)
    path = str(tmp_path / FILE_NAME)
    if case == "no_reason":
        english = (f"orch-it: push refused, {FILE_NAME}: [[exception]] entry 1 (file 'tools/other.py'):"
                   " empty or missing reason: each entry says why it is allowed\n")
        german = [DE["push_unreadable"].format(file=FILE_NAME, reason=DE["exc_no_reason"].format(
            name=DE["exc_entry"].format(number=1, file="'tools/other.py'")))]
        check(texts, english, sentences=["empty or missing reason", "each entry says why"], german=german)
    else:
        check(texts, "not readable as TOML: ", exact=False, sentences=["not readable as TOML"],
              german=[DE["exc_not_toml"].format(file=path)])
    for setting in ("ru", "de"):  # the sentence names no English word; the folder of the test is not the sentence's
        text = texts[setting].replace(str(tmp_path), "")
        for word in ENGLISH_WORDS:
            assert not re.search(rf"\b{word}\b", text, re.IGNORECASE), (setting, word, text)


def test_a_deny_says_the_way_in_the_table_words(tmp_path, monkeypatch):
    def make():
        world = FakeWorld(delete=Finding("protected_root", "/work/wt"))
        return hook.decide("rm -rf /work/wt", tool="Bash", cwd="/work", env={}, windows=False,
                           session_id="s1", world=world).reason

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, f"worktree_delete: protected_root; {WAY_EN}", sentences=[WAY_EN],
          german=[DE["guard_refusal_way"].format(python=sys.executable)])


def test_a_deny_with_no_reason_says_its_fallback_in_the_table_words(tmp_path, monkeypatch):
    texts = in_each_language(tmp_path, monkeypatch,
                             lambda: json.loads(hook._deny_json(None))["hookSpecificOutput"]["permissionDecisionReason"])
    check(texts, "refused by the orch-it guard", sentences=["refused by the orch-it guard"],
          german=[DE["guard_refused"]])


def test_the_no_repo_notice_of_the_guard_events_takes_its_words_from_the_table(tmp_path, monkeypatch, capsys):
    def make():
        hook._write_events(None, hook.Decision(False, None, []), repo=None, session=None)
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, "orch-it guard: no repo is known (the setting `repo` or a GitHub origin): the guard's events not written\n",
          sentences=["no repo is known", "the guard's events"],
          german=[DE["guard_no_repo"].format(what=DE["guard_no_repo_events"])])


def test_the_no_repo_notice_of_an_error_event_takes_its_words_from_the_table(tmp_path, monkeypatch, capsys):
    def make():
        hook._safe_write_error(None, "hook", "boom", repo=None, session=None)
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, "orch-it guard: no repo is known (the setting `repo` or a GitHub origin): the error event not written\n",
          sentences=["the error event"], german=[DE["guard_no_repo"].format(what=DE["guard_no_repo_error"])])


def test_a_bad_repo_setting_is_said_in_the_table_words(tmp_path, monkeypatch, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()

    def make():
        assert hook._repo_of(str(plain)) is None
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make, extra={"repo": "not-a-repo"})
    check(texts, "orch-it guard: the setting `repo` is no repo, read as none: ", exact=False,
          sentences=["is no repo, read as none"], german=[DE["guard_repo_setting"].split("{error}")[0]])


class BrokenStore:
    def writer(self):
        raise OSError("disk full")


def test_an_event_that_cannot_be_written_is_said_in_the_table_words(tmp_path, monkeypatch, capsys):
    def make():
        decision = hook.Decision(False, None, [hook.Fired("worktree_delete", "pass", "rm", "rm x", None)])
        hook._write_events(BrokenStore(), decision, repo=REPO, session=None)
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, "orch-it guard: event not written: disk full\n", exact=False,
          sentences=["event not written"], german=[DE["guard_event_not_written"].format(error="disk full")])


def test_a_check_event_that_cannot_be_written_is_said_in_the_table_words(tmp_path, monkeypatch, capsys):
    def make():
        install.write_check(BrokenStore(), REPO, "guard_install", "t", "passed", [], {})
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, "orch-it guards: check event not written: OSError\n",
          sentences=["check event not written"], german=[DE["check_not_written"].format(error="OSError")])


def test_the_shape_check_comment_on_github_takes_its_words_from_the_table(tmp_path, monkeypatch, no_machine_values):
    def make():
        hub = FakeHub()
        hub.add(1, "", ["ready-for-agent"], title="A ticket")
        event = {"action": "labeled", "repository": {"full_name": REPO},
                 "issue": {"number": 1, "labels": [{"name": "ready-for-agent"}]},
                 "label": {"name": "ready-for-agent"}}
        gh_check.check_event(event, hub, env={}, run=None, home=None, host=None)
        return hub.comments(REPO, 1)[-1].body

    texts = in_each_language(tmp_path, monkeypatch, make)
    english = ("The shape check took `ready-for-agent` off this ticket. Write it again with the `write-ticket`"
               " skill, then set the label again:")
    check(texts, english, exact=False, sentences=["The shape check took"],
          german=[DE["check_took_label"].format(label="ready-for-agent")])


def test_the_tickets_hook_refusal_leads_with_its_words_from_the_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ticket_hook, "judge", lambda payload, **kwargs: ticket_hook.Refusal(["x"], "x: line", None))
    monkeypatch.setattr(ticket_hook, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(ticket_hook, "make_hub", lambda: None)

    def make():
        ticket_hook.main(json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh issue create"}}))
        return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, "The tickets hook refused this command:\nx: line", sentences=["The tickets hook refused"],
          german=[DE["ticket_hook_refused"]])


def test_the_tickets_hook_internal_error_is_said_in_the_table_words(tmp_path, monkeypatch, capsys):
    def boom(payload, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ticket_hook, "judge", boom)
    monkeypatch.setattr(ticket_hook, "_record", lambda *args, **kwargs: None)

    def make():
        ticket_hook.main(json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh issue create"}}))
        return capsys.readouterr().err

    texts = in_each_language(tmp_path, monkeypatch, make)
    check(texts, "tickets hook: internal error, failing open: RuntimeError: boom\n", exact=False,
          sentences=["internal error, failing open"],
          german=[DE["ticket_hook_internal"].format(error="RuntimeError: boom")])


TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"
WITH_SKILL = TRANSCRIPTS / "write_ticket_called.jsonl"  # a session that called `write-ticket`
WITHOUT_SKILL = TRANSCRIPTS / "write_ticket_not_called.jsonl"
GOOD_BODY = fixture("good_basic")[1]  # a body that passes the shape rules, so only the tested reason is refused
PARKED = "Parked: pr_ended #12 until 2026-11-30"

# (site key, the argv of the command, its English reason as the hook wrote it before, its German reason)
TICKET_REASONS = [
    ("ticket_unreadable_word", ["gh", "issue", "edit", "1", None],
     "command_unreadable: a word built by a substitution or a variable: write it as a literal",
     DE["ticket_command_unreadable"].format(reason=DE["ticket_unreadable_word"])),
    ("ticket_unreadable_value", ["gh", "issue", "edit", "1", "--title", None],
     "command_unreadable: the value of --title: write it as a literal",
     DE["ticket_command_unreadable"].format(reason=DE["ticket_unreadable_value"].format(name="--title"))),
    ("ticket_body_stdin", ["gh", "issue", "create", "--body-file", "-", "--title", "t"],
     "body_unreadable: the body comes from stdin, an editor, the browser or a word the shell builds;"
     " write the body to a file and pass --body-file with its path",
     DE["ticket_body_way"].format(problem=DE["ticket_body_stdin"])),
    ("ticket_body_file_unreadable", ["gh", "issue", "create", "--body-file", "missing.md", "--title", "t"],
     "body_unreadable: the body file missing.md cannot be read (FileNotFoundError); write the body to a file"
     " and pass --body-file with its path",
     DE["ticket_body_way"].format(problem=DE["ticket_body_file_unreadable"].format(
         name="missing.md", error="FileNotFoundError"))),
    ("ticket_park_by_hand", ["gh", "issue", "create", "--label", "parked", "--title", "t", "--body", GOOD_BODY],
     f"park_by_hand: a park is written only by `{sys.executable} -m orch_it tickets park`",
     DE["ticket_park_by_hand"].format(python=sys.executable)),
    ("ticket_parked_label", ["gh", "issue", "edit", "5", "--add-label", "parked"],
     "park_by_hand: the `parked` label is set and taken off only by `tickets park` and `unpark`",
     DE["ticket_parked_label"]),
    ("ticket_no_ticket", ["gh", "issue", "edit", "--add-label", "ready-for-agent"],
     "issue_unreadable: the edit names no ticket the hook can read",
     DE["ticket_no_ticket"]),
    ("ticket_not_read", ["gh", "issue", "edit", "5", "--add-label", "ready-for-agent"],
     "issue_unreadable: #5 could not be read from GitHub to judge the edit",
     DE["ticket_not_read"].format(number=5)),
    ("ticket_parked_line", ["gh", "issue", "edit", "5", "--body", f"{PARKED}\n\n{GOOD_BODY}"],
     "park_by_hand: the `Parked:` line is written and taken off only by `tickets park` and `unpark`; keep it as it is",
     DE["ticket_parked_line"]),
    ("ticket_write_ticket_missing", ["gh", "issue", "create", "--title", "t", "--body", GOOD_BODY],
     "write_ticket_missing: call the Skill tool with `write-ticket` before `gh issue create`, then write the ticket again",
     DE["ticket_write_ticket_missing"].format(skill="write-ticket")),
]


@pytest.mark.parametrize("key,argv,english,german", TICKET_REASONS, ids=[case[0] for case in TICKET_REASONS])
def test_a_tickets_hook_reason_takes_its_words_from_the_table(tmp_path, monkeypatch, no_machine_values,
                                                              key, argv, english, german):
    hubs = {"ticket_not_read": SimpleNamespace(issue=lambda repo, number: None),
            "ticket_parked_line": SimpleNamespace(issue=lambda repo, number: SimpleNamespace(
                body="", title="t", labels=[], is_pr=False))}

    # every case runs in a session that called `write-ticket`, except the case that tests its absence
    transcript = WITHOUT_SKILL if key == "ticket_write_ticket_missing" else WITH_SKILL

    def make():
        call = ticket_hook._Call.of(argv, str(tmp_path))
        codes, lines, repo = call.judge({"transcript_path": str(transcript)}, hubs.get(key),
                                        lambda folder: REPO, str(tmp_path), Values(), {})
        return "\n".join(lines)

    texts = in_each_language(tmp_path, monkeypatch, make)
    # the reason under test is among the lines: the fixture body's human block is Russian, while the default
    # setting is English, so under the English setting the shape rules add their own lines, which this test does not judge
    check(texts, english, exact=False, sentences=[english.split(": ", 1)[1][:40]], german=[german])


def watch_run(outcome="failure", jobs=3):
    return watch.WorkflowRun("example-flow.yml", 4117, "https://github.com/acme/example/actions/runs/4117",
                             "2026-10-28T09:00:00Z", outcome, jobs, 0)


def test_the_watch_comment_takes_its_words_from_the_table(tmp_path, monkeypatch):
    texts = in_each_language(tmp_path, monkeypatch, lambda: watch._comment(watch_run()))
    english = ("Workflow `example-flow.yml` did not end green again: run 4117 ended as failure"
               " at https://github.com/acme/example/actions/runs/4117, ended 2026-10-28T09:00:00Z.")
    check(texts, english, sentences=["did not end green again"],
          german=[DE["watch_comment"].format(workflow="example-flow.yml", run_id=4117, how=DE["watch_how_failure"],
                                             url=watch_run().url, ended_at="2026-10-28T09:00:00Z")])
    assert "ошибка" in texts["ru"]


def test_the_watch_close_takes_its_words_from_the_table(tmp_path, monkeypatch):
    texts = in_each_language(tmp_path, monkeypatch, lambda: watch._close(watch_run("success")))
    english = ("Workflow `example-flow.yml` ends green: run 4117 at https://github.com/acme/example/actions/runs/4117,"
               " ended 2026-10-28T09:00:00Z. Closing this watch.")
    check(texts, english, sentences=["Closing this watch"],
          german=[DE["watch_close"].format(workflow="example-flow.yml", run_id=4117, url=watch_run().url,
                                           ended_at="2026-10-28T09:00:00Z")])


@pytest.mark.parametrize("outcome,jobs,key,english", [
    ("failure", 3, "watch_how_failure", "failure"),
    ("cancelled", 3, "watch_how_cancelled", "cancelled"),
    ("timed_out", 3, "watch_how_timed_out", "timed out"),
    ("other", 0, "watch_how_no_job", "no job started"),
], ids=["failure", "cancelled", "timed_out", "no_job_started"])
def test_how_a_run_ended_takes_its_words_from_the_table(tmp_path, monkeypatch, outcome, jobs, key, english):
    texts = in_each_language(tmp_path, monkeypatch, lambda: watch.how_ended(watch_run(outcome, jobs)))
    check(texts, english, german=[DE[key]])


@pytest.mark.parametrize("setting", ["unset", "ru", "de"])
def test_the_watch_ticket_agent_block_stays_english_and_the_human_parts_follow_the_setting(
        tmp_path, monkeypatch, no_machine_values, setting):
    set_language(tmp_path, monkeypatch, setting, {})
    title, body = watch.build(watch_run("timed_out"))
    human, agent = body.split("## For the agent")
    assert "ended its latest run 4117 at https://github.com/acme/example/actions/runs/4117 as timed out;" in agent
    assert not CYRILLIC.search(agent)
    assert DE["watch_how_timed_out"] not in agent and "превышено время" not in agent
    if setting == "ru":
        assert CYRILLIC.search(title) and CYRILLIC.search(human)
    elif setting == "de":
        assert title == DE["watch_title"].format(shown="example-flow")
        assert DE["watch_what"].format(shown="example-flow", run_id=4117) in human
    else:
        assert title == "Workflow example-flow did not end green"
