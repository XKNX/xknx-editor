"""Glue between the GUI/connection stack and the MDT DALI commissioning library.

Opens a (optionally Tool-Key secured) point-to-point connection to a DALI gateway, builds an
``MdtDaliCommissioner`` on the same ``DeviceProgrammer`` the download flows use, runs one caller
supplied coroutine against it, and always closes the connection. Kept out of the plugins so the
connection service stays thin and this is unit-testable.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from xknx import XKNX
from xknx.telegram import IndividualAddress

from xknxeditor.dali import MdtDaliCommissioner, MdtDaliConnection, profile_for
from xknxeditor.dali.connection import Programmer
from xknxeditor.dali.siemens import is_siemens_n141_03
from xknxeditor.download import DeviceProgrammer, DeviceSecurity

# Reuse the download package's own connection builders (plain or Tool-Key secured) so a DALI session
# gets the same secure handling as a normal download. Repo-internal; intentional cross-module use.
from xknxeditor.download.session import apdu_overhead, management_session

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.dali import DaliBusState, EcgAssignment, Scene
    from xknxeditor.dali.configuration import Configuration


def is_dali_dca_app(app_id: str | None) -> bool:
    """Whether ``app_id`` is a Type-A DALI gateway (MDT / Theben) — gates the commissioning tab."""
    return profile_for(app_id) is not None


def dali_object_index_base(app_id: str | None) -> int:
    """The KNX object-index base for ``app_id``'s DALI channel 0 (vendor-specific); 6 if unknown."""
    profile = profile_for(app_id)
    return profile.object_index_base if profile is not None else 6


def dali_channel_count(order_number: str | None) -> int:
    """Number of independent DALI bus channels a gateway has (1 if unknown).

    MDT encodes it in the order number: ``SCN-DA641`` is a 1x64 gateway (one bus), ``SCN-DA642``
    a 2x64 (two buses). The digit right after ``DA64`` is the channel count.
    """
    if order_number:
        match = re.search(r"DA64(\d)", order_number.upper())
        if match:
            return max(1, int(match.group(1)))
    return 1


