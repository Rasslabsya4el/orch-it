"""The language of human-facing text is the setting `human_language` (#413 decision 6, unit 9; #453 unit 1).

The value is read from the `settings.json` at the root of the git work tree (`settings.human_language()` takes the
tree of the current folder); an installation file that still carries the key is refused. Seams:
`settings.human_language()`, `language.text(key, **fields)` (the string for the current setting), and the grep check
over `src/` that keeps the key's only reader in `settings.py`. Invented values only: a made-up `de.toml` in a tmp folder.
"""

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from language_tree import git_tree, write_language
from orch_it import language, settings

PACKAGE_TABLES = Path(language.__file__).parent / "language_tables"
SRC = Path(settings.__file__).parents[1]
CYRILLIC = re.compile("[Ѐ-ӿ]")
KEY = "human_field_language_rule"  # a key every shipped table has (the shape check's)
LITERAL = re.compile(r"""["']human_language["']""")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))  # no git tree above the test's own folders
    monkeypatch.chdir(git_tree(tmp_path / "tree"))  # the cwd of each test: a git work tree with no language file


def configure(tmp_path, **values):
    """The installation file: the language is never set here (a `human_language` in it is refused)."""
    (tmp_path / "settings.json").write_text(json.dumps(values), "utf-8")


def plant_de(tmp_path, body: str) -> Path:
    folder = tmp_path / "my-tables"
    folder.mkdir()
    (folder / "de.toml").write_text(body, "utf-8")
    return folder


def shipped(name: str) -> dict:
    return tomllib.loads((PACKAGE_TABLES / f"{name}.toml").read_text("utf-8"))


def quoted_readers(root: Path) -> set[str]:
    """The files under `root` that name the key `human_language` as a quoted literal (a reader of the setting)."""
    found = set()
    for path in root.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and LITERAL.search(path.read_text("utf-8", "ignore")):
            found.add(path.relative_to(root).as_posix())
    return found


def test_default_is_english():
    assert settings.human_language() == "en"
    assert language.text(KEY) == shipped("en")[KEY]


def test_ru_gives_the_russian_string():
    write_language(Path.cwd(), "ru")
    assert language.text(KEY) == shipped("ru")[KEY]
    assert CYRILLIC.search(language.text(KEY))


def test_the_tree_file_at_the_root_is_read_from_a_subfolder(monkeypatch):
    write_language(Path.cwd(), "ru")
    deep = Path.cwd() / "src" / "deep"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert settings.human_language() == "ru"


def test_key_missing_in_the_tree_file_gives_english():
    (Path.cwd() / "settings.json").write_text('{"research": {}}', "utf-8")
    assert settings.human_language() == "en"


def test_a_folder_outside_any_git_tree_gives_english(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    write_language(plain, "ru")  # a tree file, but no work tree around it
    monkeypatch.chdir(plain)
    assert subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=plain, capture_output=True).returncode != 0
    assert settings.human_language() == "en"


def test_an_explicit_work_tree_wins_over_the_cwd(tmp_path):
    other = git_tree(tmp_path / "other")
    write_language(other, "en")
    write_language(Path.cwd(), "ru")
    assert settings.human_language(work_tree=other) == "en"
    assert settings.human_language() == "ru"
    assert settings.human_language(work_tree=tmp_path / "no-tree-here") == "en"


def test_a_human_language_key_in_the_installation_file_is_refused_naming_the_tree_file(tmp_path):
    configure(tmp_path, human_language="ru")
    with pytest.raises(settings.HumanLanguageError) as refused:
        settings.human_language(work_tree=Path.cwd())
    assert refused.value.code == "bad_setting"
    assert str(Path.cwd() / "settings.json") in str(refused.value)
    assert "'ru'" in str(refused.value)


def test_the_installation_refusal_names_the_tree_file_found_from_the_cwd(tmp_path):
    configure(tmp_path, human_language="ru")
    with pytest.raises(settings.HumanLanguageError) as refused:
        settings.human_language()
    assert "settings.json" in str(refused.value)
    assert Path.cwd().name in str(refused.value)


def test_the_installation_refusal_holds_outside_any_git_tree(tmp_path, monkeypatch):
    configure(tmp_path, human_language="ru")
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    with pytest.raises(settings.HumanLanguageError) as refused:
        settings.human_language()
    assert "settings.json" in str(refused.value)


