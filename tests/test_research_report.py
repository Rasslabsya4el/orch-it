"""The report is chosen from every assistant message of a run, not the last one (#396 decision 2,
row 1). The fixtures are opencode message lists (`tests/fixtures/opencode/report/`, built by
`build_report_fixtures.py`), each with the answer it must get."""

import json
import time
from pathlib import Path

import pytest

from orch_it.events import schema
from orch_it.research import report

FIXTURES = Path(__file__).parent / "fixtures" / "opencode" / "report"
CASES = sorted(FIXTURES.glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def test_the_fixture_set_holds_ten_or_more_runs_with_and_without_a_report():
    cases = [load(path) for path in CASES]
    assert len(cases) >= 10
    assert sum(case["expect"]["report_from"] is not None for case in cases) >= 5
    assert sum(case["expect"]["reason"] is not None for case in cases) >= 5


@pytest.mark.parametrize("path", CASES, ids=[path.stem for path in CASES])
def test_each_run_gets_its_report_or_the_first_failing_test(path):
    case = load(path)
    expect = case["expect"]
    chosen = report.choose(case["messages"], require_reddit=case["require_reddit"])
    assert chosen.report_from == expect["report_from"]
    assert chosen.reason == expect["reason"]
    assert chosen.compactions == expect["compactions"]
    assert chosen.last_chars == expect["last_chars"]
    if expect["report_from"] is None:
        assert chosen.text is None
    else:
        assert len(chosen.text) == chosen.chars == expect["chars"]
        assert chosen.reddit_urls == expect["reddit_urls"]


def test_the_380_run_keeps_the_report_and_not_the_201_character_reply():
    chosen = report.choose(load(FIXTURES / "lost-report-380.json")["messages"], require_reddit=True)
    assert chosen.text.startswith("# Report")
    assert "completed in the prior message" not in chosen.text
    assert chosen.links == 44


def test_a_joined_report_holds_both_halves_in_order():
    messages = load(FIXTURES / "report-split-across-compaction.json")["messages"]
    texts = [p["text"] for m in messages if m["info"]["role"] == "assistant" and not m["info"].get("summary")
             for p in m["parts"] if p["type"] == "text"]
    chosen = report.choose(messages, require_reddit=True)
    assert chosen.text == texts[1] + "\n" + texts[2]


def test_a_report_that_finished_before_the_compaction_is_never_joined_to_what_follows():
    """A model that stopped (`finish: stop`) said all it had: the reply after the continue turn
    is another message, not the report's second half."""

    messages = load(FIXTURES / "report-then-1-short.json")["messages"]
    chosen = report.choose(messages, require_reddit=True)
    assert chosen.report_from == "earlier" and not chosen.text.endswith("Done above.")


def test_the_checks_run_length_then_links_then_reddit():
    long_no_links = "word " * 1000
    assert report.check(long_no_links, require_reddit=True) == "few_links"
    links = " ".join(f"https://example.org/{n}" for n in range(5))
    assert report.check("x" * 10 + links, require_reddit=True) == "too_short"
    assert report.check(long_no_links + links, require_reddit=True) == "no_reddit"
    assert report.check(long_no_links + links, require_reddit=False) is None
    assert report.check(long_no_links + links + " https://old.reddit.com/r/a/1", require_reddit=True) is None


def test_a_reddit_link_is_a_url_on_reddit_not_the_word():
    text = "word " * 1000 + " ".join(f"https://example.org/{n}" for n in range(5))
    assert report.check(text + " see reddit.com for more", require_reddit=True) == "no_reddit"
    assert report.check(text + " https://notreddit.com/x", require_reddit=True) == "no_reddit"
    assert report.check(text + " https://redd.it/abc", require_reddit=True) is None


def test_the_same_url_twice_is_one_link():
    text = "word " * 1000 + " https://example.org/a" * 5 + " https://www.reddit.com/r/x/1"
    assert report.check(text, require_reddit=True) == "few_links"


@pytest.mark.parametrize("messages", [
    [], [{}], [{"info": None, "parts": None}], [{"info": {"role": "assistant"}, "parts": [{"type": "text"}]}],
    [{"info": {"role": "assistant", "tokens": "many"}, "parts": [{"type": "text", "text": 7}]}],
    [{"info": {"role": "user"}, "parts": [{"type": "compaction"}]}, "not a message"],
])
def test_a_half_written_or_odd_message_list_is_read_without_error(messages):
    chosen = report.choose(messages, require_reddit=True)
    assert chosen.text is None and chosen.reason in ("no_reply", "empty")


def test_the_ended_event_takes_where_the_report_came_from_and_the_reason_as_codes():
    def ended(**detail):
        return {"id": "e1", "kind": "step.ended", "at": "2026-10-26T10:00:00Z", "repo": "o/r",
                "ticket": None, "role": "external-researcher", "session": None, "parent_session": None,
                "turn": None, "step": "research_run", "run": "r1", "machine": "m", "os": "x",
                "layer_version": "0", "note": None, "detail": {"outcome": "passed"} | detail}

    schema.validate(ended(report_from="joined", compactions=1, links=4))
    schema.validate(ended(outcome="failed", reason="too_short", last_chars=201))
    for bad in ({"report_from": "first"}, {"outcome": "failed", "reason": "no reddit.com URL"}):
        with pytest.raises(schema.EventError) as refused:
            schema.validate(ended(**bad))
        assert refused.value.code == "bad_code"


def test_a_huge_run_is_read_in_time():
    one = {"info": {"role": "assistant", "finish": "stop", "tokens": {"input": 1}},
           "parts": [{"type": "text", "text": "word " * 2000}]}
    started = time.monotonic()
    chosen = report.choose([one] * 2000, require_reddit=False)
    assert chosen.reason == "few_links" and time.monotonic() - started < 30


def test_every_reason_choose_gives_is_a_code_of_the_ended_event():
    assert set(report.CHECK_REASONS + report.RUN_REASONS) <= schema.codes_of("research_run.ended", "reason")
    assert {case_["expect"]["reason"] for case_ in map(load, CASES)} - {None} <= set(
        report.CHECK_REASONS + report.RUN_REASONS)


def reply(*texts, finish="stop", **info):
    return {"info": {"role": "assistant", "finish": finish, "tokens": {"input": 1}} | info,
            "parts": [{"type": "text", "text": text} for text in texts]}


LINKS = " ".join(f"https://example.org/{n}" for n in range(3)) + " https://www.reddit.com/r/a/1"


def test_of_two_equally_long_reports_the_newer_one_is_chosen():
    text = "word " * 1000 + LINKS
    assert report.choose([reply(text), reply(text)], require_reddit=True).report_from == "last"


def test_two_text_parts_of_one_message_never_run_together_into_one_url():
    body = "word " * 1000 + " https://a.example.org/1 https://a.example.org/2 https://a.example.org/3"
    chosen = report.choose([reply(body, "tail https://www.reddit.com/r/x/1")], require_reddit=True)
    assert chosen.reason is None and chosen.links == 4


def test_blank_padding_does_not_make_a_short_text_long():
    assert report.check(" \n" * 4000 + LINKS, require_reddit=True) == "too_short"


def test_a_list_that_is_no_list_is_no_reply():
    for odd in (None, "text", 7, {"info": {}}):
        assert report.choose(odd, require_reddit=True).reason == "no_reply"


def test_links_in_parentheses_and_markdown_and_capitals_are_read_whole():
    found = report.urls("[a](https://en.wikipedia.org/wiki/Foo_(bar)) HTTPS://Example.ORG/x/ "
                        "https://example.org/x#part (https://example.org/y).")
    assert found == {"https://en.wikipedia.org/wiki/Foo_(bar)", "https://example.org/x",
                     "https://example.org/y"}


def test_a_failed_model_call_is_named_even_when_it_left_a_short_text():
    chosen = report.choose([reply("partial answer", finish="error", error={"name": "APIError"})],
                           require_reddit=True)
    assert chosen.reason == "model_error"
