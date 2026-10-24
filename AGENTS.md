# AGENTS.md

Rules for any coding agent (and human) working in this repository.

## This is a public repository

Everything committed here is public. Never commit personal or environment-specific data, including:

- Names, email addresses, phone numbers, or account identifiers.
- Server IPs, hostnames, domain names, ports of a real deployment, SSH config, or VPN details.
- Passwords, API keys, tokens, cookies, or any other credentials.
- Absolute local paths that reveal a user or machine (for example `/Users/<name>/...`).
- A real personal config file or any `.env` file. Only the demo `config.json`, containing fake or generic values, is committed.
- Personal preferences that identify the owner (location, time zone, brokerage, holdings). Defaults in code and in the demo `config.json` must be generic; personal values go in a separate, uncommitted JSON file.

## Personal and environment values live in one global config file

- All personal and environment-specific values are set in a single global JSON config file.
- The committed `config.json` is a demo only. It contains every supported key with generic defaults or obvious fake values (for example `"display_timezone": "UTC"`), so the project runs out of the box. It must never contain real personal data. When a key is added, removed, or renamed, update the demo in the same change.
- A real deployment uses its own JSON file in place of the demo. Keep it outside the repository, or name it `*.local.json` (ignored by `.gitignore`). Never edit the demo `config.json` to hold real values.
- The config path is resolved in this order: the `MARKET_TOOLBOX_CONFIG` environment variable, then the demo `config.json` in the project root. No other location is searched; do not read from the user's home directory or system paths implicitly.
- Code reads configuration only through `config.py`, which loads the JSON, applies defaults, validates types and values, and fails at startup with a clear message naming the bad key. Other modules never read the file or environment variables directly.
- Do not hard-code personal values in code, tests, Dockerfiles, `compose.yaml`, or docs. In Docker, mount the real config file read-only into the container and point `MARKET_TOOLBOX_CONFIG` at it.
- Secrets, if ever needed, go in the real config file (or a file it references), never in source or the demo. Never log secret values or the full config.
- Tests never read a real config file; they build config objects in code or use the demo or a fixture config with fake values.

Never commit runtime artifacts:

- SQLite databases and their `-wal` / `-shm` files, anything under `data/`.
- Downloaded market data, API responses, logs, or exported run records.

Before every commit, review `git diff --cached` for the items above. If something sensitive was committed, stop and tell the user; do not try to hide it with a follow-up commit, because history stays public.

## Tests use fake data only

- All test data is synthetic: generated in code or stored as small hand-written fixtures under `tests/fixtures/`.
- Never record, copy, or commit real market data from Yahoo Finance or any other provider, even as a fixture. This avoids both data-licensing issues and accidental leaks.
- Use obviously fake symbols in fixtures (for example `TEST1`, `FAKEA`) unless a test specifically checks symbol handling.
- Synthetic data must be deterministic: fixed values or a fixed random seed, so results are reproducible.
- Tests must not access the network. Replace the provider with a fake implementation of `providers/base.py`; never call yfinance in tests.
- Expected indicator values in fixtures are computed by hand or by an independent reference calculation and checked in alongside the input.
- Tests that touch SQLite use a temporary directory (`tmp_path`), never a shared or real database path.

## Portability: do not assume the current host

The machine an agent runs on is only one of many places this code will run (other developers' machines, CI, Docker on Linux servers, ARM and x86). Never treat the current host as the target environment.

- **OS:** code and scripts must work on Linux and macOS; avoid OS-specific APIs and commands unless guarded with a fallback. The container image is the deployment target, not the dev machine. If something is POSIX-only (such as `fcntl.flock`), document it and keep it isolated in one module.
- **CPU architecture:** do not assume `x86_64` or `arm64`. Choose dependencies with wheels for both, and do not pin platform-specific Docker images or binaries.
- **Paths:** no hard-coded absolute paths. Use `pathlib`, paths relative to the project, or values from the config file (such as `sqlite_path`, `calendar_exceptions_path`). Do not assume a home directory layout, a user name, or that the working directory is the repo root.
- **Time zone and locale:** never rely on the host's local time zone, locale, or encoding. Use explicit IANA time zones (`America/New_York` for market logic, UTC for storage), timezone-aware datetimes, and explicit `encoding="utf-8"` for file I/O. Tests must pass under any `TZ` and `LANG`.
- **Python and tools:** target the Python version declared in the project, not whatever is installed locally. Do not depend on globally installed tools, shell aliases, or a specific shell; scripts use `/usr/bin/env` shebangs and POSIX `sh` unless a requirement says otherwise.
- **Network and services:** do not assume internet access, a specific proxy, open ports, or that any local service is running. Make hosts, ports, and timeouts configurable.
- **Permissions:** do not assume root or a specific UID. The container runs as a non-root user; files and volumes must work with that.
- **Resources:** do not assume many cores or large memory; the design targets 1 vCPU and 1 GB.
- **Configuration:** every environment-specific value comes from the global config file, with documented, generic defaults in the demo `config.json` (see above). Relative paths inside the config are resolved against the config file's directory, not the working directory.
- **Verification:** when something was only checked on the current host, say so. Do not claim cross-platform behavior that was not tested.

## Language

- Everything in this repository is written in English: code, identifiers, comments, docstrings, log and error messages, UI text, docs, config keys, test names, fixture contents, commit messages, branch names, and PR descriptions.
- This applies even when the conversation with the user is in another language. Translate requirements into English before writing them into the repo.
- Documents brought in from elsewhere (such as the design document) must be translated to English before they are committed.

## General

- Design reference: the QQQ / VOO Elder Impulse dashboard design document (v1.2). Keep code consistent with it; if code needs to diverge, say so instead of silently changing behavior.
- Keep dependencies free and open source; do not add paid services or API keys.
