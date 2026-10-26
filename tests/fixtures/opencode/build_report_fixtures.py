"""Builds the report-selection fixtures `report/*.json` (#396 row 1): opencode message lists in
the shape of `GET /session/{id}/message`, each with what `orch_it.research.report.choose` must
answer. Run it from the repo root to rebuild them: `python tests/fixtures/opencode/build_report_fixtures.py`.

The shapes are opencode's own (1.18, read from its database): a compaction is a user message
holding a `compaction` part (`auto`, `overflow`), then an assistant message with
`summary: true` (agent `compaction`), then a synthetic user text part with
`metadata.compaction_continue`. `lost-report-380` is the run of #380 rebuilt from its numbers:
the message order, finishes, part kinds and token counts of that session, its texts replaced by
filler of the same length and URL counts (38,973 characters, 44 URLs, 9 on Reddit; the summary
13,391; the last message 201). No text, path or tool output of a real session is in here.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent / "report"
T0 = 1_700_000_000_000  # epoch ms of each fixture's first message
WORDS = ("the source says the free tier keeps one small machine per account and the forum "
         "thread adds that the limit applies per region while the wiki disagrees ").split()


def prose(chars: int, urls: int = 0, reddit: int = 0, seed: int = 0) -> str:
    """Markdown filler of exactly `chars` characters holding `urls` distinct URLs, `reddit` of
    them on reddit.com."""

    links = [f"https://www.reddit.com/r/selfhosted/comments/x{seed}a{n}/thread_{n}/" for n in range(reddit)]
    links += [f"https://docs.example.org/s{seed}/page-{n}" for n in range(urls - reddit)]
    pieces, n = [], 0
    for i, link in enumerate(links):
        pieces.append(f"\n> quoted passage {i} ({link})\n")
    body = "".join(pieces)
    words = []
    while len(body) + sum(len(w) + 1 for w in words) < chars:
        words.append(WORDS[n % len(WORDS)])
        n += 1
    text = "# Report\n" + " ".join(words) + body
    if len(text) > chars:  # cut the filler words, never a link
        text = text[: len(text) - len(body)][: chars - len(body)] + body
    assert len(text) == chars and text.count("https://") == urls, (chars, urls)
    return text


class Session:
    """Builds one message list in opencode's shape."""

    def __init__(self):
        self.messages: list[dict] = []
        self.n = 0

    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n:04d}"

    def _time(self) -> int:
        return T0 + 1000 * len(self.messages)

    def user(self, text: str = "Brief: three questions.", *, synthetic: bool = False,
             continue_turn: bool = False) -> Session:
        part = {"id": self._id("prt"), "type": "text", "text": text}
        if synthetic:
            part["synthetic"] = True
        if continue_turn:
            part["metadata"] = {"compaction_continue": True}
        self.messages.append({"info": {"id": self._id("msg"), "role": "user", "agent": "research",
                                       "time": {"created": self._time()}}, "parts": [part]})
        return self

    def assistant(self, text: str | None = None, *, finish: str = "stop", tools: tuple = (),
                  tokens: dict | None = None, summary: bool = False, error: dict | None = None) -> Session:
        parts = [{"id": self._id("prt"), "type": "step-start"}]
        if text is not None:
            parts.append({"id": self._id("prt"), "type": "text", "text": text})
        for tool in tools:
            parts.append({"id": self._id("prt"), "type": "tool", "tool": tool,
                          "state": {"status": "completed", "input": {}}})
        parts.append({"id": self._id("prt"), "type": "step-finish", "reason": finish})
        created = self._time()
        info = {"id": self._id("msg"), "role": "assistant", "agent": "compaction" if summary else "research",
                "mode": "compaction" if summary else "research", "finish": finish,
                "time": {"created": created, "completed": created + 500},
                "tokens": tokens or {"input": 1000, "output": 100, "reasoning": 10,
                                     "cache": {"read": 0, "write": 0}}}
        if summary:
            info["summary"] = True
        if error is not None:
            info["error"] = error
        self.messages.append({"info": info, "parts": parts})
        return self

    def compaction(self, summary_text: str, *, overflow: bool = False,
                   summary_tokens: dict | None = None) -> Session:
        """opencode's compaction: the marker, the summary, the injected continue turn."""

        self.messages.append({"info": {"id": self._id("msg"), "role": "user", "agent": "research",
                                       "time": {"created": self._time()}},
                              "parts": [{"id": self._id("prt"), "type": "compaction", "auto": True,
                                         "overflow": overflow}]})
        self.assistant(summary_text, summary=True, tokens=summary_tokens)
        return self.user("Continue if you have next steps, or stop and ask for clarification if "
                         "you are unsure how to proceed.", synthetic=True, continue_turn=True)


