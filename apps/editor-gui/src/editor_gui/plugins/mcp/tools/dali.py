# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""DALI tools (BUS): the commissioning of a DALI gateway's bus as its DALI tabs do it.

MDT and Theben gateways: scan a DALI channel, identify an ECG or a group, run a new or a post
installation, and program the groups and scenes; a programmed channel is saved into the device's
backup data in the project, as the tab does. Siemens KNX/DALI Gateway plus N 141/03: a read-only
scan and programming the DALI device data the project holds for it.

As in the tab, programming groups needs a scan first. Only requested ECGs enter the plan;
the commissioner reads the other assignments immediately before writing.
"""

from __future__ import annotations

from dataclasses import dataclass
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

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from editor_gui.device import Device
    from xknxeditor.dali import DaliBusState, EcgAssignment, Scene
    from xknxeditor.dali.siemens import SiemensN141BusState

IdentifyAction = Literal["blink", "stop_blink", "on", "off"]
Installation = Literal["new", "post"]
GroupChoice = int | Literal["single", "unassigned"]


@dataclass
class _Channel:
    """The last scan of one DALI channel."""

    scan: DaliBusState | None = None


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
    channel_generation: int | None = None

    @tool("read")
    def project_e64pro_read(
        node_id: int | None = None,
        device_address: str | None = None,
        as_export: bool = False,
    ) -> dict[str, str]:
        """Read the offline e64 Pro backup as XML plus its revision for project_e64pro_edit.
        Supply either a node ID or individual address. Slot and target indexes are the stored
        zero-based XML indexes. Row numbers are zero-based within the selected slot.
        Set as_export for the same standalone device XML as the DCA export button.
        """
        from xml.etree import ElementTree as ET

        from editor_gui.plugin_blob_readers.dali_backup import e64pro

        def read() -> dict[str, str]:
            project = ctx.api.project
            device = require_device(project, node_id, device_address)
            entry = e64pro.backup(device)
            if entry is None:
                raise ValueError(S.E64_INVALID_CONFIGURATION)
            return {
                "revision": e64pro.revision(entry[1]),
                "xml": (
                    project.export_e64pro(device).decode("utf-8")
                    if as_export
                    else ET.tostring(e64pro.document(entry[1]), encoding="unicode")
                ),
            }

        return ctx.run_locked(read)

    @tool("write")
    def project_e64pro_import(
        xml: str | None = None,
        revision: str = "",
        node_id: int | None = None,
        device_address: str | None = None,
        path: str | None = None,
    ) -> str:
        """Import a complete e64 Pro device XML as one offline undo step.
        Supply exactly one of path (a file on the editor host) or xml (inline XML).
        Supply the current revision from project_e64pro_read, or leave it empty
        if the device has no saved configuration. Returns the new SHA256 revision.
        """

        from editor_gui.plugin_blob_readers.dali_backup import e64pro

        def change() -> str:
            project = ctx.api.project
            device = require_device(project, node_id, device_address)
            entry = e64pro.backup(device)
            expected = entry[1] if entry else None
            actual = e64pro.revision(expected)
            if actual != revision:
                raise ValueError("DALI revision changed")
            if (path is None) == (xml is None):
                raise ValueError("Supply exactly one of path or xml")
            from pathlib import Path

            payload = (
                Path(path).read_bytes()
                if path is not None
                else (xml or "").encode("utf-8")
            )
            data = project.import_e64pro(device, expected, payload)
            return e64pro.revision(data)

        return ctx.run_locked(change)

    @tool("write")
    def project_e64pro_edit(
        revision: str,
        section: Literal[
            "scenes", "effects", "templates", "groups", "ecgs", "mds", "pbs", "gis"
        ],
        slot: str,
        action: Literal[
            "header",
            "set",
            "add",
            "remove",
            "copy",
            "up",
            "down",
            "member",
            "colour",
            "assign",
            "description",
            "import_descriptions",
            "clear_descriptions",
            "insert",
            "sort_time",
            "sort_function",
            "clear",
        ],
        row: int = -1,
        field: str = "",
        value: str = "",
        target: str = "",
        index: str = "",
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> str:
        """Change one project backup field or row, as one undo step. Returns the new SHA256 revision.
        Pass revision from project_e64pro_read. header/set take an XML field and value (including
        keepValue/keepColor as True/False). add takes target groups/ecgs and target index for a scene
        or effect, or inserts a default template command. member sets a template target True/False.
        Template weekdays is seven binary digits, Sunday to Monday from left to right:
        0000001 = Monday, 1000000 = Sunday, 0011111 = Monday to Friday.
        Template func also supports CallScene/CallEffect: value is the zero-based target (0-15).
        CallEffect scaleMode is ScaleNoneEffectStart or ScaleNoneEffectStop (default stop).
        Template header mode is Disabled, Enabled or Controlled; it updates the KNX parameter too.
        SetMaxOnValue uses 1-100, default 100. Effect delay allows 0-10 and 20-200 in steps of 10.
        colour sets a supported colour space (field) and its encoded value together.
        remove/copy/up/down address a zero-based row. description sets the slot description
        (groups/ecgs/mds/pbs/gis/scenes/effects/templates). assign links an existing logical
        input row in mds/pbs/gis to physical instance index, or unlinks with index="".
        Read available logical and physical instances from project_e64pro_read XML.
        For multiple edits in one undo step use project_batch edit_e64pro. No bus connection.
        For templates, insert adds a default command before row, sort_time/sort_function
        order commands by time/function (then the other key), and clear removes all commands,
        keeping members and settings. clear_descriptions clears only the selected category.
        import_descriptions takes field=xml/txt and the file content in value, with slot="";
        categories groups/ecgs/mds/pbs/gis use GRP_TEXT/ECG_TEXT/MD_TEXT/PB_TEXT/GI_TEXT
        XML roots and one-based text@index. TXT lines map to successive slots, '' clears a slot.
        Entries omitted from an import remain unchanged. Invalid input is rejected atomically.
        """

        from editor_gui.plugin_blob_readers.dali_backup import e64pro

        def change() -> str:
            device = require_device(ctx.api.project, node_id, device_address)
            entry = e64pro.backup(device)
            if entry is None or e64pro.revision(entry[1]) != revision:
                raise ValueError("DALI revision changed")
            data = ctx.api.project.edit_e64pro(
                device,
                entry[1],
                e64pro.Edit(section, slot, action, row, field, value, target, index),
            )
            return e64pro.revision(data)

        return ctx.run_locked(change)

    @tool("read")
    def project_e64pro_export_descriptions(
        section: Literal["groups", "ecgs", "mds", "pbs", "gis"],
        format: Literal["xml", "txt"] = "xml",
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> str:
        """Export a category's saved descriptions as XML or UTF-8 text, without bus access.

        XML uses one-based text@index and description attributes. TXT has one line per slot;
        '' represents an empty description. Text that TXT cannot preserve requires XML.
        Import with project_e64pro_edit import_descriptions, or project_batch edit_e64pro.
        """
        from editor_gui.plugin_blob_readers.dali_backup import e64pro
        from editor_gui.plugin_blob_readers.dali_backup.e64pro_descriptions import (
            export_descriptions,
        )

        def read() -> str:
            device = require_device(ctx.api.project, node_id, device_address)
            entry = e64pro.backup(device)
            if entry is None:
                raise ValueError("No DALI backup available")
            return export_descriptions(entry[1], section, format).decode("utf-8-sig")

        return ctx.run_locked(read)

    @tool("read")
    def project_e64pro_export_slot(
        section: Literal["scenes", "effects", "templates"],
        slot: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> str:
        """Export one saved scene, effect or time template as standalone XML. Slot is its zero-based index. No bus access."""
        from editor_gui.plugin_blob_readers.dali_backup import e64pro

        def read() -> str:
            device = require_device(ctx.api.project, node_id, device_address)
            entry = e64pro.backup(device)
            if entry is None:
                raise ValueError(S.E64_INVALID_CONFIGURATION)
            return e64pro.export_slot(entry[1], section, slot).decode("utf-8")

        return ctx.run_locked(read)

    @tool("write")
    def project_e64pro_import_slot(
        section: Literal["scenes", "effects", "templates"],
        slot: str,
        revision: str,
        path: str,
        node_id: int | None = None,
        device_address: str | None = None,
    ) -> str:
        """Replace one saved slot from a Scene, Effect or Tpl XML file on the editor host.
        Pass revision from project_e64pro_read. One undo step, no bus access.
        Importing a scene/effect with a different index retains the destination description
        and KNX scene number/effect mode. Other slots are preserved. Returns the new revision.
        """
        from pathlib import Path
        from xml.etree import ElementTree as ET

        from editor_gui.plugin_blob_readers.dali_backup import e64pro

        def change() -> str:
            device = require_device(ctx.api.project, node_id, device_address)
            entry = e64pro.backup(device)
            if entry is None or e64pro.revision(entry[1]) != revision:
                raise ValueError(S.E64_CONFIGURATION_CHANGED)
            parser = ET.XMLParser(
                target=ET.TreeBuilder(insert_comments=True, insert_pis=True)
            )
            xml = ET.tostring(
                ET.fromstring(Path(path).read_bytes(), parser=parser),
                encoding="unicode",
            )
            data = ctx.api.project.edit_e64pro(
                device, entry[1], e64pro.Edit(section, slot, "import", value=xml)
            )
            return e64pro.revision(data)

        return ctx.run_locked(change)

    @tool("read", open_world=True)
    def connection_dali_report(
        node_id: int | None = None,
        device_address: str | None = None,
        channel: int = 0,
        refresh: bool = True,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Read IPAS e64 Pro/Theben P64 V2 diagnostics: lamp/ECG/converter counts, error counts,
        failure percentages and saved emergency-light test results, including individual failure
        flags and converter state. Reads gateway properties only; starts no tests.
        refresh=false returns the last report shared with the GUI without bus access.
        Returns node_id, channel, read_at, summary, tests and exportable XML; no saved report returns {}.
        """
        from dataclasses import asdict

        from editor_gui.plugin_blob_readers.dali_backup import e64pro

        device = _gateway(node_id, device_address, channel)
        if not e64pro.supports(device):
            raise ValueError(S.E64_INVALID_SELECTION)
        project = ctx.api.project
        if refresh:
            generation = ctx.run_locked(lambda: project.generation)
            from xknxeditor.dali import MdtDaliCommissioner
            from xknxeditor.dali.report import DiagnosticReport

            async def read(commissioner: MdtDaliCommissioner) -> DiagnosticReport:
                return await commissioner.read_report()

            fresh = _run(
                device,
                read,
                channel,
                timeout,
                S.E64_READ_REPORT,
                writes=False,
            )
            ctx.run_locked(
                lambda: project.store_dali_report(device, channel, generation, fresh)
            )
        report = ctx.run_locked(lambda: project.dali_report(device, channel))
        if report is None:
            return {}
        return {
            "node_id": device.node_id,
            "channel": channel,
            **asdict(report),
            "xml": report.to_xml(
                ctx.run_locked(lambda: e64pro.ecg_names(device))
            ).decode("utf-8"),
        }

    def _ensure_connected() -> None:
        # Without an open connection the editor connects for the operation (see connection.py).
        if not service.can_reach_bus():
            raise ToolError(
                "no KNX connection; connect first with connection_connect (configured gateway) "
                "or connection_scan + connection_connect_gateway / connection_connect_usb"
            )

    def _gateway(
        node_id: int | None, device_address: str | None, channel: int
    ) -> Device:
        """The MDT/Theben DALI gateway (by ``node_id`` OR ``device_address``) with a channel
        ``channel``."""
        from editor_gui.programming_dali import dali_channel_count, is_dali_dca_app

        def _read() -> tuple[Device, int]:
            device = require_device(ctx.api.project, node_id, device_address)
            info = ctx.api.project.get_device_info(device.node_id)
            order = getattr(info, "order_number", "") if info is not None else ""
            return device, dali_channel_count(order if isinstance(order, str) else "")

        nonlocal channel_generation
        generation = ctx.run_locked(lambda: ctx.api.project.generation)
        if generation != channel_generation:
            channels.clear()
            channel_generation = generation
        device, count = ctx.run_locked(_read)
        if device.app is None or not is_dali_dca_app(device.app.id):
            raise ToolError(
                f"device {device.node_id} is not a DALI gateway these tools commission"
            )
        if not device.individual_address:
            raise ToolError(f"device {device.node_id} has no individual address")
        if not 0 <= channel < count:
            raise ToolError(f"the gateway has DALI channel 0 to {count - 1}")
        return device

    def _run(
        device: Device,
        op: Any,
        channel: int,
        timeout: float,
        text: str,
        *,
        writes: bool = True,
    ) -> Any:
        """Run ``op`` in the Operations queue, named ``text`` there as the tab's button;
        ``writes``: whether it changes something (all but a scan)."""
        _ensure_connected()
        generation = ctx.run_locked(lambda: ctx.api.project.generation)
        ended = run_queued_action(
            ctx,
            device.node_id,
            "dali",
            timeout,
            op=op,
            channel=channel,
            text=text,
            writes=writes,
        )
        if ended is not None:
            result = ended.result
        else:
            future = ctx.run_locked(
                lambda: service.run_dali(device, op, channel=channel, writes=writes)
            )
            if future is None:
                raise not_started(service, "the DALI operation")
            result = await_operation(service, future, timeout, "the DALI operation")
        if ctx.run_locked(lambda: ctx.api.project.generation) != generation:
            raise ToolError(
                "the project changed during the DALI operation; its result was not saved"
            )
        return result

    @tool("write", open_world=True)
    def connection_dali_program_configuration(
        revision: str,
        channel: int = 0,
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 900,
    ) -> dict[str, Any]:
        """Program the saved DCA state: mapped ECGs, full colour scenes and schedules,
        plus effects and input assignments on IPAS/Theben gateways. All tables are validated
        before connecting. Pass the current backup revision from project_e64pro_read.
        This changes hardware; project undo cannot undo the bus writes. A failure can leave
        earlier tables applied. Returns the fresh scan and clears saved dirty flags after
        successful programming and project synchronization. If the project or backup changed
        during programming, reports an error and preserves those newer edits; the hardware
        may already contain the programmed revision.
        """
        from editor_gui.plugin_blob_readers.dali_backup.e64pro import (
            revision as backup_revision,
        )
        from editor_gui.programming_dali import (
            GuardedDaliOperation,
            complete_dali_configuration,
            prepare_dali_configuration,
        )
        from xknxeditor.dali import MdtDaliCommissioner

        device = _gateway(node_id, device_address, channel)
        generation = ctx.run_locked(lambda: ctx.api.project.generation)
        expected, configuration = ctx.run_locked(
            lambda: prepare_dali_configuration(device, channel)
        )
        if backup_revision(expected) != revision:
            raise ValueError("DALI revision changed")

        async def program(commissioner: MdtDaliCommissioner) -> DaliBusState:
            return await commissioner.write_configuration(configuration)

        result = _run(
            device,
            GuardedDaliOperation(
                ctx.api.project, device, expected, generation, program, channel
            ),
            channel,
            timeout,
            S.PLUGIN_PARAMS_PROGRAM_DCA,
        )
        ctx.run_locked(
            lambda: complete_dali_configuration(
                ctx.api.project, device, expected, generation, channel
            )
        )
        channels[device.node_id, channel] = _Channel(scan=result)
        return scan_dict(device.node_id, channel, result)

    def _workspace_operation(
        device: Device,
        revision: str,
        action: str,
        section: str,
        index: int,
        row: int,
        value: int,
        secondary: int,
        bank: int,
        lock: bool,
        timeout: float,
    ) -> dict[str, Any]:
        from editor_gui.plugin_blob_readers.dali_backup.e64pro import (
            revision as backup_revision,
        )
        from editor_gui.programming_dali import (
            GuardedDaliOperation,
            finish_workspace_operation,
            prepare_workspace_operation,
        )

        generation = ctx.run_locked(lambda: ctx.api.project.generation)
        plan = ctx.run_locked(
            lambda: prepare_workspace_operation(
                device,
                action,
                section,
                index,
                row,
                value,
                secondary,
                bank,
                lock,
            )
        )
        if backup_revision(plan.expected) != revision:
            raise ToolError("DALI revision changed")
        operation = GuardedDaliOperation(
            ctx.api.project, device, plan.expected, generation, plan.execute
        )
        result = _run(
            device, operation, 0, timeout, S.EDITOR_TAB_DALI, writes=plan.writes
        )
        return ctx.run_locked(
            lambda: finish_workspace_operation(
                ctx.api.project,
                device,
                plan,
                generation,
                result,
            )
        )

    @tool("write", open_world=True)
    def connection_dali_workspace(
        revision: str,
        action: str,
        section: str = "scenes",
        index: int = 0,
        row: int = 0,
        value: int = 0,
        secondary: int = 0,
        bank: int = 2,
        lock: bool = False,
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 900,
    ) -> dict[str, Any]:
        """Run a saved IPAS/Theben DCA workspace operation using its current backup revision.

        read/write: all slots of section scenes/effects/templates/inputs. Reading replaces that
        project section in one undo step; writing verifies readback before clearing dirty flags.
        test: scene index 0..15, or template index and zero-based command row. start/stop: effect
        index. discover: address newly found inputs; identify/stop_identify: input address 0..63.
        function/duration/battery: emergency test on physical ECG index 0..63.
        read_calibration/set_calibration/save_calibration: motion controller index 0..7; value is
        percent (10..25 or 70..100, step5), secondary is measured lux for save.
        read_memory/write_memory: motion index, bank 2..199, secondary byte address 0..255,
        value byte to write, lock enables the device's write-lock command option.
        read_names/write_names: section ecgs/groups/mds; write uses saved descriptions (max20
        UTF-8 bytes). read_motion: all motion diagnostics. inhibit: converter inhibit command.
        Returns operation-specific values, devices, controllers or names. Project changes use
        atomic undo; hardware writes cannot be undone by project undo. Call project_e64pro_read
        again for the new revision. Replacement uses connection_dali_replace instead.
        """
        if action in ("replace", "restore"):
            raise ToolError("use connection_dali_replace or connection_dali_restore")
        device = _gateway(node_id, device_address, 0)
        return _workspace_operation(
            device,
            revision,
            action,
            section,
            index,
            row,
            value,
            secondary,
            bank,
            lock,
            timeout,
        )

    @tool("destructive", open_world=True)
    def connection_dali_replace(
        revision: str,
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 180,
    ) -> dict[str, Any]:
        """Start the IPAS/Theben gateway's automatic ECG replacement process.
        This changes physical DALI assignments and cannot be undone through project undo.
        Requires the current revision from project_e64pro_read. Returns gateway result/detail.
        """
        device = _gateway(node_id, device_address, 0)
        return _workspace_operation(
            device, revision, "replace", "inputs", 0, 0, 0, 0, 2, False, timeout
        )

    @tool("destructive", open_world=True)
    def connection_dali_restore(
        revision: str,
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 900,
    ) -> dict[str, Any]:
        """Restore an IPAS/Theben gateway from the current DCA backup's physical inventory.
        Requires CanRestore=True and no pending assignments. Validates all tables before
        connecting; restores ECG properties, input inventory, GTIN, calibration, assignments,
        scenes, effects and templates, verifies readback and restarts the gateway.
        This overwrites physical DALI state; project undo cannot reverse hardware changes.
        Partial failure may leave preceding tables applied. Pass project_e64pro_read revision.
        Returns an empty object after successful completion; re-read the project for its revision.
        """
        device = _gateway(node_id, device_address, 0)
        return _workspace_operation(
            device, revision, "restore", "ecgs", 0, 0, 0, 0, 2, False, timeout
        )

    def _persist(
        device: Device,
        channel: int,
        assignments: list[EcgAssignment],
        scenes: list[Scene],
    ) -> bool:
        """Save the channel's state into the project's backup data, as the tab does after a
        groups or scenes write; False without a scan to save."""
        from editor_gui.programming_dali import persist_dali_backup

        state = channels.get((device.node_id, channel))
        if state is None or state.scan is None:
            return False
        scan = state.scan

        def _save() -> bool:
            if ctx.api.project.generation != channel_generation:
                return False
            return persist_dali_backup(
                ctx.api.project,
                device,
                scan,
                assignments,
                scenes,
                channel,
                label=S.DALI_PERSIST_LABEL,
            )

        return ctx.run_locked(_save)

    def _assignments(scan: DaliBusState, groups: dict[int, int]) -> list[Any]:
        from xknxeditor.dali.model import EcgAssignment

        return [
            EcgAssignment(
                slot=e.slot,
                ets_index=e.ets_ecg_index,
                group_index=groups[e.slot],
            )
            for e in scan.present_ecgs
            if e.slot in groups
        ]

    @tool("read", open_world=True)
    def connection_dali_scan(
        node_id: int | None = None,
        device_address: str | None = None,
        channel: int = 0,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Read the state of a DALI channel of an MDT or Theben DALI gateway (by ``node_id`` OR
        ``device_address``, its individual address like "1.0.30"; ``channel`` 0, or 1 on a 2x64
        gateway) over the bus; changes nothing. Returns {node_id, channel, firmware, ecgs,
        groups}: each present ECG has {slot (its DALI short address 0-63), ecg_number, group
        (0-15, "single", "unassigned" or null), type, sub_type, long_address, min_value, alarm};
        each group {index, ecg_count, lamp_failures, ecg_failures, run_hours}."""
        device = _gateway(node_id, device_address, channel)

        def op(c: Any) -> Any:
            return c.scan()

        scan = _run(device, op, channel, timeout, S.DALI_SCAN, writes=False)
        state = channels.setdefault((device.node_id, channel), _Channel())
        state.scan = scan
        return scan_dict(device.node_id, channel, scan)

    @tool("destructive", open_world=True)
    def connection_dali_identify(
        action: IdentifyAction,
        node_id: int | None = None,
        device_address: str | None = None,
        slot: int | None = None,
        group: int | None = None,
        channel: int = 0,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Find a light on a DALI channel of an MDT or Theben gateway (by ``node_id`` OR
        ``device_address``): ``action`` blink / stop_blink / on / off for the ECG at ``slot``
        (0-63) or for the group ``group`` (0-15); without either, on / off switches every light
        of the channel (broadcast). Returns {node_id, channel, action, slot, group}."""
        if slot is not None and group is not None:
            raise ToolError("pass slot or group, not both")
        if slot is not None and not 0 <= slot < 64:
            raise ToolError("slot must be 0-63")
        if group is not None and not 0 <= group < 16:
            raise ToolError("group must be 0-15")
        device = _gateway(node_id, device_address, channel)
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

        if action in ("blink", "stop_blink"):
            text = S.DALI_BLINK if start else S.DALI_STOP
        elif slot is None and group is None:
            text = S.DALI_ALL_ON if start else S.DALI_ALL_OFF
        else:
            text = S.DALI_SWITCH_ON if start else S.DALI_SWITCH_OFF
        _run(device, op, channel, timeout, text)
        return {
            "node_id": device.node_id,
            "channel": channel,
            "action": action,
            "slot": slot,
            "group": group,
        }

    @tool("destructive", open_world=True)
    def connection_dali_installation(
        installation: Installation,
        node_id: int | None = None,
        device_address: str | None = None,
        channel: int = 0,
        timeout: float = 900.0,
    ) -> dict[str, Any]:
        """CHANGES THE DALI BUS: run a new installation (``installation="new"``: every ECG of
        the channel gets a new short address) or a post installation ("post": only ECGs without
        one are added) on an MDT or Theben gateway (by ``node_id`` OR ``device_address``), then
        read the channel. Run it only when the user asked for it. Returns what
        connection_dali_scan returns."""
        device = _gateway(node_id, device_address, channel)
        if installation == "new":

            def op(c: Any) -> Any:
                return c.new_installation(on_progress=None)
        else:

            def op(c: Any) -> Any:
                return c.post_installation(on_progress=None)

        text = (
            S.DALI_NEW_INSTALLATION
            if installation == "new"
            else S.DALI_POST_INSTALLATION
        )
        scan = _run(device, op, channel, timeout, text)
        state = channels.setdefault((device.node_id, channel), _Channel())
        state.scan = scan
        return scan_dict(device.node_id, channel, scan)

    @tool("destructive", open_world=True)
    def connection_dali_program_groups(
        groups: list[dict[str, Any]],
        node_id: int | None = None,
        device_address: str | None = None,
        channel: int = 0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """CHANGES THE DALI BUS: program the group of the ECGs of a channel of an MDT or Theben
        gateway (by ``node_id`` OR ``device_address``). Needs a connection_dali_scan of the channel first.
        ``groups`` is a nonempty list of {slot, group} with group 0-15, "single" or "unassigned".
        Requested ECGs keep their scanned logical number; all other assignments are read from
        the device immediately before writing. Completed edits are saved in the project's
        backup data (one undo step). Returns {node_id,
        channel, programmed, saved}."""
        device = _gateway(node_id, device_address, channel)
        state = channels.get((device.node_id, channel))
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
            raise ToolError("no group changes to program")

        def op(c: Any) -> Any:
            return c.commission(assignments)

        _run(device, op, channel, timeout, S.DALI_PROGRAM_GROUPS)
        for e in state.scan.present_ecgs:
            if e.slot in changes:
                e.group_index = changes[e.slot]
        saved = _persist(device, channel, assignments, [])
        return {
            "node_id": device.node_id,
            "channel": channel,
            "programmed": len(assignments),
            "saved": saved,
        }

    @tool("destructive", open_world=True)
    def connection_dali_program_scenes(
        scenes: list[dict[str, Any]],
        node_id: int | None = None,
        device_address: str | None = None,
        channel: int = 0,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """CHANGES THE DALI BUS: program scenes on a channel of an MDT or Theben gateway (by
        ``node_id`` OR ``device_address``). ``scenes`` is a list of {scene (0-15), levels: [{group (0-15), level (0-255)}]}; each scene
        keeps its KNX mapping, fade time, other targets and colours. After a connection_dali_scan of the channel, these edits are saved
        into the device's backup data in the project (one undo step). Returns {node_id, channel,
        programmed, saved}."""
        from xknxeditor.dali.model import MAX_GROUP, MAX_SCENES, Scene, SceneValue

        device = _gateway(node_id, device_address, channel)
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
            return c.write_scenes(planned, levels_only=True)

        _run(device, op, channel, timeout, S.DALI_PROGRAM_SCENES)
        saved = _persist(device, channel, [], planned)
        return {
            "node_id": device.node_id,
            "channel": channel,
            "programmed": len(planned),
            "saved": saved,
        }

    def _siemens_gateway(node_id: int | None, device_address: str | None) -> Device:
        from editor_gui.plugin_blob_readers.siemens_dali.layout import line_count

        device = ctx.run_locked(
            lambda: require_device(ctx.api.project, node_id, device_address)
        )
        if device.app is None or not line_count(device.app.id):
            raise ToolError(
                f"device {device.node_id} is not a supported classic Siemens DALI gateway"
            )
        if not device.individual_address:
            raise ToolError(f"device {device.node_id} has no individual address")
        return device

    @tool("read", open_world=True)
    def connection_siemens_dali_scan(
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 120.0,
        line: int = 0,
    ) -> dict[str, Any]:
        """Read a DALI line of a Siemens N 141/03 or N 141/21 (by ``node_id`` OR
        ``device_address``, its individual address like "1.0.30") over the bus; changes nothing.
        ``line`` is zero-based: 0 for single-line gateways, 0 or 1 for twin gateways.
        Returns {node_id, line, ecgs}; each present ECG has {slot, short_address, random_address,
        type, min_physical_level}."""
        from editor_gui.plugin_blob_readers.siemens_dali.layout import line_count
        from xknxeditor.dali.siemens import scan_n141_03

        device = _siemens_gateway(node_id, device_address)
        if type(line) is not int or not 0 <= line < line_count(
            device.app.id if device.app else ""
        ):
            raise ToolError("Invalid Siemens DALI line")
        _ensure_connected()

        def op(p: Any) -> Any:
            return scan_n141_03(p, line=line)

        ended = run_queued_action(
            ctx,
            device.node_id,
            "siemens_dali",
            timeout,
            op=op,
            text=S.SIEMENS_DALI_SCAN_BUS,
        )
        if ended is not None:
            return siemens_scan_dict(device.node_id, ended.result)
        future = ctx.run_locked(lambda: service.run_siemens_dali(device, op))
        if future is None:
            raise not_started(service, "the DALI scan")
        return siemens_scan_dict(
            device.node_id, await_operation(service, future, timeout, "the DALI scan")
        )

    @tool("destructive", open_world=True)
    def connection_siemens_dali_program(
        node_id: int | None = None,
        device_address: str | None = None,
        timeout: float = 1200.0,
    ) -> dict[str, Any]:
        """CHANGES THE DEVICE: program the DALI device data the project holds for a Siemens
        N 141/03 or N 141/21 (by ``node_id`` OR ``device_address``; its plugin data)
        onto the device, as the Program button of its plugin parameters does. Returns {node_id,
        writes}: the number of configuration arrays written. Includes scenes (unless overwrite
        is disabled), effects, control gear, sensors and time control, then waits for their
        configuration update and gateway completion. Requires the matching KNX application."""
        device = _siemens_gateway(node_id, device_address)
        entry = next(
            (
                e
                for e in getattr(device, "binary_data", []) or []
                if e.data and e.name == "BLOB_PARA"
            ),
            None,
        )
        if entry is None:
            raise ToolError(
                f"device {device.node_id} has no DALI device data in the project"
            )
        _ensure_connected()
        data = entry.data
        ended = run_queued_action(
            ctx, device.node_id, "siemens_dca", timeout, data=data
        )
        if ended is not None:
            return {"node_id": device.node_id, "writes": ended.result}
        future = ctx.run_locked(lambda: service.run_siemens_dca_program(device, data))
        if future is None:
            raise not_started(service, "programming")
        writes = await_operation(service, future, timeout, "programming")
        return {"node_id": device.node_id, "writes": writes}
