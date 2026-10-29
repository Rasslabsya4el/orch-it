"""One of the processes of tests/test_concurrent_writers.py: waits for the start file, then
writes `count` events to the store at `root`."""

import sys
import time
from pathlib import Path

from orch_it.events.store import Store

root, start_file, number, count = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
deadline = time.monotonic() + 60
while not start_file.exists():
    if time.monotonic() > deadline:
        sys.exit("start file never appeared")
    time.sleep(0.005)
store = Store.open(root)
with store.writer() as writer:
    for n in range(count):
        writer.write({
            "kind": "value.measured", "repo": "acme-x/widgets-y", "ticket": None,
            "role": "script", "session": None, "parent_session": None, "turn": None,
            "step": None, "run": f"writer-{number}", "note": None,
            "detail": {"name": "n", "value": n, "unit": "count", "source": "test",
                       "padding": "x" * (n % 300)},
        })
