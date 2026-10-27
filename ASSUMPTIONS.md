# Market and deployment assumptions

The dashboard supports completed daily stock and ETF bars from one configured
provider. Market-neutral scheduling does not promise support for every asset or
every symbol listed by a provider.

| Area | Behavior and boundary |
|---|---|
| Trading calendar | Explicit per-group calendar; no fallback to the US, a country, the host or the viewer's location. Calendar aliases retain the configured key. |
| Source coverage | Yahoo remains the only provider. A calendar being available does not prove that Yahoo covers its securities. Source timezone conflicts block publication; matching timezones alone do not prove exchange membership. |
| Session dates | Exchange-local dates, independent of browser timezone. Yahoo daily indexes must be timezone-aware. Overnight futures trade-date rules are not supported. The current session lookup searches the last 21 calendar days and reports an error if no completed session is found; extended exchange closures need a wider lookup policy. |
| Exceptions | One file grouped by configured calendar name. Each group retains its own last valid rules and error. Future holiday rules may be estimates until exchange announcements are incorporated. |
| Recovery | Short retry delays are elapsed time. Exhausted retries resume at the next 08:00 in the relevant market's timezone, including DST changes. This is a scheduling policy, not an exchange rule. |
| Data provenance | Snapshots record group, timezone, calendar package version, normalization version, source and quote currency. Rule changes cannot reuse a snapshot as if it used the new rules. |
| Display | Browser timezone is per session, or a fixed explicit IANA timezone. Missing browser context falls back to UTC. Market dates do not move. Quote units come from source metadata, including source-specific subunits; prices are not converted. |
| Precision | Small values get more display decimals. Content hashes use exact floating-point values rather than a fixed number of significant digits. |
| Missing bars | No invented bars. Strict history-gap checks remain; exchange calendars do not describe individual stock suspensions or provider gaps. |
| Symbols | Complete provider codes, unique across groups. Uppercase letters, digits, dot, hyphen and ampersand are accepted up to 64 characters. No suffix inference. Indices, currency pairs and derivatives are outside the supported product scope. |
| Indicators | EMA13 and MACD(12,26,9), 250 warmup bars by default, at most three years on the chart. These are documented product choices, not US-market rules. |
| Process and resources | One writer and one sequential update thread for modest lists on 1 vCPU / 1 GB. A slow provider request delays later symbols; this is not a parallel market-data service. |
| Portability | Python 3.13, POSIX locks isolated in locking.py, explicit UTF-8, paths relative to the selected config. Runtime loads project Streamlit settings outside the repository working directory. No platform-specific image pin. |
| Deployment | Bind address, port and paths come from config. Container mappings must match the configured port. Example loopback addresses and port 8501 are generic deployment examples. |
| Time storage | UTC instants and explicit timezone-aware datetimes. Logs, retention and export dates use UTC rather than a particular market's date. |
| Compatibility | The first release retains schema version 1 with the redesigned layout and grouped config. Earlier provisional layouts require a fresh database; no automatic rewriting or deletion is performed. |

Verification uses deterministic synthetic data and temporary databases. Tests
never request real market data. They check US, China and Australia session
boundaries, DST, group isolation, aliases, reassignment, precision and provenance.
Actual provider availability, exchange coverage, container permissions and
performance for a large watchlist must be verified in the deployment environment.
