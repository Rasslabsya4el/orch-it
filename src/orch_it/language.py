"""The text orch-it writes to a human, in the language of the setting `human_language` (#413 decision 6).

One interface: `text(key, **fields)`. The strings live in one TOML table per language (flat
`key = "string"`), found by `settings.table_path`. A language with no table is refused when the
setting is read (`settings.human_language`); a key missing in a table raises `LanguageError` naming
key and language. Never English instead of the language asked for.
"""

from __future__ import annotations

import tomllib

from orch_it import settings


class LanguageError(ValueError):
    """`code`: `table_unreadable` (the table is no TOML of strings), `missing_text` (the table has no
    such key), `missing_field` (the string needs a field the call did not give)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def text(key: str, **fields: object) -> str:
    """The string `key` in the current language. With `fields` it is `str.format`ted with them;
    with none it is returned as written (a regex or a brace in it stays)."""

    return _lookup(settings.human_language(), key, fields)


def english(key: str, **fields: object) -> str:
    """`text` in English under every setting: for what is written as a record for agents and scripts
    (a ticket's agent block), which a person's language does not change."""

    return _lookup("en", key, fields)


def _lookup(language: str, key: str, fields: dict[str, object]) -> str:
    path = settings.table_path(language)
    try:
        table = tomllib.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise LanguageError("table_unreadable", f"{path}: {e}") from None
    value = table.get(key)
    if not isinstance(value, str):
        raise LanguageError("missing_text", f"no key {key!r} in the {language!r} table ({path})")
    if not fields:
        return value
    try:
        return value.format(**fields)
    except (KeyError, IndexError) as e:
        raise LanguageError("missing_field", f"{key!r} in the {language!r} table needs {e}") from None


def text_or_refusal(key: str, **fields: object) -> str:
    """`text`, for a refusal or a guard's notice that must still be written: when the setting names no
    table (or the settings cannot be read) the words are the error's own, which name the missing table,
    and no other language's words are written instead. The caller's refusal is never lost to a setting."""

    try:
        return text(key, **fields)
    except (settings.SettingsError, LanguageError) as e:
        return str(e)
