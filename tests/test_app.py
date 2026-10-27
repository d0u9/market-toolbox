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


def shown_symbol(at):
    return at.subheader[0].value


def run_app(symbol=None):
    at = AppTest.from_file(APP, default_timeout=60)
    if symbol:
        at.query_params["symbol"] = symbol
    at.run()
    assert not at.exception
    return at


def test_page_renders_first_symbol(seeded):
    storage.touch_heartbeat(seeded, dt.datetime.now(dt.timezone.utc))
    at = run_app()
    assert shown_symbol(at) == "TEST1"
    assert not any("worker has not checked in" in w.value for w in at.warning)
    assert any("Target session: 2025-10-15" in c.value for c in at.caption)


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
    path.write_text(json.dumps({**TEST_CONFIG, "markets": {"NYSE": {"symbols": ["TEST1", "FAKEA", "FAKEB"]}}}),
                    encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(path))
    cfg = load_config(path)
    conn = storage.connect_rw(cfg.sqlite_path)
    Updater(cfg, conn, FakeProvider(), rng=random.Random(0)).tick(et(2025, 10, 15, 17))
    conn.close()
    assert shown_symbol(run_app()) == "TEST1"
    assert shown_symbol(run_app("FAKEB")) == "FAKEB"
    assert shown_symbol(run_app("NOPE")) == "TEST1"  # unknown symbol falls back


def test_many_symbols_render(tmp_path, monkeypatch):
    symbols = [f"FAKE{i}" for i in range(21)]
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**TEST_CONFIG, "markets": {"NYSE": {"symbols": symbols}}}), encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(path))
    conn = storage.connect_rw(load_config(path).sqlite_path)
    storage.ensure_symbols(conn, tuple(symbols), "NYSE")
    conn.close()
    at = run_app("FAKE20")
    assert shown_symbol(at) == "FAKE20"


def test_symbol_picker_switches_to_search_above_twenty():
    from page_helpers import symbol_picker

    assert symbol_picker(1) == symbol_picker(20) == "list"
    assert symbol_picker(21) == symbol_picker(100) == "search"


def test_saved_snapshot_refresh_failure_is_visible(seeded, monkeypatch):
    from providers.base import NetworkError

    cfg = load_config()
    provider = FakeProvider()
    provider.errors["TEST1"] = [NetworkError("synthetic refresh outage")]
    Updater(cfg, seeded, provider).tick(et(2025, 10, 15, 17))
    assert storage.get_state(seeded, "TEST1")["status"] == "updated"
    at = run_app()
    components = "\n".join(node.proto.json for node in at.get("bidi_component"))
    assert "Refresh failed; showing saved data" in components
    assert "synthetic refresh outage" in components


def rerun_watchlist(at, monkeypatch):
    from dataclasses import replace
    import threading
    from streamlit.runtime.scriptrunner_utils.script_requests import ScriptRequests

    fragment_id = at._fragment_storage.resolve_target("watchlist")[0]
    original_rerun = ScriptRequests.request_rerun

    def timer_rerun(requests, data):
        if threading.current_thread() is threading.main_thread():
            data = replace(data, fragment_id=fragment_id)
        return original_rerun(requests, data)

    monkeypatch.setattr(ScriptRequests, "request_rerun", timer_rerun)
    at.run()
    assert not at.exception


def test_watchlist_timer_refreshes_other_symbol_without_rebuilding_chart(tmp_path, monkeypatch):
    import streamlit as st

    path = tmp_path / "config.json"
    path.write_text(json.dumps(TEST_CONFIG), encoding="utf-8")
    monkeypatch.setenv(ENV_VAR, str(path))
    cfg = load_config(path)
    conn = storage.connect_rw(cfg.sqlite_path)
    provider = FakeProvider()
    provider.available_until["FAKEA"] = dt.date(2025, 10, 14)
    updater = Updater(cfg, conn, provider, rng=random.Random(0))
    updater.tick(et(2025, 10, 15, 17))
    renders = []
    original_chart = st.iframe

    def chart(*args, **kwargs):
        renders.append(True)
        return original_chart(*args, **kwargs)

    monkeypatch.setattr(st, "iframe", chart)
    try:
        at = run_app()
        sidebar = lambda: next(node.proto.json for node in at.get("bidi_component")
                               if "Watchlist" in node.proto.json)
        assert "No data yet" in sidebar()
        selected_snapshot = storage.read_symbol(conn, "TEST1")[1]["id"]
        provider.available_until.clear()
        updater.tick(et(2025, 10, 15, 17, 6))
        assert storage.read_symbol(conn, "TEST1")[1]["id"] == selected_snapshot
        rerun_watchlist(at, monkeypatch)
        assert "No data yet" not in sidebar()
        assert len(renders) == 1
    finally:
        conn.close()


def test_watchlist_click_changes_symbol_from_fragment(seeded, monkeypatch):
    from types import SimpleNamespace
    from streamlit_shadcn_ui.v2.elements import ElementsBuilder

    at = run_app()
    original_button = ElementsBuilder.button

    def click_other(builder, *args, **kwargs):
        original_button(builder, *args, **kwargs)
        return SimpleNamespace(clicked=kwargs.get("key") == "view-FAKEA")

    monkeypatch.setattr(ElementsBuilder, "button", click_other)
    rerun_watchlist(at, monkeypatch)
    assert at.query_params["symbol"] == "FAKEA"
    assert shown_symbol(at) == "FAKEA"


def test_display_zone_is_explicit_or_per_browser_with_utc_fallback():
    from page_helpers import display_zone

    assert display_zone('browser', 'Asia/Shanghai').key == 'Asia/Shanghai'
    assert display_zone('browser', 'Australia/Sydney').key == 'Australia/Sydney'
    assert display_zone('browser', None).key == 'UTC'
    assert display_zone('browser', 'Not/AZone').key == 'UTC'
    assert display_zone('UTC', 'Australia/Sydney').key == 'UTC'


def test_page_uses_browser_timezone_and_saved_quote_unit(seeded, monkeypatch):
    import streamlit as st

    monkeypatch.setattr(type(st.context), 'timezone', property(lambda self: 'Asia/Shanghai'))
    at = run_app()
    components = '\n'.join(node.proto.json for node in at.get('bidi_component'))
    assert 'Times shown in Asia/Shanghai' in components
    assert any('NYSE (America/New_York)' in c.value and 'quote unit: TST' in c.value for c in at.caption)


def test_market_reassignment_never_relabels_saved_data(seeded, monkeypatch, tmp_path):
    from dataclasses import replace
    from providers.base import NetworkError

    cfg = load_config()
    moved = replace(cfg, markets=(replace(cfg.markets[0], name='NASDAQ'),))
    provider = FakeProvider()
    provider.errors['TEST1'] = [NetworkError('synthetic outage')]
    Updater(moved, seeded, provider).tick(et(2025, 10, 15, 17, 1))
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({**TEST_CONFIG, 'markets': {'NASDAQ': {'symbols': list(cfg.symbols)}}}), encoding='utf-8')
    at = run_app()
    components = '\n'.join(node.proto.json for node in at.get('bidi_component'))
    assert 'Showing saved data from NYSE' in components
    assert any('NYSE (America/New_York)' in c.value for c in at.caption)
