"""Shared operation plans for the Siemens DALI-2 panel and MCP tools."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from xknxeditor.dali.siemens_dali2 import (
    Programmer,
    Scan,
    SiemensDali2Commissioner,
    line_count,
)

Action = Literal["read", "discover", "blink", "stop", "on", "off", "address"]


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
) -> Operation:
    """Validate the target and all arguments before queuing or opening a connection."""
    if type(line) is not int or not 0 <= line < line_count(app_id):
        raise ValueError("Unsupported Siemens DALI-2 application or line")
    if action not in ("read", "discover", "blink", "stop", "on", "off", "address"):
        raise ValueError("Unknown Siemens DALI-2 operation")
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
        action, line, sensors, address, new_address, random_address, timeout
    )
