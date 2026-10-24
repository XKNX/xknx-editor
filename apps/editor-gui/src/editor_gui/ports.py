"""Server ports: bind a listening socket only when no other program uses the port."""

from __future__ import annotations

import socket

# How long to wait for a program answering on the port. A program listening on this computer is
# answered by the system at once; for a free port Windows waits the whole time, at every start.
_PROBE_TIMEOUT = 0.05


class PortInUseError(OSError):
    """``port`` is already used by another program."""

    def __init__(self, port: int) -> None:
        super().__init__(f"port {port} is already in use")
        self.port = port


def bind_port(host: str, port: int) -> socket.socket | None:
    """A socket bound to ``host``:``port`` for a server, None when the port is in use. Also a
    program already answering there counts (Windows lets a socket bind to an address another
    program listens on for all its addresses)."""
    if port:
        probe = {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1"}.get(host, host)
        try:
            with socket.create_connection((probe, port), timeout=_PROBE_TIMEOUT):
                return None
        except OSError:
            pass
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        # The port to this server alone (on Windows another program could share it otherwise).
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        return None
    return sock
