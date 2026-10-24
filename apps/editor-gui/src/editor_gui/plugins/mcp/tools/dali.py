# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""DALI tools (BUS): the commissioning of a DALI gateway's bus as its DALI tabs do it.

MDT and Theben gateways: scan a DALI channel, identify an ECG or a group, run a new or a post
installation, and program the groups and scenes; a programmed channel is saved into the device's
backup data in the project, as the tab does. Siemens KNX/DALI Gateway plus N 141/03: a read-only
scan and programming the DALI device data the project holds for it.

As in the tab, programming the groups needs a scan of the channel first: the plan keeps every
present ECG's number and takes the group from the scan unless the call changes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from fastmcp.exceptions import ToolError

from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.device import Device
    from xknxeditor.dali import DaliBusState, Scene
    from xknxeditor.dali.siemens import SiemensN141BusState

IdentifyAction = Literal["blink", "stop_blink", "on", "off"]
Installation = Literal["new", "post"]
GroupChoice = int | Literal["single", "unassigned"]


@dataclass
class _Channel:
    """What the tools know of one DALI channel: the last scan and the scenes written last."""

    scan: DaliBusState | None = None
    scenes: list[Scene] = field(default_factory=list["Scene"])


def _group_value(group_index: int) -> int | str | None:
    from xknxeditor.dali.model import GROUP_SINGLE, GROUP_UNASSIGNED, MAX_GROUP

    if 0 <= group_index < MAX_GROUP:
        return group_index
    if group_index == GROUP_SINGLE:
        return "single"
    if group_index == GROUP_UNASSIGNED:
        return "unassigned"
    return None


def _group_index(choice: Any) -> int:
    from xknxeditor.dali.model import GROUP_SINGLE, GROUP_UNASSIGNED, MAX_GROUP

    if choice == "single":
        return GROUP_SINGLE
    if choice == "unassigned":
        return GROUP_UNASSIGNED
    if isinstance(choice, int) and 0 <= choice < MAX_GROUP:
        return choice
    raise ToolError(
        f"group must be 0-{MAX_GROUP - 1}, 'single' or 'unassigned', not {choice!r}"
    )


def scan_dict(node_id: int, channel: int, scan: DaliBusState) -> dict[str, Any]:
    """A scan as the tools return it."""
    from xknxeditor.dali.model import SHORT_ADDR_UNASSIGNED

    return {
        "node_id": node_id,
        "channel": channel,
        "firmware": ".".join(map(str, scan.firmware)) if scan.firmware else None,
        "ecgs": [
            {
                "slot": e.slot,
                "ecg_number": (
                    None
                    if e.ets_ecg_index == SHORT_ADDR_UNASSIGNED
                    else e.ets_ecg_index
                ),
                "group": _group_value(e.group_index),
                "type": e.ecg_type,
                "sub_type": e.sub_type,
                "long_address": e.long_address,
                "min_value": e.min_value,
                "alarm": e.alarm,
            }
            for e in scan.present_ecgs
        ],
        "groups": [
            {
                "index": g.index,
                "ecg_count": g.ecg_count,
                "lamp_failures": g.lamp_failures,
                "ecg_failures": g.ecg_failures,
                "run_hours": g.run_hours,
            }
            for g in scan.groups
        ],
    }


