"""The usage of a research run: its own session and every session under it (#396 decision 3).

opencode's `task` tool starts a child session, and a child may start its own; the old meter
summed the top session only, so one run's four helpers (907,170 tokens in) were missing from its
row of 82,027 (#380 F3). `meter` walks the whole tree, breadth first and without recursion, at
any depth: a session's children are the ones opencode lists for it plus any its own `task` calls
name, each session read once however often it is named (a cycle included). Each assistant
message's `info.tokens` counts, the compaction summary included (its tokens were spent): input,
output, reasoning, cache read and cache write, every one recorded. A session whose read fails
with one of the caller's `read_errors` is counted in `unread_sessions` and the rest summed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, fields
from typing import Callable

MAX_SESSIONS = 100_000  # a tree past this is no real run; the walk stops and counts the rest unread


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(*(getattr(self, f.name) + getattr(other, f.name) for f in fields(Usage)))

    def detail(self, prefix: str = "") -> dict:
        return {f"{prefix}tokens_in": self.input, f"{prefix}tokens_out": self.output,
                f"{prefix}tokens_reasoning": self.reasoning, f"{prefix}tokens_cache_read": self.cache_read,
                f"{prefix}tokens_cache_write": self.cache_write}


@dataclass(frozen=True)
class TreeUsage:
    own: Usage
    helpers: Usage  # every descendant session, at any depth
    helper_sessions: int
    helper_depth: int  # 0: no helper; 1: children only; 2: a helper of a helper ...
    unread_sessions: int
    search_429: int  # web searches refused with HTTP 429, over the tree

    @property
    def total(self) -> Usage:
        return self.own + self.helpers

    def detail(self) -> dict:
        """The `research_run` `ended` fields of the tree's usage."""

        return (self.own.detail() | self.helpers.detail("helper_")
                | {"helper_sessions": self.helper_sessions, "helper_depth": self.helper_depth,
                   "unread_sessions": self.unread_sessions, "search_429": self.search_429})


def meter(root: str, *, children: Callable[[str], list], messages: Callable[[str], list],
          read_errors: tuple[type[BaseException], ...] = ()) -> TreeUsage:
    """The usage of the tree under `root`. `children(sid)` lists a session's child ids,
    `messages(sid)` its message list; an error of `read_errors` from either marks that session
    unread, any other error is raised."""

    own = helpers = Usage()
    seen = {root}
    queue = deque([(root, 0)])
    helper_sessions = depth = unread = limited = 0
    while queue:
        sid, level = queue.popleft()
        try:
            listed = messages(sid)
            named = children(sid)
        except read_errors:
            unread += 1
            continue
        listed = listed if isinstance(listed, list) else []
        used = sum((_tokens(message) for message in listed), Usage())
        limited += sum(map(_is_429, _tool_parts(listed)))
        if level == 0:
            own = used
        else:
            helpers += used
            helper_sessions += 1
            depth = max(depth, level)
        for child in [*(named if isinstance(named, list) else []), *_task_children(listed)]:
            if isinstance(child, str) and child and child not in seen:
                if len(seen) >= MAX_SESSIONS:
                    unread += 1
                    continue
                seen.add(child)
                queue.append((child, level + 1))
    return TreeUsage(own, helpers, helper_sessions, depth, unread, limited)


def _count(value) -> int:
    return value if type(value) is int and value > 0 else 0


def _tokens(message) -> Usage:
    info = message.get("info") if isinstance(message, dict) else None
    if not isinstance(info, dict) or info.get("role") != "assistant":
        return Usage()
    tokens = info.get("tokens")
    if not isinstance(tokens, dict):
        return Usage()
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return Usage(_count(tokens.get("input")), _count(tokens.get("output")), _count(tokens.get("reasoning")),
                 _count(cache.get("read")), _count(cache.get("write")))


def _tool_parts(listed: list):
    for message in listed:
        parts = message.get("parts") if isinstance(message, dict) else None
        for part in parts if isinstance(parts, list) else []:
            if isinstance(part, dict) and part.get("type") == "tool" and isinstance(part.get("state"), dict):
                yield part


def _task_children(listed: list) -> list:
    """The child ids the session's own `task` calls name (opencode records each in the call's
    `state.metadata.sessionId`)."""

    found = []
    for part in _tool_parts(listed):
        metadata = part["state"].get("metadata")
        if part.get("tool") == "task" and isinstance(metadata, dict):
            found.append(metadata.get("sessionId"))
    return found


def _is_429(part: dict) -> bool:
    state = part["state"]
    return part.get("tool") == "websearch" and state.get("status") == "error" and "429" in str(state.get("error"))
