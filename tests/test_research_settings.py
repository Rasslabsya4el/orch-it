"""The settings of research runs (#396 decisions 3-5): the model aliases, the provider, the key's
variable, the bounds and the state folder are read from the `research` setting; no provider
model id, key or path of a machine is in the code."""

import json
import re
from pathlib import Path

import pytest

from orch_it.research.names import find_names, refused_names
from orch_it.research.settings import ResearchRefused, ResearchSettings
from orch_it.settings import SettingsError
from test_research_names import KNOWN_FAMILIES

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
    ("state", "relative/folder"), ("helper", True), ("stall_minute", 5),
    ("default_model", [{"up_to_chars": 50, "model": "small"}, {"up_to_chars": 50, "model": "big"}, {"model": "big"}]),
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
    assert chosen.key({"ORCH_IT_TEST_KEY": " k-1\n"}) == "k-1"
    for env in ({}, {"ORCH_IT_TEST_KEY": ""}, {"ORCH_IT_TEST_KEY": "  \n"}):
        with pytest.raises(ResearchRefused) as refused:
            chosen.key(env)
        assert refused.value.code == "key_missing" and "ORCH_IT_TEST_KEY" in str(refused.value)


def test_the_key_variable_defaults_to_a_neutral_name_and_the_mcp_front_passes_that_name():
    unset = ResearchSettings.from_settings({"research": {k: v for k, v in RESEARCH.items() if k != "key_env"}})
    assert unset.key_env == "RESEARCH_API_KEY"
    env = json.loads((Path(__file__).parents[1] / ".mcp.json").read_text("utf-8"))["mcpServers"]["orch-it-research"]["env"]
    assert [name for name in env if not name.startswith("ORCH_IT_")] == ["RESEARCH_API_KEY"]


def test_a_key_variable_of_another_name_in_the_settings_is_the_one_read():
    chosen = settings(key_env="ACME_LLM_KEY")
    assert chosen.key({"ACME_LLM_KEY": "k-2"}) == "k-2"
    with pytest.raises(ResearchRefused) as refused:
        chosen.key({"RESEARCH_API_KEY": "k-2"})
    assert refused.value.code == "key_missing" and "ACME_LLM_KEY" in str(refused.value)
    assert chosen.opencode_config()["provider"]["test-provider"]["options"]["apiKey"] == "{env:ACME_LLM_KEY}"


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
    # the known families (gpt, deepseek, gemini, ...) are the names check's job: see the test below
    "a model id of the Luna, Terra or Claude families": re.compile(r"-luna\b|-terra\b|claude-(opus|sonnet|haiku|fable)-\d"),
    "the Windows registry": re.compile(r"\bwinreg\b"),
    "ctypes.windll": re.compile(r"\bwindll\b"),
    "a drive-letter path": re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(\\\\|\\|/)(?!/)"),
}


def _bridge_files() -> list[Path]:
    files = [path for place in BRIDGE for path in ([place] if place.is_file() else place.rglob("*"))
             if path.is_file() and path.suffix in (".py", ".json", ".sql", ".md")]
    assert any(path.name == "settings.py" for path in files)
    return files


@pytest.mark.parametrize("rule", SOURCE_RULES, ids=list(SOURCE_RULES))
def test_the_source_holds_no(rule):
    """Rows 5 and 6: the code is the same on any machine with any provider."""

    hits = [f"{path}: {line}" for path in _bridge_files()
            for line in path.read_text("utf-8", "replace").splitlines() if SOURCE_RULES[rule].search(line)]
    assert hits == []


def test_the_source_names_no_known_family():
    """Rows 5 and 6, by the names check (#412 unit 5): no family of the fixed list is named in the bridge's code."""

    files = {path.relative_to(SRC.parent).as_posix(): path.read_text("utf-8", "replace") for path in _bridge_files()}
    assert find_names(files, refused_names(None, KNOWN_FAMILIES)) == []


def test_the_task_tool_is_denied_last_so_no_earlier_rule_lets_it_through():
    """#174 carried: opencode applies the last matching permission; `task` comes last at the top
    and in the agent, whatever else the config allows."""

    config = settings().opencode_config()
    assert list(config["permission"])[-1] == "task" and config["permission"]["task"] == "deny"
    [agent] = config["agent"].values()
    assert list(agent["permission"])[-1] == "task" and agent["permission"]["task"] == "deny"
    assert agent["permission"]["webfetch"] == "allow"
