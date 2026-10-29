"""The shape check on GitHub (#398 decision 3, the GitHub side): a workflow on `issues` events
calls `check_event` with the event's payload when a ticket gets `ready-for-agent` or a ready
ticket is edited, so a label set by hand on github.com, past every session, is judged too.

The ticket is read again through the `Hub` and judged as it stands now by `shape.violations`,
the same function the session hook calls. With violations, a comment lists every one (first,
so a label is never taken off unexplained) and the `ready-for-agent` label comes off; the body
is never edited. Any other event, a ticket no longer ready, or a pull request is skipped.

The literal check (#413 unit 6) judges the same body with this run's values (`read_values`: on GitHub Actions
the runner's own environment, the repo, owner and actor, the home folder, and the checkout's git setting) and
the check names of the workflows in the workspace. A values line that is not checked is the comment's last line.

A ticket the layer's own code opened (the skills issue, the red-on-main, no-result and workflow-watch tickets) is
written on the writer's machine, in the writer's `human_language` and with the layer's own commands in it, and
judged there when it is written. A runner has neither that language nor those values, so on GitHub such a ticket is
judged by the shape rules alone, without the language rule and the literal rules (#424 unit 8). It is such a ticket
when its body carries `CODE_WRITTEN_MARK` and its author is a bot or the repo's owner; a person's ticket is judged in
full whatever its body says.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from orch_it import language
from orch_it.tickets.hub import LAYER_MARK, Hub
from orch_it.tickets.model import CODE_WRITTEN_MARK, READY_LABEL, parse
from orch_it.tickets.shape import literal_violations, violations

CHECK_ERROR_CODES = ("event_unreadable",)
WRITER_BOUND_CODES = frozenset({"human_field_wrong_language"})  # judged where the ticket is written, not on a runner


class CheckError(ValueError):
    """`code`: `event_unreadable` (the event file is no JSON object)."""

    def __init__(self, code: str, detail: str = ""):
        if code not in CHECK_ERROR_CODES:
            raise ValueError(f"not a check error code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class CheckResult:
    number: int | None
    outcome: str  # kept | removed | skipped
    codes: list[str] = field(default_factory=list)

    @property
    def line(self) -> str:
        said = f": {', '.join(self.codes)}" if self.codes else ""
        return f"#{self.number} {self.outcome}{said}" if self.number else self.outcome


def read_event(path: str | Path) -> dict:
    try:
        found = json.loads(Path(path).read_text("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise CheckError("event_unreadable", f"{path}: {type(e).__name__}") from None
    if not isinstance(found, dict):
        raise CheckError("event_unreadable", f"{path}: not an object")
    return found


def check_event(event: object, hub: Hub, *, env=None, run=None, home=None, host=None) -> CheckResult:
    """Judge the ticket of an `issues` event; take `ready-for-agent` off a ticket with violations. `env`,
    `run`, `home` and `host` go to `read_values` (defaults: this process's environment and machine)."""

    issue_part = event.get("issue") if isinstance(event, dict) else None
    repository = event.get("repository") if isinstance(event, dict) else None
    if not isinstance(issue_part, dict) or not isinstance(repository, dict):
        return CheckResult(None, "skipped")
    number, repo = issue_part.get("number"), repository.get("full_name")
    if type(number) is not int or not isinstance(repo, str):
        return CheckResult(None, "skipped")
    labels = issue_part.get("labels") if isinstance(issue_part.get("labels"), list) else []
    action = event.get("action")
    if action == "labeled":
        if _name(event.get("label")) != READY_LABEL:
            return CheckResult(number, "skipped")
    elif action != "edited" or READY_LABEL not in {_name(label) for label in labels}:
        return CheckResult(number, "skipped")
    issue = hub.issue(repo, number)
    ready = [label for label in issue.labels if label.lower() == READY_LABEL] if issue else []
    if issue is None or issue.is_pr or not ready:
        return CheckResult(number, "skipped")  # its label already came off, or it is gone
    from orch_it.guards.kinds import read_job_names
    from orch_it.guards.values import read_values

    workspace = (os.environ if env is None else env).get("GITHUB_WORKSPACE") or os.getcwd()
    values = read_values(env=env, run=run, home=home, host=host)
    checks = read_job_names(str(Path(workspace) / ".github" / "workflows"))
    found = violations(parse(issue.body, title=issue.title, labels=issue.labels))
    if _opened_by_the_layer(event, issue.body):
        found = [v for v in found if v.code not in WRITER_BOUND_CODES]
    else:
        found += literal_violations(issue.body, values=values, checks=checks)
    if not found:
        return CheckResult(number, "kept")
    # the comment's words are read before the label moves: a language that cannot be read never leaves a label
    # off without its comment (text_or_refusal writes the error's own words in that case)
    notice = language.text_or_refusal("check_took_label", label=READY_LABEL)
    listed = "\n".join(f"- `{v.code}`: {_quiet(v.rule)}" for v in found)
    tail = values.render_not_checked()
    # the label first: a comment never says it came off when it did not; a failed comment fails the run
    hub.remove_label(repo, number, ready[0])
    hub.comment(repo, number, f"{LAYER_MARK}\n{notice}\n\n{listed}\n" + (f"{tail}\n" if tail else ""))
    return CheckResult(number, "removed", list(dict.fromkeys(v.code for v in found)))


def _opened_by_the_layer(event: dict, body: str) -> bool:
    """The layer's own code opened the ticket: its body has the mark, and the event (GitHub's own data) names a
    bot or the repo's owner (the layer's commands run under the owner's `gh`) as the author."""

    if CODE_WRITTEN_MARK not in body:
        return False
    author, owner = event["issue"].get("user"), event["repository"].get("owner")
    login = author.get("login") if isinstance(author, dict) else None
    if not isinstance(login, str) or not login:
        return False
    if author.get("type") == "Bot":
        return True
    return isinstance(owner, dict) and isinstance(owner.get("login"), str) and login.lower() == owner["login"].lower()


def _quiet(text: str) -> str:
    """A detail copied from the body, with no `@name` that would notify someone."""

    return text.replace("@", "@" + chr(0x200B))


def _name(label: object) -> str:
    name = label.get("name") if isinstance(label, dict) else label
    return name.strip().lower() if isinstance(name, str) else ""
