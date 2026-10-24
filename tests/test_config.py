import json

import pytest

from config import DEMO_CONFIG, ENV_VAR, ConfigError, build_config, load_config, resolve_config_path


def test_demo_config_loads_with_generic_values():
    cfg = load_config(DEMO_CONFIG)
    assert cfg.display_timezone == "UTC"
    assert cfg.sqlite_path == DEMO_CONFIG.parent / "data" / "market.sqlite3"
    assert cfg.run_export_dir.parent == cfg.sqlite_path.parent
    assert cfg.lock_path.parent == cfg.sqlite_path.parent


def test_demo_config_lists_every_key():
    from config import DEFAULTS

    assert set(json.loads(DEMO_CONFIG.read_text(encoding="utf-8"))) == set(DEFAULTS)


def test_resolution_order(tmp_path):
    assert resolve_config_path({}) == DEMO_CONFIG
    assert resolve_config_path({ENV_VAR: str(tmp_path / "x.json")}) == tmp_path / "x.json"


def test_relative_paths_resolve_against_config_dir(tmp_path, monkeypatch):
    monkeypatch.chdir("/")
    cfg = build_config({"sqlite_path": "d/m.sqlite3"}, tmp_path / "sub" / "c.json")
    assert cfg.sqlite_path == (tmp_path / "sub" / "d" / "m.sqlite3").resolve()


@pytest.mark.parametrize("raw,key", [
    ({"symbols": []}, "symbols"),
    ({"symbols": ["qqq"]}, "symbols"),
    ({"symbols": ["A", "A"]}, "symbols"),
    ({"display_timezone": "Mars/Base"}, "display_timezone"),
    ({"data_provider": "stooq"}, "data_provider"),
    ({"data_provider": "other"}, "data_provider"),
    ({"data_provider": []}, "data_provider"),
    ({"data_provider": {}}, "data_provider"),
    ({"market_calendar": "LSE"}, "market_calendar"),
    ({"market_calendar": {}}, "market_calendar"),
    ({"market_calendar": []}, "market_calendar"),
    ({"history_years": 2}, "history_years"),
    ({"history_years": True}, "history_years"),
    ({"warmup_bars": "250"}, "warmup_bars"),
    ({"request_timeout_seconds": 0}, "request_timeout_seconds"),
    ({"log_level": "LOUD"}, "log_level"),
    ({"web_host": ""}, "web_host"),
    ({"web_host": 123}, "web_host"),
    ({"web_port": 0}, "web_port"),
    ({"web_port": True}, "web_port"),
    ({"sqlite_path": ""}, "sqlite_path"),
    ({"ema_fast": 12}, "ema_fast"),
])
def test_invalid_values_name_the_key(tmp_path, raw, key):
    with pytest.raises(ConfigError, match=key):
        build_config(raw, tmp_path / "c.json")


def test_missing_and_malformed_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "none.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(bad)
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(arr)
