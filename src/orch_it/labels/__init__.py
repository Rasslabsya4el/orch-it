"""The label set of the new layer and `labels sync` (#398 decision 6).

`labels.json` beside this module is the set: the five roles, `parked` and `priority`, each with a
colour and a description, in the order the layer shows them. `sync` makes a repo's labels match
it: a missing label is created, one with another colour, description or letter case of its name
is updated, an equal one is kept. A label of the repo that is not in the set is listed as
`foreign` and never changed: sync deletes nothing, so it cannot take a label from a person or
from the old layer. The old layer's `autopilot`, `autopilot-stuck`, `provisional`, `hard-push` and
`flow-fix` are not carried.

`gh` is the seam: a function that takes the argument list of one `gh` call and returns its stdout
(default: the real CLI of the importer), so the whole sync runs in tests without a network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from orch_it.events import schema
from orch_it.testruns import github
from orch_it.testruns.github import Gh, GithubError

LABELS_FILE = Path(__file__).with_name("labels.json")
DESCRIPTION_CHARS = 100  # GitHub's own limit
LABEL_ERROR_CODES = ("labels_unreadable",)
_COLOUR = re.compile(r"[0-9a-f]{6}")


class LabelError(Exception):
    """`code`: `labels_unreadable` (the label file is missing or is no object of
    `{name: {"color", "description"}}` with 6 lowercase hex digits and a description of at most
    100 characters)."""

    def __init__(self, code: str, detail: str = ""):
        if code not in LABEL_ERROR_CODES:
            raise ValueError(f"not a label error code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def label_set() -> dict[str, dict[str, str]]:
    """The layer's labels, `{name: {"color": ..., "description": ...}}`, in file order."""

    try:
        found = json.loads(LABELS_FILE.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise LabelError("labels_unreadable", f"{LABELS_FILE.name}: {e}") from None
    if not isinstance(found, dict) or not found:
        raise LabelError("labels_unreadable", "expected an object of labels")
    seen: set[str] = set()
    for name, label in found.items():
        if not name or name.lower() in seen:
            raise LabelError("labels_unreadable", f"name {name!r} is empty or repeated")
        seen.add(name.lower())
        if not isinstance(label, dict) or set(label) != {"color", "description"}:
            raise LabelError("labels_unreadable", f"{name}: expected color and description")
        colour, description = label["color"], label["description"]
        if not isinstance(colour, str) or not _COLOUR.fullmatch(colour):
            raise LabelError("labels_unreadable", f"{name}: color is not 6 lowercase hex digits")
        if not isinstance(description, str) or len(description) > DESCRIPTION_CHARS:
            raise LabelError("labels_unreadable", f"{name}: description is no text of at most "
                                                  f"{DESCRIPTION_CHARS} characters")
    return found


def _read(repo: str, gh: Gh) -> list[dict]:
    """Every label of the repo; pages may come printed one after the other."""

    found: list[dict] = []
    for page in github._json_values(gh(["api", f"repos/{repo}/labels", "--paginate"])):
        if not isinstance(page, list):
            raise GithubError("gh_bad_output", "labels: expected a list")
        for label in page:
            if not isinstance(label, dict) or not isinstance(label.get("name"), str):
                raise GithubError("gh_bad_output", "labels: a label without a name")
            found.append(label)
    return found


def _same(held: dict, name: str, want: dict[str, str]) -> bool:
    """Whether a label of the repo is the wanted one: name to the letter, colour without `#`
    in lowercase, a missing or null description as empty."""

    colour = str(held.get("color") or "").lstrip("#").lower()
    return (held["name"] == name and colour == want["color"]
            and (held.get("description") or "") == want["description"])


def _fields(name: str, want: dict[str, str], key: str) -> list[str]:
    return ["-f", f"{key}={name}", "-f", f"color={want['color']}",
            "-f", f"description={want['description']}"]


def _settle(repo: str, held: dict, name: str, want: dict[str, str], gh: Gh) -> str:
    """A label the repo holds: kept when equal, else updated (a PATCH names the label as the repo
    spells it, so a change of letter case is a rename)."""

    if _same(held, name, want):
        return "kept"
    gh(["api", "-X", "PATCH", f"repos/{repo}/labels/{quote(held['name'], safe='')}",
        *_fields(name, want, "new_name")])
    return "updated"


def _create(repo: str, name: str, want: dict[str, str], gh: Gh) -> str:
    """Create the label. When that fails, another sync may have made it at the same moment (the
    text of `gh`'s error is not relied on): read again, and when it is there now, settle it as an
    existing label; else the original error stands."""

    try:
        gh(["api", "-X", "POST", f"repos/{repo}/labels", *_fields(name, want, "name")])
        return "created"
    except GithubError as failure:
        try:
            held = next((l for l in _read(repo, gh) if l["name"].lower() == name.lower()), None)
        except GithubError:
            held = None
        if held is None:
            raise failure from None
        return _settle(repo, held, name, want, gh)


def sync(repo: str, *, gh: Gh | None = None) -> list[tuple[str, str]]:
    """Make the repo's labels match the set; return `(name, action)` rows: the set's labels in
    file order (`created`, `updated` or `kept`), then the repo's other labels sorted by name
    (`foreign`). The label file is read and the repo checked before `gh` is called; nothing is
    deleted."""

    wanted = label_set()
    if not schema.is_repo(repo):
        raise GithubError("bad_repo", str(repo))
    gh = gh or github.run_gh
    held = {label["name"].lower(): label for label in _read(repo, gh)}
    rows = []
    for name, want in wanted.items():
        label = held.pop(name.lower(), None)
        rows.append((name, _create(repo, name, want, gh) if label is None
                     else _settle(repo, label, name, want, gh)))
    rows += [(label["name"], "foreign") for label in sorted(held.values(), key=lambda l: l["name"])]
    return rows
