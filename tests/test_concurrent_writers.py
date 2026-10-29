"""Row 3 of #393: 8 processes writing 1000 events each at once, while the index is refreshed
under them, leave 8000 events in the index, 0 torn lines and 0 duplicate ids."""

import pytest
import json
import subprocess
import sys
import time
from pathlib import Path

from orch_it.events.store import Store

WRITERS, EACH = 8, 1000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


pytestmark = pytest.mark.os_touching  # paths, processes, shells, locks or hooks (#414 unit 4)


def test_eight_writers_at_once_lose_and_tear_nothing(tmp_path):
    root, start = tmp_path / "events", tmp_path / "go"
    store = Store.open(root)
    script = Path(__file__).with_name("concurrent_writer.py")
    processes = [
        subprocess.Popen([sys.executable, str(script), str(root), str(start), str(n), str(EACH)],
                         creationflags=NO_WINDOW)
        for n in range(WRITERS)
    ]
    deadline = time.monotonic() + 120
    try:
        start.touch()
        while any(p.poll() is None for p in processes) and time.monotonic() < deadline:
            store.refresh()  # reads under the writers: half-written lines wait
    finally:
        codes = [p.wait(timeout=120) for p in processes]
    assert codes == [0] * WRITERS

    report = store.refresh()
    assert report.bad_lines == 0 and report.pending_bytes == 0
    assert store.query("SELECT count(*), count(DISTINCT id) FROM events").rows == [(8000, 8000)]
    per_writer = store.query("SELECT run, count(*), count(DISTINCT value) FROM events GROUP BY run")
    assert sorted(per_writer.rows) == [(f"writer-{n}", EACH, EACH) for n in range(WRITERS)]

    lines = [line for path in root.glob("*/*.jsonl") for line in path.read_bytes().split(b"\n")]
    events = [json.loads(line) for line in lines if line]
    assert len(events) == 8000 and len({e["id"] for e in events}) == 8000
