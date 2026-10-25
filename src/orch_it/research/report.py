"""Which text of a run is its report (#396 decision 2).

The old bridges took the last assistant message, and lost a finished report when opencode
compacted the session after it and the model answered the injected "Continue..." turn in one
line (#380). `choose` reads every assistant message of the run instead. The candidates are:

- each assistant message's text, except opencode's compaction summary (`summary: true`), which
  is opencode's digest of the run, never the model's report;
- each chain of texts joined across a compaction that cut a message: the last assistant message
  before the compaction ended without finishing (`finish` neither `stop` nor `tool-calls`) or the
  compaction says `overflow`, and the next text comes after the injected continue turn (a
  synthetic user part with `metadata.compaction_continue`). A message that finished (`stop`)
  said all it had and is never joined to what follows.

The chosen one is the longest candidate that passes `check`; `report_from` says where it came
from: `last` (the last assistant message, the one the old bridges took), `earlier` (another
single message) or `joined`. With no candidate passing, the reason is the first failing test of
the longest candidate, or why there is no candidate at all: `model_error` (a message carries
opencode's `info.error`), `no_reply` (no assistant message), `empty` (no assistant text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MIN_CHARS = 3000
MIN_LINKS = 3
CHECK_REASONS = ("too_short", "few_links", "no_reddit")  # in the order the tests run
RUN_REASONS = ("model_error", "no_reply", "empty")
FINISHED = ("stop", "tool-calls")  # a message that ended on its own, not cut
_URL = re.compile(r"https?://[^\s<>()\[\]{}\"'`|\\]+")
_URL_TAIL = ".,;:!?*_~"


@dataclass(frozen=True)
class Report:
    text: str | None  # None: no candidate passed the check
    report_from: str | None  # last, earlier, joined
    reason: str | None  # None when a report was chosen
    chars: int
    links: int
    reddit_urls: int
    compactions: int
    last_chars: int  # the text of the last assistant message: what the old bridges took


def urls(text: str) -> set[str]:
    """The distinct URLs of `text`, trailing punctuation cut."""

    return {found.rstrip(_URL_TAIL) for found in _URL.findall(text)}


def is_reddit(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("reddit.com", "redd.it") or host.endswith(".reddit.com")


def check(text: str, *, require_reddit: bool) -> str | None:
    """None when `text` is a report, else the first test it fails: length, then links, then the
    Reddit rule, so the reason names the real cause (#380 F2)."""

    if len(text) < MIN_CHARS:
        return "too_short"
    found = urls(text)
    if len(found) < MIN_LINKS:
        return "few_links"
    if require_reddit and not any(map(is_reddit, found)):
        return "no_reddit"
    return None


def choose(messages: list, *, require_reddit: bool) -> Report:
    """The report of a run from its message list (`GET /session/{id}/message`). Any odd item
    (not an object, no info, a part without text) is read as nothing, never an error."""

    singles: list[tuple[str, int]] = []  # (text, index among the assistant messages)
    joined: list[str] = []
    compactions = 0
    error = False
    replies = 0
    chain: list[str] = []
    finish = None  # of the newest assistant message
    cut = False  # a compaction cut the newest message
    resumed = False  # the continue turn came after that compaction
    last_text = ""
    for message in messages:
        info, parts = _info(message), _parts(message)
        role = info.get("role")
        if role == "user":
            if any(part.get("type") == "compaction" for part in parts):
                compactions += 1
                cut = finish not in FINISHED or any(
                    part.get("overflow") is True for part in parts if part.get("type") == "compaction")
                resumed = False
            elif parts and all(_is_continue(part) for part in parts):
                resumed = True
            else:  # a prompt of its own: what follows answers it
                chain, cut, resumed = [], False, False
            continue
        if role != "assistant" or info.get("summary") is True:
            continue
        replies += 1
        error = error or bool(info.get("error"))
        finish = info.get("finish")
        text = "".join(part["text"] for part in parts
                       if part.get("type") == "text" and isinstance(part.get("text"), str))
        last_text = text
        if not text.strip():
            continue
        singles.append((text, replies))
        if chain and cut and resumed:
            chain.append(text)
            joined.append("\n".join(chain))
        else:
            chain = [text]
        cut = resumed = False

    candidates = [(text, "last" if index == replies else "earlier") for text, index in singles]
    candidates += [(text, "joined") for text in joined]
    candidates.sort(key=lambda candidate: len(candidate[0]), reverse=True)  # stable: a single wins a tie
    for text, where in candidates:
        if check(text, require_reddit=require_reddit) is None:
            found = urls(text)
            return Report(text, where, None, len(text), len(found), sum(map(is_reddit, found)),
                          compactions, len(last_text))
    if candidates:
        reason = check(candidates[0][0], require_reddit=require_reddit)
    else:
        reason = "model_error" if error else "empty" if replies else "no_reply"
    return Report(None, None, reason, 0, 0, 0, compactions, len(last_text))


def _info(message) -> dict:
    info = message.get("info") if isinstance(message, dict) else None
    return info if isinstance(info, dict) else {}


def _parts(message) -> list[dict]:
    parts = message.get("parts") if isinstance(message, dict) else None
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def _is_continue(part: dict) -> bool:
    metadata = part.get("metadata")
    return (part.get("type") == "text" and part.get("synthetic") is True
            and isinstance(metadata, dict) and metadata.get("compaction_continue") is True)
