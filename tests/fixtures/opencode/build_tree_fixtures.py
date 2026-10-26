"""Builds the session-tree fixtures `tree/*.json` (#396 row 2): per session its children (as
`GET /session/{id}/children` lists them) and its messages (as `GET /session/{id}/message`), the
tokens of each session split over a few assistant messages. Run it from the repo root to rebuild
them: `python tests/fixtures/opencode/build_tree_fixtures.py`. The expected totals are not
here: the tests hold them, summed by hand.

`depth-1-helpers` is the #380 F3 run: a terra run whose `task` tool made four child sessions;
the per-session totals are that run's (input, output, reasoning, cache read, cache write), the
ids made up. `depth-3` adds a grandchild under one child (the helper of a helper, the outside
case) and a child with no children. `depth-0` is a run with no helper.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent / "tree"


def messages(session: str, totals: tuple[int, int, int, int, int], pieces: int = 3) -> list[dict]:
    """A user prompt, then `pieces` assistant messages whose tokens add up to `totals`."""

    out = [{"info": {"id": f"{session}-m0", "role": "user"}, "parts": [{"type": "text", "text": "go"}]}]
    left = list(totals)
    for n in range(pieces):
        share = left if n == pieces - 1 else [value // (pieces - n) for value in left]
        left = [a - b for a, b in zip(left, share)]
        i, o, r, read, write = share
        out.append({"info": {"id": f"{session}-m{n + 1}", "role": "assistant", "finish": "tool-calls",
                             "tokens": {"total": sum(share), "input": i, "output": o, "reasoning": r,
                                        "cache": {"read": read, "write": write}}},
                    "parts": [{"type": "step-start"}, {"type": "text", "text": f"step {n}"}]})
    return out


def task_part(child: str) -> dict:
    """The parent's `task` tool call that started `child` (opencode records the child's id)."""

    return {"type": "tool", "tool": "task", "state": {"status": "completed", "input": {},
                                                      "metadata": {"sessionId": child}}}


def tree(root: str, sessions: dict[str, tuple[tuple, list[str]]]) -> dict:
    out = {}
    for sid, (totals, children) in sessions.items():
        listed = messages(sid, totals)
        listed[-1]["parts"] += [task_part(child) for child in children]
        out[sid] = {"children": children, "messages": listed}
    return {"root": root, "sessions": out}


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "depth-0": tree("run", {"run": ((12000, 900, 150, 40000, 512), [])}),
        "depth-1-helpers": tree("run", {
            "run": ((82027, 10703, 5001, 248832, 0), ["h1", "h2", "h3", "h4"]),
            "h1": ((163463, 6031, 7140, 675840, 0), []),
            "h2": ((266526, 8096, 21297, 2507776, 0), []),
            "h3": ((199695, 6686, 16305, 1095168, 0), []),
            "h4": ((277486, 12618, 26376, 2793984, 0), []),
        }),
        "depth-3": tree("run", {
            "run": ((1000, 200, 30, 5000, 100), ["c1", "c2"]),
            "c1": ((20000, 3000, 400, 70000, 0), ["g1"]),
            "c2": ((500, 50, 0, 0, 7), []),
            "g1": ((300000, 9000, 1200, 2000000, 4096), []),
        }),
    }
    for name, fixture in fixtures.items():
        (HERE / f"{name}.json").write_text(json.dumps(fixture, indent=1) + "\n", "utf-8", newline="\n")


if __name__ == "__main__":
    main()
