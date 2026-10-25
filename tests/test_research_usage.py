"""A run's usage is its session and every descendant session at any depth, each of the five
token counts recorded (#396 decision 3, row 2). The trees are fixtures
(`tests/fixtures/opencode/tree/`); the expected totals below are summed by hand from the
per-session numbers in `build_tree_fixtures.py`, and the depth-1 ones are #380 F3's (907,170
in, 33,431 out, 7.07M cache read in four helpers)."""

import json
from pathlib import Path

import pytest

from orch_it.events.store import Store
from orch_it.research import usage

TREES = Path(__file__).parent / "fixtures" / "opencode" / "tree"


class Tree:
    """The two readers the meter takes, served from a fixture tree."""

    def __init__(self, name: str):
        fixture = json.loads((TREES / f"{name}.json").read_text("utf-8"))
        self.root = fixture["root"]
        self.sessions = fixture["sessions"]
        self.reads: list[str] = []

    def children(self, sid: str) -> list[str]:
        return list(self.sessions[sid]["children"])

    def messages(self, sid: str) -> list:
        self.reads.append(sid)
        return self.sessions[sid]["messages"]

    def meter(self) -> usage.TreeUsage:
        return usage.meter(self.root, children=self.children, messages=self.messages)


def token_fields(tokens_in, tokens_out, reasoning, cache_read, cache_write, prefix=""):
    return {f"{prefix}tokens_in": tokens_in, f"{prefix}tokens_out": tokens_out,
            f"{prefix}tokens_reasoning": reasoning, f"{prefix}tokens_cache_read": cache_read,
            f"{prefix}tokens_cache_write": cache_write}


def test_a_helper_of_a_helper_is_counted_in_every_field():
    detail = Tree("depth-3").meter().detail()
    assert detail == (token_fields(1000, 200, 30, 5000, 100)
                      | token_fields(320500, 12050, 1600, 2070000, 4103, prefix="helper_")
                      | {"helper_sessions": 3, "helper_depth": 2, "unread_sessions": 0, "search_429": 0})


def test_the_total_of_a_tree_is_the_run_and_its_helpers():
    metered = Tree("depth-3").meter()
    assert metered.total == usage.Usage(321500, 12250, 1630, 2075000, 4203)


def test_the_380_helpers_are_counted_beside_the_run():
    detail = Tree("depth-1-helpers").meter().detail()
    assert detail == (token_fields(82027, 10703, 5001, 248832, 0)
                      | token_fields(907170, 33431, 71118, 7072768, 0, prefix="helper_")
                      | {"helper_sessions": 4, "helper_depth": 1, "unread_sessions": 0, "search_429": 0})


def test_a_run_with_no_helper_has_zero_helper_tokens():
    detail = Tree("depth-0").meter().detail()
    assert detail == (token_fields(12000, 900, 150, 40000, 512) | token_fields(0, 0, 0, 0, 0, prefix="helper_")
                      | {"helper_sessions": 0, "helper_depth": 0, "unread_sessions": 0, "search_429": 0})


def test_a_child_known_only_from_its_parent_s_task_call_is_counted():
    tree = Tree("depth-3")
    tree.sessions["c1"]["children"] = []  # the children list missed g1; c1's task part names it
    assert tree.meter().detail()["helper_tokens_in"] == 320500


def test_a_session_listed_twice_or_in_a_cycle_is_counted_once():
    tree = Tree("depth-3")
    tree.sessions["g1"]["children"] = ["run", "c1"]
    tree.sessions["c2"]["children"] = ["g1"]
    metered = tree.meter()
    assert metered.detail()["helper_tokens_in"] == 320500
    assert sorted(tree.reads) == ["c1", "c2", "g1", "run"]


def test_a_session_that_cannot_be_read_is_counted_unread_and_the_rest_is_summed():
    tree = Tree("depth-3")
    real = tree.messages

    def messages(sid):
        if sid == "c2":
            raise ConnectionError("gone")
        return real(sid)

    metered = usage.meter("run", children=tree.children, messages=messages, read_errors=(ConnectionError,))
    assert metered.detail()["unread_sessions"] == 1
    assert metered.detail()["helper_tokens_in"] == 320000


def test_an_error_the_caller_did_not_name_is_not_swallowed():
    tree = Tree("depth-0")

    def messages(sid):
        raise KeyError(sid)

    with pytest.raises(KeyError):
        usage.meter("run", children=tree.children, messages=messages, read_errors=(ConnectionError,))


@pytest.mark.parametrize("tokens", [None, "many", {"input": "3"}, {"input": -5, "cache": "x"},
                                    {"input": True}, {"input": 2.5}, {"cache": {"read": None}}])
