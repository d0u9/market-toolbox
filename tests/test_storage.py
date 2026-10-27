import datetime as dt
import sqlite3
import subprocess
import sys
import textwrap

import pytest

import storage
from locking import LockHeld, WriterLock
from tests.fakes import recent_runs


def test_read_only_connection_cannot_write(tmp_path):
    path = tmp_path / "data" / "m.sqlite3"
    storage.connect_rw(path).close()
    ro = storage.connect_ro(path)
    assert storage.get_meta(ro, 'schema_version') == '1'
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
    ro.close()


def test_read_only_before_creation_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        storage.connect_ro(tmp_path / "missing.sqlite3")
    assert not (tmp_path / "missing.sqlite3").exists()  # reader never creates it


def test_wal_mode_and_reader_sees_new_commits(tmp_path):
    path = tmp_path / "m.sqlite3"
    rw = storage.connect_rw(path)
    assert rw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    storage.ensure_symbols(rw, ("TEST1",), "NYSE")
    ro = storage.connect_ro(path)
    assert storage.read_symbol(ro, "TEST1")[0]["status"] == "initializing"
    with storage.transaction(rw):
        storage.update_state(rw, "TEST1", status="updated")
    assert storage.read_symbol(ro, "TEST1")[0]["status"] == "updated"
    # A reader inside a transaction keeps a consistent view while the writer commits.
    ro.execute("BEGIN")
    ro.execute("SELECT status FROM symbol_state").fetchone()
    with storage.transaction(rw):
        storage.update_state(rw, "TEST1", status="failed")
    assert ro.execute("SELECT status FROM symbol_state").fetchone()[0] == "updated"
    ro.execute("COMMIT")
    assert ro.execute("SELECT status FROM symbol_state").fetchone()[0] == "failed"


def test_wal_files_rebuilt_after_removal(tmp_path):
    path = tmp_path / "m.sqlite3"
    rw = storage.connect_rw(path)
    storage.ensure_symbols(rw, ("TEST1",), "NYSE")
    rw.close()  # clean close checkpoints and removes -wal/-shm
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"m.sqlite3{suffix}").unlink(missing_ok=True)
    rw = storage.connect_rw(path)
    ro = storage.connect_ro(path)
    assert storage.read_symbol(ro, "TEST1")[0] is not None


def test_prune_and_export_runs(tmp_path):
    rw = storage.connect_rw(tmp_path / "m.sqlite3")
    now = dt.datetime(2025, 10, 15, tzinfo=dt.timezone.utc)
    storage.ensure_symbols(rw, ("TEST1",), "NYSE")
    old = storage.start_run(rw, "TEST1", dt.date(2025, 6, 1), 1, now - dt.timedelta(days=100))
    new = storage.start_run(rw, "TEST1", dt.date(2025, 10, 14), 1, now)
    storage.prune_runs(rw, now)
    ids = [r["id"] for r in recent_runs(rw, "TEST1", 10)]
    assert ids == [new] and old not in ids
    out = tmp_path / "exports"
    for i in range(10):
        storage.export_runs(rw, out, dt.date(2025, 10, 1) + dt.timedelta(days=i))
    files = sorted(p.name for p in out.iterdir())
    assert len(files) == storage.EXPORT_KEEP and files[-1] == "update_runs-2025-10-10.csv"
    assert "TEST1" in (out / files[-1]).read_text(encoding="utf-8")


def test_lock_is_exclusive(tmp_path):
    path = tmp_path / ".worker.lock"
    with WriterLock(path):
        with pytest.raises(LockHeld):
            WriterLock(path).acquire()
    WriterLock(path).acquire()


def test_lock_released_when_holder_crashes(tmp_path):
    path = tmp_path / ".worker.lock"
    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(storage.__file__.rsplit('/', 1)[0])!r})
        from locking import WriterLock
        WriterLock(__import__('pathlib').Path({str(path)!r})).acquire()
        os._exit(9)  # crash without releasing
    """)
    assert subprocess.run([sys.executable, "-c", code]).returncode == 9
    with WriterLock(path):
        pass


def test_old_layout_with_same_version_is_rejected_without_rewriting_data(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')
        conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    for connect in (storage.connect_rw, storage.connect_ro):
        with pytest.raises(storage.SchemaError, match='fresh database'):
            connect(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '1'
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 1


def test_uninitialized_database_is_treated_as_not_ready(tmp_path):
    path = tmp_path / 'new.sqlite3'
    path.touch()
    with pytest.raises(FileNotFoundError):
        storage.connect_ro(path)
    storage.connect_rw(path).close()
    storage.connect_ro(path).close()


def test_naive_timestamps_are_not_interpreted_using_host_timezone():
    with pytest.raises(ValueError, match='timezone'):
        storage.iso(dt.datetime(2025, 10, 15, 12))
