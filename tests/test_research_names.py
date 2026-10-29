"""The bridge's text names no provider or model (#412 unit 5): the check reads the names to refuse
from the bridge's settings at run time, plus the fixed list of known families kept here. Every name
in these tests is invented (`acme-llm`) or a family word. The tests pass a settings dict in; only the
installation tests read a settings file, a tmp one named by ORCH_IT_SETTINGS."""

import json
import shutil
from pathlib import Path

from orch_it import cli
from orch_it.research.names import bridge_texts, find_names, refused_names, refused_names_from_installation

ROOT = Path(__file__).resolve().parents[1]
KNOWN_FAMILIES = ("gpt", "openai", "codex", "gemini", "llama", "mistral", "deepseek", "qwen", "grok", "kimi")
SETTINGS = {"research": {
    "provider": {"id": "acme-llm", "name": "Invented Maker", "npm": "@invented-sdk/llm-kit",
                 "options": {"baseURL": "https://api.acme-llm.example/v1"},
                 "models": {"acme-llm/acme-1": {"name": "Invented Large"}}},
    "models": {"small": "acme-llm/acme-1"},
}}


def test_the_orch_it_tree_names_no_known_family():
    assert find_names(bridge_texts(ROOT), refused_names(None, KNOWN_FAMILIES)) == []


def test_a_copy_with_gpt_back_in_the_research_start_description_is_red(tmp_path):
    for rel in bridge_texts(ROOT):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    mcp = tmp_path / "src" / "orch_it" / "research" / "mcp.py"
    text = mcp.read_text("utf-8")
    assert "by a research model (it can open Reddit" in text
    mcp.write_text(text.replace("by a research model (it can open Reddit", "by a GPT model (it can open Reddit", 1), "utf-8")
    line = next(n for n, row in enumerate(mcp.read_text("utf-8").splitlines(), 1) if "by a GPT model" in row)
    hits = find_names(bridge_texts(tmp_path), refused_names(None, KNOWN_FAMILIES))
    assert ("src/orch_it/research/mcp.py", line, "gpt") in hits


def test_a_provider_no_list_names_is_refused_from_the_settings_and_only_from_them(tmp_path):
    doc = tmp_path / "docs" / "agents" / "example.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("The brief goes to acme-llm, the first alias.\n", "utf-8")
    texts = bridge_texts(tmp_path)
    assert find_names(texts, refused_names(SETTINGS, KNOWN_FAMILIES)) == [("docs/agents/example.md", 1, "acme-llm")]
    assert find_names(texts, refused_names(None, KNOWN_FAMILIES)) == []


def test_a_line_marked_avoid_is_skipped_even_with_a_refused_word():
    files = {"GLOSSARY.md": "_Avoid_: acme-llm, gpt tool\n"}
    assert find_names(files, refused_names(SETTINGS, KNOWN_FAMILIES)) == []
    assert find_names({"GLOSSARY.md": "The brief goes to acme-llm.\n"}, refused_names(SETTINGS, KNOWN_FAMILIES)) \
        == [("GLOSSARY.md", 1, "acme-llm")]


def test_a_word_that_only_contains_a_refused_name_is_no_hit_acmellmx_xacme_llmx_gptq():
    files = {"README.md": "the acmellmx tool, xacme-llmx and gptq are other words\n"}
    assert find_names(files, refused_names(SETTINGS, KNOWN_FAMILIES)) == []


def test_neutral_words_and_names_under_three_characters_are_dropped():
    raw = {"research": {"provider": {"id": "provider", "name": "ab", "models": {}},
                        "models": {"small": "model/xy-9"}}}
    assert refused_names(raw) == {"small", "xy-9"}


def test_an_alias_is_a_name_only_from_five_characters():
    raw = {"research": {"provider": {"id": "acme-llm"},
                        "models": {"big": "acme-llm/acme-1", "small": "acme-llm/acme-2"}}}
    names = refused_names(raw)
    assert "small" in names and "big" not in names