def tokens(i: int, o: int, r: int, read: int, write: int = 0) -> dict:
    return {"total": i + o + r + read + write, "input": i, "output": o, "reasoning": r,
            "cache": {"write": write, "read": read}}


# The #380 session (opencode.db, its message order): (finish, tools, tokens) per assistant step
STEPS_380 = [
    ("tool-calls", ("websearch",) * 8, tokens(6158, 481, 125, 0)),
    ("tool-calls", ("webfetch",) * 12, tokens(22000, 507, 2850, 5632)),
    ("tool-calls", ("websearch",) * 12, tokens(56998, 639, 1382, 27136)),
    ("tool-calls", ("webfetch",) * 12, tokens(21991, 423, 1650, 83456)),
    ("tool-calls", ("webfetch",) * 12, tokens(88801, 532, 2016, 104960)),
    ("tool-calls", ("websearch",) * 8, tokens(67638, 443, 2281, 193024)),
    ("tool-calls", ("webfetch",) * 8, tokens(16737, 347, 2585, 259584)),
    ("tool-calls", ("grep",), tokens(22013, 70, 1652, 275968)),
    ("tool-calls", ("read",), tokens(1419, 59, 0, 297472)),
    ("tool-calls", ("websearch",) * 7, tokens(1933, 420, 1613, 298496)),
    ("tool-calls", ("webfetch",) * 7, tokens(10789, 289, 2446, 299520)),
    ("tool-calls", ("grep",), tokens(38926, 62, 3890, 309760)),
    ("tool-calls", ("read",), tokens(1430, 61, 1185, 347648)),
    ("tool-calls", ("grep",), tokens(914, 64, 1455, 348672)),
    ("tool-calls", ("webfetch",) * 4, tokens(1178, 170, 2183, 348672)),
    ("tool-calls", ("websearch",), tokens(11298, 61, 1571, 349696)),
]
LAST_380 = ("The requested research report has been completed in the prior message. No workspace "
            "files were changed, per the instruction to deliver the report only in chat.\n\nThere "
            "are no remaining prescribed steps.")
assert len(LAST_380) == 201


def lost_report_380() -> Session:
    s = Session().user(prose(2278, seed=99))
    for n, (finish, tools, used) in enumerate(STEPS_380):
        text = prose(134) if n == 0 else None
        s.assistant(text, finish=finish, tools=tools, tokens=used)
    s.assistant(prose(38973, urls=44, reddit=9, seed=380), tokens=tokens(4656, 9075, 6068, 359936))
    s.compaction(prose(13391, urls=41, reddit=6, seed=381), summary_tokens=tokens(33757, 3191, 2070, 0))
    s.assistant(LAST_380, tokens=tokens(17290, 42, 360, 4608))
    return s


REPORT = dict(chars=6000, urls=8, reddit=2)


