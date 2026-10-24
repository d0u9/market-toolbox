#!/usr/bin/env python3
"""Background worker: the only database writer.

Usage:
    python worker.py           # run forever, checking every 60 seconds
    python worker.py --once    # one manual check (shares the writer lock)

Exit codes: 0 ok; 1 (--once) a symbol is not up to date; 2 bad config;
3 another writer holds the lock; 4 the data directory is not writable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import signal
import sqlite3
import sys
import threading

import storage
from config import ConfigError, load_config
from locking import LockHeld, WriterLock
from providers.base import get_provider
from services.update import MARKET_TZ, STATUS_UPDATED, Updater

CHECK_INTERVAL_SECONDS = 60
log = logging.getLogger("worker")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def daily_housekeeping(conn, cfg, now: dt.datetime, last_day: dt.date | None) -> dt.date:
    day = now.astimezone(MARKET_TZ).date()
    if day != last_day:
        storage.prune_runs(conn, now)
        path = storage.export_runs(conn, cfg.run_export_dir, day)
        log.info("exported update runs to %s", path.name)
    return day


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run a single check and exit")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    lock = WriterLock(cfg.lock_path)
    try:
        lock.acquire()
        conn = storage.connect_rw(cfg.sqlite_path)
    except LockHeld as exc:
        log.error("%s; is the worker already running?", exc)
        return 3
    except (OSError, sqlite3.OperationalError) as exc:
        lock.release()
        log.error("cannot write to the data directory %s as uid %s: %s",
                  cfg.data_dir, getattr(os, "getuid", lambda: "?")(), exc)
        return 4

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    try:
        provider = get_provider(cfg.data_provider, cfg.request_timeout_seconds,
                                cfg.provider_cache_dir)
        updater = Updater(cfg, conn, provider)
        log.info("worker started for %s using %s", ", ".join(cfg.symbols), cfg.data_provider)
        last_day = None
        while True:
            now = utcnow()
            try:
                updater.tick(now)
                storage.touch_heartbeat(conn, now)
                last_day = daily_housekeeping(conn, cfg, now, last_day)
            except Exception:
                log.exception("worker tick failed")
            if args.once or stop.wait(CHECK_INTERVAL_SECONDS):
                break
        not_updated = [s for s in cfg.symbols
                       if storage.get_state(conn, s)["status"] != STATUS_UPDATED]
        conn.close()
    finally:
        lock.release()
    if args.once and not_updated:
        log.warning("not up to date: %s", ", ".join(not_updated))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