def test_the_host_of_a_provider_option_url_gives_its_words_of_four_or_more_and_no_other_option_string():
    raw = {"research": {"provider": {"id": "acme-llm",
                                    "options": {"baseURL": "https://api.acme-llm.example/v1", "timeout": "5 seconds"}},
                        "models": {"small": "acme-llm/acme-1"}}}
    names = refused_names(raw)
    assert {"acme-llm", "example"} <= names
    assert "api" not in names and "seconds" not in names


def test_a_provider_model_entry_s_name_is_a_name():
    assert "invented large" in refused_names(SETTINGS)


def test_a_provider_name_is_a_name_and_each_of_its_words_of_four_or_more_is_one():
    assert {"invented maker", "invented", "maker"} <= refused_names(SETTINGS)


def test_the_known_list_is_lower_cased_and_filtered_the_same_way():
    assert refused_names(None, ["GPT", "ab", "Research"]) == {"gpt"}


def test_the_installation_settings_are_read_through_their_path(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(SETTINGS), "utf-8")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(path))
    assert refused_names_from_installation(KNOWN_FAMILIES) >= {"acme-llm", "gpt"}


def test_the_names_check_command_prints_each_hit_and_exits_1(tmp_path, monkeypatch, capsys):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(SETTINGS), "utf-8")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(settings_file))
    tree = tmp_path / "tree"
    (tree / "docs" / "agents").mkdir(parents=True)
    (tree / "docs" / "agents" / "note.md").write_text("# Notes\n\nThe brief goes to acme-llm.\n", "utf-8")
    assert cli.main(["research-check-names", "--work-tree", str(tree)]) == 1
    assert capsys.readouterr().out == "docs/agents/note.md:3: acme-llm\n"


def test_the_names_check_command_exits_0_with_no_such_line(tmp_path, monkeypatch, capsys):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps(SETTINGS), "utf-8")
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(settings_file))
    tree = tmp_path / "tree"
    (tree / "docs" / "agents").mkdir(parents=True)
    (tree / "docs" / "agents" / "note.md").write_text("# Notes\n\nThe brief goes to a model.\n", "utf-8")
    assert cli.main(["research-check-names", "--work-tree", str(tree)]) == 0
    assert "acme-llm" not in capsys.readouterr().out


def test_the_check_reads_the_skills_and_every_doc_folder_but_not_the_records(tmp_path):
    for rel in (".claude/skills/x/SKILL.md", ".claude/skills/x/notes.txt", "docs/deep/nested/note.md",
                "docs/agents/a.md", "docs/research/r.md", "docs/adr/0001.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n", "utf-8")
    assert set(bridge_texts(tmp_path)) == {".claude/skills/x/SKILL.md", "docs/deep/nested/note.md", "docs/agents/a.md"}


def test_the_research_subcommands_help_description_and_epilog_are_read_with_their_lines(tmp_path):
    (tmp_path / "src" / "orch_it").mkdir(parents=True)
    (tmp_path / "src" / "orch_it" / "cli.py").write_text(
        "commands = None\n"
        'commands.add_parser("research", help="HELP-MARK", description="DESCRIPTION-MARK")\n'
        'commands.add_parser("research-mcp", help="MCP-MARK", epilog="EPILOG-MARK")\n'
        'commands.add_parser("other", help="OTHER-MARK")\n', "utf-8")
    text = bridge_texts(tmp_path)["src/orch_it/cli.py"].splitlines()
    assert "HELP-MARK" in text[1] and "DESCRIPTION-MARK" in text[1]
    assert "MCP-MARK" in text[2] and "EPILOG-MARK" in text[2]
    assert "OTHER-MARK" not in "\n".join(text)


def test_the_text_read_is_the_bridge_files_and_not_the_records():
    keys = set(bridge_texts(ROOT))
    assert {".mcp.json", "src/orch_it/research/mcp.py", "src/orch_it/research/settings.py",
            "src/orch_it/cli.py", "GLOSSARY.md", "README.md"} <= keys
    assert not [key for key in keys if key.startswith(("docs/research/", "docs/adr/"))]
    assert "run a brief through the research bridge" in bridge_texts(ROOT)["src/orch_it/cli.py"]
    assert "add_parser" not in bridge_texts(ROOT)["src/orch_it/cli.py"]
