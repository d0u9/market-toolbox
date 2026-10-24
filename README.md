# market-toolbox

A self-hosted dashboard that shows the daily Elder Impulse state of any list
of US-listed tickers (the demo config tracks QQQ and VOO). The page shows an
overview table with one row per symbol and a grid of charts, two per row; a
sidebar picker chooses which charts to draw. Add a symbol by adding it to
`symbols` in the config; nothing else changes.
After each US trading session a background worker fetches daily bars, validates
them, computes EMA13 / MACD(12,26,9) and publishes a snapshot to SQLite. A
Streamlit page reads that local cache only.

It shows completed daily bars only. There are no real-time quotes, no trading
and no buy/sell signals. Data comes from Yahoo Finance through `yfinance` for
personal research. The free source can rate-limit, change or lag, so delivery
at a fixed time each day is not guaranteed.

## Layout

```text
app.py                    Streamlit page (read-only database connection)
worker.py                 Scheduler, retries, run export; the only writer
config.py                 Loads and validates the global JSON config
calendar_exceptions.yaml  Confirmed closures / special close times
providers/                Daily bar protocol and the Yahoo adapter
services/calendar.py      Sessions, exceptions, target session
services/indicators.py    EMA, MACD histogram, Elder state (pure functions)
services/validation.py    Bar validation and history-gap grading
services/update.py        Fetch -> validate -> compute -> publish, retry state
storage.py                SQLite schema, atomic publish, read queries
locking.py                Single-writer lock (POSIX fcntl.flock)
charts.py                 Plotly figures
tests/                    Synthetic-data tests (no network)
data/                     Runtime data (not committed): database, WAL/SHM, exports,
                          writer lock, provider cache
```

## Configuration

All settings live in one JSON file. The path is taken from
`MARKET_TOOLBOX_CONFIG`; if that is unset, the demo `config.json` in the
project root is used. Relative paths are resolved against the config file's
directory. Keep a real config outside the repository or name it
`*.local.json`, which git ignores.

| Key | Default | Meaning |
|---|---|---|
| `symbols` | `["QQQ", "VOO"]` | Tickers to track |
| `data_provider` | `yahoo` | Only `yahoo` is implemented; `stooq` is reserved and rejected |
| `market_calendar` | `NYSE` | Session calendar |
| `calendar_exceptions_path` | `calendar_exceptions.yaml` | Confirmed closures / early closes |
| `display_timezone` | `UTC` | IANA zone for timestamps on the page only |
| `history_years` | `5` | Calendar years fetched on every update |
| `warmup_bars` | `250` | Leading bars without a valid Elder state |
| `update_delay_minutes` | `30` | Wait after the session close before fetching |
| `sqlite_path` | `data/market.sqlite3` | Database |
| `run_export_dir` | `data/exports` | Daily CSV export of `update_runs` (last 7 kept) |
| `request_timeout_seconds` | `30` | Provider request timeout |
| `log_level` | `INFO` | Logging level |

EMA13 and MACD(12,26,9) are fixed. Market logic always uses `America/New_York`.
Stored timestamps are in UTC.

## Run locally

Requires Python 3.13.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
python worker.py --once      # one manual update (shares the writer lock)
                             # exit 0 ok, 1 not up to date, 2 bad config,
                             # 3 another writer running, 4 data dir not writable
