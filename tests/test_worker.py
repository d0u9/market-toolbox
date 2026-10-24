import datetime as dt
import json

import pytest

import storage
import worker
from config import ENV_VAR
from tests.fakes import TEST_CONFIG, FakeProvider, et


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(TEST_CONFIG), encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(path))
    return path


def test_once_publishes_writes_heartbeat_and_exports(config_file, monkeypatch, tmp_path):
    provider = FakeProvider()
    monkeypatch.setattr(worker, "get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(worker, "utcnow", lambda: et(2025, 10, 15, 17))
    assert worker.main(["--once"]) == 0
    conn = storage.connect_ro(tmp_path / "data" / "market.sqlite3")
    assert storage.get_meta(conn, "worker_heartbeat") == "2025-10-15T21:00:00+00:00"
    assert storage.read_symbol(conn, "TEST1")[0]["status"] == "updated"
    assert list((tmp_path / "data" / "exports").glob("update_runs-*.csv"))


def test_once_exit_code_when_not_up_to_date(config_file, monkeypatch):
    provider = FakeProvider()
    provider.available_until["FAKEA"] = dt.date(2025, 10, 14)
    monkeypatch.setattr(worker, "get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(worker, "utcnow", lambda: et(2025, 10, 15, 17))
    assert worker.main(["--once"]) == 1


def test_unwritable_data_dir_is_reported(config_file, tmp_path, caplog):
    (tmp_path / "data").write_text("not a directory", encoding="utf-8")
    assert worker.main(["--once"]) == 4
    assert "cannot write to the data directory" in caplog.text


def test_bad_config_exit_code(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text('{"symbols": []}', encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(bad))
    assert worker.main(["--once"]) == 2


def test_second_writer_is_refused(config_file, tmp_path):
    from locking import WriterLock

    with WriterLock(tmp_path / "data" / ".worker.lock"):
        assert worker.main(["--once"]) == 3


@pytest.mark.parametrize("already_stopped", [False, True])
def test_stop_prevents_more_symbol_requests(config_file, monkeypatch, tmp_path, already_stopped):
    import threading
    from config import load_config
    from locking import WriterLock

    stop = threading.Event()

    class StoppingProvider(FakeProvider):
        def fetch_daily(self, *args):
            result = super().fetch_daily(*args)
            stop.set()
            return result

    provider = StoppingProvider()
    cfg = load_config(config_file)
    monkeypatch.setattr(worker, "get_provider", lambda *args: provider)
    monkeypatch.setattr(worker, "utcnow", lambda: et(2025, 10, 15, 17))
    if already_stopped:
        stop.set()
    assert worker.run(cfg, stop) == 0
    assert [call[0] for call in provider.calls] == ([] if already_stopped else ["TEST1"])
    conn = storage.connect_ro(cfg.sqlite_path)
    try:
        if not already_stopped:
            assert storage.read_symbol(conn, "TEST1")[0]["status"] == "updated"
        assert storage.read_symbol(conn, "FAKEA")[1] is None
    finally:
        conn.close()
    with WriterLock(cfg.lock_path):
        pass
