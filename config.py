"""Load and validate the global JSON config file.

Resolution order: the ``MARKET_TOOLBOX_CONFIG`` environment variable, then the
demo ``config.json`` in the project root. Relative paths inside the file are
resolved against the config file's directory. Other modules must obtain
configuration only through this module.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV_VAR = "MARKET_TOOLBOX_CONFIG"
PROJECT_ROOT = Path(__file__).resolve().parent
DEMO_CONFIG = PROJECT_ROOT / "config.json"

# Providers with a working adapter. Reserved names are accepted by the schema
# but rejected with a clear message until an adapter is implemented and verified.
IMPLEMENTED_PROVIDERS = {"yahoo"}
RESERVED_PROVIDERS = {"stooq"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.&\-]{0,63}$")

DEFAULTS: dict[str, Any] = {
    "markets": {"NASDAQ": {"symbols": ["QQQ"], "update_delay_minutes": 30},
                "NYSE": {"symbols": ["VOO"], "update_delay_minutes": 30}},
    "data_provider": "yahoo",
    "calendar_exceptions_path": "calendar_exceptions.yaml",
    "display_timezone": "browser",
    "history_years": 5,
    "warmup_bars": 250,
    "sqlite_path": "data/market.sqlite3",
    "run_export_dir": "data/exports",
    "request_timeout_seconds": 30,
    "log_level": "INFO",
    "web_host": "0.0.0.0",
    "web_port": 8501,
}


class ConfigError(ValueError):
    """Raised when the config file is missing or contains an invalid value."""


@dataclass(frozen=True)
class MarketConfig:
    name: str
    symbols: tuple[str, ...]
    timezone: str
    update_delay_minutes: int

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class Config:
    config_path: Path
    markets: tuple[MarketConfig, ...]
    data_provider: str
    calendar_exceptions_path: Path
    display_timezone: str
    history_years: int
    warmup_bars: int
    sqlite_path: Path
    run_export_dir: Path
    request_timeout_seconds: float
    log_level: str
    web_host: str
    web_port: int

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(symbol for market in self.markets for symbol in market.symbols)

    def market_for(self, symbol: str) -> MarketConfig:
        return next(market for market in self.markets if symbol in market.symbols)

    @property
    def data_dir(self) -> Path:
        return self.sqlite_path.parent

    @property
    def lock_path(self) -> Path:
        return self.data_dir / ".worker.lock"

    @property
    def provider_cache_dir(self) -> Path:
        return self.data_dir / "provider-cache"


def resolve_config_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    value = env.get(ENV_VAR)
    return Path(value) if value else DEMO_CONFIG


def load_config(path: Path | str | None = None) -> Config:
    config_path = Path(path) if path is not None else resolve_config_path()

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ConfigError(f"duplicate config key: {key!r}")
            result[key] = value
        return result

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {config_path}") from None
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"config file {config_path} cannot be read: {exc}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {config_path} is not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {config_path} must contain a JSON object")
    return build_config(raw, config_path)


def build_config(raw: dict[str, Any], config_path: Path | str) -> Config:
    """Validate a raw mapping. Tests use this to build configs in code."""
    config_path = Path(config_path)
    unknown = sorted(set(raw) - set(DEFAULTS))
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(unknown)}")
    values = {**DEFAULTS, **raw}
    base_dir = config_path.resolve().parent

    def path_value(key: str) -> Path:
        value = values[key]
        if not isinstance(value, str) or not value:
            raise ConfigError(f"'{key}' must be a non-empty string path")
        p = Path(value)
        return p if p.is_absolute() else (base_dir / p)

    def int_value(key: str, lo: int, hi: int) -> int:
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ConfigError(f"'{key}' must be an integer between {lo} and {hi}")
        return value

    import pandas_market_calendars as mcal

    raw_markets = values["markets"]
    if not isinstance(raw_markets, dict) or not raw_markets:
        raise ConfigError("'markets' must be a non-empty object keyed by calendar name")
    markets = []
    seen = set()
    known_calendars = set(mcal.get_calendar_names())
    for name, settings in raw_markets.items():
        key = f"markets.{name}"
        if name not in known_calendars:
            raise ConfigError(f"'{key}' is not a registered trading calendar")
        if not isinstance(settings, dict) or set(settings) - {"symbols", "update_delay_minutes"}:
            raise ConfigError(f"'{key}' must contain only symbols and update_delay_minutes")
        symbols = settings.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            raise ConfigError(f"'{key}.symbols' must be a non-empty list")
        for symbol in symbols:
            if not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol):
                raise ConfigError(f"'{key}.symbols' contains an invalid ticker: {symbol!r}")
            if symbol in seen:
                raise ConfigError(f"'{key}.symbols' contains duplicate ticker {symbol!r}")
            seen.add(symbol)
        delay = settings.get("update_delay_minutes", 30)
        if isinstance(delay, bool) or not isinstance(delay, int) or not 0 <= delay <= 1440:
            raise ConfigError(f"'{key}.update_delay_minutes' must be an integer between 0 and 1440")
        try:
            calendar = mcal.get_calendar(name)
            timezone = str(calendar.tz)
            ZoneInfo(timezone)
        except Exception as exc:
            raise ConfigError(f"'{key}' cannot load its calendar/timezone: {exc}") from None
        markets.append(MarketConfig(name, tuple(symbols), timezone, delay))

    provider = values["data_provider"]
    if not isinstance(provider, str):
        raise ConfigError("'data_provider' must be a string")
    if provider in RESERVED_PROVIDERS:
        raise ConfigError(f"'data_provider' {provider!r} is reserved but not implemented yet")
    if provider not in IMPLEMENTED_PROVIDERS:
        raise ConfigError(
            f"'data_provider' must be one of {sorted(IMPLEMENTED_PROVIDERS)}, got {provider!r}"
        )

    tz = values["display_timezone"]
    try:
        if not isinstance(tz, str):
            raise ZoneInfoNotFoundError
        if tz != "browser":
            ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"'display_timezone' must be 'browser' or a valid IANA time zone: {tz!r}") from None

    timeout = values["request_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
        raise ConfigError("'request_timeout_seconds' must be a number between 1 and 300")

    level = values["log_level"]
    if not isinstance(level, str) or level.upper() not in LOG_LEVELS:
        raise ConfigError(f"'log_level' must be one of {sorted(LOG_LEVELS)}")

    host = values["web_host"]
    if not isinstance(host, str) or not host.strip() or any(c.isspace() for c in host):
        raise ConfigError("'web_host' must be a non-empty host address without whitespace")

    history_years = int_value("history_years", 4, 30)
    warmup_bars = int_value("warmup_bars", 50, 1000)

    return Config(
        config_path=config_path,
        markets=tuple(markets),
        data_provider=provider,
        calendar_exceptions_path=path_value("calendar_exceptions_path"),
        display_timezone=tz,
        history_years=history_years,
        warmup_bars=warmup_bars,
        sqlite_path=path_value("sqlite_path"),
        run_export_dir=path_value("run_export_dir"),
        request_timeout_seconds=float(timeout),
        log_level=level.upper(),
        web_host=host,
        web_port=int_value("web_port", 1, 65535),
    )
