# market-toolbox

A self-hosted dashboard that shows the daily Elder Impulse state of any list
of US-listed tickers (the demo config tracks QQQ and VOO). Pick a symbol in the
sidebar; the page shows that one symbol, and the URL (`?symbol=QQQ`) links
straight to it. The UI uses [streamlit-shadcn-ui](https://github.com/ObservedObserver/streamlit-shadcn-ui)
(MIT) components. The candlestick chart uses
[TradingView Lightweight Charts™](https://www.tradingview.com/lightweight-charts/)
5.2.1 (Apache-2.0), vendored in `static/lightweight-charts/` (served by Streamlit and cached by
the browser) so the page needs
no CDN; its attribution is shown on the chart and in the page footer. Add a symbol by adding it to
`symbols` in the config; nothing else changes.
After each US trading session a background worker fetches daily bars, validates
them, computes EMA13 / MACD(12,26,9) and publishes a snapshot to SQLite. A
Streamlit page reads that local cache only. The supported runtime runs both in
one Python process: the updater is a background thread started once at server
startup, even when no browser is connected. Page reruns and additional sessions
do not start additional updaters. Existing snapshots are refreshed once on startup
to apply current calculation settings; a restart alone does not bypass pending
retry delays.

It shows completed daily bars only. There are no real-time quotes, no trading
and no buy/sell signals. Data comes from Yahoo Finance through `yfinance` for
personal research. The free source can rate-limit, change or lag, so delivery
at a fixed time each day is not guaranteed.

## Layout

```text
runtime.py                Single-process entry point: web server and updater thread
app.py                    Streamlit page (read-only database connection)
worker.py                 Update loop, retries, run export; the only writer
config.py                 Loads and validates the global JSON config
calendar_exceptions.yaml  Confirmed closures / special close times
providers/                Daily bar protocol and the Yahoo adapter
services/calendar.py      Sessions, exceptions, target session
services/indicators.py    EMA, MACD histogram, Elder state (pure functions)
services/validation.py    Bar validation and history-gap grading
services/update.py        Fetch -> validate -> compute -> publish, retry state
storage.py                SQLite schema, atomic publish, read queries
locking.py                Single-writer lock (POSIX fcntl.flock)
charts.py                 TradingView Lightweight Charts page
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
| `web_host` | `0.0.0.0` | Web server bind address |
| `web_port` | `8501` | Web server port inside the container or local process |

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
python runtime.py            # web + background updater in one process
```

## Run with Docker

One container runs one Python process, with the web server and a background
update thread. No separate worker container or cron job is needed. Stop signals
shut down the web server and let the updater finish its in-flight work, close
SQLite, and release the writer lock. No further symbol requests or update cycles
start after a stop is observed. Docker waits up to 330 seconds before force-killing
the process, allowing the maximum configured per-request timeout (300 seconds)
plus cleanup. A provider fetch can make multiple network calls; this is not a
hard time limit for the whole fetch. Increase Docker's stop timeout if needed.
If the updater exits unexpectedly, the
server stops and the process exits with an error so Docker can restart it.

Build the image and prepare the persistent directory and private config:

```sh
cp config.json config.local.json
mkdir -p data
docker build -t market-toolbox:local .
```

Edit `config.local.json` as needed. Keep `web_host` as `0.0.0.0`, `web_port` as
`8501`, and data paths under `data/` for the container commands below. The config
is mounted at `/app/config.runtime.json`, so relative paths resolve under `/app`.
A host absolute path does not refer to the same location inside the container.
The demo and private configuration must never contain deployment secrets in Git.

On Linux, allow the container's non-root user to write the data directory:

```sh
sudo chown -R 10001:10001 data
```

Start everything with one command:

```sh
docker run -d --name market-toolbox \
  --restart unless-stopped --stop-timeout 330 \
  -p 127.0.0.1:8501:8501 \
  --mount type=bind,src="$(pwd)/data",dst=/app/data \
  --mount type=bind,src="$(pwd)/config.local.json",dst=/app/config.runtime.json,readonly \
  --mount type=bind,src="$(pwd)/calendar_exceptions.yaml",dst=/app/calendar_exceptions.yaml,readonly \
  -e MARKET_TOOLBOX_CONFIG=/app/config.runtime.json \
  --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges \
  market-toolbox:local
```

Open `http://localhost:8501`. If that host port is occupied, use
`-p 127.0.0.1:8502:8501` and open port 8502 instead. The host mapping listens
only on loopback; use an SSH tunnel or VPN to access a remote deployment.
For public access, place an authenticated HTTPS reverse proxy in front and
forward WebSockets.

`./data` stores SQLite, its WAL/SHM files, exports and provider cache. It survives
container removal and image rebuilds. Only the updater writes; the page opens
SQLite read-only. Do not start a standalone worker against the same database
while this runtime is running.

### Optional Docker Compose

Compose also runs just one service, `app`:

```sh
MARKET_TOOLBOX_CONFIG_FILE=./config.local.json docker compose up -d --build
```

| Host variable | Default | Purpose |
|---|---|---|
| `MARKET_TOOLBOX_CONFIG_FILE` | `./config.json` | Config file mounted read-only |
| `APP_UID` / `APP_GID` | `10001` | Container user; must be able to write `./data` |
| `WEB_PORT` | `8501` | Host port bound to `127.0.0.1` |
| `MARKET_TOOLBOX_IMAGE` | `market-toolbox:local` | Image to run |

Keep these variables consistent across Compose commands. Changing `WEB_PORT`
changes only the host mapping; keep `web_port` at 8501 inside the container.
After editing a mounted config or calendar file, recreate the container to
reload it (some editors replace mounted files):

```sh
docker compose up -d --force-recreate
```

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
docker logs -f market-toolbox       # web and update logs
docker stop market-toolbox          # stop both, preserving ./data
docker start market-toolbox         # restart both
```

With Compose, use `docker compose logs -f app`. After changing code, run
`docker compose up -d --build`; the data directory is retained. With plain
Docker, stop and remove the old container, rebuild the image, and run the
command above again. Keep the mounted config and data directory.

Backups: market data can be re-downloaded, so no daily database backup is
taken. Keep your config, `calendar_exceptions.yaml` and `requirements.lock`.
The updater writes `data/exports/update_runs-YYYY-MM-DD.csv` daily. If you want
a full database copy, stop the container first or use SQLite's backup API
(`sqlite3 data/market.sqlite3 ".backup copy.sqlite3"`). Copying the main file
alone while the worker runs can miss data still in the WAL file.

Troubleshooting:
- Page says "Initializing": the update thread has not published yet. Check the container logs.
- "The update worker has not checked in": the updater has not reported for more
  than 5 minutes; the page keeps showing the last published data.
- Runtime exits with "cannot write to the data directory": make `./data`
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

- Charts use TradingView Lightweight Charts instead of Plotly. The design
  lists Lightweight Charts as a later upgrade; it was brought forward for a
  more professional candlestick view (bar width, crosshair, scrolling).

- Configuration uses the single JSON file described above instead of a `.env` file.
- The default display time zone is `UTC`; set your own in a local config.
- The design lists `.env.example`; it is replaced by the JSON config plus the
  compose variables documented above.
