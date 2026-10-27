"""Cross-market scheduling, provenance and failure isolation with synthetic data."""

import datetime as dt
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

import storage
from config import ConfigError, build_config
from providers.base import NetworkError, ProviderError
from providers.yahoo import normalize_frame
from services.calendar import CalendarExceptions, MarketCalendar
from services.retry import next_recovery_time
from services.update import Updater
from tests.fakes import FakeProvider, TEST_CONFIG, make_config, et

UTC = dt.timezone.utc
DAY = dt.date(2025, 10, 15)


def current(conn, symbol):
    return storage.read_symbol(conn, symbol)[1]


def test_same_instant_has_different_market_targets_and_own_histories(tmp_path):
    cfg = make_config(tmp_path, markets={
        'NASDAQ': {'symbols': ['TESTUS']},
        'SSE': {'symbols': ['TESTCN']},
        'ASX': {'symbols': ['TESTAU']},
    })
    provider = FakeProvider(markets={'TESTUS': 'NASDAQ', 'TESTCN': 'SSE', 'TESTAU': 'ASX'})
    conn = storage.connect_rw(cfg.sqlite_path)
    try:
        updater = Updater(cfg, conn, provider)
        # China and Australia have closed; the US session has not started.
        updater.tick(dt.datetime(2025, 10, 15, 8, tzinfo=UTC))
        assert current(conn, 'TESTUS')['latest_session'] == '2025-10-14'
        assert current(conn, 'TESTCN')['latest_session'] == '2025-10-15'
        assert current(conn, 'TESTAU')['latest_session'] == '2025-10-15'
        for symbol in cfg.symbols:
            snapshot = current(conn, symbol)
            market = cfg.market_for(symbol)
            assert snapshot['market'] == market.name
            assert snapshot['market_timezone'] == market.timezone
            assert snapshot['quote_currency'] == 'TST'
        assert updater.calendars['NASDAQ'].name == 'NASDAQ'
    finally:
        conn.close()


def test_one_calendar_failure_does_not_block_another(tmp_path, monkeypatch):
    cfg = make_config(tmp_path, markets={'NYSE': {'symbols': ['TESTUS']}, 'ASX': {'symbols': ['TESTAU']}})
    conn = storage.connect_rw(cfg.sqlite_path)
    try:
        updater = Updater(cfg, conn, FakeProvider(markets={'TESTAU': 'ASX'}))
        monkeypatch.setattr(updater.calendars['NYSE'], 'target_session',
                            lambda *args: (_ for _ in ()).throw(RuntimeError('synthetic calendar failure')))
        updater.tick(dt.datetime(2025, 10, 15, 8, tzinfo=UTC))
        assert current(conn, 'TESTUS') is None
        assert 'synthetic calendar failure' in storage.get_market(conn, 'NYSE')['error']
        assert current(conn, 'TESTAU')['latest_session'] == '2025-10-15'
    finally:
        conn.close()


def test_exception_changes_are_scoped_and_aliases_preserved(tmp_path):
    cfg = make_config(tmp_path, markets={'NASDAQ': {'symbols': ['TEST1']}, 'NYSE': {'symbols': ['FAKEA']}})
    conn = storage.connect_rw(cfg.sqlite_path)
    provider = FakeProvider()
    try:
        updater = Updater(cfg, conn, provider)
        updater.tick(et(2025, 10, 15, 17))
        first = current(conn, 'TEST1')['id']
        other = current(conn, 'FAKEA')['id']
        cfg.calendar_exceptions_path.write_text(
            "NASDAQ:\n  early_closes:\n    - {date: '2025-10-01', close_time: '13:00', reason: synthetic}\n",
            encoding='utf-8',
        )
        provider.calls.clear()
        updater.tick(et(2025, 10, 15, 17, 1))
        assert [c[0] for c in provider.calls] == ['TEST1']
        assert current(conn, 'TEST1')['id'] != first
        assert current(conn, 'FAKEA')['id'] == other
        assert updater.calendars['NYSE'].exceptions.early_closes == {}
    finally:
        conn.close()


def test_invalid_group_uses_its_saved_rules_while_other_group_keeps_running(tmp_path):
    cfg = make_config(tmp_path, markets={'NYSE': {'symbols': ['TESTUS']}, 'ASX': {'symbols': ['TESTAU']}})
    conn = storage.connect_rw(cfg.sqlite_path)
    try:
        Updater(cfg, conn, FakeProvider(markets={'TESTAU': 'ASX'}))
        cfg.calendar_exceptions_path.write_text('NYSE: {early_closes: [123]}\nASX: {}\n', encoding='utf-8')
        updater = Updater(cfg, conn, FakeProvider(markets={'TESTAU': 'ASX'}))
        updater.tick(et(2025, 10, 15, 17))
        assert storage.get_market(conn, 'NYSE')['error']
        assert storage.get_market(conn, 'ASX')['error'] is None
        assert all(current(conn, symbol) is not None for symbol in cfg.symbols)
    finally:
        conn.close()


