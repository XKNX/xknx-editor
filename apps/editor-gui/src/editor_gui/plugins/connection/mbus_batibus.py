"""The frames of the M-Bus and BatiBus tunnels of a KNX USB interface, as they are kept and
shown: received and sent ones, the last ``MAX_FRAMES``."""

from __future__ import annotations

import datetime
import threading
from collections import deque
from dataclasses import dataclass

from editor_gui.plugins.connection.xknx_usb import ProtocolID, USBTransferFrame

# How many frames are kept.
MAX_FRAMES = 500

# The protocols, by their names as shown and as MCP names them.
PROTOCOLS: tuple[tuple[ProtocolID, str, str], ...] = (
    (ProtocolID.M_BUS_TUNNEL, "M-Bus", "m_bus"),
    (ProtocolID.BATIBUS_TUNNEL, "BatiBus", "batibus"),
)


def protocol_name(protocol: int) -> str:
    """A tunnel's protocol as shown: "M-Bus" or "BatiBus" (else its number, ``04h``)."""
    return next(
        (name for known, name, _ in PROTOCOLS if known == protocol),
        f"{protocol:02X}h",
    )


def protocol_key(protocol: int) -> str:
    """A tunnel's protocol as MCP names it: "m_bus" or "batibus" (else its number)."""
    return next(
        (key for known, _, key in PROTOCOLS if known == protocol), str(protocol)
    )


def parse_protocol(key: str) -> ProtocolID:
    """The protocol MCP names ``key`` ("m_bus" or "batibus"). Raises ``ValueError``."""
    wanted = key.strip().lower().replace("-", "_")
    for protocol, _, name in PROTOCOLS:
        if wanted == name:
            return protocol
    raise ValueError(key)


def parse_hex(text: str) -> bytes:
    """``text`` as octets in hexadecimal, separated by spaces or not (``44 AE 0C``, ``44ae0c``).
    Raises ``ValueError`` when it is none."""
    digits = "".join(text.split())
    if digits.lower().startswith("0x"):
        digits = digits[2:]
    return bytes.fromhex(digits)


def parse_octet(text: str) -> int:
    """``text`` as one octet in hexadecimal (``00`` to ``FF``). Raises ``ValueError``."""
    value = parse_hex(text.strip().rjust(2, "0"))
    if len(value) != 1:
        raise ValueError(text)
    return value[0]


@dataclass(frozen=True, slots=True)
class MbusBatibusFrame:
    """A frame of an M-Bus or BatiBus tunnel: its number in the log (counting from 1), when it
    was received or sent, and its protocol, service (header octet 6), manufacturer code and
    body as they are."""

    id: int
    time: datetime.datetime
    sent: bool
    protocol: int
    service: int
    manufacturer_code: int
    body: bytes


class MbusBatibusFrameLog:
    """The frames of the M-Bus and BatiBus tunnels, the last ``max_frames``; frames are added
    from the connection thread, read from any."""

    def __init__(self, max_frames: int = MAX_FRAMES) -> None:
        self._lock = threading.Lock()
        self._frames: deque[MbusBatibusFrame] = deque(maxlen=max_frames)
        self._last_id = 0

    def add(self, frame: USBTransferFrame, *, sent: bool) -> MbusBatibusFrame:
        """Keep ``frame``, received (or ``sent``) now."""
        with self._lock:
            self._last_id += 1
            kept = MbusBatibusFrame(
                id=self._last_id,
                time=datetime.datetime.now().astimezone(),
                sent=sent,
                protocol=int(frame.protocol),
                service=frame.service,
                manufacturer_code=frame.manufacturer_code,
                body=frame.body,
            )
            self._frames.append(kept)
        return kept

    def frames(
        self, *, since: int = 0, limit: int | None = None
    ) -> list[MbusBatibusFrame]:
        """The kept frames after the one numbered ``since``, oldest first; with ``limit`` only
        the newest that many."""
        with self._lock:
            found = [frame for frame in self._frames if frame.id > since]
        if limit is not None and limit >= 0:
            found = found[len(found) - limit :] if limit else []
        return found

    @property
    def last_id(self) -> int:
        """The number of the newest frame (0 before the first)."""
        with self._lock:
            return self._last_id

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    def clear(self) -> None:
        """Forget the kept frames (the numbering goes on)."""
        with self._lock:
            self._frames.clear()
