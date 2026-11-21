# pyright: reportUnusedFunction=false
"""Siemens DALI-2 bus inventory, identification and physical address changes."""

from __future__ import annotations

import dataclasses
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool
from editor_gui.plugins.mcp.tools.connection import (
    await_operation,
    not_started,
    run_queued_action,
)
from editor_gui.plugins.mcp.tools.project import require_device
from editor_gui.plugins.project.strings import S
from editor_gui.programming_siemens_dali2 import (
    Action,
    GuardedOperation,
    Operation,
    Result,
    Target,
    prepare_operation,
)
from xknxeditor.dali.siemens_dali2 import (
    ColorTemperatureState,
    GearState,
    RgbwState,
    Scan,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.device import Device


def scan_dict(node_id: int, scan: Scan | None) -> dict[str, Any]:
    """Keep an unavailable cache distinct from a successful, empty inventory."""
    if scan is None:
        return {"node_id": node_id, "available": False, "devices": []}
    return {
        "node_id": node_id,
        "available": True,
        "line": scan.line,
        "sensors": scan.sensors,
        "interface_id": scan.interface_id,
        "devices": [asdict(device) for device in scan.devices],
        "raw_hex": scan.raw.hex(),
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    service = ctx.api.connection

    def run(
        action: Action,
        node_id: int | None,
        device_address: str | None,
        line: int,
        sensors: bool,
        timeout: float,
        address: int = 0,
        new_address: int = 0,
        random_address: int = 0,
        duration: float = 15,
        level: int = 0,
        kelvin: int | None = None,
        rgbw: tuple[int, int, int, int] | None = None,
    ) -> dict[str, Any]:
        def prepare() -> tuple[Device, Operation, Target]:
            device = require_device(ctx.api.project, node_id, device_address)
            if not device.individual_address:
                raise ValueError("Device has no individual address")
            operation = prepare_operation(
                device.app.id if device.app else None,
                action,
                line=line,
                sensors=sensors,
                address=address,
                new_address=new_address,
                random_address=random_address,
                timeout=timeout,
                duration=duration,
                level=level,
                kelvin=kelvin,
                rgbw=rgbw,
            )
            target = (
                ctx.api.project.generation,
                device.node_id,
                device.app.id if device.app else "",
                device.individual_address,
                line,
                sensors,
            )
            return device, operation, target

        device, operation, target = ctx.run_locked(prepare)
        control = service.siemens_dali2_identification
        if action == "stop" and operation.legacy:
            stopping = control.stop(target)
            if stopping is not None:
                stopping.ended.result(timeout + 15)
                return {
                    "node_id": device.node_id,
                    "line": line,
                    "sensors": sensors,
                    "action": "stop",
                }
        if not service.can_reach_bus():
            raise ToolError("No KNX connection")
        text = {
            "read": S.E64_BUS_ACTIONS["read"],
            "discover": S.DALI_SCAN,
            "blink": S.DALI_BLINK,
            "stop": S.DALI_STOP,
            "on": S.DALI_SWITCH_ON,
            "off": S.DALI_SWITCH_OFF,
            "address": S.E64_SHORT_ADDRESS,
            "status": S.E64_STATUS,
            "level": S.E64_BRIGHTNESS,
            "temperature": S.E64_COLOR_TEMPERATURE,
            "rgbw": "RGBW",
        }[action]
        session = (
            control.start(target) if operation.legacy and action == "blink" else None
        )
        if session is not None:
            operation = dataclasses.replace(operation, session=session)

        async def execute(programmer: Any) -> Result:
            error: BaseException | None = None
            if session is not None:
                session.started.set()
            try:
                return await operation(programmer)
            except BaseException as exc:
                error = exc
                raise
            finally:
                if session is not None:
                    control.finish(target, session, error)

        wait = timeout + 15 + (duration if session is not None else 0)
        guarded = GuardedOperation(
            ctx.api.project, target, execute if session is not None else operation
        )
        try:
            ended = run_queued_action(
                ctx,
                device.node_id,
                "siemens_dali",
                wait,
                op=guarded,
                text=text,
                writes=operation.writes,
            )
            if ended is not None:
                result = ended.result
            else:
                future = ctx.run_locked(
                    lambda: service.run_siemens_dali(
                        device,
                        guarded,
                        writes=operation.writes,
                    )
                )
                if future is None:
                    raise not_started(service, text)
                result = await_operation(service, future, wait, text)
        except BaseException as exc:
            if session is not None:
                session.stop.set()
                if not session.started.is_set():
                    control.finish(target, session, exc)
            raise
        finally:
            if session is not None and not session.started.is_set():
                control.finish(target, session)
        if action in ("read", "discover", "address"):
            return scan_dict(device.node_id, result)
        if action in ("status", "level"):
            if not isinstance(result, GearState):
                raise ToolError("Invalid DALI-2 gear response")
            return {"node_id": device.node_id, **asdict(result)}
        if action == "temperature":
            if not isinstance(result, ColorTemperatureState):
                raise ToolError("Invalid DALI-2 color temperature response")
            return {
                "node_id": device.node_id,
                **asdict(result),
                "kelvin": result.kelvin,
            }
        if action == "rgbw":
            if not isinstance(result, RgbwState):
                raise ToolError("Invalid DALI-2 RGBW response")
            return {"node_id": device.node_id, **asdict(result)}
        return {
            "node_id": device.node_id,
            "line": line,
            "sensors": sensors,
            "address": address,
            "action": action,
        }

    @tool("read", open_world=True)
    def connection_siemens_dali2_read(
        node_id: int | None = None,
        device_address: str | None = None,
        line: int = 0,
        sensors: bool = False,
        timeout: float = 60,
    ) -> dict[str, Any]:
        """Read a Siemens DALI-2 cached inventory without starting discovery.
        Select by node_id OR device_address; line is zero-based (0 or 1 for twin gateways).
        sensors=False reads control gear; True reads input devices and their instances.
        Returns available=False if the cache is missing, otherwise line, sensors, interface_id,
        devices and raw_hex. Each device has short_address (0..63), random_address (24-bit),
        flags, version, types, min_level and instances ({number, type, resolution}).
        The cache reflects the last gateway discovery, not necessarily the current DALI bus.
        Supported applications: Siemens 9A1B/9A1C, versions 01/02/03/30.
        """
        return run("read", node_id, device_address, line, sensors, timeout)

    @tool("destructive", open_world=True)
    def connection_siemens_dali2_discover(
        node_id: int | None = None,
        device_address: str | None = None,
        line: int = 0,
        sensors: bool = False,
        timeout: float = 300,
    ) -> dict[str, Any]:
        """CHANGES THE DEVICE: discover Siemens DALI-2 gear or inputs; may assign addresses.
        Select by node_id OR device_address. Zero-based line; sensors=True selects inputs.
        Returns the same inventory as connection_siemens_dali2_read after discovery completes.
        Restores gateway polling afterward. Does not write the saved KNX configuration.
        """
        return run("discover", node_id, device_address, line, sensors, timeout)

    @tool("destructive", open_world=True)
    def connection_siemens_dali2_identify(
        address: int,
        action: Literal["blink", "stop", "on", "off"] = "blink",
        node_id: int | None = None,
        device_address: str | None = None,
        line: int = 0,
        sensors: bool = False,
        timeout: float = 15,
        duration: float = 15,
    ) -> dict[str, Any]:
        """Identify Siemens DALI-2 gear or inputs at short address 0..63.
        Select by node_id OR device_address; line is zero-based, sensors=True selects inputs.
        Version 01 flashes gear at maximum/minimum or identifies sensors for duration seconds
        (0 < duration <= 300), then turns gear off or terminates sensor identification.
        It checks the gateway hardware and holds the exclusive bus slot until cleanup completes.
        A concurrent stop request ends that session; canceling the operation also runs cleanup.
        Version 01 inputs support blink/stop only; gear also supports on/off.
        Versions 02/03/30 use gateway identification with a two-second interval until stop.
        Returns node_id, line, sensors, address and action after command acknowledgement.
        """
        return run(
            action,
            node_id,
            device_address,
            line,
            sensors,
            timeout,
            address,
            duration=duration,
        )

    @tool("destructive", open_world=True)
    def connection_siemens_dali2_set_level(
        address: int,
        level: int,
        node_id: int | None = None,
        device_address: str | None = None,
        line: int = 0,
        timeout: float = 15,
    ) -> dict[str, Any]:
        """Test one Siemens DALI-2 ECG with a direct arc level, then read its current state.
        Select the gateway by node_id OR device_address, zero-based line (0 or 1 for twin),
        and ECG short address 0..63. level is a DALI arc value 0..254, not a percentage;
        0 turns off, 254 requests maximum. Does not change saved KNX parameters.
        Returns the same result as connection_siemens_dali2_status; the returned level is
        the current queried value and may differ while fading or at the device's limits.
        Supported applications: Siemens 9A1B/9A1C, versions 01/02/03/30.
        """
        return run(
            "level", node_id, device_address, line, False, timeout, address, level=level
        )

    @tool("destructive", open_world=True)
    def connection_siemens_dali2_status(
        address: int,
        node_id: int | None = None,
        device_address: str | None = None,
        line: int = 0,
        timeout: float = 15,
    ) -> dict[str, Any]:
        """Query live status and actual arc level for one Siemens DALI-2 control gear.
        Select by node_id OR device_address, zero-based line and ECG short address 0..63.
        Returns node_id, line, address, status (DALI QUERY STATUS byte), level (0..254),
        status_receive_state and level_receive_state (0=response, 1=no response, 2=undecodable).
        Missing values are null, never a fabricated healthy status or zero brightness.
        Status bits 0/1 indicate control gear/lamp failure.
        Sends DALI queries and temporarily pauses gateway polling, then restores it.
        Requires bus writes to be allowed because queries use function-property commands.
        Supported applications: Siemens 9A1B/9A1C, versions 01/02/03/30.
        """
        return run("status", node_id, device_address, line, False, timeout, address)

    @tool("destructive", open_world=True)
    def connection_siemens_dali2_color_temperature(
        address: int,
        kelvin: int | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
        line: int = 0,
        timeout: float = 15,
    ) -> dict[str, Any]:
        """Read or test color temperature of one Siemens DALI-2 ECG.
        Select by node_id OR device_address, zero-based line and short address 0..63.
        Omit kelvin to read only; supplying 1000..20000 K changes the lamp and then reads it.
        Returns node_id, line, address, raw mirek and observed kelvin (null for unavailable/MASK).
        Observed values may differ from the request due to device limits or fading.
        Both modes temporarily pause gateway polling and require bus writes to be allowed.
        Restores polling and leaves saved KNX parameters unchanged. Supported applications:
        Siemens 9A1B/9A1C versions 02/03/30; version 01 uses a different color protocol.
        """
        return run(
            "temperature",
            node_id,
            device_address,
            line,
            False,
            timeout,
            address,
            kelvin=kelvin,
        )

    @tool("destructive", open_world=True)
    def connection_siemens_dali2_rgbw(
        address: int,
        channels: tuple[int, int, int, int] | None = None,
        node_id: int | None = None,
        device_address: str | None = None,
        line: int = 0,
        timeout: float = 15,
    ) -> dict[str, Any]:
        """Read or test RGBW channels of one Siemens DALI-2 ECG.
        Select by node_id OR device_address, zero-based line and short address 0..63.
        Omit channels to read only; supply [red, green, blue, white] (raw 0..255 values)
        to change the lamp before reading its actual channels. White is always explicit.
        Returns node_id, line, address, red, green, blue and white as observed raw values.
        Both modes temporarily pause gateway polling and require bus writes to be allowed.
        Restores polling and leaves saved KNX parameters unchanged. Supported applications:
        Siemens 9A1B/9A1C versions 02/03/30; requires RGBW-capable control gear.
        """
        return run(
            "rgbw",
            node_id,
            device_address,
            line,
            False,
            timeout,
            address,
            rgbw=channels,
        )

    @tool("destructive", open_world=True)
    def connection_siemens_dali2_address(
        address: int,
        new_address: int,
        random_address: int,
        node_id: int | None = None,
        device_address: str | None = None,
        line: int = 0,
        sensors: bool = False,
        timeout: float = 300,
    ) -> dict[str, Any]:
        """CHANGES THE DEVICE: move a Siemens DALI-2 physical device to a free short address.
        Select by node_id OR device_address; zero-based line; sensors=True selects inputs.
        address/new_address are distinct 0..63 values; random_address is the nonzero 24-bit
        identity from discovery. Fresh scans check identity, conflicts and the final address.
        Returns the verified inventory like connection_siemens_dali2_read. Does not change
        saved KNX parameter mappings. Project undo cannot reverse this bus operation.
        """
        return run(
            "address",
            node_id,
            device_address,
            line,
            sensors,
            timeout,
            address,
            new_address,
            random_address,
        )
