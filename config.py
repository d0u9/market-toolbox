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
SUPPORTED_CALENDARS = {"NYSE"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")

DEFAULTS: dict[str, Any] = {
    "symbols": ["QQQ", "VOO"],
    "data_provider": "yahoo",
    "market_calendar": "NYSE",
    "calendar_exceptions_path": "calendar_exceptions.yaml",
    "display_timezone": "UTC",
    "history_years": 5,
    "warmup_bars": 250,
    "update_delay_minutes": 30,
    "sqlite_path": "data/market.sqlite3",
    "run_export_dir": "data/exports",
    "request_timeout_seconds": 30,
    "log_level": "INFO",
}


class ConfigError(ValueError):
    """Raised when the config file is missing or contains an invalid value."""


@dataclass(frozen=True)
class Config:
    config_path: Path
    symbols: tuple[str, ...]
    data_provider: str
    market_calendar: str
    calendar_exceptions_path: Path
    display_timezone: str
    history_years: int
    warmup_bars: int
    update_delay_minutes: int
    sqlite_path: Path
    run_export_dir: Path
    request_timeout_seconds: float
    log_level: str

    @property
    def display_tz(self) -> ZoneInfo:
        return ZoneInfo(self.display_timezone)

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
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {config_path}") from None
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

    symbols = values["symbols"]
    if not isinstance(symbols, list) or not symbols:
        raise ConfigError("'symbols' must be a non-empty list of ticker strings")
    for s in symbols:
        if not isinstance(s, str) or not SYMBOL_RE.match(s):
            raise ConfigError(f"'symbols' contains an invalid ticker: {s!r}")
    if len(set(symbols)) != len(symbols):
        raise ConfigError("'symbols' contains duplicates")

    provider = values["data_provider"]
    if provider in RESERVED_PROVIDERS:
        raise ConfigError(f"'data_provider' {provider!r} is reserved but not implemented yet")
    if provider not in IMPLEMENTED_PROVIDERS:
        raise ConfigError(
            f"'data_provider' must be one of {sorted(IMPLEMENTED_PROVIDERS)}, got {provider!r}"
        )

    calendar = values["market_calendar"]
    if calendar not in SUPPORTED_CALENDARS:
        raise ConfigError(f"'market_calendar' must be one of {sorted(SUPPORTED_CALENDARS)}")

    tz = values["display_timezone"]
    try:
        if not isinstance(tz, str):
            raise ZoneInfoNotFoundError
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"'display_timezone' is not a valid IANA time zone: {tz!r}") from None

    timeout = values["request_timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
        raise ConfigError("'request_timeout_seconds' must be a number between 1 and 300")

    level = values["log_level"]
    if not isinstance(level, str) or level.upper() not in LOG_LEVELS:
        raise ConfigError(f"'log_level' must be one of {sorted(LOG_LEVELS)}")

    history_years = int_value("history_years", 4, 30)
    warmup_bars = int_value("warmup_bars", 50, 1000)

    return Config(
        config_path=config_path,
        symbols=tuple(symbols),
        data_provider=provider,
        market_calendar=calendar,
        calendar_exceptions_path=path_value("calendar_exceptions_path"),
        display_timezone=tz,
        history_years=history_years,
        warmup_bars=warmup_bars,
        update_delay_minutes=int_value("update_delay_minutes", 0, 720),
        sqlite_path=path_value("sqlite_path"),
        run_export_dir=path_value("run_export_dir"),
        request_timeout_seconds=float(timeout),
        log_level=level.upper(),
    )
