import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test tries to open a network connection."""

    def guard(*args, **kwargs):
        raise RuntimeError("network access is not allowed in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


@pytest.fixture(autouse=True)
def synthetic_calendar_exceptions(tmp_path):
    """Every test starts with a private, explicitly empty exceptions file."""
    (tmp_path / "exceptions.yaml").write_text("{}\n", encoding="utf-8")
