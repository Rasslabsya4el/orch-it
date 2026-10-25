"""The store: one append-only JSON Lines file per writer, read through a SQLite index that is
a cache of the files (#393 decisions 1-3, rows 2, 3, 7, 8)."""

import json
import shutil

import pytest

from orch_it.events.store import Store, StoreError


def envelope(**over):
    base = {
        "kind": "value.measured", "repo": "Rasslabsya4el/orch-it", "ticket": None,
        "role": "script", "session": None, "parent_session": None, "turn": None,
        "step": None, "run": None, "note": None,
        "detail": {"name": "ram", "value": 16, "unit": "GB", "source": "test"},
    }
    base.update(over)
    return base


def lines_of(root):
    return [line for path in sorted(root.glob("*/*.jsonl"))
            for line in path.read_bytes().split(b"\n") if line]


def test_a_written_event_is_one_line_in_the_writer_s_own_file(tmp_path):
    store = Store.open(tmp_path / "events")
    with store.writer() as writer:
        written = writer.write(envelope())
    [path] = (tmp_path / "events").glob("*/*.jsonl")
    assert path.parent.name == written["at"][:10]
    assert json.loads(path.read_text("utf-8")) == written
    assert written["machine"] and written["os"] and written["layer_version"]


def test_two_writers_never_share_a_file(tmp_path):
    store = Store.open(tmp_path)
    with store.writer() as a, store.writer() as b:
        a.write(envelope())
        b.write(envelope())
    assert len(list(tmp_path.glob("*/*.jsonl"))) == 2


def test_a_refused_event_writes_nothing(tmp_path):
    store = Store.open(tmp_path)
    with store.writer() as writer:
        with pytest.raises(Exception) as caught:
            writer.write(envelope(kind="nonsense.happened"))
    assert caught.value.code == "unknown_kind"
    assert lines_of(tmp_path) == []


def test_a_written_event_is_queryable_with_its_envelope_as_columns(tmp_path):
    store = Store.open(tmp_path)
    with store.writer() as writer:
        e = writer.write(envelope(ticket=393))
    rows = store.query("SELECT id, kind, repo, ticket, name, value FROM events").rows
    assert rows == [(e["id"], "value.measured", "Rasslabsya4el/orch-it", 393, "ram", 16)]


def test_an_unknown_detail_field_is_accepted_and_queryable(tmp_path):
    store = Store.open(tmp_path)
    with store.writer() as writer:
        writer.write(envelope(detail={"name": "x", "flavour": "salty"}))
    result = store.query("SELECT json_extract(detail, '$.flavour') FROM events")
    assert result.rows == [("salty",)]


def test_at_is_compared_in_utc_whatever_offset_it_was_written_with(tmp_path):
    store = Store.open(tmp_path)
    with store.writer() as writer:
        writer.write(envelope(at="2026-10-25T14:00:00+02:00", note="later local, earlier utc"))
        writer.write(envelope(at="2026-10-25T12:30:00+00:00", note="earlier local, later utc"))
    rows = store.query("SELECT note, at FROM events ORDER BY at").rows
    assert rows == [("later local, earlier utc", "2026-10-25T12:00:00.000000Z"),
                    ("earlier local, later utc", "2026-10-25T12:30:00.000000Z")]


def test_the_index_reads_new_lines_incrementally(tmp_path):
    store = Store.open(tmp_path)
    with store.writer() as writer:
        writer.write(envelope())
        assert store.query("SELECT count(*) FROM events").rows == [(1,)]
        writer.write(envelope())
        assert store.query("SELECT count(*) FROM events").rows == [(2,)]
    assert store.refresh().added == 0


def test_a_half_written_last_line_is_skipped_until_it_is_complete(tmp_path):
    store = Store.open(tmp_path)
    with store.writer() as writer:
        e = writer.write(envelope())
    [path] = tmp_path.glob("*/*.jsonl")
    full = json.dumps(envelope(id="half-1", at="2026-10-25T10:00:00Z", machine="m", os="o",
                               layer_version="0"))
    with path.open("ab") as f:
        f.write(full[:40].encode())
    assert store.query("SELECT id FROM events").rows == [(e["id"],)]
    with path.open("ab") as f:
        f.write(full[40:].encode() + b"\n")
    assert sorted(store.query("SELECT id FROM events").rows) == sorted([(e["id"],), ("half-1",)])
    assert store.refresh().bad_lines == 0


