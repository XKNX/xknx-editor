"""Shared operation plans for the Siemens DALI-2 panel and MCP tools."""

from __future__ import annotations

import math
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from xknxeditor.dali.siemens_dali2 import (
    ColorTemperatureState,
    GearState,
    Programmer,
    RgbwState,
    Scan,
    SiemensDali2Commissioner,
    line_count,
    supports_color_commands,
    uses_legacy_identification,
)

Action = Literal[
    "read",
    "discover",
    "blink",
    "stop",
    "on",
    "off",
    "address",
    "status",
    "level",
    "temperature",
    "rgbw",
]
Target = tuple[int, int, str, str, int, bool]
Result = Scan | GearState | ColorTemperatureState | RgbwState | None

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService


@dataclass(frozen=True)
class GuardedOperation:
    """Keep a prepared operation bound to its project and device across connection setup."""

    project: ProjectService
    target: Target
    execute: Callable[[Programmer], Awaitable[Result]]

    def validate_device(self, device: Device) -> None:
        from editor_gui.plugins.project.strings import S

        generation, node_id, app_id, address, _, _ = self.target
        current = self.project.find_device_by_node_id(node_id)
        if (
            self.project.generation != generation
            or current is None
            or any(
                d.node_id != node_id
                or d.individual_address != address
                or d.app is None
                or d.app.id != app_id
                for d in (device, current)
            )
        ):
            raise ValueError(S.E64_CONFIGURATION_CHANGED)

    async def __call__(self, programmer: Programmer) -> Result:
        return await self.execute(programmer)


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
    level: int = 0
    kelvin: int | None = None
    rgbw: tuple[int, int, int, int] | None = None

    @property
    def writes(self) -> bool:
        return self.action != "read"

    async def __call__(self, programmer: Programmer) -> Result:
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
        if self.action == "status":
            return await commissioner.read_gear_state(
                self.address, timeout=self.timeout
            )
        if self.action == "level":
            return await commissioner.set_level(
                self.address, self.level, timeout=self.timeout
            )
        if self.action == "temperature":
            return await commissioner.color_temperature(
                self.address, self.kelvin, timeout=self.timeout
            )
        if self.action == "rgbw":
            return await commissioner.rgbw(
                self.address, self.rgbw, timeout=self.timeout
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
    level: int = 0,
    kelvin: int | None = None,
    rgbw: tuple[int, int, int, int] | None = None,
) -> Operation:
    """Validate the target and all arguments before queuing or opening a connection."""
    if type(line) is not int or not 0 <= line < line_count(app_id):
        raise ValueError("Unsupported Siemens DALI-2 application or line")
    if action not in (
        "read",
        "discover",
        "blink",
        "stop",
        "on",
        "off",
        "address",
        "status",
        "level",
        "temperature",
        "rgbw",
    ):
        raise ValueError("Unknown Siemens DALI-2 operation")
    if action in ("status", "level", "temperature", "rgbw") and sensors:
        raise ValueError("Tests are available for control gear only")
    if action == "level" and (type(level) is not int or not 0 <= level <= 254):
        raise ValueError("DALI arc level must be between 0 and 254")
    legacy = uses_legacy_identification(app_id)
    if action in ("temperature", "rgbw"):
        if not supports_color_commands(app_id):
            raise ValueError("Color tests require application version 02, 03 or 30")
        if kelvin is not None and (
            action != "temperature"
            or type(kelvin) is not int
            or not 1000 <= kelvin <= 20000
        ):
            raise ValueError("Color temperature must be between 1000 and 20000 K")
        if rgbw is not None and (
            action != "rgbw"
            or len(rgbw) != 4
            or any(type(value) is not int or not 0 <= value <= 255 for value in rgbw)
        ):
            raise ValueError("RGBW requires four integer channels between 0 and 255")
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
        level,
        kelvin,
        rgbw,
    )
