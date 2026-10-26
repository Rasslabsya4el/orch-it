"""The brain's hand-off home (orchestration-layer#403 decision 3): one pinned issue per repo, title
`Brain hand-off`, label `brain-handoff`, its number the setting `brain_handoff_issue`.

- `ensure` makes it so and changes nothing when it already is: the label in the repo, one open
  issue with the label (the set one; else the oldest open one carrying the label; else a new one),
  the label on it, the issue pinned, the setting naming it. Other open issues with the label are
  named (`others`), never changed.
- `write` is a hand-off: one comment on the issue (the text, and a link to the ticket in work when
  there is one), a one-line pointer back on that ticket, and a `handoff.written` event.
- `latest` is the newest comment on it not written by a bot: what the next chat reads.

`HandoffHub` is the seam: `orch_it.tickets.hub.GhHub` answers it through `gh`; the tests answer it
from memory. The issue is never parked, never offered by the ready queue and never in the sweep
(`orch_it.tickets.queues`).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol

from orch_it import settings
from orch_it.brain import HANDOFF_SETTING, BrainSettings
from orch_it.testruns.github import GithubError
from orch_it.tickets.hub import LAYER_MARK, Comment, Hub, Issue

TITLE = "Brain hand-off"
LABEL = "brain-handoff"
ROLE = "brain"
BODY = ("The brain's hand-off home. Each comment is one hand-off: what is done, what is next, the live runs with "
        "their readers, the pending question, the owner's words verbatim. The brain's session hook hands the newest "
        "one to the next brain chat. This issue is never offered as work and never closed.")
COMMENT_CHARS = 65_000  # GitHub takes at most 65,536 characters in a comment
HANDOFF_ERROR_CODES = ("handoff_issue_unset", "handoff_issue_missing", "handoff_empty", "handoff_too_long",
                       "handoff_unreadable", "pin_refused")


class HandoffError(Exception):
    """`code`: `handoff_issue_unset` (no `brain_handoff_issue`: run `brain handoff ensure`),
    `handoff_issue_missing` (the set issue is not an open issue), `handoff_empty`,
    `handoff_unreadable` (the hand-off's file cannot be read as UTF-8 text),
    `handoff_too_long` (over `COMMENT_CHARS`), `pin_refused` (GitHub would not pin it: a repo
    pins at most three issues)."""

    def __init__(self, code: str, detail: str = ""):
        if code not in HANDOFF_ERROR_CODES:
            raise ValueError(f"not a hand-off error code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class HandoffHub(Hub, Protocol):
    def listed(self, repo: str, label: str) -> list[Issue]:
        """The repo's open issues (no pull requests) carrying the label."""

    def create(self, repo: str, title: str, body: str, labels: list[str]) -> int:
        """Open an issue; its number."""

    def pinned(self, repo: str) -> list[int]:
        """The numbers of the repo's pinned issues."""

    def pin(self, repo: str, number: int) -> None: ...

    def ensure_label(self, repo: str, name: str) -> bool:
        """Create the layer's label in the repo when it has none of that name; True when created."""


@dataclass(frozen=True)
class Ensured:
    number: int
    actions: tuple[str, ...]  # of: label_created, created, labelled, pinned, setting_written
    others: tuple[int, ...] = ()  # other open issues carrying the label, left as they are

    def line(self) -> str:
        said = ", ".join(self.actions) or "nothing to change"
        others = f"; also labelled {LABEL}: {', '.join(f'#{n}' for n in self.others)}" if self.others else ""
        return f"brain hand-off #{self.number}: {said}{others}\n"


def ensure(hub: HandoffHub, repo: str) -> Ensured:
    """See the module text."""

    wanted = BrainSettings.of(settings.load(), repo).handoff_issue
    actions = ["label_created"] if hub.ensure_label(repo, LABEL) else []
    issue = hub.issue(repo, wanted) if wanted else None
    labelled = sorted(hub.listed(repo, LABEL), key=lambda found: found.number)
    if issue is None or not issue.open or issue.is_pr:
        if labelled:
            issue = labelled[0]
        else:
            issue = hub.issue(repo, hub.create(repo, TITLE, BODY, [LABEL]))
            if issue is None:
                raise GithubError("gh_bad_output", f"{repo}: the new hand-off issue cannot be read")
            actions.append("created")
    if LABEL not in issue.labels:
        hub.write(repo, issue.number, issue.body, [*issue.labels, LABEL])
        actions.append("labelled")
    if issue.number not in hub.pinned(repo):
        try:
            hub.pin(repo, issue.number)
        except GithubError as e:
            raise HandoffError("pin_refused", f"{repo}#{issue.number}: {e}") from None
        actions.append("pinned")
    if wanted != issue.number:
        settings.save_in_repo(repo, HANDOFF_SETTING, issue.number)
        actions.append("setting_written")
    others = tuple(found.number for found in labelled if found.number != issue.number)
    return Ensured(issue.number, tuple(actions), others)


def latest(hub: Hub, repo: str, number: int) -> Comment | None:
    """The newest comment on the hand-off issue not written by a bot, or None."""

    said = [comment for comment in hub.comments(repo, number) if not comment.bot]
    return max(said, key=lambda comment: comment.at) if said else None


def write(hub: Hub, store, repo: str, text: str, *, ticket: int | None = None, session: str | None = None) -> str:
    """Post one hand-off; returns its line. The comment is posted before the event is written: a
    store that cannot be written is said on stderr and the hand-off stands."""

    number = BrainSettings.of(settings.load(), repo).handoff_issue
    if number is None:
        raise HandoffError("handoff_issue_unset", f"{repo}: run `python -m orch_it brain handoff ensure`")
    text = text.strip()
    if not text:
        raise HandoffError("handoff_empty")
    body = f"{text}\n\nTicket in work: #{ticket}" if ticket is not None else text
    if len(body) > COMMENT_CHARS:
        raise HandoffError("handoff_too_long", f"{len(body)} characters, at most {COMMENT_CHARS}")
    home = hub.issue(repo, number)
    if home is None or not home.open or home.is_pr:
        raise HandoffError("handoff_issue_missing", f"{repo}#{number}: run `python -m orch_it brain handoff ensure`")
    hub.comment(repo, number, body)
    if ticket is not None:
        hub.comment(repo, ticket, f"The brain's hand-off on this ticket: #{number} {LAYER_MARK}")
    try:
        with store.writer() as writer:
            writer.write({"kind": "handoff.written", "repo": repo, "ticket": ticket, "role": ROLE, "session": session,
                          "parent_session": None, "turn": None, "step": None, "run": None, "note": None,
                          "detail": {"context_tokens": None, "tickets_closed": None, "tickets_parked": None,
                                     "entry_ref": f"{repo}#{number}"}})
    except Exception as e:  # noqa: BLE001 - the hand-off is posted whatever the store does
        print(f"brain handoff: event not written: {type(e).__name__}: {e}", file=sys.stderr)
    return f"hand-off written on #{number}" + (f", pointer on #{ticket}" if ticket is not None else "") + "\n"
