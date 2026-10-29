"""The language of human-facing text is the setting `human_language` (#413 decision 6, unit 9).
Seams: `language.text(key, **fields)` (the string for the current setting) and
`settings.human_language()` (the setting read; a value with no table is refused there). Strings live
in one TOML table per language, in the package and in the folder of the setting `language_tables_dir`.
Invented values only: a made-up `de.toml` in a tmp folder."""

import json
import re
import tomllib
from pathlib import Path

import pytest

from orch_it import language, settings

PACKAGE_TABLES = Path(language.__file__).parent / "language_tables"
CYRILLIC = re.compile("[Ѐ-ӿ]")
KEY = "human_field_language_rule"  # a key every shipped table has (the shape check's)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)


def configure(tmp_path, **values):
    (tmp_path / "settings.json").write_text(json.dumps(values), "utf-8")


def plant_de(tmp_path, body: str) -> Path:
    folder = tmp_path / "my-tables"
    folder.mkdir()
    (folder / "de.toml").write_text(body, "utf-8")
    return folder


def shipped(name: str) -> dict:
    return tomllib.loads((PACKAGE_TABLES / f"{name}.toml").read_text("utf-8"))


def test_default_is_english():
    assert settings.human_language() == "en"
    assert language.text(KEY) == shipped("en")[KEY]


def test_ru_gives_the_russian_string(tmp_path):
    configure(tmp_path, human_language="ru")
    assert language.text(KEY) == shipped("ru")[KEY]
    assert CYRILLIC.search(language.text(KEY))


def test_planted_table_in_language_tables_dir_is_used(tmp_path):
    folder = plant_de(tmp_path, 'greeting = "Hallo, {name}!"\n')
    configure(tmp_path, human_language="de", language_tables_dir=str(folder))
    assert language.text("greeting", name="Pat") == "Hallo, Pat!"


def test_language_with_no_table_is_refused_at_read_naming_the_table(tmp_path):
    configure(tmp_path, human_language="fr")
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
    configure(tmp_path, human_language="fr", language_tables_dir=str(folder))
    with pytest.raises(settings.SettingsError) as refused:
        settings.human_language()
    assert str(folder / "fr.toml") in str(refused.value)
    assert str(PACKAGE_TABLES / "fr.toml") in str(refused.value)


def test_a_language_that_is_no_table_name_is_refused(tmp_path):
    configure(tmp_path, human_language="../settings")
    with pytest.raises(settings.SettingsError) as refused:
        settings.human_language()
    assert refused.value.code == "bad_setting"


def test_key_missing_in_a_table_raises_naming_key_and_language(tmp_path):
    folder = plant_de(tmp_path, 'greeting = "Hallo"\n')
    configure(tmp_path, human_language="de", language_tables_dir=str(folder))
    with pytest.raises(language.LanguageError) as missing:
        language.text(KEY)
    assert KEY in str(missing.value)
    assert "'de'" in str(missing.value)
    assert "Hallo" not in str(missing.value)


def test_a_field_the_string_needs_but_the_call_lacks_raises(tmp_path):
    folder = plant_de(tmp_path, 'greeting = "Hallo, {name} {title}!"\n')
    configure(tmp_path, human_language="de", language_tables_dir=str(folder))
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
    with pytest.raises(settings.SettingsError) as refused:
        settings.human_language()
    assert refused.value.code == "bad_setting"
