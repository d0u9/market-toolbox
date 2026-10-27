# Changelog

## v0.1.0

- Group symbols by trading calendar, with independent sessions, timezones,
  update delays, calendar exceptions and recovery scheduling.
- Display timestamps in each browser's timezone by default, or a configured
  fixed IANA timezone. Trading dates remain market-local dates.
- Record snapshot market provenance, calendar and normalization versions, and
  source quote units. Keep saved provenance when a market assignment changes.
- Isolate calendar failures and retain each group's last valid exception rules.
- Preserve small quote values and exact content fingerprints; support complete
  non-US symbol spelling without guessing market membership from suffixes.
- Use UTC for logs and exports, load project settings independently of the
  working directory, and read healthcheck address/port from global config.

### Configuration and database requirements

Use the `markets` groups in the demo config as the configuration format. The
old top-level `symbols`, `market_calendar` and `update_delay_minutes` keys are
no longer accepted. Calendar exceptions are grouped by the same calendar names.

The redesigned database remains schema version 1 for this first release.
It requires a fresh database; earlier provisional layouts are rejected without
automatic migration, rewriting or deletion. Before restarting an existing
deployment, update its private config and select a fresh `sqlite_path`.

### Validation and supported scope

The test suite contains 196 synthetic-data checks, including Chromium chart
tests, cross-market scheduling, DST, provenance and failure isolation. Browser
sessions with China and Australia timezones were also checked locally.

The application supports completed daily stock and ETF bars through Yahoo.
Real provider coverage and availability depend on the symbol and deployment
network. See [ASSUMPTIONS.md](ASSUMPTIONS.md) for the remaining boundaries.
