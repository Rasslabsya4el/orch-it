"""`python -m orch_it research <brief>`, the command-line front of the research bridge (#396
decision 1): one line of the result, exit 0 passed, 1 failed, 3 any other outcome, 2 a refusal;
`--result-file` writes the result shape as JSON."""

import json

from orch_it import cli
from research_bridge import BRIEF, PROMPT_ONLY, Bridge, report_messages


def prepared(tmp_path, monkeypatch, **scenario) -> Bridge:
    """A bench whose settings file and environment the command reads."""

    made = Bridge(tmp_path, **scenario)
    for name, value in made.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(made.settings_file))
    made.brief = tmp_path / "brief.md"
    made.brief.write_text(BRIEF, "utf-8")
    return made


def main(bridge, *args):
    return cli.main(["--store", str(bridge.store.root), "research", str(bridge.brief),
                     "--work-tree", str(bridge.work_tree), "--repo", "owner/repo", *args])


def test_a_passed_run_prints_its_line_and_exits_0(tmp_path, monkeypatch, capsys):
    bridge = prepared(tmp_path, monkeypatch)
    assert main(bridge, "--model", "tiny") == 0
    line = capsys.readouterr().out
    assert line.startswith("run=") and "outcome=passed" in line and "report_from=last" in line
    assert (bridge.work_tree / "docs" / "research" / "2026-10-26-test.md").is_file()
    assert bridge.seen()["prompts"][0]["model"]["modelID"] == "model-x-9"


def test_the_result_file_holds_the_result_shape(tmp_path, monkeypatch):
    bridge = prepared(tmp_path, monkeypatch)
    result = tmp_path / "result.json"
    assert main(bridge, "--result-file", str(result)) == 0
    shape = json.loads(result.read_text("utf-8"))
    assert shape["outcome"] == "passed" and shape["tokens"]["tokens_in"] > 0
    assert shape["report_path"].endswith("2026-10-26-test.md")


def test_an_alias_not_in_the_settings_is_refused_with_the_ones_there_are(tmp_path, monkeypatch, capsys):
    bridge = prepared(tmp_path, monkeypatch)
    assert main(bridge, "--model", "luna") == 2
    err = capsys.readouterr().err
    assert err.startswith("error: unknown_model") and "tiny" in err and "other" in err
    assert not bridge.record.exists()


def test_a_brief_that_cannot_be_read_is_refused(tmp_path, monkeypatch, capsys):
    bridge = prepared(tmp_path, monkeypatch)
    bridge.brief.unlink()
    assert main(bridge) == 2
    assert "brief_unreadable" in capsys.readouterr().err


def test_a_failed_run_exits_1(tmp_path, monkeypatch, capsys):
    bridge = prepared(tmp_path, monkeypatch, messages=report_messages("thin"))
    assert main(bridge) == 1
    assert "reason=too_short" in capsys.readouterr().out


def test_a_bounded_run_exits_3(tmp_path, monkeypatch, capsys):
    bridge = prepared(tmp_path, monkeypatch, mode="hang", messages=PROMPT_ONLY, minutes=0.02)
    assert main(bridge) == 3
    assert "reason=time_bound" in capsys.readouterr().out