def test_moving_group_keeps_saved_provenance_and_requires_new_publication(tmp_path):
    cfg = make_config(tmp_path)
    conn = storage.connect_rw(cfg.sqlite_path)
    provider = FakeProvider()
    try:
        Updater(cfg, conn, provider).tick(et(2025, 10, 15, 17))
        old = current(conn, 'TEST1')
        moved = replace(cfg, markets=(replace(cfg.markets[0], name='NASDAQ'),))
        provider.errors['TEST1'] = [NetworkError('synthetic outage')]
        updater = Updater(moved, conn, provider)
        updater.tick(et(2025, 10, 15, 17, 1))
        assert storage.get_state(conn, 'TEST1')['market'] == 'NASDAQ'
        assert storage.get_state(conn, 'TEST1')['status'] != 'updated'
        assert current(conn, 'TEST1')['id'] == old['id']
        assert current(conn, 'TEST1')['market'] == 'NYSE'
        retry_at = storage.parse_ts(storage.get_state(conn, 'TEST1')['next_attempt_at'])
        updater.process_symbol('TEST1', DAY, retry_at)
        assert current(conn, 'TEST1')['market'] == 'NASDAQ'
        assert current(conn, 'TEST1')['id'] != old['id']
        assert current(conn, 'TEST1')['content_hash'] != old['content_hash']
    finally:
        conn.close()


def test_rule_change_never_reuses_old_normalization(tmp_path, monkeypatch):
    from services import update

    cfg = make_config(tmp_path)
    conn = storage.connect_rw(cfg.sqlite_path)
    try:
        provider = FakeProvider()
        Updater(cfg, conn, provider).tick(et(2025, 10, 15, 17))
        old = current(conn, 'TEST1')['id']
        monkeypatch.setattr(update, 'NORMALIZATION_VERSION', 'synthetic-next-normalization')
        Updater(cfg, conn, provider).tick(et(2025, 10, 15, 17, 1))
        assert current(conn, 'TEST1')['id'] != old
    finally:
        conn.close()


@pytest.mark.parametrize('zone', ['Asia/Shanghai', 'Australia/Sydney', 'America/New_York'])
def test_daily_bar_date_stays_in_its_market(zone):
    import pandas as pd

    raw = pd.DataFrame({'Open': [10.0], 'High': [11.0], 'Low': [9.0], 'Close': [10.5], 'Volume': [100]},
                       index=pd.DatetimeIndex(['2025-10-15']).tz_localize(zone))
    assert list(normalize_frame(raw, zone).index) == [DAY]
    other = 'America/New_York' if zone != 'America/New_York' else 'Australia/Sydney'
    with pytest.raises(ProviderError, match='conflicts'):
        normalize_frame(raw, other)
    with pytest.raises(ProviderError, match='no exchange timezone'):
        normalize_frame(raw.tz_localize(None), zone)


@pytest.mark.parametrize('zone', ['Asia/Shanghai', 'Australia/Sydney', 'America/New_York'])
def test_recovery_follows_market_timezone(zone):
    now = dt.datetime(2025, 10, 4, 0, tzinfo=UTC)
    recovered = next_recovery_time(now, ZoneInfo(zone))
    assert recovered > now
    assert recovered.astimezone(ZoneInfo(zone)).hour == 8


def test_australian_early_close_is_local_and_not_limited_by_new_york_date():
    day = dt.date(2025, 12, 24)
    cal = MarketCalendar('ASX', CalendarExceptions(early_closes={day: (dt.time(12), 'synthetic')}))
    now = dt.datetime(2025, 12, 24, 12, 30, tzinfo=ZoneInfo('Australia/Sydney'))
    assert now.astimezone(ZoneInfo('America/New_York')).date() < day
    assert cal.target_session(now, 30) == day


@pytest.mark.parametrize('markets', [
    {}, {'NO_SUCH_CALENDAR': {'symbols': ['TEST1']}}, {'NYSE': {'symbols': []}},
    {'NYSE': {'symbols': ['test1']}}, {'NYSE': {'symbols': ['TEST1', 'TEST1']}},
    {'NYSE': {'symbols': ['TEST1']}, 'ASX': {'symbols': ['TEST1']}},
    {'NYSE': {'symbols': ['TEST1'], 'timezone': 'UTC'}},
    {'NYSE': {'symbols': ['TEST1'], 'update_delay_minutes': True}},
])
def test_invalid_market_groups_fail_with_named_key(tmp_path, markets):
    with pytest.raises(ConfigError, match='markets'):
        build_config({**TEST_CONFIG, 'markets': markets}, tmp_path / 'config.json')
