import datetime as dt
import random

import pytest

import storage
from providers.base import NetworkError, RateLimited
from services import update as upd
from services.update import (
    MAX_ATTEMPTS,
    STATUS_FAILED,
    STATUS_INITIALIZING,
    STATUS_UPDATED,
    STATUS_WAITING,
    Updater,
    next_recovery_time,
)
from tests.fakes import FakeProvider, et, make_config

D = dt.date
WED = D(2025, 10, 15)
THU = D(2025, 10, 16)
NOW_WED = et(2025, 10, 15, 17)  # after Wednesday close + 30 min
NOW_THU = et(2025, 10, 16, 17)


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    conn = storage.connect_rw(cfg.sqlite_path)
    provider = FakeProvider()
    updater = Updater(cfg, conn, provider, rng=random.Random(1))
    yield cfg, conn, provider, updater
    conn.close()


def state(conn, symbol="TEST1"):
    return storage.get_state(conn, symbol)


def snapshot_count(conn, symbol="TEST1"):
    return conn.execute("SELECT COUNT(*) FROM snapshots WHERE symbol = ?", (symbol,)).fetchone()[0]


def current(conn, symbol="TEST1"):
    return storage.get_snapshot(conn, state(conn, symbol)["current_snapshot_id"])


def test_first_run_publishes_both_symbols(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    for symbol in cfg.symbols:
        s = state(conn, symbol)
        assert s["status"] == STATUS_UPDATED and s["quality"] == "complete"
        snap = current(conn, symbol)
        assert snap["latest_session"] == snap["target_session"] == WED.isoformat()
    values = storage.read_values(conn, current(conn)["id"])
    assert values.index[-1] == WED
    assert values["elder_state"].iloc[:cfg.warmup_bars].eq("unavailable").all()
    assert not values["elder_state"].iloc[cfg.warmup_bars:].eq("unavailable").any()


def test_request_window_and_exclusive_end(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    symbol, start, end = provider.calls[0]
    assert start == D(2020, 10, 15)
    assert end == WED + dt.timedelta(days=1)
    # Repeating the same target asks for the same window.
    updater.process_symbol("TEST1", WED, NOW_WED)
    assert len(provider.calls) == 2  # no second request once up to date


def test_future_rows_are_filtered(env):
    cfg, conn, provider, updater = env
    provider.extra_future_rows = 3
    updater.tick(NOW_WED)
    assert storage.read_values(conn, current(conn)["id"]).index[-1] == WED


def test_unchanged_content_does_not_add_snapshot(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    first = current(conn)["id"]
    with storage.transaction(conn):
        storage.update_state(conn, "TEST1", force_refresh=1)
    later = NOW_WED + dt.timedelta(minutes=5)
    updater.tick(later)
    assert current(conn)["id"] == first and snapshot_count(conn) == 1
    s = state(conn)
    assert s["last_checked_at"] == storage.iso(later)
    assert s["content_updated_at"] == storage.iso(NOW_WED)
    runs = [r["status"] for r in storage.recent_runs(conn, "TEST1")]
    assert runs[0] == "unchanged"


def test_next_day_publishes_and_keeps_only_two_snapshots(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    updater.tick(NOW_THU)
    updater.tick(et(2025, 10, 17, 17))
    s = state(conn)
    assert snapshot_count(conn) == 2
    assert current(conn)["latest_session"] == "2025-10-17"
    prev = storage.get_snapshot(conn, s["previous_snapshot_id"])
    assert prev["latest_session"] == THU.isoformat()
    orphans = conn.execute(
        "SELECT COUNT(*) FROM daily_values WHERE snapshot_id NOT IN (SELECT id FROM snapshots)"
    ).fetchone()[0]
    assert orphans == 0


def test_revision_of_history_is_picked_up_next_day(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    frame = provider.frame_for("TEST1")
    frame.loc[D(2025, 10, 14), "close"] *= 1.001  # source revises the prior day
    frame.loc[D(2025, 10, 14), "high"] *= 1.002
    updater.tick(NOW_THU)
    values = storage.read_values(conn, current(conn)["id"])
    assert values.loc[D(2025, 10, 14), "close"] == pytest.approx(frame.loc[D(2025, 10, 14), "close"])


def test_target_missing_waits_and_keeps_old_snapshot(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    old = current(conn)["id"]
    provider.available_until["TEST1"] = WED  # Thursday not published yet
    updater.tick(NOW_THU)
    s = state(conn)
    assert s["status"] == STATUS_WAITING and s["attempts"] == 1
    assert current(conn)["id"] == old
    assert "waiting for data" in s["status_detail"]
    # The other symbol is not blocked.
    assert current(conn, "FAKEA")["latest_session"] == THU.isoformat()
    # No request before the backoff elapses.
    calls = len(provider.calls)
    updater.tick(NOW_THU + dt.timedelta(minutes=3))
    assert len(provider.calls) == calls
    # Data arrives; next attempt succeeds.
    del provider.available_until["TEST1"]
    updater.tick(NOW_THU + dt.timedelta(minutes=6))
    s = state(conn)
    assert s["status"] == STATUS_UPDATED and s["attempts"] == 0
    assert current(conn)["latest_session"] == THU.isoformat()


def test_backoff_sequence_then_failed_and_recovery(env):
    cfg, conn, provider, updater = env
    provider.available_until["TEST1"] = D(2025, 10, 14)
    now = NOW_WED
    delays = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        updater.process_symbol("TEST1", WED, now)
        s = state(conn)
        assert s["attempts"] == attempt
        nxt = storage.parse_ts(s["next_attempt_at"])
        delays.append((nxt - now).total_seconds() / 60)
        now = nxt
    for got, base in zip(delays, upd.RETRY_DELAYS_MINUTES):
        assert base * 0.9 - 0.02 <= got <= base * 1.1 + 0.02
    s = state(conn)
    assert s["status"] == STATUS_INITIALIZING  # never had a snapshot
    nxt = storage.parse_ts(s["next_attempt_at"])
    assert nxt.astimezone(upd.MARKET_TZ).hour == 8


def test_failed_status_after_exhaustion_with_existing_snapshot(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    provider.available_until["TEST1"] = WED
    now = NOW_THU
    for _ in range(MAX_ATTEMPTS):
        updater.process_symbol("TEST1", THU, now)
        now = storage.parse_ts(state(conn)["next_attempt_at"])
    assert state(conn)["status"] == STATUS_FAILED
    assert current(conn)["latest_session"] == WED.isoformat()


def test_new_target_resets_attempts(env):
    cfg, conn, provider, updater = env
    provider.available_until["TEST1"] = D(2025, 10, 14)
    updater.process_symbol("TEST1", WED, NOW_WED)
    assert state(conn)["attempts"] == 1
    provider.available_until.pop("TEST1")
    updater.process_symbol("TEST1", THU, NOW_THU)
    s = state(conn)
    assert s["attempts"] == 0 and s["status"] == STATUS_UPDATED
    assert current(conn)["latest_session"] == THU.isoformat()


def test_consecutive_rate_limits_stop_until_recovery(env):
    cfg, conn, provider, updater = env
    provider.errors["TEST1"] = [RateLimited("429"), RateLimited("429")]
    updater.process_symbol("TEST1", WED, NOW_WED)
    first = storage.parse_ts(state(conn)["next_attempt_at"])
    assert first - NOW_WED < dt.timedelta(minutes=6)
    updater.process_symbol("TEST1", WED, first)
    s = state(conn)
    assert storage.parse_ts(s["next_attempt_at"]) == next_recovery_time(first)
    assert "rate_limited" in s["status_detail"]


def test_network_error_is_recorded(env):
    cfg, conn, provider, updater = env
    provider.errors["TEST1"] = [NetworkError("timed out")]
    updater.process_symbol("TEST1", WED, NOW_WED)
    run = storage.recent_runs(conn, "TEST1")[0]
    assert run["error_kind"] == "network_error" and run["status"] == STATUS_WAITING


def test_validation_failure_blocks_and_keeps_old(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    provider.drop["TEST1"] = {D(2025, 3, 3), D(2025, 6, 2)}  # two history gaps
    updater.tick(NOW_THU)
    s = state(conn)
    assert s["status"] == STATUS_FAILED and "validation failed" in s["status_detail"]
    assert current(conn)["latest_session"] == WED.isoformat()


def test_single_gap_publishes_with_quality_flags(env):
    cfg, conn, provider, updater = env
    gap = D(2025, 3, 3)  # Monday; more than 100 sessions before the target
    provider.drop["TEST1"] = {gap}
    updater.tick(NOW_WED)
    s = state(conn)
    assert s["status"] == STATUS_UPDATED and s["quality"] == "history_gap"
    assert gap.isoformat() in s["gaps_json"]
    values = storage.read_values(conn, current(conn)["id"])
    assert gap not in values.index
    assert values["quality_flag"].sum() == 100
    assert values.loc[D(2025, 3, 4), "quality_flag"] == 1
    assert values.loc[D(2025, 2, 28), "quality_flag"] == 0
    assert "missing session" in storage.recent_runs(conn, "TEST1")[0]["error_summary"]
    # Repair: data reappears; full recalculation clears the flags.
    provider.drop.clear()
    updater.tick(NOW_THU)
    s = state(conn)
    assert s["quality"] == "complete" and s["gaps_json"] == "[]"
    assert storage.read_values(conn, current(conn)["id"])["quality_flag"].sum() == 0


def test_restart_resumes_pending_retry(env, tmp_path):
    cfg, conn, provider, updater = env
    provider.available_until["TEST1"] = D(2025, 10, 14)
    updater.process_symbol("TEST1", WED, NOW_WED)
    pending = state(conn)["next_attempt_at"]
    conn.close()
    conn2 = storage.connect_rw(cfg.sqlite_path)
    updater2 = Updater(cfg, conn2, provider)
    calls = len(provider.calls)
    updater2.process_symbol("TEST1", WED, NOW_WED + dt.timedelta(minutes=1))
    assert len(provider.calls) == calls  # still respects the persisted backoff
    s = storage.get_state(conn2, "TEST1")
    assert s["next_attempt_at"] == pending and s["attempts"] == 1
    conn2.close()


def test_crashed_run_is_marked_abandoned(env):
    cfg, conn, provider, updater = env
    storage.start_run(conn, "TEST1", WED, 1, NOW_WED)
    Updater(cfg, conn, provider)
    assert storage.recent_runs(conn, "TEST1")[0]["status"] == "abandoned"


def test_catch_up_after_downtime_skips_intermediate_days(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    calls = len(provider.calls)
    updater.tick(et(2025, 10, 24, 17))  # a week later
    assert len(provider.calls) == calls + 2  # one request per symbol, not per day
    assert current(conn)["latest_session"] == "2025-10-24"


def test_weekend_retries_pending_friday(env):
    cfg, conn, provider, updater = env
    provider.available_until["TEST1"] = D(2025, 10, 16)
    updater.tick(et(2025, 10, 17, 17))
    assert state(conn)["status"] == STATUS_INITIALIZING
    provider.available_until.clear()
    updater.tick(et(2025, 10, 18, 12))
    assert current(conn)["latest_session"] == "2025-10-17"


def test_exceptions_change_forces_recalculation(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    old_hash = current(conn)["content_hash"]
    cfg.calendar_exceptions_path.write_text(
        "closures: []\nearly_closes:\n  - {date: '2025-10-01', close_time: '13:00', reason: test}\n",
        encoding="utf-8",
    )
    updater.tick(NOW_WED + dt.timedelta(minutes=1))
    snap = current(conn)
    assert snap["content_hash"] != old_hash
    assert state(conn)["force_refresh"] == 0


def test_invalid_exceptions_keep_previous(env, caplog):
    cfg, conn, provider, updater = env
    cfg.calendar_exceptions_path.write_text(
        "closures:\n  - {date: '2025-06-02', reason: confirmed test closure}\n", encoding="utf-8"
    )
    provider.drop["TEST1"] = {D(2025, 6, 2)}
    updater.tick(NOW_WED)
    assert state(conn)["quality"] == "complete"  # confirmed closure is not a gap
    cfg.calendar_exceptions_path.write_text("closures: [\n", encoding="utf-8")
    updater.tick(NOW_WED + dt.timedelta(minutes=1))
    assert "keeping previous version" in caplog.text
    assert D(2025, 6, 2) in updater.exceptions.closures


def test_forced_refresh_failure_keeps_updated_status(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    with storage.transaction(conn):
        storage.update_state(conn, "TEST1", force_refresh=1)
    provider.errors["TEST1"] = [NetworkError("down")]
    updater.tick(NOW_WED + dt.timedelta(minutes=1))
    assert state(conn)["status"] == STATUS_UPDATED


def test_publish_failure_rolls_back(env, monkeypatch):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    before = (current(conn)["id"], snapshot_count(conn),
              conn.execute("SELECT COUNT(*) FROM daily_values").fetchone()[0])

    real = storage.finish_run

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(storage, "finish_run", boom)
    with pytest.raises(RuntimeError):
        updater.process_symbol("TEST1", THU, NOW_THU)
    monkeypatch.setattr(storage, "finish_run", real)
    after = (current(conn)["id"], snapshot_count(conn),
             conn.execute("SELECT COUNT(*) FROM daily_values").fetchone()[0])
    assert after == before


def test_new_target_shows_waiting_before_attempt(env):
    cfg, conn, provider, updater = env
    updater.tick(NOW_WED)
    provider.errors["TEST1"] = [NetworkError("down")]
    updater.process_symbol("TEST1", THU, NOW_THU)
    assert state(conn)["status"] == STATUS_WAITING


def test_invalid_exceptions_at_startup_use_last_valid_version(env):
    cfg, conn, provider, updater = env
    cfg.calendar_exceptions_path.write_text(
        "closures:\n  - {date: '2025-06-02', reason: confirmed test closure}\n", encoding="utf-8"
    )
    updater.tick(NOW_WED)
    good_version = updater.exceptions.version
    cfg.calendar_exceptions_path.write_text("closures: [\n", encoding="utf-8")
    restarted = Updater(cfg, conn, provider)  # worker restart with a broken file
    assert restarted.exceptions.version == good_version
    assert D(2025, 6, 2) in restarted.exceptions.closures
    assert D(2025, 6, 2) not in restarted.calendar.sessions(D(2025, 6, 1), D(2025, 6, 4))


def test_invalid_exceptions_on_first_ever_start(tmp_path, caplog):
    cfg = make_config(tmp_path)
    cfg.calendar_exceptions_path.write_text("closures: [\n", encoding="utf-8")
    conn = storage.connect_rw(cfg.sqlite_path)
    updater = Updater(cfg, conn, FakeProvider())
    assert updater.exceptions.closures == {}
    assert "no earlier valid calendar exceptions" in caplog.text
    conn.close()


def test_target_cache_follows_market_date(env):
    cfg, conn, provider, updater = env
    cal = updater.calendar
    assert cal.target_session(NOW_WED, 30) == WED
    assert cal.target_session(NOW_THU, 30) == THU
    assert cal.target_session(et(2025, 10, 16, 9), 30) == WED
