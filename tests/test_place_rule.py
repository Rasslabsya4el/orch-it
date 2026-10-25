"""The place rule (#394 decision 5, row 4): a table of cases whose picks were computed by hand.

The places, in the rule's tie order: `own` (Linux, capacity 1), `machine:box` (Linux, capacity
2), `machine:win` (Windows, capacity 1), `github` (Linux and Windows, capacity 20). Every
expected finish below is start overhead (median of measured starts, 0 with none) + run time
(median of measured runs; a place with none takes the own place's median), worked out by hand
in each case's comment. The rule reads no OS state, so the table holds on every OS."""

import pytest

from orch_it.testruns.place_rule import Measured, Offer, choose

OWN = Offer("own", ("linux",), on=True, capacity=1)
BOX = Offer("machine:box", ("linux",), on=True, capacity=2)
WIN = Offer("machine:win", ("windows",), on=True, capacity=1)
GH = Offer("github", ("linux", "windows"), on=True, capacity=20)
GH_OFF = Offer("github", ("linux", "windows"), on=False, capacity=20)
ALL = [OWN, BOX, WIN, GH]

OWN_12 = Measured(starts=(0.0,), runs=(10.0, 12.0, 14.0))  # start 0, run median 12
GH_9 = Measured(starts=(2.0, 4.0), runs=(5.0, 7.0, 6.0))  # start median 3, run median 6