async def run_dali_operation[T](
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    channel: int,
    op: Callable[[MdtDaliCommissioner], Awaitable[T]],
    *,
    object_index_base: int | None = None,
    app_id: str | None = None,
) -> T:
    """Open a connection to ``address``, run ``op`` against an ``MdtDaliCommissioner``, then close.

    ``security`` (from the keyring) enables a Tool-Key secured session, matching the download flows.
    ``object_index_base`` is the vendor's DALI object index for channel 0 (see dali profiles).
    ``app_id`` picks the ``DaliProfile`` so the connection applies per-vendor element-size overrides
    (e.g. Theben P64's 10-byte Group_Status); when ``None`` the connection uses MDT defaults.
    """
    target = IndividualAddress(address)
    manager = management_session(xknx, target, security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        # Negotiate the device's real APDU length (defaults to the mandatory 15). Multi-byte DALI
        # property writes (and a secure session's overhead) would not fit the 15-octet default.
        programmer.max_apdu_length = await programmer.read_max_apdu_length()
        profile = profile_for(app_id) if app_id is not None else None
        commissioner = MdtDaliCommissioner(
            MdtDaliConnection(programmer, channel, object_index_base, profile=profile)
        )
        return await op(commissioner)
    finally:
        with contextlib.suppress(Exception):
            await manager.close()


async def run_siemens_dali_operation[T](
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    op: Callable[[Programmer], Awaitable[T]],
) -> T:
    """Open a connection to ``address``, run ``op`` against a raw ``Programmer``, then close.

    Siemens N141/03 drives DALI through standard A_PropertyValue_Read on interface objects 35/36
    (no vendor-specific commissioner wrapper), so the glue hands the operation the programmer
    directly rather than a vendor-specific commissioner object.
    """
    target = IndividualAddress(address)
    manager = management_session(xknx, target, security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        programmer.max_apdu_length = await programmer.read_max_apdu_length()
        return await op(programmer)
    finally:
        with contextlib.suppress(Exception):
            await manager.close()


__all__ = [
    "dali_channel_count",
    "dali_object_index_base",
    "is_dali_dca_app",
    "is_siemens_n141_03",
    "run_dali_operation",
    "run_siemens_dali_operation",
]


def persist_dali_backup(
    project: ProjectService,
    device: Device,
    scan: DaliBusState,
    assignments: list[EcgAssignment],
    scenes: list[Scene],
    channel: int,
    *,
    label: str,
) -> bool:
    """Write a DALI channel's state back into the project blob after a bus commissioning run
    (one undo step named ``label``).

    MDT (gc16) blobs are fully modelled: the scan + edits are merged onto the existing
    ``DaliGC16-Backup-Store`` (``.N`` for channel N+1), preserving templates/descriptions.
    Only the supplied group assignments and scene levels are updated. Other scene
    targets, fade times, mappings and colours are preserved. Returns False when the
    device has no supported backup to update.
    """
    from xknxeditor.dali import merge_commissioning_blob, patch_assignments_in_blob
    from xknxeditor.dali.profiles import backup_blob_name
    from xknxeditor.dali.project_persistence import patch_scene_levels_in_blob

    current = project.find_device_by_node_id(device.node_id)
    if current is None:
        return False
    device = current
    app_id = device.app.id if device.app is not None else None
    profile = profile_for(app_id)
    if profile is None:
        return False
    name = backup_blob_name(profile.backup_blob_base, channel)
    existing = project.device_binary_data(device, name)
    if profile.key == "mdt":
        metadata = project.get_project_metadata()
        blob = merge_commissioning_blob(
            existing=existing,
            state=scan,
            assignments=assignments,
            scenes=[],
            individual_address=device.individual_address,
            project_name=metadata.name if metadata is not None else "",
        )
    elif existing is not None:
        blob = patch_assignments_in_blob(existing, assignments)
    else:
        return False
    blob = patch_scene_levels_in_blob(blob, scenes)
    project.persist_dali_backup(device, name, blob, label=label)
    return True


def prepare_dali_configuration(
    device: Device, channel: int = 0
) -> tuple[bytes, Configuration]:
    """Validate the saved configuration before any network operation."""
    from xknxeditor.dali.configuration import compile_configuration
    from xknxeditor.dali.profiles import backup_blob_name

    profile = profile_for(device.app.id if device.app else None)
    if profile is None:
        raise ValueError("Unsupported DALI gateway")
    name = backup_blob_name(profile.backup_blob_base, channel)
    data = next(
        (entry.data for entry in device.binary_data if entry.name == name), None
    )
    if data is None:
        raise ValueError("No saved DALI configuration")
    configuration = compile_configuration(data)
    if not profile.supports_extended_templates and any(
        not t.reset_on_midnight or any(c.func == 18 for c in t.commands)
        for t in configuration.templates
    ):
        raise ValueError("Template features are unsupported by this gateway")
    return bytes(data), configuration


def complete_dali_configuration(
    project: ProjectService,
    device: Device,
    expected: bytes,
    generation: int,
    channel: int = 0,
) -> None:
    """Mark exactly the programmed snapshot synchronized, keeping newer edits untouched."""
    from xml.etree import ElementTree as ET

    from editor_gui.plugins.project.strings import S
    from xknxeditor.dali.configuration import compile_configuration
    from xknxeditor.dali.profiles import backup_blob_name
    from xknxeditor.dali.project_persistence import patch_assignments_in_blob

    current = project.find_device_by_node_id(device.node_id)
    profile = profile_for(device.app.id if device.app else None)
    if current is None or profile is None or project.generation != generation:
        raise ValueError(S.E64_CONFIGURATION_CHANGED)
    name = backup_blob_name(profile.backup_blob_base, channel)
    actual = next(
        (entry.data for entry in current.binary_data if entry.name == name), None
    )
    if actual != expected:
        raise ValueError(S.E64_CONFIGURATION_CHANGED)
    assignments = compile_configuration(expected).assignments
    root = ET.fromstring(patch_assignments_in_blob(expected, list(assignments)))
    for ecg in root.findall("ecgs/ECG"):
        if ecg.get("short-address", "255") == "255" and ecg.get("flags") in (
            "2",
            "3",
            "6",
        ):
            ecg.set("flags", "1")
    for section in ("scenes", "effects", "templates"):
        for slot in root.findall(section + "/*"):
            slot.set("isDirty", "False")
    for section in ("mds", "pbs", "gis"):
        for instance in root.findall(section + "/*/Instances/EtsInstance"):
            reference = int(instance.get("ref-idx", "-1"))
            instance.set("origin", str(reference))
            instance.set("flags", "4" if 0 <= reference < 254 else "1")
    project.persist_dali_backup(
        current,
        name,
        ET.tostring(root, encoding="utf-16le"),
        label=S.DALI_PERSIST_LABEL,
    )


@dataclass(frozen=True)
class PreparedDaliOperation:
    expected: bytes
    execute: Callable[[MdtDaliCommissioner], Awaitable[dict[str, Any]]]
    writes: bool


@dataclass(frozen=True)
class GuardedDaliOperation:
    project: ProjectService
    device: Device
    expected: bytes | None
    generation: int
    execute: Callable[[MdtDaliCommissioner], Awaitable[Any]]
    channel: int = 0

    def validate_device(self, current: Device) -> None:
        """Reject a queued plan whose project, target or saved configuration changed."""
        from editor_gui.plugins.project.strings import S
        from xknxeditor.dali.profiles import backup_blob_name

        profile = profile_for(self.device.app.id if self.device.app else None)
        if (
            self.project.generation != self.generation
            or current.node_id != self.device.node_id
            or current.individual_address != self.device.individual_address
            or current.app is None
            or self.device.app is None
            or current.app.id != self.device.app.id
            or profile is None
        ):
            raise ValueError(S.E64_CONFIGURATION_CHANGED)
        name = backup_blob_name(profile.backup_blob_base, self.channel)
        if (
            next((b.data for b in current.binary_data if b.name == name), None)
            != self.expected
        ):
            raise ValueError(S.E64_CONFIGURATION_CHANGED)

    async def __call__(self, commissioner: MdtDaliCommissioner) -> Any:
        return await self.execute(commissioner)


@dataclass(frozen=True)
class PreparedDaliReplacement:
    expected: bytes | None
    name: str
    channel: int
    execute: Callable[[MdtDaliCommissioner], Awaitable[tuple[DaliBusState, bytes]]]


def prepare_dali_replacement(
    device: Device, channel: int = 0
) -> PreparedDaliReplacement:
    """Prepare replacement and refresh physical records without changing planned settings."""
    from xml.etree import ElementTree as ET

    from editor_gui.plugins.project.strings import S
    from xknxeditor.dali.profiles import backup_blob_name
    from xknxeditor.dali.project_persistence import build_backup_store_xml
    from xknxeditor.dali.properties import PropertyType

    profile = profile_for(device.app.id if device.app else None)
    if profile is None or channel < 0:
        raise ValueError(S.E64_UNSUPPORTED_OPERATION)
    name = backup_blob_name(profile.backup_blob_base, channel)
    expected = next((bytes(b.data) for b in device.binary_data if b.name == name), None)
    if expected is None and profile.key != "mdt":
        raise ValueError(S.E64_INCOMPLETE_CONFIGURATION)
    root = ET.fromstring(expected) if expected is not None else None
    if root is not None and (
        root.tag != "device"
        or root.get("type") != ("gc16" if profile.key == "mdt" else "e64pro")
        or len(root.findall("devecgs")) != 1
    ):
        raise ValueError(S.E64_INVALID_CONFIGURATION)
    if root is not None:
        indexes = [e.get("index", "") for e in root.findall("devecgs/DevECG")]
        if len(set(indexes)) != len(indexes) or any(
            not i.isascii()
            or not i.isdecimal()
            or str(int(i)) != i
            or not 0 <= int(i) < 64
            for i in indexes
        ):
            raise ValueError(S.E64_INVALID_CONFIGURATION)

    async def execute(commissioner: MdtDaliCommissioner) -> tuple[DaliBusState, bytes]:
        if (
            commissioner.connection.profile != profile
            or commissioner.connection.channel != channel
        ):
            raise ValueError(S.E64_CONFIGURATION_CHANGED)
        scan = await commissioner.replace_ecg()
        scenes = await commissioner.read_scenes() if expected is None else []
        templates = await commissioner.read_templates() if expected is None else []
        fresh = ET.fromstring(
            build_backup_store_xml(
                state=scan,
                scenes=scenes,
                templates=templates,
                individual_address=device.individual_address,
            )
        )
        physical = fresh.find("devecgs")
        assert physical is not None
        if profile.key != "mdt":
            gtins = await commissioner.connection.read_table(PropertyType.GTIN, 64)
            for item in physical:
                index = int(item.attrib["index"])
                item.set(
                    "gtin", str(int.from_bytes(gtins[index * 6 : index * 6 + 6], "big"))
                )
        if expected is None:
            return scan, ET.tostring(fresh, encoding="utf-16le")
        updated = ET.fromstring(expected)
        target = updated.find("devecgs")
        assert target is not None
        existing = {e.get("index"): e for e in target.findall("DevECG")}
        for item in physical:
            previous = existing.get(item.get("index"))
            if previous is None:
                target.append(item)
            else:
                for attribute in ("min-ctemp", "max-ctemp"):
                    previous.attrib.pop(attribute, None)
                previous.attrib.update(item.attrib)
        return scan, ET.tostring(updated, encoding="utf-16le")

    return PreparedDaliReplacement(expected, name, channel, execute)


def finish_dali_replacement(
    project: ProjectService,
    device: Device,
    plan: PreparedDaliReplacement,
    generation: int,
    data: bytes,
) -> None:
    """Save exactly the replaced gateway's readback as one undo step."""
    from editor_gui.plugins.project.strings import S

    current = project.find_device_by_node_id(device.node_id)
    if current is None:
        raise ValueError(S.E64_CONFIGURATION_CHANGED)
    GuardedDaliOperation(
        project, device, plan.expected, generation, plan.execute, plan.channel
    ).validate_device(current)
    project.persist_dali_backup(current, plan.name, data, label=S.DALI_PERSIST_LABEL)


def prepare_workspace_operation(
    device: Device,
    action: str,
    section: str,
    index: int = 0,
    row: int = 0,
    value: int = 0,
    secondary: int = 0,
    bank: int = 2,
    lock: bool = False,
    *,
    test_group: int | None = None,
) -> PreparedDaliOperation:
    """Use one validated operation plan for the GUI and MCP workspace."""
    from dataclasses import asdict
    from xml.etree import ElementTree as ET

    from editor_gui.plugin_blob_readers.dali_backup import e64pro
    from editor_gui.plugins.project.strings import S
    from xknxeditor.dali.configuration import (
        COLOR_FUNCTIONS,
        COLOR_TYPES,
        effects_from_xml,
        input_inventory_from_xml,
        inputs_from_xml,
        scenes_from_xml,
        templates_from_xml,
    )
    from xknxeditor.dali.configuration_readback import (
        merge_effects,
        merge_inputs,
        merge_scenes,
        merge_templates,
    )
    from xknxeditor.dali.connection import DaliCommissioningError
    from xknxeditor.dali.effects import effect_plan
    from xknxeditor.dali.service_operations import (
        NAME_TABLES,
        encode_names,
        validate_maintenance,
    )
    from xknxeditor.dali.verification import verify_scenes, verify_templates

    entry = e64pro.backup(device)
    if entry is None:
        raise ValueError("No saved DALI configuration")
    expected = entry[1]
    root = e64pro.document(expected)
    if test_group is not None and (
        action != "test" or section != "templates" or not 0 <= test_group < 16
    ):
        raise ValueError(S.E64_INVALID_SELECTION)
    if action == "replace":
        replacement = prepare_dali_replacement(device)

        async def execute_replacement(
            commissioner: MdtDaliCommissioner,
        ) -> dict[str, Any]:
            _, data = await replacement.execute(commissioner)
            return {"configuration": data}

        return PreparedDaliOperation(expected, execute_replacement, True)
    if action in ("read", "write", "test", "start", "stop"):
        if section not in (
            "scenes",
            "effects",
            "templates",
            "inputs",
        ) or not 0 <= index < (64 if section == "inputs" else 16):
            raise ValueError("Invalid DALI selection")
        if action in ("start", "stop") and section != "effects":
            raise ValueError("Start/stop requires an effect")
        if action == "test" and section not in ("scenes", "templates"):
            raise ValueError("Test requires a scene or template action")
    elif action not in (
        "discover",
        "identify",
        "stop_identify",
        "function",
        "duration",
        "battery",
        "read_calibration",
        "set_calibration",
        "save_calibration",
        "read_memory",
        "write_memory",
        "read_names",
        "write_names",
        "read_motion",
        "inhibit",
        "replace",
        "restore",
    ):
        raise ValueError("Unknown DALI operation")
    if action in ("identify", "stop_identify") and not 0 <= index < 64:
        raise ValueError("Invalid input address")
    if action not in (
        "read",
        "write",
        "test",
        "start",
        "stop",
        "discover",
        "identify",
        "stop_identify",
        "restore",
    ):
        validate_maintenance(action, index, value, secondary, bank, section)
    restore_plan = None
    if action == "restore":
        from xknxeditor.dali.restore import prepare_restore

        restore_plan = prepare_restore(expected)
    if action in ("set_calibration", "save_calibration"):
        controller = root.find(f"mds/MdInput[@index='{index}']")
        if (
            controller is None
            or controller.get("wclc") != "2"
            or controller.get("wclbct") != "1"
        ):
            raise ValueError(
                "Selected motion controller does not use constant light control"
            )
    scenes = scenes_from_xml(root) if section == "scenes" and action == "write" else []
    effects = (
        effects_from_xml(root) if section == "effects" and action == "write" else []
    )
    templates = (
        templates_from_xml(root)
        if section == "templates" and action in ("write", "test")
        else []
    )
    inputs = (
        inputs_from_xml(root) if section == "inputs" and action == "write" else None
    )
    input_inventory = input_inventory_from_xml(root) if inputs is not None else None
    names = None
    if effects:
        effect_plan(effects)
    if action == "test" and section == "templates":
        template = next(t for t in templates if t.index == index)
        if not 0 <= row < len(template.commands):
            raise ValueError("Invalid template row")
        command = template.commands[row]
        if test_group is not None:
            if command.func in (17, 18):
                raise ValueError(S.E64_UNSUPPORTED_OPERATION)
            groups = root.findall(f"groups/Group[@index='{test_group}']")
            if len(groups) != 1:
                raise ValueError(S.E64_INVALID_SELECTION)
            modes = COLOR_TYPES.get(int(groups[0].get("ctype", "0")), ())
            if (
                modes
                and command.func in COLOR_FUNCTIONS
                and COLOR_FUNCTIONS[command.func] not in modes
            ):
                raise ValueError(S.E64_INVALID_COLOR)
        elif command.func not in (17, 18) and not template.groups and not template.ecgs:
            raise ValueError(S.E64_INVALID_SELECTION)
    if action == "write_names":
        if section not in NAME_TABLES:
            raise ValueError("Invalid name category")
        names = [
            next(
                e.get("description", "")
                for e in root.findall(section + "/*")
                if e.get("index") == str(i)
            )
            for i in range(NAME_TABLES[section][1])
        ]
        encode_names(section, names)

    async def execute(commissioner: MdtDaliCommissioner) -> dict[str, Any]:
        data: bytes | None = None
        result: dict[str, Any] = {}
        if action == "restore" and restore_plan is not None:
            from xknxeditor.dali.restore import restore

            await restore(commissioner, restore_plan)
            updated = ET.fromstring(expected)
            for part in ("scenes", "effects", "templates"):
                for slot in updated.findall(part + "/*"):
                    slot.set("isDirty", "False")
            data = ET.tostring(updated, encoding="utf-16le")
        elif action == "read":
            if section == "scenes":
                rows = await commissioner.read_scenes()
                data = merge_scenes(expected, rows)
            elif section == "effects":
                data = merge_effects(expected, await commissioner.read_effects())
            elif section == "templates":
                data = merge_templates(expected, await commissioner.read_templates())
            else:
                devices = await commissioner.read_inputs()
                data = merge_inputs(expected, devices)
                result = {"devices": [asdict(d) for d in devices]}
        elif action == "write":
            if section == "scenes":
                await commissioner.write_scenes(scenes)
                verify_scenes(scenes, await commissioner.read_scenes())
            elif section == "effects":
                await commissioner.write_effects(effects)
                if await commissioner.read_effects() != sorted(
                    effects, key=lambda e: e.index
                ):
                    raise DaliCommissioningError("effect readback does not match")
            elif section == "templates":
                await commissioner.write_templates(templates)
                verify_templates(templates, await commissioner.read_templates())
            elif inputs is not None:
                devices = await commissioner.write_inputs(
                    inputs, expected_inventory=input_inventory
                )
                result = {"devices": [asdict(d) for d in devices]}
                data = merge_inputs(expected, devices)
            if section != "inputs":
                updated = ET.fromstring(expected)
                for slot in updated.findall(section + "/*"):
                    slot.set("isDirty", "False")
                data = ET.tostring(updated, encoding="utf-16le")
        elif action == "test":
            if section == "scenes":
                await commissioner.test_scene(index)
            else:
                await commissioner.test_template_command(
                    next(t for t in templates if t.index == index),
                    row,
                    group=test_group,
                )
        elif action in ("start", "stop"):
            await commissioner.set_effect_running(index, action == "start")
        elif action == "discover":
            devices = await commissioner.discover_inputs()
            data = merge_inputs(expected, devices)
            result = {"devices": [asdict(d) for d in devices]}
        elif action in ("identify", "stop_identify"):
            await commissioner.identify_input(index, action == "identify")
        else:
            result = await commissioner.maintenance(
                action, index, value, secondary, bank, lock, section, names
            )
            if action in ("read_calibration", "save_calibration"):
                updated = ET.fromstring(expected)
                controller = updated.find(f"mds/MdInput[@index='{index}']")
                if controller is None:
                    raise ValueError("Missing motion controller")
                for prefix, suffix in (("minimum", "min"), ("maximum", "max")):
                    for attribute, key in (
                        ("v", "level"),
                        ("m", "lux"),
                        ("b", "sensor"),
                    ):
                        controller.set(
                            attribute + suffix, str(result[prefix + "_" + key])
                        )
                data = ET.tostring(updated, encoding="utf-16le")
            if action == "read_names":
                updated = ET.fromstring(expected)
                for i, name in enumerate(result["names"]):
                    next(
                        e
                        for e in updated.findall(section + "/*")
                        if e.get("index") == str(i)
                    ).set("description", name)
                data = ET.tostring(updated, encoding="utf-16le")
        if data is not None:
            result["configuration"] = data
        return result

    return PreparedDaliOperation(
        expected,
        execute,
        action not in ("read", "read_calibration", "read_names", "read_motion"),
    )


def finish_workspace_operation(
    project: ProjectService,
    device: Device,
    plan: PreparedDaliOperation,
    generation: int,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Apply readback on the project thread after a successful bus operation."""
    from editor_gui.plugins.project.strings import S

    if project.generation != generation:
        raise ValueError(S.E64_CONFIGURATION_CHANGED)
    current = project.find_device_by_node_id(device.node_id)
    if current is None:
        raise ValueError(S.E64_CONFIGURATION_CHANGED)
    GuardedDaliOperation(
        project, device, plan.expected, generation, plan.execute
    ).validate_device(current)
    result = dict(result)
    data = result.pop("configuration", None)
    if data is not None:
        project.apply_e64pro_readback(device, plan.expected, data)
    return result