def test_a_complete_line_that_is_no_event_is_counted_never_indexed(tmp_path):
    store = Store.open(tmp_path)
    (tmp_path / "2026-10-25").mkdir()
    (tmp_path / "2026-10-25" / "w-bad.jsonl").write_bytes(b'{"kind": "x"}\nnot json\n')
    report = store.refresh()
    assert (report.added, report.bad_lines) == (0, 2)
    assert store.query("SELECT code FROM bad_lines ORDER BY line_offset").rows == [
        ("missing_field",), ("bad_json",)]


def test_deleting_the_index_and_rebuilding_gives_the_same_rows(tmp_path):
    store = Store.open(tmp_path)
    with store.writer() as writer:
        for n in range(5):
            writer.write(envelope(note=str(n)))
    before = store.query("SELECT * FROM events ORDER BY id").rows
    store.index_path.unlink()
    assert Store.open(tmp_path).query("SELECT * FROM events ORDER BY id").rows == before


def test_a_second_machine_s_files_merge_by_id_without_a_duplicate(tmp_path):
    a, b = Store.open(tmp_path / "a"), Store.open(tmp_path / "b")
    shared = envelope(id="h-same-on-both", at="2026-10-25T10:00:00Z", machine="m", os="o",
                      layer_version="0")
    with a.writer() as wa, b.writer() as wb:
        wa.write(envelope(note="a only"))
        wb.write(envelope(note="b only"))
        wa.write(dict(shared))
        wb.write(dict(shared))
    assert a.query("SELECT count(*) FROM events").rows == [(2,)]
    for day in (tmp_path / "b").iterdir():
        if day.is_dir():
            shutil.copytree(day, tmp_path / "a" / day.name, dirs_exist_ok=True)
    rows = a.query("SELECT note FROM events ORDER BY note").rows
    assert rows == [(None,), ("a only",), ("b only",)]


def test_a_store_inside_a_git_work_tree_is_refused_before_any_write(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    target = tmp_path / "repo" / "data" / "events"
    with pytest.raises(StoreError) as caught:
        Store.open(target)
    assert caught.value.code == "store_in_git_tree"
    assert not (tmp_path / "repo" / "data").exists()


def test_a_worktree_s_git_file_counts_as_a_git_work_tree(tmp_path):
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: elsewhere\n")
    with pytest.raises(StoreError) as caught:
        Store.open(tmp_path / "wt" / "events")
    assert caught.value.code == "store_in_git_tree"


def test_user_sql_cannot_change_the_index(tmp_path):
    store = Store.open(tmp_path)
    with pytest.raises(StoreError) as caught:
        store.query("DELETE FROM events")
    assert caught.value.code == "query_failed"


def test_a_detail_too_deep_to_write_is_refused_with_a_code(tmp_path):
    deep = []
    for _ in range(100_000):
        deep = [deep]
    store = Store.open(tmp_path)
    with store.writer() as writer:
        with pytest.raises(Exception) as caught:
            writer.write(envelope(detail={"tree": deep}))
    assert caught.value.code == "bad_type"
    assert lines_of(tmp_path) == []


def test_the_default_store_is_the_setting(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCH_IT_STORE", raising=False)
    monkeypatch.setenv("ORCH_IT_SETTINGS", str(tmp_path / "settings.json"))
    from orch_it import settings
    settings.save("store", str(tmp_path / "chosen"))
    assert Store.open().root == (tmp_path / "chosen").absolute()
    monkeypatch.setenv("ORCH_IT_STORE", str(tmp_path / "env"))
    assert Store.open().root == (tmp_path / "env").absolute()


def test_live_ids_are_time_ordered_and_import_ids_repeat():
    import time
    from orch_it.events.ids import import_id, live_id
    first = live_id()
    time.sleep(0.002)
    assert live_id() > first
    assert len({live_id() for _ in range(10_000)}) == 10_000
    assert import_id("claude-code", "msg_1", "turn.used") == import_id("claude-code", "msg_1", "turn.used")
    assert import_id("claude-code", "msg_1", "turn.used") != import_id("claude-code", "msg_1", "tool.called")
