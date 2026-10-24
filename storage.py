"""SQLite schema, atomic snapshot publication and read-only queries.

Only the worker writes. The web app opens the database with ``mode=ro`` and
reads a snapshot and its state inside one read transaction.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 10_000
SNAPSHOT_RUN_RETENTION_DAYS = 90
EXPORT_KEEP = 7

VALUE_COLUMNS = [
    "open", "high", "low", "close", "volume",
    "ema13", "ema26", "macd", "signal", "histogram", "elder_state", "quality_flag",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    target_session TEXT NOT NULL,
    latest_session TEXT NOT NULL,
    source TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    params_json TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    exceptions_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    quality TEXT NOT NULL,
    gaps_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_symbol ON snapshots(symbol);
CREATE TABLE IF NOT EXISTS daily_values (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    session_date TEXT NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume REAL NOT NULL,
    ema13 REAL, ema26 REAL, macd REAL, signal REAL, histogram REAL,
    elder_state TEXT NOT NULL,
    quality_flag INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, session_date)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS symbol_state (
    symbol TEXT PRIMARY KEY,
    current_snapshot_id INTEGER REFERENCES snapshots(id),
    previous_snapshot_id INTEGER REFERENCES snapshots(id),
    last_checked_at TEXT,
    content_updated_at TEXT,
    quality TEXT,
    gaps_json TEXT NOT NULL DEFAULT '[]',
    quality_message TEXT,
    target_session TEXT,
    status TEXT NOT NULL DEFAULT 'initializing',
    status_detail TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    force_refresh INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS update_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    target_session TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    error_kind TEXT,
    error_summary TEXT
);
CREATE INDEX IF NOT EXISTS update_runs_started ON update_runs(started_at);
"""

STATE_FIELDS = {
    "current_snapshot_id", "previous_snapshot_id", "last_checked_at", "content_updated_at",
    "quality", "gaps_json", "quality_message", "target_session", "status", "status_detail",
    "attempts", "next_attempt_at", "force_refresh",
}


def iso(ts: dt.datetime | None) -> str | None:
    return None if ts is None else ts.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str | None) -> dt.datetime | None:
    return None if value is None else dt.datetime.fromisoformat(value)


