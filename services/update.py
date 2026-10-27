"""Fetch, validate, compute and publish; plus per-symbol retry scheduling."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import random
import sqlite3
import threading

import pandas as pd
import yaml

import storage
from config import Config
from providers.base import DailyBarProvider, DataNotFound, ProviderError, RateLimited, NORMALIZATION_VERSION
from services import indicators, retry, status, validation
from services.calendar import (
    CalendarExceptionsError,
    MarketCalendar,
    parse_exceptions,
    years_before,
)

log = logging.getLogger(__name__)

DISPLAY_YEARS = 3


def content_hash(bars: pd.DataFrame, source: str, adjustment: str,
                 exceptions_version: str, warmup_bars: int, *, market: str,
                 market_timezone: str, calendar_version: str, quote_currency: str | None) -> str:
    h = hashlib.sha256()
    h.update(f"{source}|{adjustment}|{indicators.CALCULATION_VERSION}|"
             f"{exceptions_version}|{warmup_bars}|{market}|{market_timezone}|"
             f"{calendar_version}|{NORMALIZATION_VERSION}|{quote_currency}\n".encode())
    for day, row in zip(bars.index, bars[["open", "high", "low", "close", "volume"]].itertuples(index=False)):
        h.update((day.isoformat() + "," + ",".join(float(v).hex() for v in row) + "\n").encode())
    return h.hexdigest()


class Updater:
    def __init__(
        self,
        cfg: Config,
        conn: sqlite3.Connection,
        provider: DailyBarProvider,
        rng: random.Random | None = None,
    ) -> None:
        self.cfg = cfg
        self.conn = conn
        self.provider = provider
        self.rng = rng or random.Random()
        self._exceptions_file_hash: str | None = None
        self.calendars: dict[str, MarketCalendar] = {}
        self.exception_errors: dict[str, str] = {}
        for market in cfg.markets:
            storage.ensure_symbols(conn, market.symbols, market.name)
        storage.abandon_running_runs(conn)
        # Reapply current rules once per startup without bypassing retry delays.
        with storage.transaction(conn):
            for symbol in cfg.symbols:
                if storage.get_state(conn, symbol)["current_snapshot_id"] is not None:
                    storage.update_state(conn, symbol, force_refresh=1)
        self.refresh_exceptions()
        if not self.calendars:
            raise CalendarExceptionsError("No usable market calendars; check calendar_exceptions_path and market errors")

    # -- calendar exceptions ------------------------------------------------

    def _keep_previous(self, name: str, error: str) -> None:
        self.exception_errors[name] = error
        storage.market_error(self.conn, name, error)
        log.error("%s: %s", name, error)
        if name not in self.calendars:
            saved = storage.get_market(self.conn, name)
            if saved["exceptions_text"] is not None:
                exceptions = parse_exceptions(saved["exceptions_text"], f"stored exceptions for {name}")
                self.calendars[name] = MarketCalendar(name, exceptions)

    def refresh_exceptions(self) -> bool:
        """Reload each configured group's rules independently, retaining its last valid rules."""
        path = self.cfg.calendar_exceptions_path
        try:
            raw = path.read_bytes()
            current = hashlib.sha256(raw).hexdigest()
            if current == self._exceptions_file_hash:
                return False
            self._exceptions_file_hash = current
            data = yaml.safe_load(raw.decode("utf-8"))
            if data is None:
                data = {}
            names = {market.name for market in self.cfg.markets}
            if not isinstance(data, dict) or any(not isinstance(k, str) for k in data):
                raise CalendarExceptionsError("top level must be an object keyed by configured calendar name")
            unknown = set(data) - names
            if unknown:
                raise CalendarExceptionsError(f"unknown calendar group(s): {sorted(unknown)}")
        except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
            for market in self.cfg.markets:
                self._keep_previous(market.name, f"Calendar exceptions not loaded from {path.name}: {exc}")
            return False

        for market in self.cfg.markets:
            name = market.name
            try:
                text = yaml.safe_dump(data.get(name, {}), sort_keys=True)
                exceptions = parse_exceptions(text, f"{path.name}:{name}")
                calendar = MarketCalendar(name, exceptions)
            except (CalendarExceptionsError, ValueError, RuntimeError) as exc:
                self._keep_previous(name, str(exc))
                continue
            saved = storage.get_market(self.conn, name)
            with storage.transaction(self.conn):
                if saved["exceptions_version"] is not None and saved["exceptions_version"] != exceptions.version:
                    for symbol in market.symbols:
                        storage.update_state(self.conn, symbol, force_refresh=1)
                storage.save_market(self.conn, name, text, exceptions.version)
            self.calendars[name] = calendar
            self.exception_errors.pop(name, None)
        return True

    def snapshot_matches(self, symbol: str, snapshot: dict | None) -> bool:
        market = self.cfg.market_for(symbol)
        calendar = self.calendars.get(market.name)
        return bool(snapshot and calendar and snapshot["market"] == market.name
                    and snapshot["market_timezone"] == market.timezone
                    and snapshot["calendar_version"] == calendar.version
                    and snapshot["normalization_version"] == NORMALIZATION_VERSION)

    # -- scheduling ---------------------------------------------------------

    def tick(self, now: dt.datetime, stop: threading.Event | None = None) -> None:
        self.refresh_exceptions()
        for market in self.cfg.markets:
            if stop is not None and stop.is_set():
                return
            calendar = self.calendars.get(market.name)
            if calendar is None:
                continue
            try:
                target = calendar.target_session(now, market.update_delay_minutes)
                storage.market_error(self.conn, market.name, self.exception_errors.get(market.name))
            except Exception as exc:
                storage.market_error(self.conn, market.name, f"Calendar scheduling failed: {exc}")
                log.exception("calendar scheduling failed for %s", market.name)
                continue
            for symbol in market.symbols:
                if stop is not None and stop.is_set():
                    return
                try:
                    self.process_symbol(symbol, target, now)
                except Exception:
                    log.exception("unexpected error while updating %s", symbol)

    def process_symbol(self, symbol: str, target: dt.date, now: dt.datetime) -> None:
        state = storage.get_state(self.conn, symbol)
        snapshot = storage.get_snapshot(self.conn, state["current_snapshot_id"])
        if state["target_session"] != target.isoformat():
            # A newer target takes priority and restarts the retry cycle.
            if snapshot is None:
                shown = status.INITIALIZING
            elif snapshot["latest_session"] == target.isoformat() and self.snapshot_matches(symbol, snapshot):
                shown = status.UPDATED
            else:
                shown = status.WAITING
            with storage.transaction(self.conn):
                storage.update_state(self.conn, symbol, target_session=target.isoformat(),
                                     attempts=0, next_attempt_at=None, status=shown,
                                     status_detail=None)
            state = storage.get_state(self.conn, symbol)

        if self.snapshot_matches(symbol, snapshot) and snapshot["latest_session"] == target.isoformat() and not state["force_refresh"]:
            if state["status"] != status.UPDATED:
                with storage.transaction(self.conn):
                    storage.update_state(self.conn, symbol, status=status.UPDATED)
            return

        next_at = storage.parse_ts(state["next_attempt_at"])
        if next_at is not None and now < next_at:
            return
        self.attempt(symbol, target, now, state["attempts"] + 1)

    def _previous_run_kind(self, symbol: str, run_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT error_kind FROM update_runs WHERE symbol = ? AND id < ? ORDER BY id DESC LIMIT 1",
            (symbol, run_id),
        ).fetchone()
        return None if row is None else row["error_kind"]

    # -- one attempt --------------------------------------------------------

    def attempt(self, symbol: str, target: dt.date, now: dt.datetime, attempts: int) -> None:
        run_id = storage.start_run(self.conn, symbol, target, attempts, now)
        try:
            self._attempt(symbol, target, now, attempts, run_id)
        except Exception as exc:
            kind, failed, waiting_detail = self._classify(exc, symbol, run_id)
            repeated_rate_limit = (kind == RateLimited.kind
                                   and self._previous_run_kind(symbol, run_id) == RateLimited.kind)
            retry_at, exhausted = retry.next_attempt(attempts, now, self.rng, self.cfg.market_for(symbol).tz, repeated_rate_limit)
            outcome = status.FAILED if (failed or exhausted) else status.WAITING
            detail = f"{waiting_detail}; next attempt {storage.iso(retry_at)}"
            log.warning("%s %s attempt %d: %s", symbol, target, attempts, detail)
            with storage.transaction(self.conn):
                storage.finish_run(self.conn, run_id, outcome, now, kind, waiting_detail)
                state = storage.get_state(self.conn, symbol)
                snapshot = storage.get_snapshot(self.conn, state["current_snapshot_id"])
                if snapshot is None:
                    shown = status.INITIALIZING
                elif snapshot["latest_session"] == target.isoformat() and self.snapshot_matches(symbol, snapshot):
                    # A forced recalculation failed; the published data is still current.
                    shown = status.UPDATED
                else:
                    shown = outcome
                storage.update_state(
                    self.conn, symbol,
                    status=shown,
                    status_detail=detail,
                    attempts=attempts,
                    next_attempt_at=storage.iso(retry_at),
                )

    def _classify(self, exc: Exception, symbol: str, run_id: int) -> tuple[str, bool, str]:
        if isinstance(exc, validation.TargetMissing):
            return "target_missing", False, f"waiting for data: {exc}"
        if isinstance(exc, validation.ValidationFailed):
            return "validation_failed", True, f"validation failed: {exc}"
        if isinstance(exc, DataNotFound):
            return exc.kind, False, f"data not found: {exc}"
        if isinstance(exc, ProviderError):
            return exc.kind, False, f"{exc.kind}: {exc}"
        log.exception("unexpected update error for %s", symbol)
        return "internal_error", True, f"internal error: {type(exc).__name__}: {exc}"

    def _attempt(self, symbol: str, target: dt.date, now: dt.datetime,
                 attempts: int, run_id: int) -> None:
        start = years_before(target, self.cfg.history_years)
        end_exclusive = target + dt.timedelta(days=1)
        market = self.cfg.market_for(symbol)
        calendar = self.calendars[market.name]
        bars = self.provider.fetch_daily(symbol, start, end_exclusive, market.timezone)
        frame = bars.frame[[d <= target for d in bars.frame.index]]
        frame = frame[[d >= start for d in frame.index]]

        expected = calendar.sessions(start, end_exclusive)
        display_sessions = [d for d in expected if d > years_before(target, DISPLAY_YEARS)]
        required = self.cfg.warmup_bars + len(display_sessions)
        result = validation.validate_bars(frame, expected, target, required)
        for w in result.warnings:
            log.warning("%s: %s", symbol, w)
        if not result.ok:
            raise validation.ValidationFailed("; ".join(result.errors))

        quality_message = "; ".join(result.warnings) or None
        gaps_json = storage.dumps([g.isoformat() for g in result.gaps])
        digest = content_hash(frame, bars.source, bars.adjustment,
                              calendar.exceptions.version, self.cfg.warmup_bars,
                              market=market.name, market_timezone=market.timezone,
                              calendar_version=calendar.version, quote_currency=bars.params.get("currency"))
        state = storage.get_state(self.conn, symbol)
        snapshot = storage.get_snapshot(self.conn, state["current_snapshot_id"])
        common = dict(
            last_checked_at=storage.iso(now), quality=result.quality, gaps_json=gaps_json,
            quality_message=quality_message, status=status.UPDATED, status_detail=None,
            attempts=0, next_attempt_at=None, force_refresh=0,
        )

        if snapshot is not None and snapshot["content_hash"] == digest:
            with storage.transaction(self.conn):
                storage.update_state(self.conn, symbol, **common)
                storage.finish_run(self.conn, run_id, status.RUN_UNCHANGED, now, None, quality_message)
            log.info("%s %s: content unchanged", symbol, target)
            return

        values = indicators.compute_indicators(frame, self.cfg.warmup_bars)
        values["quality_flag"] = validation.gap_flags(values.index, result.gaps)
        meta = dict(
            market=market.name, market_timezone=market.timezone,
            calendar_version=calendar.version, normalization_version=NORMALIZATION_VERSION,
            quote_currency=bars.params.get("currency"),
            target_session=target.isoformat(),
            latest_session=frame.index[-1].isoformat(),
            source=bars.source,
            adjustment=bars.adjustment,
            params_json=storage.dumps(bars.params),
            fetched_at_utc=storage.iso(now),
            calculation_version=indicators.CALCULATION_VERSION,
            exceptions_version=calendar.exceptions.version,
            content_hash=digest,
            quality=result.quality,
            gaps_json=gaps_json,
        )
        snapshot_id = storage.publish_snapshot(
            self.conn, symbol, meta, values, {**common, "content_updated_at": storage.iso(now)},
            run=(run_id, now, quality_message),
        )
        log.info("%s %s: published snapshot %d (%s)", symbol, target, snapshot_id, result.quality)