def siemens_scan_dict(node_id: int, scan: SiemensN141BusState) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "line": scan.line,
        "ecgs": [
            {
                "slot": e.slot,
                "short_address": e.short_address,
                "random_address": e.random_address,
                "type": e.type,
                "min_physical_level": e.min_phys_dim_value,
            }
            for e in scan.present_ecgs
        ],
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    service = ctx.api.connection
    channels: dict[tuple[int, int], _Channel] = {}

    def _ensure_connected() -> None:
        if service.xknx is None:
            raise ToolError(
                "no KNX connection; connect first with connection_connect (configured gateway) "
                "or connection_scan + connection_connect_gateway"
            )

    def _gateway(node_id: int, channel: int) -> Device:
        """The MDT/Theben DALI gateway ``node_id`` with a channel ``channel``."""
        from editor_gui.programming_dali import dali_channel_count, is_dali_dca_app

        def _read() -> tuple[Device | None, int]:
            device = ctx.api.project.find_device_by_node_id(node_id)
            info = ctx.api.project.get_device_info(node_id)
            order = getattr(info, "order_number", "") if info is not None else ""
            return device, dali_channel_count(order if isinstance(order, str) else "")

        device, count = ctx.run_locked(_read)
        if device is None:
            raise ToolError(
                f"no device with node_id {node_id}; call project_list_devices for valid ids"
            )
        if device.app is None or not is_dali_dca_app(device.app.id):
            raise ToolError(
                f"device {node_id} is not a DALI gateway these tools commission"
            )
        if not device.individual_address:
            raise ToolError(f"device {node_id} has no individual address")
        if not 0 <= channel < count:
            raise ToolError(f"the gateway has DALI channel 0 to {count - 1}")
        return device

    def _run(device: Device, op: Any, channel: int, timeout: float) -> Any:
        _ensure_connected()
        future = ctx.run_locked(lambda: service.run_dali(device, op, channel=channel))
        if future is None:
            raise ToolError(
                "the DALI operation could not start (another bus operation runs)"
            )
        return future.result(timeout=timeout)

    def _persist(device: Device, node_id: int, channel: int) -> bool:
        """Save the channel's state into the project's backup data, as the tab does after a
        groups or scenes write; False without a scan to save."""
        from editor_gui.plugins.project.strings import S
        from editor_gui.programming_dali import persist_dali_backup

        state = channels.get((node_id, channel))
        if state is None or state.scan is None:
            return False
        scan = state.scan
        assignments = _assignments(scan, {})

        def _save() -> None:
            persist_dali_backup(
                ctx.api.project,
                device,
                scan,
                assignments,
                state.scenes,
                channel,
                label=S.DALI_PERSIST_LABEL,
            )

        ctx.run_locked(_save)
        return True

    def _assignments(scan: DaliBusState, groups: dict[int, int]) -> list[Any]:
        from xknxeditor.dali.model import EcgAssignment

        return [
            EcgAssignment(
                slot=e.slot,
                ets_index=e.ets_ecg_index,
                group_index=groups.get(e.slot, e.group_index),
            )
            for e in scan.present_ecgs
        ]

    @tool(read_only=True, open_world=True)
    def connection_dali_scan(
        node_id: int, channel: int = 0, timeout: float = 120.0
    ) -> dict[str, Any]:
        """Read the state of a DALI channel of an MDT or Theben DALI gateway (``node_id`` from
        project_list_devices; ``channel`` 0, or 1 on a 2x64 gateway) over the bus; changes
        nothing. Returns {node_id, channel, firmware, ecgs, groups}: each present ECG has {slot
        (its DALI short address 0-63), ecg_number, group (0-15, "single", "unassigned" or null),
        type, sub_type, long_address, min_value, alarm}; each group {index, ecg_count,
        lamp_failures, ecg_failures, run_hours}."""
        device = _gateway(node_id, channel)

        def op(c: Any) -> Any:
            return c.scan()

        scan = _run(device, op, channel, timeout)
        state = channels.setdefault((node_id, channel), _Channel())
        state.scan = scan
        return scan_dict(node_id, channel, scan)

    @tool(open_world=True)
    def connection_dali_identify(
        node_id: int,
        action: IdentifyAction,
        slot: int | None = None,
        group: int | None = None,
        channel: int = 0,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Find a light on a DALI channel of an MDT or Theben gateway: ``action`` blink /
        stop_blink / on / off for the ECG at ``slot`` (0-63) or for the group ``group`` (0-15);
        without either, on / off switches every light of the channel (broadcast). Returns
        {node_id, channel, action, slot, group}."""
        if slot is not None and group is not None:
            raise ToolError("pass slot or group, not both")
        device = _gateway(node_id, channel)
        start = action in ("blink", "on")
        if slot is not None:
            if action in ("blink", "stop_blink"):

                def op(c: Any) -> Any:
                    return c.blink_ecg(slot, start)
            else:

                def op(c: Any) -> Any:
                    return c.switch_ecg(slot, start)
        elif group is not None:
            if action in ("blink", "stop_blink"):

                def op(c: Any) -> Any:
                    return c.blink_group(group, start)
            else:

                def op(c: Any) -> Any:
                    return c.switch_group(group, start)
        else:
            if action not in ("on", "off"):
                raise ToolError("blink needs a slot or a group")

            def op(c: Any) -> Any:
                return c.broadcast(start)

        _run(device, op, channel, timeout)
        return {
            "node_id": node_id,
            "channel": channel,
            "action": action,
            "slot": slot,
            "group": group,
        }

    @tool(destructive=True, open_world=True)
    def connection_dali_installation(
        node_id: int,
        installation: Installation,
        channel: int = 0,
        timeout: float = 900.0,
    ) -> dict[str, Any]:
        """CHANGES THE DALI BUS: run a new installation (``installation="new"``: every ECG of
        the channel gets a new short address) or a post installation ("post": only ECGs without
        one are added) on an MDT or Theben gateway, then read the channel. Run it only when the
        user asked for it. Returns what connection_dali_scan returns."""
        device = _gateway(node_id, channel)
        if installation == "new":

            def op(c: Any) -> Any:
                return c.new_installation(on_progress=None)
        else:

            def op(c: Any) -> Any:
                return c.post_installation(on_progress=None)

        scan = _run(device, op, channel, timeout)
        state = channels.setdefault((node_id, channel), _Channel())
        state.scan = scan
        return scan_dict(node_id, channel, scan)

    @tool(destructive=True, open_world=True)
    def connection_dali_program_groups(
        node_id: int,
        groups: list[dict[str, Any]],
        channel: int = 0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """CHANGES THE DALI BUS: program the group of the ECGs of a channel of an MDT or Theben
        gateway. Needs a connection_dali_scan of the channel first: every present ECG keeps its
        number, ``groups`` changes the group of some: a list of {slot, group} with group 0-15,
        "single" or "unassigned"; the others keep the group of the scan. The channel's state is
        then saved into the device's backup data in the project (one undo step). Returns {node_id,
        channel, programmed, saved}."""
        device = _gateway(node_id, channel)
        state = channels.get((node_id, channel))
        if state is None or state.scan is None:
            raise ToolError("scan the channel first with connection_dali_scan")
        present = {e.slot for e in state.scan.present_ecgs}
        changes: dict[int, int] = {}
        for entry in groups:
            slot = entry.get("slot")
            if not isinstance(slot, int) or slot not in present:
                raise ToolError(f"no ECG at slot {slot!r} in the last scan")
            changes[slot] = _group_index(entry.get("group"))
        assignments = _assignments(state.scan, changes)
        if not assignments:
            raise ToolError("the last scan found no ECG to program")

        def op(c: Any) -> Any:
            return c.commission(assignments)

        _run(device, op, channel, timeout)
        for e in state.scan.present_ecgs:
            if e.slot in changes:
                e.group_index = changes[e.slot]
        saved = _persist(device, node_id, channel)
        return {
            "node_id": node_id,
            "channel": channel,
            "programmed": len(assignments),
            "saved": saved,
        }

    @tool(destructive=True, open_world=True)
    def connection_dali_program_scenes(
        node_id: int,
        scenes: list[dict[str, Any]],
        channel: int = 0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """CHANGES THE DALI BUS: program scenes on a channel of an MDT or Theben gateway.
        ``scenes`` is a list of {scene (0-15), levels: [{group (0-15), level (0-255)}]}; scene n
        is KNX scene n+1. After a connection_dali_scan of the channel, its state is then saved
        into the device's backup data in the project (one undo step). Returns {node_id, channel,
        programmed, saved}."""
        from xknxeditor.dali.model import MAX_GROUP, MAX_SCENES, Scene, SceneValue

        device = _gateway(node_id, channel)
        planned: list[Scene] = []
        for entry in scenes:
            index = entry.get("scene")
            if not isinstance(index, int) or not 0 <= index < MAX_SCENES:
                raise ToolError(f"scene must be 0-{MAX_SCENES - 1}, not {index!r}")
            values: list[SceneValue] = []
            levels: list[dict[str, Any]] = entry.get("levels") or []
            for level_entry in levels:
                group = level_entry.get("group")
                level = level_entry.get("level")
                if not isinstance(group, int) or not 0 <= group < MAX_GROUP:
                    raise ToolError(f"group must be 0-{MAX_GROUP - 1}, not {group!r}")
                if not isinstance(level, int) or not 0 <= level <= 255:
                    raise ToolError(f"level must be 0-255, not {level!r}")
                values.append(SceneValue(scene=index, target_index=group, level=level))
            if values:
                planned.append(
                    Scene(
                        index=index,
                        knx_scene=index + 1,
                        values=sorted(values, key=lambda v: v.target_index),
                    )
                )
        if not planned:
            raise ToolError("pass at least one scene with a level")
        planned.sort(key=lambda s: s.index)

        def op(c: Any) -> Any:
            return c.write_scenes(planned)

        _run(device, op, channel, timeout)
        state = channels.setdefault((node_id, channel), _Channel())
        state.scenes = planned
        saved = _persist(device, node_id, channel)
        return {
            "node_id": node_id,
            "channel": channel,
            "programmed": len(planned),
            "saved": saved,
        }

    def _siemens_gateway(node_id: int) -> Device:
        from editor_gui.programming_dali import is_siemens_n141_03

        device = ctx.run_locked(lambda: ctx.api.project.find_device_by_node_id(node_id))
        if device is None:
            raise ToolError(
                f"no device with node_id {node_id}; call project_list_devices for valid ids"
            )
        if device.app is None or not is_siemens_n141_03(device.app.id):
            raise ToolError(
                f"device {node_id} is not a Siemens KNX/DALI Gateway plus N 141/03"
            )
        if not device.individual_address:
            raise ToolError(f"device {node_id} has no individual address")
        return device

    @tool(read_only=True, open_world=True)
    def connection_siemens_dali_scan(
        node_id: int, timeout: float = 120.0
    ) -> dict[str, Any]:
        """Read the DALI line of a Siemens KNX/DALI Gateway plus N 141/03 (``node_id`` from
        project_list_devices) over the bus; changes nothing. Returns {node_id, line, ecgs}; each
        present ECG has {slot, short_address, random_address, type, min_physical_level}."""
        from xknxeditor.dali.siemens import scan_n141_03

        device = _siemens_gateway(node_id)
        _ensure_connected()
        future = ctx.run_locked(
            lambda: service.run_siemens_dali(device, lambda p: scan_n141_03(p, line=0))
        )
        if future is None:
            raise ToolError(
                "the DALI scan could not start (another bus operation runs)"
            )
        return siemens_scan_dict(node_id, future.result(timeout=timeout))

    @tool(destructive=True, open_world=True)
    def connection_siemens_dali_program(
        node_id: int, timeout: float = 300.0
    ) -> dict[str, Any]:
        """CHANGES THE DEVICE: program the DALI device data the project holds for a Siemens
        KNX/DALI Gateway plus N 141/03 (its plugin data) onto the device, as the Program button of
        its plugin parameters does. Returns {node_id, writes}: the number of property writes that
        succeeded."""
        device = _siemens_gateway(node_id)
        entry = next(
            (
                e
                for e in getattr(device, "binary_data", []) or []
                if e.data and "BLOB_PARA" in (e.name or "")
            ),
            None,
        )
        if entry is None:
            raise ToolError(f"device {node_id} has no DALI device data in the project")
        _ensure_connected()
        future = ctx.run_locked(
            lambda: service.run_siemens_dca_program(device, entry.data)
        )
        if future is None:
            raise ToolError("programming could not start (another bus operation runs)")
        return {"node_id": node_id, "writes": future.result(timeout=timeout)}
