"""Fetch, validate, compute and publish; plus per-symbol retry scheduling."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import random
import sqlite3
from dataclasses import dataclass
from typing import Callable

import pandas as pd

import storage
from config import Config
from providers.base import DailyBarProvider, DataNotFound, ProviderError, RateLimited
from services import indicators, validation
from services.calendar import (
    MARKET_TZ,
    CalendarExceptions,
    CalendarExceptionsError,
    MarketCalendar,
    parse_exceptions,
    years_before,
)

log = logging.getLogger(__name__)

STATUS_UPDATED = "updated"
STATUS_WAITING = "waiting"
STATUS_FAILED = "failed"
STATUS_INITIALIZING = "initializing"

DISPLAY_YEARS = 3
RETRY_DELAYS_MINUTES = [5, 15, 30, 60, 60, 60, 60]
MAX_ATTEMPTS = 8
RECOVERY_HOUR_ET = 8
JITTER_RATIO = 0.1


@dataclass
class AttemptOutcome:
    status: str  # run status: published, unchanged, waiting, failed
    detail: str = ""


def next_recovery_time(now: dt.datetime) -> dt.datetime:
    local = now.astimezone(MARKET_TZ)
    candidate = local.replace(hour=RECOVERY_HOUR_ET, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate = dt.datetime.combine(
            local.date() + dt.timedelta(days=1), dt.time(RECOVERY_HOUR_ET), MARKET_TZ
        )
    return candidate.astimezone(dt.timezone.utc)


def content_hash(bars: pd.DataFrame, source: str, adjustment: str,
                 exceptions_version: str, warmup_bars: int) -> str:
    h = hashlib.sha256()
    h.update(f"{source}|{adjustment}|{indicators.CALCULATION_VERSION}|"
             f"{exceptions_version}|{warmup_bars}\n".encode())
    for day, row in zip(bars.index, bars[["open", "high", "low", "close", "volume"]].itertuples(index=False)):
        h.update((day.isoformat() + "," + ",".join(f"{v:.10g}" for v in row) + "\n").encode())
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
        self.exceptions = CalendarExceptions()
        self.calendar = MarketCalendar(cfg.market_calendar, self.exceptions)
        storage.ensure_symbols(conn, cfg.symbols)
        storage.abandon_running_runs(conn)
        self.refresh_exceptions()

    # -- calendar exceptions ------------------------------------------------

    def refresh_exceptions(self) -> bool:
        """Reload the exceptions file when its content changes. True if reloaded.

        A file that fails to parse keeps the previous version. At startup the
        previous version is the last valid text stored in the database.
        """
        path = self.cfg.calendar_exceptions_path
        try:
            raw: bytes | None = path.read_bytes()
        except FileNotFoundError:
            raw = None
        current = "missing" if raw is None else hashlib.sha256(raw).hexdigest()
        if current == self._exceptions_file_hash:
            return False
        first_load = self._exceptions_file_hash is None
        self._exceptions_file_hash = current
        try:
            if raw is None:
                exceptions = CalendarExceptions(version="missing")
            else:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise CalendarExceptionsError(f"{path}: not valid UTF-8: {exc}") from None
                exceptions = parse_exceptions(text, str(path))
        except CalendarExceptionsError as exc:
            log.error("calendar exceptions not reloaded, keeping previous version: %s", exc)
            if first_load:
                self._load_stored_exceptions()
            return False

        self._use_exceptions(exceptions)
        stored = storage.get_meta(self.conn, "exceptions_version")
        with storage.transaction(self.conn):
            if not first_load or (stored is not None and stored != exceptions.version):
                log.info("calendar exceptions changed (version %s); forcing recalculation",
                         exceptions.version)
                for symbol in self.cfg.symbols:
                    storage.update_state(self.conn, symbol, force_refresh=1, next_attempt_at=None)
            storage.set_meta(self.conn, "exceptions_version", exceptions.version)
            storage.set_meta(self.conn, "exceptions_text", "" if raw is None else raw.decode("utf-8"))
        return True

    def _use_exceptions(self, exceptions: CalendarExceptions) -> None:
        self.exceptions = exceptions
        self.calendar = MarketCalendar(self.cfg.market_calendar, exceptions)

    def _load_stored_exceptions(self) -> None:
        version = storage.get_meta(self.conn, "exceptions_version")
        text = storage.get_meta(self.conn, "exceptions_text")
        if version is None or text is None:
            log.error("no earlier valid calendar exceptions stored; running without exceptions")
            return
        if version == "missing":
            exceptions = CalendarExceptions(version="missing")
        else:
            exceptions = parse_exceptions(text, "stored calendar exceptions")
        self._use_exceptions(exceptions)
        log.warning("using the last valid calendar exceptions (version %s)", exceptions.version)

    # -- scheduling ---------------------------------------------------------

    def tick(self, now: dt.datetime) -> None:
        self.refresh_exceptions()
        target = self.calendar.target_session(now, self.cfg.update_delay_minutes)
        for symbol in self.cfg.symbols:
            try:
                self.process_symbol(symbol, target, now)
            except Exception:  # one symbol must never block the other
                log.exception("unexpected error while updating %s", symbol)

    def process_symbol(self, symbol: str, target: dt.date, now: dt.datetime) -> AttemptOutcome | None:
        state = storage.get_state(self.conn, symbol)
        snapshot = storage.get_snapshot(self.conn, state["current_snapshot_id"])
        if state["target_session"] != target.isoformat():
            # A newer target takes priority and restarts the retry cycle.
            if snapshot is None:
                status = STATUS_INITIALIZING
            elif snapshot["latest_session"] == target.isoformat():
                status = STATUS_UPDATED
            else:
                status = STATUS_WAITING
            with storage.transaction(self.conn):
                storage.update_state(self.conn, symbol, target_session=target.isoformat(),
                                     attempts=0, next_attempt_at=None, status=status,
                                     status_detail=None)
            state = storage.get_state(self.conn, symbol)

        if snapshot and snapshot["latest_session"] == target.isoformat() and not state["force_refresh"]:
            if state["status"] != STATUS_UPDATED:
                with storage.transaction(self.conn):
                    storage.update_state(self.conn, symbol, status=STATUS_UPDATED)
            return None

        next_at = storage.parse_ts(state["next_attempt_at"])
        if next_at is not None and now < next_at:
            return None
        return self.attempt(symbol, target, now, state["attempts"] + 1)

    def _retry_at(self, attempts: int, now: dt.datetime) -> tuple[dt.datetime, bool]:
        """Return (next attempt time, exhausted)."""
        if attempts >= MAX_ATTEMPTS:
            return next_recovery_time(now), True
        base = RETRY_DELAYS_MINUTES[min(attempts, len(RETRY_DELAYS_MINUTES)) - 1]
        jitter = 1 + self.rng.uniform(-JITTER_RATIO, JITTER_RATIO)
        return now + dt.timedelta(minutes=base * jitter), False

    def _previous_run_kind(self, symbol: str, run_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT error_kind FROM update_runs WHERE symbol = ? AND id < ? ORDER BY id DESC LIMIT 1",
            (symbol, run_id),
        ).fetchone()
        return None if row is None else row["error_kind"]

    # -- one attempt --------------------------------------------------------

    def attempt(self, symbol: str, target: dt.date, now: dt.datetime, attempts: int) -> AttemptOutcome:
        run_id = storage.start_run(self.conn, symbol, target, attempts, now)
        try:
            return self._attempt(symbol, target, now, attempts, run_id)
        except Exception as exc:
            kind, failed, waiting_detail = self._classify(exc, symbol, run_id)
            retry_at, exhausted = self._retry_at(attempts, now)
            if kind == RateLimited.kind and self._previous_run_kind(symbol, run_id) == RateLimited.kind:
                retry_at, exhausted = next_recovery_time(now), True
            status = STATUS_FAILED if (failed or exhausted) else STATUS_WAITING
            detail = f"{waiting_detail}; next attempt {storage.iso(retry_at)}"
            log.warning("%s %s attempt %d: %s", symbol, target, attempts, detail)
            with storage.transaction(self.conn):
                storage.finish_run(self.conn, run_id, status, now, kind, waiting_detail)
                state = storage.get_state(self.conn, symbol)
                snapshot = storage.get_snapshot(self.conn, state["current_snapshot_id"])
                if snapshot is None:
                    shown = STATUS_INITIALIZING
                elif snapshot["latest_session"] == target.isoformat():
                    # A forced recalculation failed; the published data is still current.
                    shown = STATUS_UPDATED
                else:
                    shown = status
                storage.update_state(
                    self.conn, symbol,
                    status=shown,
                    status_detail=detail,
                    attempts=attempts,
                    next_attempt_at=storage.iso(retry_at),
                )
            return AttemptOutcome(status, detail)

    def _classify(self, exc: Exception, symbol: str, run_id: int) -> tuple[str, bool, str]:
        if isinstance(exc, validation.TargetMissing):
            return "target_missing", False, f"waiting for data: {exc}"
        if isinstance(exc, ValidationFailed):
            return "validation_failed", True, f"validation failed: {exc}"
        if isinstance(exc, DataNotFound):
            return exc.kind, False, f"data not found: {exc}"
        if isinstance(exc, ProviderError):
            return exc.kind, False, f"{exc.kind}: {exc}"
        log.exception("unexpected update error for %s", symbol)
        return "internal_error", True, f"internal error: {type(exc).__name__}: {exc}"

    def _attempt(self, symbol: str, target: dt.date, now: dt.datetime,
                 attempts: int, run_id: int) -> AttemptOutcome:
        start = years_before(target, self.cfg.history_years)
        end_exclusive = target + dt.timedelta(days=1)
        bars = self.provider.fetch_daily(symbol, start, end_exclusive)
        frame = bars.frame[[d <= target for d in bars.frame.index]]
        frame = frame[[d >= start for d in frame.index]]

        expected = self.calendar.sessions(start, end_exclusive)
        display_sessions = [d for d in expected if d > years_before(target, DISPLAY_YEARS)]
        required = self.cfg.warmup_bars + len(display_sessions)
        result = validation.validate_bars(frame, expected, target, required)
        for w in result.warnings:
            log.warning("%s: %s", symbol, w)
        if not result.ok:
            raise ValidationFailed("; ".join(result.errors))

        quality_message = "; ".join(result.warnings) or None
        gaps_json = storage.dumps([g.isoformat() for g in result.gaps])
        digest = content_hash(frame, bars.source, bars.adjustment,
                              self.exceptions.version, self.cfg.warmup_bars)
        state = storage.get_state(self.conn, symbol)
        snapshot = storage.get_snapshot(self.conn, state["current_snapshot_id"])
        common = dict(
            last_checked_at=storage.iso(now), quality=result.quality, gaps_json=gaps_json,
            quality_message=quality_message, status=STATUS_UPDATED, status_detail=None,
            attempts=0, next_attempt_at=None, force_refresh=0,
        )

        if snapshot is not None and snapshot["content_hash"] == digest:
            with storage.transaction(self.conn):
                storage.update_state(self.conn, symbol, **common)
                storage.finish_run(self.conn, run_id, "unchanged", now, None, quality_message)
            log.info("%s %s: content unchanged", symbol, target)
            return AttemptOutcome("unchanged")

        values = indicators.compute_indicators(frame, self.cfg.warmup_bars)
        values["quality_flag"] = validation.gap_flags(values.index, result.gaps)
        meta = dict(
            target_session=target.isoformat(),
            latest_session=frame.index[-1].isoformat(),
            source=bars.source,
            adjustment=bars.adjustment,
            params_json=storage.dumps(bars.params),
            fetched_at_utc=storage.iso(now),
            calculation_version=indicators.CALCULATION_VERSION,
            exceptions_version=self.exceptions.version,
            content_hash=digest,
            quality=result.quality,
            gaps_json=gaps_json,
        )
        snapshot_id = storage.publish_snapshot(
            self.conn, symbol, meta, values, {**common, "content_updated_at": storage.iso(now)},
            run=(run_id, now, quality_message),
        )
        log.info("%s %s: published snapshot %d (%s)", symbol, target, snapshot_id, result.quality)
        return AttemptOutcome("published")


class ValidationFailed(Exception):
    pass


def make_clock() -> Callable[[], dt.datetime]:
    return lambda: dt.datetime.now(dt.timezone.utc)