def connect_rw(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with transaction(conn):
        for stmt in filter(str.strip, SCHEMA.split(";")):
            conn.execute(stmt)
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    return conn


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open read-only. Raises ``FileNotFoundError`` until the worker creates it."""
    if not path.exists():
        raise FileNotFoundError(path)
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection, mode: str = "IMMEDIATE") -> Iterator[None]:
    conn.execute(f"BEGIN {mode}")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --- worker side -----------------------------------------------------------

def ensure_symbols(conn: sqlite3.Connection, symbols: tuple[str, ...]) -> None:
    with transaction(conn):
        for s in symbols:
            conn.execute("INSERT OR IGNORE INTO symbol_state(symbol) VALUES (?)", (s,))


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def touch_heartbeat(conn: sqlite3.Connection, now: dt.datetime) -> None:
    with transaction(conn):
        set_meta(conn, "worker_heartbeat", iso(now))


def get_state(conn: sqlite3.Connection, symbol: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM symbol_state WHERE symbol = ?", (symbol,)).fetchone()
    if row is None:
        raise KeyError(symbol)
    return dict(row)


def update_state(conn: sqlite3.Connection, symbol: str, **fields: Any) -> None:
    bad = set(fields) - STATE_FIELDS
    if bad:
        raise ValueError(f"unknown state fields: {bad}")
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE symbol_state SET {assignments} WHERE symbol = ?", (*fields.values(), symbol)
    )


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int | None) -> dict[str, Any] | None:
    if snapshot_id is None:
        return None
    row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return None if row is None else dict(row)


def publish_snapshot(
    conn: sqlite3.Connection,
    symbol: str,
    meta: dict[str, Any],
    values: pd.DataFrame,
    state_fields: dict[str, Any],
    run: tuple[int, dt.datetime, str | None] | None = None,
) -> int:
    """Insert a snapshot with all rows and switch the published reference.

    Runs in one write transaction; keeps only the current and previous snapshot.
    """
    with transaction(conn):
        state = get_state(conn, symbol)
        cur = conn.execute(
            """INSERT INTO snapshots(symbol, target_session, latest_session, source, adjustment,
                   params_json, fetched_at_utc, calculation_version, exceptions_version,
                   content_hash, quality, gaps_json)
               VALUES (:symbol, :target_session, :latest_session, :source, :adjustment,
                   :params_json, :fetched_at_utc, :calculation_version, :exceptions_version,
                   :content_hash, :quality, :gaps_json)""",
            {"symbol": symbol, **meta},
        )
        snapshot_id = int(cur.lastrowid)
        rows = (
            (snapshot_id, d.isoformat(), *[_sql_value(v) for v in row])
            for d, row in zip(values.index, values[VALUE_COLUMNS].itertuples(index=False, name=None))
        )
        conn.executemany(
            f"INSERT INTO daily_values(snapshot_id, session_date, {', '.join(VALUE_COLUMNS)}) "
            f"VALUES ({', '.join('?' * (len(VALUE_COLUMNS) + 2))})",
            rows,
        )
        previous = state["current_snapshot_id"]
        update_state(
            conn, symbol,
            current_snapshot_id=snapshot_id, previous_snapshot_id=previous, **state_fields,
        )
        keep = [i for i in (snapshot_id, previous) if i is not None]
        conn.execute(
            f"DELETE FROM snapshots WHERE symbol = ? AND id NOT IN ({', '.join('?' * len(keep))})",
            (symbol, *keep),
        )
        if run is not None:
            run_id, now, note = run
            finish_run(conn, run_id, "published", now, None, note)
    return snapshot_id


def _sql_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def start_run(conn: sqlite3.Connection, symbol: str, target: dt.date, attempt: int, now: dt.datetime) -> int:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO update_runs(symbol, target_session, started_at, status, attempt) "
            "VALUES (?, ?, ?, 'running', ?)",
            (symbol, target.isoformat(), iso(now), attempt),
        )
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, now: dt.datetime,
               error_kind: str | None = None, error_summary: str | None = None) -> None:
    conn.execute(
        "UPDATE update_runs SET finished_at = ?, status = ?, error_kind = ?, error_summary = ? WHERE id = ?",
        (iso(now), status, error_kind, (error_summary or None) and error_summary[:1000], run_id),
    )


def abandon_running_runs(conn: sqlite3.Connection) -> None:
    """Close runs left 'running' by a crashed worker."""
    with transaction(conn):
        conn.execute("UPDATE update_runs SET status = 'abandoned' WHERE status = 'running'")


def prune_runs(conn: sqlite3.Connection, now: dt.datetime) -> None:
    cutoff = now - dt.timedelta(days=SNAPSHOT_RUN_RETENTION_DAYS)
    with transaction(conn):
        conn.execute("DELETE FROM update_runs WHERE started_at < ?", (iso(cutoff),))


def export_runs(conn: sqlite3.Connection, export_dir: Path, day: dt.date) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    target = export_dir / f"update_runs-{day.isoformat()}.csv"
    rows = conn.execute("SELECT * FROM update_runs ORDER BY id").fetchall()
    tmp = target.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(rows[0].keys() if rows else
                        ["id", "symbol", "target_session", "started_at", "finished_at",
                         "status", "attempt", "error_kind", "error_summary"])
        writer.writerows(tuple(r) for r in rows)
    tmp.replace(target)
    for old in sorted(export_dir.glob("update_runs-*.csv"))[:-EXPORT_KEEP]:
        old.unlink()
    return target


# --- read side --------------------------------------------------------------

def read_symbol(conn: sqlite3.Connection, symbol: str) -> tuple[dict | None, dict | None]:
    """Return (state, snapshot) consistently. Values load separately by snapshot id."""
    with transaction(conn, "DEFERRED"):
        row = conn.execute("SELECT * FROM symbol_state WHERE symbol = ?", (symbol,)).fetchone()
        state = None if row is None else dict(row)
        snapshot = get_snapshot(conn, state["current_snapshot_id"]) if state else None
    return state, snapshot


def read_values(conn: sqlite3.Connection, snapshot_id: int) -> pd.DataFrame:
    frame = pd.read_sql_query(
        f"SELECT session_date, {', '.join(VALUE_COLUMNS)} FROM daily_values "
        "WHERE snapshot_id = ? ORDER BY session_date",
        conn, params=(snapshot_id,),
    )
    frame["session_date"] = [dt.date.fromisoformat(d) for d in frame["session_date"]]
    return frame.set_index("session_date")


def recent_runs(conn: sqlite3.Connection, symbol: str, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM update_runs WHERE symbol = ? ORDER BY id DESC LIMIT ?", (symbol, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)