def cases() -> dict[str, dict]:
    report = prose(seed=1, **REPORT)
    out = {}

    def case(name, session, about, *, require_reddit=True, **expect):
        out[name] = {"about": about, "require_reddit": require_reddit,
                     "expect": {"report_from": None, "reason": None, "compactions": 0} | expect,
                     "messages": session.messages}

    case("lost-report-380", lost_report_380(),
         "#380: the 38,973-character report, then compaction, summary, continue and a 201-character reply",
         report_from="earlier", chars=38973, compactions=1, reddit_urls=9, last_chars=201)

    s = Session().user().assistant("Searching first.", finish="tool-calls", tools=("websearch",))
    s.assistant(report).compaction(prose(3500, urls=5, reddit=1, seed=2)).assistant("Done above.")
    case("report-then-1-short", s, "a report, then one short reply after a compaction",
         report_from="earlier", chars=6000, compactions=1, reddit_urls=2, last_chars=11)

    s = Session().user().assistant(report)
    for n in range(2):
        s.compaction(prose(3500, urls=5, reddit=1, seed=10 + n)).assistant(f"Nothing left to do ({n}).")
    case("report-then-2-short", s, "a report, then two short replies, each after a compaction",
         report_from="earlier", chars=6000, compactions=2, reddit_urls=2, last_chars=23)

    s = Session().user().assistant(report)
    s.compaction(prose(3500, urls=5, reddit=1, seed=20))
    for n in range(5):
        s.assistant(f"Checking item {n} again.", finish="tool-calls", tools=("webfetch",))
    s.assistant("All items checked; the report above stands.")
    case("report-then-5-short", s,
         "a report, then five short messages (four with tool calls) after a compaction",
         report_from="earlier", chars=6000, compactions=1, reddit_urls=2, last_chars=43)

    first = prose(2500, urls=2, reddit=1, seed=30)
    second = prose(2600, urls=2, reddit=0, seed=31)
    s = Session().user().assistant("Plan: search, then write.", finish="tool-calls", tools=("websearch",))
    s.assistant(first, finish="length").compaction(prose(3200, urls=3, reddit=1, seed=32), overflow=True)
    s.assistant(second)
    case("report-split-across-compaction", s,
         "the report cut by an overflow compaction and finished after the continue turn",
         report_from="joined", chars=2500 + 1 + 2600, compactions=1, reddit_urls=1, last_chars=2600)

    s = Session().user().assistant(report).compaction(prose(3500, urls=5, reddit=1, seed=33), overflow=True)
    s.assistant(LAST_380)
    case("report-then-overflow-compaction", s,
         "a finished report, then a compaction that says overflow, then the 201-character reply: never joined",
         report_from="earlier", chars=6000, compactions=1, reddit_urls=2, last_chars=201)

    s = Session().user().assistant(report)
    case("report-alone", s, "the report in one message with nothing after it",
         report_from="last", chars=6000, reddit_urls=2, last_chars=6000)

    s = Session().user().assistant(prose(1316, urls=4, reddit=1, seed=40))
    case("thin", s, "a 1,316-character answer: too short", reason="too_short", last_chars=1316)

    s = Session().user().assistant(prose(201, seed=41))
    case("short-no-reddit", s, "201 characters with no link and no Reddit: the length test fails first",
         reason="too_short", last_chars=201)

    s = Session().user().assistant(prose(5000, urls=1, seed=42))
    case("no-links", s, "long enough, one link", reason="few_links", last_chars=5000)

    no_reddit = Session().user().assistant(prose(5000, urls=6, seed=43))
    case("no-reddit", no_reddit, "long, six links, none on Reddit, Reddit required",
         reason="no_reddit", last_chars=5000)
    case("no-reddit-allowed", no_reddit, "the same run with --no-reddit: accepted",
         require_reddit=False, report_from="last", chars=5000, reddit_urls=0, last_chars=5000)

    s = Session().user().assistant(prose(4000, urls=4, seed=44))
    s.compaction(prose(9000, urls=12, reddit=3, seed=45)).assistant("Nothing more.")
    case("summary-is-no-report", s,
         "the compaction summary is long with Reddit links, the model's own text is not: the summary is never chosen",
         reason="no_reddit", compactions=1, last_chars=13)

    s = Session().user().assistant("", tokens=tokens(900, 0, 0, 0))
    case("empty", s, "the model answered with no text", reason="empty", last_chars=0)

    s = Session().user()
    case("no-reply", s, "the prompt is there, no assistant message", reason="no_reply", last_chars=0)

    s = Session().user()
    for tool in ("websearch", "webfetch", "webfetch"):
        s.assistant(None, finish="tool-calls", tools=(tool,))
    case("only-tool-calls", s, "assistant steps with tool calls and no text", reason="empty", last_chars=0)

    s = Session().user().assistant(None, finish="error", tokens=tokens(0, 0, 0, 0),
                                   error={"name": "APIError", "data": {"message": "429 Too Many Requests"}})
    case("model-error", s, "the model call failed", reason="model_error", last_chars=0)

    s = Session().user().assistant(report).assistant(None, finish="error",
                                                     error={"name": "MessageAbortedError", "data": {}})
    case("report-then-error", s, "a report, then an error on a later step: the report stands",
         report_from="earlier", chars=6000, reddit_urls=2, last_chars=0)
    return out


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    for old in HERE.glob("*.json"):
        old.unlink()
    for name, case in cases().items():
        (HERE / f"{name}.json").write_text(json.dumps(case, indent=1, ensure_ascii=False) + "\n", "utf-8",
                                           newline="\n")


if __name__ == "__main__":
    main()
