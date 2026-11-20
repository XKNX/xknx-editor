"""Shared operation plans for the Siemens DALI-2 panel and MCP tools."""

from __future__ import annotations

import math
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Literal

from xknxeditor.dali.siemens_dali2 import (
    Programmer,
    Scan,
    SiemensDali2Commissioner,
    line_count,
    uses_legacy_identification,
)

Action = Literal["read", "discover", "blink", "stop", "on", "off", "address"]
Target = tuple[int, int, str, str, int, bool]


@dataclass
class IdentificationSession:
    """Cooperative stop requests complete only after the bus cleanup."""

    stop: threading.Event = field(default_factory=threading.Event)
    started: threading.Event = field(default_factory=threading.Event)
    ended: Future[None] = field(default_factory=Future[None])


@dataclass
class IdentificationControl:
    """Identification sessions shared by the GUI and MCP for one connection service."""

    _sessions: dict[Target, IdentificationSession] = field(
        default_factory=dict[Target, IdentificationSession]
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self, target: Target) -> IdentificationSession:
        with self._lock:
            if target in self._sessions:
                raise ValueError("Identification is already running")
            session = IdentificationSession()
            self._sessions[target] = session
            return session

    def stop(self, target: Target) -> IdentificationSession | None:
        with self._lock:
            session = self._sessions.get(target)
            if session is not None:
                session.stop.set()
            return session

    def finish(
        self,
        target: Target,
        session: IdentificationSession,
        error: BaseException | None = None,
    ) -> None:
        with self._lock:
            if self._sessions.get(target) is session:
                del self._sessions[target]
            if not session.ended.done():
                if error is None:
                    session.ended.set_result(None)
                else:
                    session.ended.set_exception(error)


@dataclass(frozen=True)
class Operation:
    """An immutable bus operation; the connection service enforces its write guard."""

    action: Action
    line: int
    sensors: bool
    address: int
    new_address: int
    random_address: int
    timeout: float
    lines: int
    legacy: bool
    duration: float
    session: IdentificationSession | None = field(default=None, compare=False)

    @property
    def writes(self) -> bool:
        return self.action != "read"

    async def __call__(self, programmer: Programmer) -> Scan | None:
        commissioner = SiemensDali2Commissioner(programmer, self.line)
        if self.action == "read":
            return await commissioner.read_cached(
                sensors=self.sensors, timeout=self.timeout
            )
        if self.action == "discover":
            return await commissioner.discover(
                sensors=self.sensors, timeout=self.timeout
            )
        if self.action == "address":
            return await commissioner.change_short_address(
                self.address,
                self.new_address,
                self.random_address,
                sensors=self.sensors,
                timeout=self.timeout,
            )
        if self.legacy:
            await commissioner.identify_legacy(
                self.address,
                lines=self.lines,
                sensors=self.sensors,
                start=self.action != "stop",
                mode={"blink": 0, "stop": 0, "on": 1, "off": 2}[self.action],
                duration=self.duration,
                stopped=self.session.stop.is_set if self.session else lambda: False,
                timeout=self.timeout,
            )
            return None
        await commissioner.identify(
            self.address,
            sensors=self.sensors,
            start=self.action != "stop",
            mode={"blink": 0, "stop": 0, "on": 1, "off": 2}[self.action],
            timeout=self.timeout,
        )
        return None


def prepare_operation(
    app_id: str | None,
    action: Action,
    *,
    line: int = 0,
    sensors: bool = False,
    address: int = 0,
    new_address: int = 0,
    random_address: int = 0,
    timeout: float = 300,
    duration: float = 15,
    session: IdentificationSession | None = None,
) -> Operation:
    """Validate the target and all arguments before queuing or opening a connection."""
    if type(line) is not int or not 0 <= line < line_count(app_id):
        raise ValueError("Unsupported Siemens DALI-2 application or line")
    if action not in ("read", "discover", "blink", "stop", "on", "off", "address"):
        raise ValueError("Unknown Siemens DALI-2 operation")
    legacy = uses_legacy_identification(app_id)
    if legacy and sensors and action in ("on", "off"):
        raise ValueError("Input devices support blink and stop only")
    if not math.isfinite(duration) or not 0 < duration <= 300:
        raise ValueError("Identification duration must be between 0 and 300 seconds")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    if action not in ("read", "discover") and (
        type(address) is not int or not 0 <= address < 64
    ):
        raise ValueError("DALI short address must be between 0 and 63")
    if action == "address" and (
        type(new_address) is not int
        or not 0 <= new_address < 64
        or new_address == address
        or type(random_address) is not int
        or not 0 < random_address <= 0xFFFFFF
    ):
        raise ValueError("Invalid DALI-2 target or random address")
    return Operation(
        action,
        line,
        sensors,
        address,
        new_address,
        random_address,
        timeout,
        line_count(app_id),
        legacy,
        duration,
        session,
    )
