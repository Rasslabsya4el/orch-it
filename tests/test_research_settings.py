"""The settings of research runs (#396 decisions 3-5): the model aliases, the provider, the key's
variable, the bounds and the state folder are read from the `research` setting; no provider
model id, key or path of a machine is in the code."""

import re
from pathlib import Path

import pytest

from orch_it.research.settings import ResearchRefused, ResearchSettings
from orch_it.settings import SettingsError

SRC = Path(__file__).parents[1] / "src"
PROVIDER = {"id": "test-provider", "npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "http://127.0.0.1:9/v1"}}
RESEARCH = {"provider": PROVIDER, "key_env": "ORCH_IT_TEST_KEY",
            "models": {"small": "model-s-1", "big": "model-b-2"},
            "default_model": [{"up_to_chars": 100, "model": "small"}, {"model": "big"}]}


def settings(**research) -> ResearchSettings:
    return ResearchSettings.from_settings({"research": RESEARCH | research})


def test_an_alias_maps_to_its_provider_model_id():
    assert settings().model_id("big") == "model-b-2"


def test_an_alias_not_in_the_settings_is_refused_naming_the_ones_that_are():
    with pytest.raises(ResearchRefused) as refused:
        settings().model_id("luna")
    assert refused.value.code == "unknown_model"
    assert "big" in str(refused.value) and "small" in str(refused.value)


def test_the_default_model_follows_the_brief_s_size():
    chosen = settings()
    assert (chosen.alias_for(99), chosen.alias_for(100), chosen.alias_for(101)) == ("small", "small", "big")
    assert settings(default_model="small").alias_for(10**6) == "small"


def test_with_no_default_model_the_first_alias_is_the_default():
    assert settings(default_model=None).alias_for(5) == "small"


def test_no_research_setting_at_all_reads_but_refuses_a_run():
    empty = ResearchSettings.from_settings({})
    assert (empty.helpers, empty.minutes, empty.stall_minutes) == (False, 20.0, 10.0)
    with pytest.raises(ResearchRefused) as refused:
        empty.model_id("small")
    assert refused.value.code == "research_not_set"


@pytest.mark.parametrize("key,value", [
    ("models", {"": "x"}), ("models", {"a b": "x"}), ("models", {"a": ""}), ("models", {"a": " x"}),
    ("models", ["a"]), ("default_model", "nope"), ("default_model", [{"up_to_chars": 5, "model": "small"}]),
    ("default_model", [{"up_to_chars": -1, "model": "small"}, {"model": "big"}]),
    ("default_model", [{"model": "big"}, {"up_to_chars": 5, "model": "small"}]),
    ("helpers", "yes"), ("minutes", 0), ("minutes", float("nan")), ("minutes", 10**9), ("stall_minutes", True),
    ("key_env", "A-B"), ("key_env", ""), ("provider", {"id": ""}), ("provider", {"id": "p", "options": []}),
    ("provider", "p"), ("opencode", []), ("opencode", ["ok", 3]), ("opencode", "a\x00b"), ("state", ""),
])
def test_a_setting_of_the_wrong_shape_is_refused_naming_it(key, value):
    with pytest.raises(SettingsError) as refused:
        settings(**{key: value})
    assert refused.value.code == "bad_setting" and f"research.{key}" in str(refused.value)


def test_research_that_is_no_object_is_refused():
    with pytest.raises(SettingsError):
        ResearchSettings.from_settings({"research": ["x"]})


def test_the_key_is_read_from_the_variable_the_setting_names():
    chosen = settings()
    assert chosen.key({"ORCH_IT_TEST_KEY": "k-1"}) == "k-1"
    for env in ({}, {"ORCH_IT_TEST_KEY": ""}):
        with pytest.raises(ResearchRefused) as refused:
            chosen.key(env)
        assert refused.value.code == "key_missing" and "ORCH_IT_TEST_KEY" in str(refused.value)


def test_the_opencode_config_names_the_key_by_its_variable_and_lists_every_model():
    config = settings().opencode_config()
    provider = config["provider"]["test-provider"]
    assert provider["options"]["apiKey"] == "{env:ORCH_IT_TEST_KEY}"
    assert set(provider["models"]) == {"model-s-1", "model-b-2"}
    assert config["permission"]["task"] == "deny" and config["permission"]["bash"] == "deny"


def test_helpers_allowed_lets_the_task_tool_through():
    assert settings(helpers=True).opencode_config()["permission"]["task"] == "allow"


def test_a_provider_s_own_model_entry_is_kept():
    provider = PROVIDER | {"models": {"model-b-2": {"limit": {"context": 1000}}}}
    config = settings(provider=provider).opencode_config()
    assert config["provider"]["test-provider"]["models"]["model-b-2"] == {"limit": {"context": 1000}}


def test_the_state_folder_is_the_setting_or_beside_the_store(tmp_path):
    assert settings(state=str(tmp_path)).state == tmp_path
    assert settings().state.name == "research"


# The bridge's own code: the research package and the process helper it starts opencode with.
# (The Claude price table of the event store names Claude model ids by design; the guards of
# #395 read long paths through ctypes on Windows: neither is the bridge.)
BRIDGE = [SRC / "orch_it" / "research", SRC / "orch_it" / "proctree.py"]
SOURCE_RULES = {
    "a provider model id": re.compile(r"gpt-\d|-luna\b|-terra\b|deepseek-|gemini-\d|claude-(opus|sonnet|haiku|fable)-\d"),
    "the Windows registry": re.compile(r"\bwinreg\b"),
    "ctypes.windll": re.compile(r"\bwindll\b"),
    "a drive-letter path": re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(\\\\|\\|/)(?!/)"),
}


@pytest.mark.parametrize("rule", SOURCE_RULES, ids=list(SOURCE_RULES))
def test_the_source_holds_no(rule):
    """Rows 5 and 6: the code is the same on any machine with any provider."""

    files = [path for place in BRIDGE for path in ([place] if place.is_file() else place.rglob("*"))
             if path.is_file() and path.suffix in (".py", ".json", ".sql", ".md")]
    assert any(path.name == "settings.py" for path in files)
    hits = [f"{path}: {line}" for path in files
            for line in path.read_text("utf-8", "replace").splitlines() if SOURCE_RULES[rule].search(line)]
    assert hits == []
