"""Runs the Streamlit page headless against a synthetic database."""

import datetime as dt
import json
import random

import pytest
from streamlit.testing.v1 import AppTest

import storage
from config import ENV_VAR, load_config
from services.update import Updater
from tests.fakes import TEST_CONFIG, FakeProvider, et

APP = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(TEST_CONFIG), encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(path))
    cfg = load_config(path)
    conn = storage.connect_rw(cfg.sqlite_path)
    Updater(cfg, conn, FakeProvider(), rng=random.Random(0)).tick(et(2025, 10, 15, 17))
    yield conn
    conn.close()


def run_app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    return at


def test_page_renders_first_symbol(seeded):
    storage.touch_heartbeat(seeded, dt.datetime.now(dt.timezone.utc))
    at = run_app()
    assert [h.value for h in at.subheader] == ["TEST1"]
    assert not any("worker has not checked in" in w.value for w in at.warning)
    assert sum("Update status: **Updated**" in c.value for c in at.caption) == 1


def test_stale_worker_is_reported(seeded):
    storage.touch_heartbeat(seeded, dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10))
    at = run_app()
    assert any("worker has not checked in" in w.value for w in at.warning)


def test_waiting_for_database(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(TEST_CONFIG), encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(path))
    at = run_app()
    assert any("waiting for the worker" in i.value for i in at.info)


def test_any_number_of_symbols(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**TEST_CONFIG, "symbols": ["TEST1", "FAKEA", "FAKEB"]}),
                    encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(path))
    cfg = load_config(path)
    conn = storage.connect_rw(cfg.sqlite_path)
    Updater(cfg, conn, FakeProvider(), rng=random.Random(0)).tick(et(2025, 10, 15, 17))
    conn.close()
    at = run_app()
    assert at.sidebar.radio[0].options == ["TEST1", "FAKEA", "FAKEB"]
    assert [h.value for h in at.subheader] == ["TEST1"]
    at.sidebar.radio[0].set_value("FAKEB").run()
    assert not at.exception
    assert [h.value for h in at.subheader] == ["FAKEB"]


def test_many_symbols_use_a_dropdown(tmp_path, monkeypatch):
    symbols = [f"FAKE{i}" for i in range(21)]
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**TEST_CONFIG, "symbols": symbols}), encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(path))
    conn = storage.connect_rw(load_config(path).sqlite_path)
    storage.ensure_symbols(conn, tuple(symbols))
    conn.close()
    at = run_app()
    assert not at.sidebar.radio or at.sidebar.radio[0].label != "Symbol"
    assert at.sidebar.selectbox[0].options == symbols