def test_odd_token_values_count_as_nothing(tokens):
    messages = [{"info": {"role": "assistant", "tokens": tokens}}, "junk", {"info": None},
                {"info": {"role": "assistant", "tokens": {"input": 7}}}]
    metered = usage.meter("run", children=lambda sid: [], messages=lambda sid: messages)
    assert metered.own == usage.Usage(7, 0, 0, 0, 0)


def test_user_messages_carry_no_tokens_and_a_compaction_summary_does():
    messages = [{"info": {"role": "user", "tokens": {"input": 999}}},
                {"info": {"role": "assistant", "summary": True, "tokens": {"input": 30, "output": 3}}}]
    metered = usage.meter("run", children=lambda sid: [], messages=lambda sid: messages)
    assert metered.own == usage.Usage(30, 3, 0, 0, 0)


def test_search_429s_are_counted_across_the_tree():
    tree = Tree("depth-3")
    limited = {"type": "tool", "tool": "websearch",  # as opencode records Exa's refusal
               "state": {"status": "error", "error": "StatusCode: non 2xx status code (429 POST https://mcp.exa.ai/mcp)"}}
    others = [{"type": "tool", "tool": "websearch", "state": {"status": "error", "error": "timeout"}},
              {"type": "tool", "tool": "websearch", "state": {"status": "error", "error": "port 14290 refused"}},
              {"type": "tool", "tool": "webfetch", "state": {"status": "completed", "output": "HTTP 429 explained"}}]
    tree.sessions["run"]["messages"][-1]["parts"] += [limited, *others]
    tree.sessions["g1"]["messages"][-1]["parts"] += [limited, limited]
    assert tree.meter().detail()["search_429"] == 3


def test_the_ended_event_carries_the_numbers_of_the_tree_into_the_store(tmp_path):
    store = Store.open(tmp_path / "events")
    detail = Tree("depth-3").meter().detail()
    with store.writer() as writer:
        writer.write({"kind": "step.ended", "repo": "o/r", "ticket": None, "role": "external-researcher",
                      "session": None, "parent_session": None, "turn": None, "step": "research_run",
                      "run": "r1", "note": None, "detail": {"outcome": "passed"} | detail})
    rows = store.query("SELECT tokens_in + helper_tokens_in, tokens_out + helper_tokens_out,"
                       " tokens_reasoning + helper_tokens_reasoning, tokens_cache_read + helper_tokens_cache_read,"
                       " tokens_cache_write + helper_tokens_cache_write, helper_depth FROM events"
                       " WHERE step = 'research_run'").rows
    assert rows == [(321500, 12250, 1630, 2075000, 4203, 2)]


def test_a_wide_tree_is_walked_without_recursion():
    sessions = {"run": [f"s{n}" for n in range(3000)]}
    sessions |= {f"s{n}": [f"s{n + 1}"] for n in range(2999)}  # also a chain 3000 deep
    one = [{"info": {"role": "assistant", "tokens": {"input": 1}}}]
    metered = usage.meter("run", children=lambda sid: sessions.get(sid, []), messages=lambda sid: one)
    assert metered.detail()["helper_sessions"] == 3000
    assert metered.helpers.input == 3000


def test_a_session_whose_children_cannot_be_listed_keeps_its_tokens_and_its_task_children():
    tree = Tree("depth-3")

    def children(sid):
        if sid == "c1":
            raise ConnectionError("gone")
        return tree.children(sid)

    metered = usage.meter("run", children=children, messages=tree.messages, read_errors=(ConnectionError,))
    assert metered.detail()["helper_tokens_in"] == 320500  # c1 kept, g1 found through c1's task call
    assert metered.detail()["unread_sessions"] == 1


def test_a_reply_that_is_no_list_is_counted_unread():
    tree = Tree("depth-3")
    metered = usage.meter("run", children=tree.children,
                          messages=lambda sid: {"messages": []} if sid == "c2" else tree.messages(sid))
    assert metered.detail()["unread_sessions"] == 1 and metered.detail()["helper_tokens_in"] == 320000


def test_children_listed_by_opencode_alone_are_found():
    tree = Tree("depth-3")
    for session in tree.sessions.values():
        for message in session["messages"]:
            message["parts"] = [part for part in message["parts"] if part.get("tool") != "task"]
    assert tree.meter().detail()["helper_tokens_in"] == 320500


def test_a_tree_that_never_ends_is_cut_at_the_cap():
    made = iter(range(10**9))
    metered = usage.meter("run", children=lambda sid: [f"s{next(made)}", f"s{next(made)}"],
                          messages=lambda sid: [])
    assert metered.helper_sessions == usage.MAX_SESSIONS - 1
    assert metered.unread_sessions > 0