def test_language_with_no_table_is_refused_at_read_naming_the_table(tmp_path):
    write_language(Path.cwd(), "fr")
    with pytest.raises(settings.SettingsError) as refused:
        settings.human_language()
    assert refused.value.code == "bad_setting"
    message = str(refused.value)
    assert "human_language: no table for 'fr'" in message
    assert str(PACKAGE_TABLES / "fr.toml") in message
    with pytest.raises(settings.SettingsError):  # text() never falls back to English
        language.text(KEY)


def test_refusal_names_the_extra_folder_too(tmp_path):
    folder = tmp_path / "empty-tables"
    folder.mkdir()
    configure(tmp_path, language_tables_dir=str(folder))
    write_language(Path.cwd(), "fr")
    with pytest.raises(settings.SettingsError) as refused:
        settings.human_language()
    assert str(folder / "fr.toml") in str(refused.value)
    assert str(PACKAGE_TABLES / "fr.toml") in str(refused.value)


def test_a_language_that_is_no_table_name_is_refused():
    write_language(Path.cwd(), "../settings")
    with pytest.raises(settings.SettingsError) as refused:
        settings.human_language()
    assert refused.value.code == "bad_setting"


def test_a_tree_value_that_is_no_language_code_is_refused():
    (Path.cwd() / "settings.json").write_text('{"human_language": 7}', "utf-8")
    with pytest.raises(settings.HumanLanguageError) as refused:
        settings.human_language()
    assert refused.value.code == "bad_setting"


def test_planted_table_in_language_tables_dir_is_used(tmp_path):
    folder = plant_de(tmp_path, 'greeting = "Hallo, {name}!"\n')
    configure(tmp_path, language_tables_dir=str(folder))
    write_language(Path.cwd(), "de")
    assert language.text("greeting", name="Pat") == "Hallo, Pat!"


def test_key_missing_in_a_table_raises_naming_key_and_language(tmp_path):
    folder = plant_de(tmp_path, 'greeting = "Hallo"\n')
    configure(tmp_path, language_tables_dir=str(folder))
    write_language(Path.cwd(), "de")
    with pytest.raises(language.LanguageError) as missing:
        language.text(KEY)
    assert KEY in str(missing.value)
    assert "'de'" in str(missing.value)
    assert "Hallo" not in str(missing.value)


def test_a_field_the_string_needs_but_the_call_lacks_raises(tmp_path):
    folder = plant_de(tmp_path, 'greeting = "Hallo, {name} {title}!"\n')
    configure(tmp_path, language_tables_dir=str(folder))
    write_language(Path.cwd(), "de")
    with pytest.raises(language.LanguageError) as missing:
        language.text("greeting", name="Pat")
    assert "title" in str(missing.value)
    assert language.text("greeting") == "Hallo, {name} {title}!"  # no fields: returned as written


def test_english_text_has_no_cyrillic_letter_in_any_key():
    for key, value in shipped("en").items():
        assert isinstance(value, str)
        assert not CYRILLIC.search(value), key


def test_en_and_ru_tables_have_the_same_keys():
    assert set(shipped("en")) == set(shipped("ru"))
    assert set(shipped("en")) >= {KEY, "human_field_letters"}


def test_bad_language_tables_dir_is_refused(tmp_path):
    configure(tmp_path, language_tables_dir=7)
    write_language(Path.cwd(), "ru")  # the table lookup runs only for a language the tree file names
    with pytest.raises(settings.SettingsError) as refused:
        settings.human_language()
    assert refused.value.code == "bad_setting"


def test_only_settings_py_reads_the_language_key_in_src():
    assert quoted_readers(SRC) == {"orch_it/settings.py"}


def test_a_second_reader_in_a_copy_of_src_turns_the_grep_red(tmp_path):
    copy = tmp_path / "src"
    shutil.copytree(SRC, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "orch_it" / "second_reader.py").write_text(
        'def lang(raw):\n    return raw.get("human_language", "en")\n', "utf-8")
    readers = quoted_readers(copy)
    assert readers != {"orch_it/settings.py"}
    assert "orch_it/second_reader.py" in readers


def test_the_repo_commits_its_language_in_the_root_settings_file_and_nothing_else():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert json.loads((root / "settings.json").read_text(encoding="utf-8")) == {"human_language": "ru"}
