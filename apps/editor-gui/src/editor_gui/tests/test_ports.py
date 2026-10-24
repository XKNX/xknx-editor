"""A server port is taken only when no other program uses it, and checking that is quick."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import pytest

from editor_gui.ports import bind_port


@pytest.fixture
def listener() -> Iterator[int]:
    """A program listening on a port for all addresses; its port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    sock.listen()
    yield sock.getsockname()[1]
    sock.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_port_another_program_listens_on_is_not_taken(listener: int) -> None:
    assert bind_port("127.0.0.1", listener) is None
    assert bind_port("0.0.0.0", listener) is None


def test_a_free_port_is_taken_quickly() -> None:
    port = _free_port()
    start = time.perf_counter()
    sock = bind_port("127.0.0.1", port)
    elapsed = time.perf_counter() - start
    assert sock is not None
    assert sock.getsockname()[1] == port
    sock.close()
    assert elapsed < 0.25


def test_port_0_takes_one_the_system_picks() -> None:
    sock = bind_port("127.0.0.1", 0)
    assert sock is not None
    assert sock.getsockname()[1] != 0
    sock.close()
