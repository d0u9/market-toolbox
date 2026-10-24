"""Single-writer process lock.

POSIX-only: uses ``fcntl.flock`` on a lock file in the data volume. The kernel
releases the lock when the process exits or crashes, so no PID file is used.
Linux and macOS are supported; this module is the only POSIX-specific code.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path


class LockHeld(RuntimeError):
    pass


class WriterLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LockHeld(f"another writer holds {self.path}") from None
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "WriterLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