CASES = [
    # 1. nothing measured anywhere: own, every place offered with no expected finish
    ("nothing measured goes to own", "any", ALL, {}, {}, {},
     "own", "nothing_measured",
     [("own", None, None), ("machine:box", None, None), ("machine:win", None, None),
      ("github", None, None)]),
    # 2. a Windows run, nothing measured: own and box lack the OS; the first place left is win
    ("os_missing drops; nothing measured takes the first place left", "windows", ALL, {}, {}, {},
     "machine:win", "nothing_measured",
     [("own", None, "os_missing"), ("machine:box", None, "os_missing"), ("machine:win", None, None),
      ("github", None, None)]),
    # 3. own 0 + 12 = 12; box and win unmeasured take own's 12; github 3 + 6 = 9: github
    ("the earliest expected finish wins", "any", ALL, {}, {"own": OWN_12, "github": GH_9}, {},
     "github", "earliest_finish",
     [("own", 12.0, None), ("machine:box", 12.0, None), ("machine:win", 12.0, None),
      ("github", 9.0, None)]),
    # 4. github off; own 12, box 12, win 12 tie: own first
    ("place_off drops; a tie goes to own", "any", [OWN, BOX, WIN, GH_OFF], {},
     {"own": OWN_12, "github": GH_9}, {},
     "own", "earliest_finish",
     [("own", 12.0, None), ("machine:box", 12.0, None), ("machine:win", 12.0, None),
      ("github", None, "place_off")]),
    # 5. own at capacity (1 live of 1), github off; box 12 and win 12 tie: box, the first machine
    ("busy drops; a tie between machines goes by settings order", "any", [OWN, BOX, WIN, GH_OFF],
     {"own": 1}, {"own": OWN_12}, {},
     "machine:box", "earliest_finish",
     [("own", None, "busy"), ("machine:box", 12.0, None), ("machine:win", 12.0, None),
      ("github", None, "place_off")]),
    # 6. box measured 1 + 5 = 6 would win, but it holds 2 live runs of 2: own 12 vs github 9
    ("a faster place at capacity is dropped", "linux", ALL, {"machine:box": 2},
     {"own": OWN_12, "machine:box": Measured((1.0,), (4.0, 6.0)), "github": GH_9}, {},
     "github", "earliest_finish",
     [("own", 12.0, None), ("machine:box", None, "busy"), ("machine:win", None, "os_missing"),
      ("github", 9.0, None)]),
    # 7. box 1 + 5 = 6 beats own 12 and github 9
    ("a measured machine faster than own wins", "linux", ALL, {"machine:box": 1},
     {"own": OWN_12, "machine:box": Measured((1.0,), (4.0, 6.0)), "github": GH_9}, {},
     "machine:box", "earliest_finish",
     [("own", 12.0, None), ("machine:box", 6.0, None), ("machine:win", None, "os_missing"),
      ("github", 9.0, None)]),
    # 8. box 1 + 8 = 9 ties github 3 + 6 = 9: machines before github
    ("a tie between a machine and github goes to the machine", "linux", ALL, {},
     {"own": OWN_12, "machine:box": Measured((1.0,), (8.0,)), "github": GH_9}, {},
     "machine:box", "earliest_finish",
     [("own", 12.0, None), ("machine:box", 9.0, None), ("machine:win", None, "os_missing"),
      ("github", 9.0, None)]),
    # 9. own 0 + 9 = 9 ties github 9: own
    ("a tie between own and github goes to own", "linux", [OWN, GH], {},
     {"own": Measured((0.0,), (9.0,)), "github": GH_9}, {},
     "own", "earliest_finish",
     [("own", 9.0, None), ("github", 9.0, None)]),
    # 10. own busy, box busy, win lacks Linux, github off: no place left
    ("no place left chooses nothing", "linux", [OWN, BOX, WIN, GH_OFF],
     {"own": 1, "machine:box": 2}, {"own": OWN_12}, {},
     None, None,
     [("own", None, "busy"), ("machine:box", None, "busy"), ("machine:win", None, "os_missing"),
      ("github", None, "place_off")]),
    # 11. own unmeasured, github measured (runs 5, 7, 6): no own median, so the unmeasured places
    #     take the median of every measured run, 6: own 0 + 6 = 6, box 6, github 3 + 6 = 9: own
    ("an unmeasured own takes the median of all measured runs", "linux", [OWN, BOX, GH], {},
     {"github": GH_9}, {},
     "own", "earliest_finish",
     [("own", 6.0, None), ("machine:box", 6.0, None), ("github", 9.0, None)]),
    # 12. an even count: own runs 10 and 20 have median 15; github 0 + 14 = 14: github
    ("the median of an even count is the mean of the middle two", "linux", [OWN, GH], {},
     {"own": Measured((0.0,), (10.0, 20.0)), "github": Measured((0.0,), (14.0,))}, {},
     "github", "earliest_finish",
     [("own", 15.0, None), ("github", 14.0, None)]),
    # 13. box vanished earlier in this run: dropped place_vanished; own 12, github 9: github
    ("a place that vanished in this run is not offered again", "linux", ALL, {},
     {"own": OWN_12, "machine:box": Measured((1.0,), (1.0,)), "github": GH_9},
     {"machine:box": "place_vanished"},
     "github", "earliest_finish",
     [("own", 12.0, None), ("machine:box", None, "place_vanished"),
      ("machine:win", None, "os_missing"), ("github", 9.0, None)]),
    # 14. a macOS run: no place has macOS
    ("a run whose OS no place has finds no place", "macos", ALL, {}, {"own": OWN_12}, {},
     None, None,
     [("own", None, "os_missing"), ("machine:box", None, "os_missing"),
      ("machine:win", None, "os_missing"), ("github", None, "os_missing")]),
    # 15. a place switched off and lacking the OS is dropped for the OS, the first check
    ("the OS is checked before on and capacity", "linux",
     [OWN, Offer("machine:win", ("windows",), on=False, capacity=0)], {"own": 1}, {}, {},
     None, None,
     [("own", None, "busy"), ("machine:win", None, "os_missing")]),
    # 16. capacity 0 is always busy; own 12 is the only place left
    ("a place of capacity 0 is busy with no live run", "linux",
     [OWN, Offer("machine:box", ("linux",), on=True, capacity=0)], {},
     {"own": OWN_12, "machine:box": Measured((0.0,), (1.0,))}, {},
     "own", "earliest_finish",
     [("own", 12.0, None), ("machine:box", None, "busy")]),
    # 17. box has measured runs but no measured start: start 0 + run 4 = 4; own 12: box
    ("a place with runs but no measured start starts at 0", "linux", [OWN, BOX], {},
     {"own": OWN_12, "machine:box": Measured((), (4.0,))}, {},
     "machine:box", "earliest_finish",
     [("own", 12.0, None), ("machine:box", 4.0, None)]),
]


@pytest.mark.parametrize(
    "need_os, places, live, measured, excluded, chosen, reason, offered",
    [case[1:] for case in CASES], ids=[case[0] for case in CASES])
def test_the_rule_picks_the_hand_computed_place(need_os, places, live, measured, excluded,
                                                chosen, reason, offered):
    decision = choose(need_os, places, live, measured, excluded)
    assert (decision.chosen, decision.reason) == (chosen, reason)
    assert [(o.place, o.expected_minutes, o.dropped) for o in decision.offered] == offered


def test_the_table_has_at_least_twelve_cases_covering_every_drop_reason():
    assert len(CASES) >= 12
    drops = {o[2] for case in CASES for o in case[-1] if o[2]}
    assert drops == {"os_missing", "place_off", "busy", "place_vanished"}


def test_the_decision_detail_lists_every_offered_place():
    decision = choose("linux", ALL, {"own": 1}, {"github": GH_9}, {})
    assert decision.detail() == [
        {"place": "own", "expected_minutes": None, "dropped": "busy"},
        {"place": "machine:box", "expected_minutes": 6.0, "dropped": None},
        {"place": "machine:win", "expected_minutes": None, "dropped": "os_missing"},
        {"place": "github", "expected_minutes": 9.0, "dropped": None},
    ]