python worker.py             # keep running; checks every 60 seconds
streamlit run app.py
```

## Run with Docker Compose

```sh
mkdir -p data
docker compose up -d --build
```

- `web` and `worker` run from the same image as a non-root user.
- `./data` on the host is mounted read-write into both containers at
  `/app/data`. SQLite needs the directory writable for its WAL/SHM files.
  The web app still opens the database with `mode=ro` and never writes.
  Everything in `./data` survives restarts, rebuilds and upgrades.
- Containers run with a read-only image filesystem, all capabilities dropped
  and `no-new-privileges`; only `./data` and a `/tmp` tmpfs are writable.
- The page binds to `127.0.0.1:8501` only. Reach it through a VPN or an SSH
  tunnel. To expose it publicly, put a reverse proxy in front with HTTPS and
  authentication, and make sure WebSockets are forwarded (Streamlit needs them).

Host environment variables that `compose.yaml` reads:

| Variable | Default | Purpose |
|---|---|---|
| `MARKET_TOOLBOX_CONFIG_FILE` | `./config.json` | Host path of your real config; mounted read-only |
| `APP_UID` / `APP_GID` | `10001` | Container user; must be able to write `./data` |
| `WEB_PORT` | `8501` | Host port bound to `127.0.0.1` |
| `MARKET_TOOLBOX_IMAGE` | `market-toolbox:local` | Image to run, e.g. a prebuilt one from ghcr.io |

For example, with a personal config next to the compose file:

```sh
sudo chown -R 10001:10001 data     # or set APP_UID/APP_GID to the owner of ./data
MARKET_TOOLBOX_CONFIG_FILE=./config.local.json docker compose up -d --build
```

Inside the container the config is mounted at `/app/config.runtime.json`, so
relative paths in it resolve under `/app`. Keep `sqlite_path` and
`run_export_dir` relative (under `data/`). An absolute host path would not exist
inside the container.

`calendar_exceptions.yaml` is mounted as a single file. Some editors save by
replacing the file, which the container does not see. After editing it, run
`docker compose restart worker`.

### Prebuilt images

CI (`.github/workflows/ci.yml`) runs the tests, then builds `linux/amd64` and
`linux/arm64` images and pushes them to `ghcr.io/<owner>/market-toolbox`:

| Tag | Built from |
|---|---|
| `latest` | the default branch |
| `dev` | any other branch; shared, so the most recent push wins |
| `X.Y.Z`, `X.Y` | a `vX.Y.Z` git tag |
| `sha-<short>` | every published build |

Pull requests from forks are built and tested but never pushed.

To run a prebuilt image instead of building on the server:

```sh
export MARKET_TOOLBOX_IMAGE=ghcr.io/<owner>/market-toolbox:dev   # or :latest
docker compose pull
docker compose up -d --no-build
```

The first push creates the package on ghcr.io as private. Make it public once
under the package's settings so it can be pulled without logging in.

### Operations

```sh
docker compose logs -f worker                 # update activity and errors
docker compose exec worker python worker.py --once   # refused while the worker holds the lock
git pull && docker compose up -d --build        # upgrade; ./data is kept
```

Backups: market data can be re-downloaded, so no daily database backup is
taken. Keep your config, `calendar_exceptions.yaml` and `requirements.lock`.
The worker writes `data/exports/update_runs-YYYY-MM-DD.csv` daily. If you want
a full database copy, stop the worker first or use SQLite's backup API
(`sqlite3 data/market.sqlite3 ".backup copy.sqlite3"`). Copying the main file
alone while the worker runs can miss data still in the WAL file.

Troubleshooting:
- Page says "Initializing": the worker has not published yet. Check its logs.
- "The update worker has not checked in": the worker has been down for more
  than 5 minutes; the page keeps showing the last published data.
- Worker exits with "cannot write to the data directory": make `./data`
  writable by the container user (`APP_UID`/`APP_GID`).
- "Waiting for data": the target session is not available from the source
  yet. Retries run after 5, 15 and 30 minutes, then hourly, up to 8 attempts
  in total. After that, one recovery attempt runs each day at 08:00 New York time.
- Two rate-limit errors in a row stop retries until the 08:00 recovery attempt.
- `another writer holds .../.worker.lock`: a worker is already running.

## Data rules

- Prices use yfinance `auto_adjust=True` (split- and dividend-adjusted). They
  are not historical traded prices. Other platforms may differ if they use
  unadjusted prices or another EMA seed.
- Each update re-fetches the full window, so revisions by the source are picked
  up the next trading day.
- Validation blocks publication when the target day is missing, dates are
  duplicated, OHLC values are invalid, the head of the window cannot cover
  warmup + 3 years of display, or more than one session is missing mid-history.
- A single missing session mid-history is published with a warning. The 100
  bars after it are flagged, and the page shows a warning while any flagged bar
  is in view.
- Missing days are never filled in. A confirmed closure goes in
  `calendar_exceptions.yaml`, never a day the source simply failed to deliver.

## Indicators

```text
EMA(n)[0] = C[0];  EMA(n)[t] = a*C[t] + (1-a)*EMA(n)[t-1],  a = 2/(n+1)
MACD = EMA12 - EMA26;  Signal = EMA9(MACD);  Histogram = MACD - Signal
green: EMA13 up AND histogram up
red:   EMA13 down AND histogram down
blue:  anything else (ties included)
unavailable: missing values or warmup
```

## Portability notes

- `locking.py` uses `fcntl.flock`, so it runs on POSIX systems only (Linux, macOS).
- The test suite passed on Linux under several `TZ`/`LANG` settings, with both
  the locked dependency versions and newer pandas. The Docker image was built
  and run on Linux x86_64 only. arm64 and macOS were not tested.
- The Yahoo request and retry paths still need to be verified from the actual
  deployment server: data-center IPs are often rate-limited. Yahoo was not
  reachable from the environment this was developed in.

## Differences from the design document (v1.2)

- Configuration uses the single JSON file described above instead of a `.env` file.
- The default display time zone is `UTC`; set your own in a local config.
- The design lists `.env.example`; it is replaced by the JSON config plus the
  compose variables documented above.
