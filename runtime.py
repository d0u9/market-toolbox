#!/usr/bin/env python3
"""Start the dashboard and its update thread in one Python process."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import signal
import sys
import threading

from config import Config, ConfigError, load_config
import worker

log = logging.getLogger(__name__)


async def serve(cfg: Config, server) -> int:
    """Own one updater for the server lifetime, independent of browser sessions."""
    stop = threading.Event()
    loop = asyncio.get_running_loop()
    worker_result = [0]

    def update() -> None:
        try:
            worker_result[0] = worker.run(cfg, stop)
        except BaseException:
            log.exception("Update thread failed")
            worker_result[0] = 1
        if not stop.is_set():
            worker_result[0] = worker_result[0] or 1
            log.error("Update thread exited unexpectedly; stopping the dashboard")
            loop.call_soon_threadsafe(server.stop)

    thread = threading.Thread(target=update, name="market-updater")

    def shutdown(*_) -> None:
        stop.set()
        loop.call_soon_threadsafe(server.stop)

    previous_handlers = {}
    started = False
    try:
        await server.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, shutdown)
        thread.start()
        started = True
        await server.stopped
    finally:
        stop.set()
        if started:
            # Let an in-flight provider request finish before closing SQLite
            # and releasing the writer lock in the update thread.
            await asyncio.to_thread(thread.join)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
    return worker_result[0]


def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Streamlit's server API is internal; keep its use here and verify it
    # against the version pinned in requirements.lock.
    from streamlit.web import bootstrap
    from streamlit.web.server import Server

    bootstrap.load_config_options({
        "server_headless": True,
        "server_address": cfg.web_host,
        "server_port": cfg.web_port,
        "server_fileWatcherType": "none",
    })

    async def start() -> int:
        server = Server(str(Path(__file__).with_name("app.py")), is_hello=False)
        return await serve(cfg, server)

    return asyncio.run(start())


if __name__ == "__main__":
    sys.exit(main())
