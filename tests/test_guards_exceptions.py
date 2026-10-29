"""The exceptions list of #413 unit 2, in `orch_it.guards.exceptions`: `guards-exceptions.toml` entries with a
`file` (a glob or a path), a `pattern` (a regex over the line) and a `reason`. An entry with an empty or missing
reason fails on its own, naming the entry. `is_allowed_place` is the policy: the settings file and `docs/research/`.
All values are invented.
"""

import pytest

from orch_it.guards.exceptions import Exceptions, ExceptionsError, is_allowed_place, load_exceptions

SETTINGS = "conf/settings.json"


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def write(tmp_path, body):
    path = tmp_path / "guards-exceptions.toml"
    path.write_text(body, encoding="utf-8")
    return path


GOOD = r'''
[[exception]]
file = "tools/*.py"
pattern = 'ctypes\.windll'
kinds = ["one_os_call"]
reason = "invented: a probe that runs on one OS only"
'''


def test_entry_with_reason_loads_and_allows_a_matching_line(tmp_path):
    exceptions = load_exceptions(write(tmp_path, GOOD))
    assert isinstance(exceptions, Exceptions)
    assert exceptions.allows("tools/probe.py", "value = ctypes.windll.kernel32", "one_os_call") is True


def test_glob_must_match_the_file_and_pattern_the_line(tmp_path):
    exceptions = load_exceptions(write(tmp_path, GOOD))
    assert exceptions.allows("src/probe.py", "value = ctypes.windll.kernel32", "one_os_call") is False
    assert exceptions.allows("tools/probe.py", "value = nothing", "one_os_call") is False


def test_exact_path_matches_only_that_file(tmp_path):
    body = '[[exception]]\nfile = "scripts/exact.sh"\npattern = "fcntl"\nkinds = ["one_os_call"]\nreason = "invented"\n'
    exceptions = load_exceptions(write(tmp_path, body))
    assert exceptions.allows("scripts/exact.sh", "import fcntl", "one_os_call") is True
    assert exceptions.allows("scripts/exact.sh.bak", "import fcntl", "one_os_call") is False


def test_double_star_glob_matches_nested_folders(tmp_path):
    body = GOOD.replace('"tools/*.py"', '"docs/**/*.md"').replace(r"'ctypes\.windll'", "'probe'")
    exceptions = load_exceptions(write(tmp_path, body))
    assert exceptions.allows("docs/a/b/notes.md", "a probe here", "one_os_call") is True
    assert exceptions.allows("src/docs.md", "a probe here", "one_os_call") is False


def test_empty_reason_fails_naming_the_entry(tmp_path):
    body = GOOD + '\n[[exception]]\nfile = "tools/other.py"\npattern = "probe"\nkinds = ["one_os_call"]\nreason = "   "\n'
    with pytest.raises(ExceptionsError, match="reason") as err:
        load_exceptions(write(tmp_path, body))
    assert "tools/other.py" in str(err.value)


def test_each_refusal_carries_a_stable_code_beside_its_sentence(tmp_path):
    with pytest.raises(ExceptionsError) as err:
        load_exceptions(write(tmp_path, '[[exception]]\nfile = "tools/lone.py"\npattern = "probe"\nkinds = ["one_os_call"]\n'))
    assert err.value.code == "no_reason"
    assert "empty or missing reason" in str(err.value)


def test_missing_reason_fails_naming_the_entry(tmp_path):
    body = '[[exception]]\nfile = "tools/lone.py"\npattern = "probe"\nkinds = ["one_os_call"]\n'
    with pytest.raises(ExceptionsError, match="reason") as err:
        load_exceptions(write(tmp_path, body))
    assert "tools/lone.py" in str(err.value)


def test_invalid_regex_fails_naming_the_entry(tmp_path):
    body = '[[exception]]\nfile = "tools/bad.py"\npattern = "("\nkinds = ["one_os_call"]\nreason = "invented"\n'
    with pytest.raises(ExceptionsError) as err:
        load_exceptions(write(tmp_path, body))
    assert "tools/bad.py" in str(err.value)


def test_missing_file_means_no_exceptions(tmp_path):
    exceptions = load_exceptions(tmp_path / "absent.toml")
    assert exceptions.allows("tools/probe.py", "ctypes.windll", "one_os_call") is False


def test_settings_file_is_an_allowed_place():
    assert is_allowed_place(SETTINGS, SETTINGS) is True


def test_anything_under_docs_research_is_an_allowed_place():
    assert is_allowed_place("docs/research/brief.md", SETTINGS) is True
    assert is_allowed_place("docs\\research\\brief.md", SETTINGS) is True


def test_anything_under_docs_adr_is_an_allowed_place():
    assert is_allowed_place("docs/adr/0007-invented-decision.md", SETTINGS) is True
    assert is_allowed_place("docs\\adr\\0007-invented-decision.md", SETTINGS) is True


def test_other_paths_are_not_allowed_places():
    assert is_allowed_place("src/orch_it/names.py", SETTINGS) is False
    assert is_allowed_place("docs/researchers/brief.md", SETTINGS) is False
    assert is_allowed_place("docs/research.md", SETTINGS) is False
    assert is_allowed_place("docs/adrs/0007.md", SETTINGS) is False


def test_an_entry_excuses_only_its_kinds(tmp_path):
    exceptions = load_exceptions(write(tmp_path, GOOD))
    assert exceptions.allows("tools/probe.py", "ctypes.windll", "one_os_call") is True
    assert exceptions.allows("tools/probe.py", "ctypes.windll", "account") is False  # same file, same line, another kind


def test_a_list_of_files_is_one_entry(tmp_path):
    body = GOOD.replace('"tools/*.py"', '["tools/*.py", "bin/probe.sh"]')
    exceptions = load_exceptions(write(tmp_path, body))
    assert len(exceptions.entries) == 1
    assert exceptions.allows("tools/probe.py", "ctypes.windll", "one_os_call") is True
    assert exceptions.allows("bin/probe.sh", "ctypes.windll", "one_os_call") is True
    assert exceptions.allows("bin/other.sh", "ctypes.windll", "one_os_call") is False


def test_an_entry_with_no_kinds_fails_naming_the_entry(tmp_path):
    body = '[[exception]]\nfile = "tools/blanket.py"\npattern = "probe"\nreason = "invented"\n'
    with pytest.raises(ExceptionsError, match="kinds") as err:
        load_exceptions(write(tmp_path, body))
    assert "tools/blanket.py" in str(err.value)


@pytest.mark.parametrize("kinds", ["[]", '["no_such_kind"]', '["home"]', '"one_os_call"', "[1]"])
def test_a_kind_the_scan_cannot_excuse_fails_naming_the_entry(tmp_path, kinds):
    body = f'[[exception]]\nfile = "tools/odd.py"\npattern = "probe"\nkinds = {kinds}\nreason = "invented"\n'
    with pytest.raises(ExceptionsError, match="kinds") as err:
        load_exceptions(write(tmp_path, body))
    assert "tools/odd.py" in str(err.value)
