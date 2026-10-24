"""Single-process lifecycle checks with synthetic data and no network."""

import asyncio
import threading

import pytest

from config import build_config
from locking import WriterLock
import runtime
import storage
import worker
from tests.fakes import FakeProvider, TEST_CONFIG, et


class FakeServer:
    def __init__(self):
        self.done = asyncio.Event()
        self.started = False

    async def start(self):
        self.started = True

    def stop(self):
        self.done.set()

    @property
    def stopped(self):
        return self.done.wait()


def test_worker_starts_without_browser_and_releases_lock(tmp_path, monkeypatch):
    cfg = build_config(TEST_CONFIG, tmp_path / "config.json")
    monkeypatch.setattr(worker, "get_provider", lambda *args: FakeProvider())
    monkeypatch.setattr(worker, "utcnow", lambda: et(2025, 10, 15, 17))
    # Complete a real synthetic publication, then stop the web server.
    original_housekeeping = worker.daily_housekeeping
    calls = []

    async def scenario():
        server = FakeServer()
        loop = asyncio.get_running_loop()

        def housekeeping(*args):
            result = original_housekeeping(*args)
            calls.append(threading.current_thread().name)
            loop.call_soon_threadsafe(server.stop)
            return result

        monkeypatch.setattr(worker, "daily_housekeeping", housekeeping)
        assert await runtime.serve(cfg, server) == 0
        assert server.started

    asyncio.run(scenario())
    assert calls == ["market-updater"]
    assert not any(t.name == "market-updater" for t in threading.enumerate())
    with storage.connect_ro(cfg.sqlite_path) as conn:
        assert storage.read_symbol(conn, "TEST1")[0]["status"] == "updated"
        assert storage.get_meta(conn, "worker_heartbeat") is not None
    with WriterLock(cfg.lock_path):
        pass


@pytest.mark.parametrize("crash", [False, True])
def test_worker_failure_stops_server(tmp_path, monkeypatch, crash):
    cfg = build_config(TEST_CONFIG, tmp_path / "config.json")

    def fail(*args):
        if crash:
            raise RuntimeError("synthetic worker failure")
        return 3

    monkeypatch.setattr(worker, "run", fail)

    async def scenario():
        server = FakeServer()
        assert await runtime.serve(cfg, server) == (1 if crash else 3)
        assert server.done.is_set()

    asyncio.run(scenario())


def test_web_start_failure_does_not_start_worker(tmp_path, monkeypatch):
    cfg = build_config(TEST_CONFIG, tmp_path / "config.json")
    calls = []
    monkeypatch.setattr(worker, "run", lambda *args: calls.append(True))

    class BrokenServer(FakeServer):
        async def start(self):
            raise RuntimeError("synthetic bind failure")

    with pytest.raises(RuntimeError, match="bind failure"):
        asyncio.run(runtime.serve(cfg, BrokenServer()))
    assert calls == []


def test_sigterm_stops_worker_and_restores_handlers(tmp_path, monkeypatch):
    import signal

    cfg = build_config(TEST_CONFIG, tmp_path / "config.json")
    previous = signal.getsignal(signal.SIGTERM)
    stopped = threading.Event()

    async def scenario():
        loop = asyncio.get_running_loop()

        def run(*args):
            stop = args[1]
            loop.call_soon_threadsafe(signal.raise_signal, signal.SIGTERM)
            assert stop.wait(5)
            stopped.set()
            return 0

        monkeypatch.setattr(worker, "run", run)
        assert await runtime.serve(cfg, FakeServer()) == 0

    asyncio.run(scenario())
    assert stopped.is_set()
    assert signal.getsignal(signal.SIGTERM) == previous


def test_main_wires_real_server_and_global_config(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from config import ENV_VAR
    from streamlit import config as streamlit_config
    from streamlit.runtime import Runtime
    from streamlit.web.server import Server

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        **TEST_CONFIG, "web_host": "127.0.0.1", "web_port": 9876,
    }), encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(config_file))
    # Isolate Streamlit's process globals from AppTest and restore after this test.
    monkeypatch.setattr(Runtime, "_instance", None)
    monkeypatch.setattr(streamlit_config, "_config_options", streamlit_config._config_options)
    seen = []

    async def serve(cfg, server):
        assert isinstance(server, Server)
        assert Path(server.main_script_path) == Path(runtime.__file__).with_name("app.py")
        assert callable(server.start) and callable(server.stop)
        assert cfg.config_path == config_file
        assert streamlit_config.get_option("server.address") == "127.0.0.1"
        assert streamlit_config.get_option("server.port") == 9876
        assert streamlit_config.get_option("server.headless") is True
        assert streamlit_config.get_option("server.fileWatcherType") == "none"
        seen.append(server)
        return 7

    monkeypatch.setattr(runtime, "serve", serve)
    assert runtime.main() == 7
    assert len(seen) == 1


def test_main_bad_config_never_starts_server(tmp_path, monkeypatch, capsys):
    from config import ENV_VAR

    config_file = tmp_path / "config.json"
    config_file.write_text('{"web_port": 0}', encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(config_file))
    calls = []
    monkeypatch.setattr(runtime, "serve", lambda *args: calls.append(True))
    assert runtime.main() == 2
    assert "web_port" in capsys.readouterr().err
    assert calls == []
