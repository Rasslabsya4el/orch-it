"""The closed sets of the event list and the check every event passes before it is stored.

`closed_sets.json` beside this module is the only place a kind, step id, outcome or reason
code is defined (#393 decision 4). `validate` raises `EventError` on a closed-set breach; the
error carries a code from `ERROR_CODES`, never prose to parse.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import cache
from importlib import resources

PAIR_KINDS = ("step.started", "step.ended")
SIDE_OF_PAIR = {"step.started": "started", "step.ended": "ended"}

ERROR_CODES = (
    "not_an_object",
    "unknown_field",
    "missing_field",
    "null_field",
    "bad_type",
    "unknown_kind",
    "unknown_step",
    "step_missing",
    "bad_at",
    "at_without_zone",
    "bad_repo",
    "bad_code",
    "missing_outcome",
    "bad_outcome",
    "too_long",
)

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class EventError(ValueError):
    """An event outside the closed sets. `code` is one of `ERROR_CODES`; `field` names the
    offending field (dotted for a detail field) or is None."""

    def __init__(self, code: str, field: str | None = None, value: object = None):
        assert code in ERROR_CODES, code
        self.code = code
        self.field = field
        self.value = value
        super().__init__(f"{code}: {field}={value!r}" if field else code)


@cache
def _sets() -> dict:
    text = resources.files("orch_it.events").joinpath("closed_sets.json").read_text("utf-8")
    return json.loads(text)


def envelope_fields() -> tuple[str, ...]:
    return tuple(_sets()["envelope"])


def step_ids() -> tuple[str, ...]:
    return tuple(_sets()["steps"])


def point_kinds() -> tuple[str, ...]:
    return tuple(_sets()["kinds"])


def kinds() -> tuple[str, ...]:
    return PAIR_KINDS + point_kinds()


def outcomes() -> list[str]:
    return list(_sets()["codes"]["outcome"])


def code_lists() -> dict[str, list[str]]:
    return {name: list(codes) for name, codes in _sets()["codes"].items()}


def fields_of(owner: str) -> dict[str, dict]:
    """The named fields of `owner`: `envelope`, a point kind, or `<step id>.started` /
    `<step id>.ended`. A `<step>.ended` side always names `outcome` and `reason`."""

    sets = _sets()
    if owner == "envelope":
        return dict(sets["envelope"])
    if owner in sets["kinds"]:
        return dict(sets["kinds"][owner])
    step, _, side = owner.rpartition(".")
    if step in sets["steps"] and side in ("started", "ended"):
        fields = dict(sets["steps"][step][side])
        if side == "ended":
            fields = {"outcome": {"codes": "outcome"}, "reason": {"codes": None}} | fields
        return fields
    raise KeyError(owner)


def codes_of(owner: str, field: str) -> set[str] | None:
    """The codes `field` of `owner` must take, or None when its value is open. A step's ended
    `reason` takes its own list plus the codes any `step.ended` may carry; a step whose ended
    `reason` is named but has no list written is open."""

    spec = fields_of(owner).get(field)
    if spec is None:
        return None
    name = spec.get("codes")
    lists = _sets()["codes"]
    if owner.endswith(".ended") and field == "reason":
        if "codes" not in spec:
            return None
        own = set(lists[name]) if name else set()
        return own | set(lists["step_ended_any"])
    return set(lists[name]) if name else None


def validate(event: object) -> dict:
    """Return `event` unchanged when it is inside the closed sets, else raise `EventError`.

    An event is the envelope's fields, every one present (null where the envelope allows it),
    plus `detail`, an object. A named detail field with a code list must take a code from it;
    any other detail field is open."""

    if not isinstance(event, dict):
        raise EventError("not_an_object")
    envelope = _sets()["envelope"]
    for name in event:
        if name not in envelope and name != "detail":
            raise EventError("unknown_field", name)
    for name, spec in envelope.items():
        if name not in event:
            raise EventError("missing_field", name)
        _check_envelope_field(name, spec, event[name])
    if "detail" not in event:
        raise EventError("missing_field", "detail")
    detail = event["detail"]
    if not isinstance(detail, dict):
        raise EventError("bad_type", "detail", detail)

    kind = event["kind"]
    if kind in PAIR_KINDS:
        if event["step"] is None:
            raise EventError("step_missing", "step")
        owner = f"{event['step']}.{SIDE_OF_PAIR[kind]}"
        if kind == "step.ended":
            if "outcome" not in detail:
                raise EventError("missing_outcome", "detail.outcome")
            if detail["outcome"] not in _sets()["codes"]["outcome"]:
                raise EventError("bad_outcome", "detail.outcome", detail["outcome"])
    else:
        owner = kind
    for name, value in detail.items():
        spec = fields_of(owner).get(name)
        if spec is None or value is None:
            continue
        if "max_chars" in spec and isinstance(value, str) and len(value) > spec["max_chars"]:
            raise EventError("too_long", f"detail.{name}", value)
        codes = codes_of(owner, name)
        if codes is not None:
            for item in _code_items(value):
                if not _code_in(item, codes):
                    raise EventError("bad_code", f"detail.{name}", item)
    return event


def _check_envelope_field(name: str, spec: dict, value: object) -> None:
    if value is None:
        if not spec["nullable"]:
            raise EventError("null_field", name)
        return
    kind = spec["type"]
    if kind == "int":
        if type(value) is not int:
            raise EventError("bad_type", name, value)
        return
    if not isinstance(value, str) or not value:
        raise EventError("bad_type", name, value)
    if kind == "kind" and value not in kinds():
        raise EventError("unknown_kind", name, value)
    if kind == "step" and value not in _sets()["steps"]:
        raise EventError("unknown_step", name, value)
    if kind == "repo" and not _REPO.match(value):
        raise EventError("bad_repo", name, value)
    if kind == "code" and value not in _sets()["codes"][spec["codes"]]:
        raise EventError("bad_code", name, value)
    if kind == "timestamp":
        parse_at(value)


def parse_at(value: str) -> datetime:
    """An `at` as an aware datetime; raises `bad_at` for no timestamp, `at_without_zone` for
    one without an offset (a reader compares in UTC, so a local time without one is unknown)."""

    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise EventError("bad_at", "at", value) from None
    if moment.tzinfo is None:
        raise EventError("at_without_zone", "at", value)
    return moment


def _code_items(value: object) -> list:
    if isinstance(value, dict):
        return list(value)
    if isinstance(value, list):
        return value
    return [value]


def _code_in(item: object, codes: set[str]) -> bool:
    if not isinstance(item, str):
        return False
    if item in codes:
        return True
    return any(code.endswith(">") and "<" in code and item.startswith(code[: code.index("<")])
               and len(item) > code.index("<") for code in codes)


def detail_field_names() -> list[str]:
    """Every detail field the list names, once, in a stable order (the index's columns)."""

    sets = _sets()
    names: dict[str, None] = {"outcome": None, "reason": None}
    for step in sets["steps"].values():
        for side in ("started", "ended"):
            names.update(dict.fromkeys(step[side]))
    for fields in sets["kinds"].values():
        names.update(dict.fromkeys(fields))
    return list(names)
