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


def test_page_renders_both_symbols(seeded):
    storage.touch_heartbeat(seeded, dt.datetime.now(dt.timezone.utc))
    at = run_app()
    assert [h.value for h in at.subheader] == ["TEST1", "FAKEA"]
    assert not any("worker has not checked in" in w.value for w in at.warning)
    assert sum("Update status: **Updated**" in c.value for c in at.caption) == 2


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
