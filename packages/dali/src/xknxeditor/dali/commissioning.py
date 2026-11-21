"""High-level MDT DALI commissioning flows over an :class:`MdtDaliConnection`.

Implements: read-only bus scan (P1), identify/blink (P2), group/ECG assignment download (P3), scene
write (P4), new/post installation (P5, DALI bus scan + short-address assignment), colour-control
templates/effects, and full-config restore (EVG_STATIC + EVG_PROP + commission + scenes + templates).
Every write / installation flow should be preceded by a scan and gated behind a user confirm in
the GUI.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xknxeditor.dali.configuration import Configuration
    from xknxeditor.dali.inputs import InputDevice, InputIdentity
    from xknxeditor.dali.report import DiagnosticReport

from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.model import (
    GROUP_MISSING,
    GROUP_SINGLE,
    GROUP_UNASSIGNED,
    MAX_ECG,
    MAX_GROUP,
    MAX_SCENES,
    MAX_TEMPLATES,
    MAX_TPL_COMMANDS,
    SHORT_ADDR_UNASSIGNED,
    DaliBusState,
    EcgAssignment,
    EcgState,
    Effect,
    GroupState,
    Scene,
    SceneValue,
    Template,
    TemplateCommand,
    group_present,
)
from xknxeditor.dali.properties import (
    BLINK_AUTO,
    BLINK_STOP,
    DeviceCommand,
    PropertyType,
    element_size_for,
)
from xknxeditor.dali.structs import (
    SCENE_TERMINATOR,
    TEMPLATE_TERMINATOR,
    Command,
    CommissionSync,
    EcgProp,
    EcgStatic,
    EcgStatus,
    GroupDyn,
    GroupFailure,
    GroupStatus,
    SceneItemValue,
    TemplateProp,
    decode_table,
    encode_table,
    encode_template_members,
)

_MAX_SCENE_VALUE_RECORDS = MAX_SCENES * MAX_ECG


class MdtDaliCommissioner:
    """DALI bus commissioning for one channel of an MDT gateway."""

    def __init__(self, connection: MdtDaliConnection) -> None:
        self._c = connection

    @property
    def connection(self) -> MdtDaliConnection:
        return self._c

    async def read_report(self) -> DiagnosticReport:
        """Read failure counters and saved emergency-light test results."""
        from xknxeditor.dali.report import read_report

        return await read_report(self._c)

    async def maintenance(
        self,
        action: str,
        index: int = 0,
        value: int = 0,
        secondary: int = 0,
        bank: int = 2,
        lock: bool = False,
        section: str = "ecgs",
        names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a validated maintenance operation and return its diagnostic values."""
        from xknxeditor.dali.service_operations import maintenance

        return await maintenance(
            self._c, action, index, value, secondary, bank, lock, section, names
        )

    async def read_inputs(self) -> tuple[InputDevice, ...]:
        from xknxeditor.dali.inputs import read_inputs

        return await read_inputs(self._c)

    async def write_inputs(
        self,
        assignments: bytes,
        *,
        expected_inventory: tuple[InputIdentity, ...] | None = None,
    ) -> tuple[InputDevice, ...]:
        from xknxeditor.dali.inputs import write_inputs

        return await write_inputs(
            self._c, assignments, expected_inventory=expected_inventory
        )

    async def discover_inputs(self, *, reset: bool = False) -> tuple[InputDevice, ...]:
        from xknxeditor.dali.effects import require_effects

        require_effects(self._c)
        await self._c.run_command(
            DeviceCommand.INPUT_NEW_INSTALLATION
            if reset
            else DeviceCommand.INPUT_POST_INSTALLATION,
            timeout=300,
        )
        return await self.read_inputs()

    async def identify_input(self, address: int, start: bool = True) -> None:
        from xknxeditor.dali.effects import require_effects

        require_effects(self._c)
        if not 0 <= address < 64:
            raise DaliCommissioningError("invalid input address")
        await self._c.run_command(
            DeviceCommand.INPUT_IDENTIFY, address, int(start), timeout=10
        )

    async def test_scene(self, index: int) -> None:
        if not 0 <= index < MAX_SCENES:
            raise DaliCommissioningError("invalid scene index")
        await self._c.send_command(DeviceCommand.TEST_SCENE, index)

    async def set_effect_running(self, index: int, running: bool) -> None:
        from xknxeditor.dali.effects import require_effects

        require_effects(self._c)
        if not 0 <= index < 16:
            raise DaliCommissioningError("invalid effect index")
        await self._c.send_command(DeviceCommand.START_STOP_EFFECT, index, int(running))

    async def test_template_command(
        self, template: Template, row: int, *, group: int | None = None
    ) -> None:
        """Run one saved command on its members or a single group."""
        from xknxeditor.dali.structs import ExtCommand

        self._template_plan([template])
        if not 0 <= row < len(template.commands):
            raise DaliCommissioningError("invalid template command row")
        command = template.commands[row]
        if group is not None:
            if not 0 <= group < MAX_GROUP:
                raise DaliCommissioningError("invalid test group")
            if command.func in (17, 18):
                raise DaliCommissioningError(
                    "scene and effect calls cannot target a group"
                )
            await self._c.write_table(
                PropertyType.EXT_COMMAND,
                ExtCommand(command.func, command.value, 2, group).encode(),
                1,
            )
            return
        if command.func == 17:
            await self.test_scene(command.value[0])
            return
        if command.func == 18:
            await self.set_effect_running(command.value[0], bool(command.value[1]))
            return
        if not template.groups and not template.ecgs:
            raise DaliCommissioningError("template has no targets")
        await self._c.write_table(
            PropertyType.COLOUR_C_ASSIGN,
            encode_template_members(template.groups, template.ecgs),
            1,
            start_index=template.index + 1,
        )
        await self._c.write_table(
            PropertyType.EXT_COMMAND,
            ExtCommand(command.func, command.value, 0, template.index).encode(),
            1,
        )

    async def write_configuration(self, configuration: Configuration) -> DaliBusState:
        """Apply a complete saved configuration, preparing every table before the first write."""
        from xknxeditor.dali.configuration import validate_configuration
        from xknxeditor.dali.effects import effect_plan, require_effects
        from xknxeditor.dali.inputs import check_input_inventory

        validate_configuration(configuration)
        self._assignment_plan(configuration.assignments)
        template_plan = self._template_plan(configuration.templates)
        if configuration.effects:
            require_effects(self._c)
            effect_plan(configuration.effects)
        if configuration.inputs is not None:
            require_effects(self._c)
            await check_input_inventory(
                self._c, configuration.inputs, configuration.input_inventory
            )
        scene_plan = await self._scene_plan(configuration.scenes)
        if configuration.assignments:
            await self.commission(configuration.assignments)
        await self._write_scene_plan(scene_plan)
        if configuration.effects:
            await self.write_effects(configuration.effects)
        await self._write_template_plan(template_plan)
        if configuration.inputs is not None:
            await self.write_inputs(
                configuration.inputs, expected_inventory=configuration.input_inventory
            )
        from xknxeditor.dali.verification import verify_scenes, verify_templates

        verify_scenes(configuration.scenes, await self.read_scenes())
        if configuration.effects and await self.read_effects() != sorted(
            configuration.effects, key=lambda e: e.index
        ):
            raise DaliCommissioningError(
                "effect readback does not match programmed configuration"
            )
        verify_templates(configuration.templates, await self.read_templates())
        return await self.scan()

    # --- P1: read-only scan ------------------------------------------------

    async def scan(self) -> DaliBusState:
        """Read the full bus state (firmware, per-slot ECG props/status/static, group data)."""
        firmware = await self._c.read_firmware()
        props = decode_table(
            await self._c.read_table(PropertyType.EVG_PROP, MAX_ECG), 4
        )
        status = decode_table(
            await self._c.read_table(PropertyType.EVG_STATUS, MAX_ECG), 5
        )
        static = decode_table(
            await self._c.read_table(PropertyType.EVG_STATIC, MAX_ECG), 8
        )
        ecgs: list[EcgState] = []
        for slot in range(MAX_ECG):
            p = EcgProp.decode(props[slot]) if slot < len(props) else None
            if p is None:
                continue
            st = EcgStatic.decode(static[slot]) if slot < len(static) else None
            stat = EcgStatus.decode(status[slot]) if slot < len(status) else None
            ecgs.append(
                EcgState(
                    slot=slot,
                    present=group_present(p.group_index),
                    ecg_type=p.ecg_type,
                    sub_type=p.sub_type,
                    group_index=p.group_index,
                    ets_ecg_index=p.ets_ecg_index,
                    long_address=st.long_address if st else 0,
                    min_value=st.min_value if st else 0,
                    min_color_temp=st.min_color_temp if st else 0,
                    max_color_temp=st.max_color_temp if st else 0,
                    alarm=stat.alarm if stat else 0,
                )
            )
        groups = await self._read_groups()
        return DaliBusState(
            channel=self._c.channel, firmware=firmware, ecgs=ecgs, groups=groups
        )

    async def _read_groups(self) -> list[GroupState]:
        dyn = decode_table(
            await self._c.read_table(PropertyType.GROUP_DYN, MAX_GROUP), 6
        )
        status = decode_table(
            await self._c.read_table(PropertyType.GROUP_STATUS, MAX_GROUP),
            element_size_for(PropertyType.GROUP_STATUS, self._c.profile),
        )
        failure = decode_table(
            await self._c.read_table(PropertyType.GROUP_FAILURE, MAX_GROUP), 6
        )
        groups: list[GroupState] = []
        for i in range(MAX_GROUP):
            d = GroupDyn.decode(dyn[i]) if i < len(dyn) else GroupDyn(0, 0)
            s = GroupStatus.decode(status[i]) if i < len(status) else None
            f = GroupFailure.decode(failure[i]) if i < len(failure) else None
            groups.append(
                GroupState(
                    index=i,
                    run_hours=d.run_hours,
                    run_seconds=d.run_seconds,
                    knx_value=s.knx_value if s else 0,
                    ecg_count=f.ecg_count if f else 0,
                    lamp_failures=f.lamp_fail_count if f else 0,
                    ecg_failures=f.ecg_fail_count if f else 0,
                )
            )
        return groups

    # --- P2: identify ------------------------------------------------------

    async def switch_ecg(self, slot: int, on: bool) -> None:
        """Switch a single ECG (by physical slot) fully on/off."""
        if not 0 <= slot < MAX_ECG:
            raise DaliCommissioningError("invalid ECG slot")
        await self._c.send_command(DeviceCommand.SET_ECG_VALUE, slot, 0xFF if on else 0)

    async def blink_ecg(self, slot: int, start: bool = True) -> None:
        """Start/stop auto-blink on a single ECG (to physically identify it)."""
        if not 0 <= slot < MAX_ECG:
            raise DaliCommissioningError("invalid ECG slot")
        param1 = slot | (BLINK_AUTO if start else BLINK_STOP)
        await self._c.send_command(DeviceCommand.BLINK, param1, 0)

    async def switch_group(self, group: int, on: bool) -> None:
        if not 0 <= group < MAX_GROUP:
            raise DaliCommissioningError("invalid DALI group")
        await self._c.send_command(
            DeviceCommand.SET_GROUP_VALUE, group, 0xFF if on else 0
        )

    async def blink_group(self, group: int, start: bool = True) -> None:
        if not 0 <= group < MAX_GROUP:
            raise DaliCommissioningError("invalid DALI group")
        param1 = group | (0 if start else BLINK_STOP)
        await self._c.send_command(DeviceCommand.BLINK, param1, 0)

    async def broadcast(self, on: bool) -> None:
        """Switch all ECGs on/off (broadcast)."""
        await self._c.send_command(DeviceCommand.SET_ALL_ECG, 0xFF if on else 0, 0)

    # --- P3: assignment download ------------------------------------------

    async def commission(self, assignments: Sequence[EcgAssignment]) -> bool:
        """Write the full 64-slot group/ECG assignment table, apply it, and verify.

        The ComissionSync table is always the complete 64 slots (PLUGIN_SYNC applies every slot), so
        it is first seeded from the device's CURRENT mapping (a fresh EVG_PROP read) and the caller's
        ``assignments`` are overlaid on top — otherwise unlisted slots would be silently wiped to
        unassigned. Returns True when the device read-back matches the requested (ets, group) mapping.

        Raises :class:`DaliCommissioningError` if the post-write verification fails — a
        silent ``False`` return would be swallowed by the service-layer completion
        callback and the user would see "DALI commissioning done" when it actually failed.
        """
        by_slot = self._assignment_plan(assignments)
        current = decode_table(
            await self._c.read_table(PropertyType.EVG_PROP, MAX_ECG), 4
        )
        reassigned = {
            a.ets_index for a in assignments if a.ets_index != SHORT_ADDR_UNASSIGNED
        }
        for slot, record in enumerate(current):
            if (
                slot not in by_slot
                and record[2] != GROUP_MISSING
                and record[3] in reassigned
            ):
                by_slot[slot] = EcgAssignment(
                    slot, SHORT_ADDR_UNASSIGNED, GROUP_UNASSIGNED
                )
        elements: list[bytes] = []
        for slot in range(MAX_ECG):
            if slot in by_slot:
                sync = CommissionSync(
                    by_slot[slot].ets_index, by_slot[slot].group_index
                )
            elif slot < len(current):
                p = EcgProp.decode(current[slot])
                sync = CommissionSync(p.ets_ecg_index, p.group_index)
            else:
                sync = CommissionSync(slot, GROUP_UNASSIGNED)
            elements.append(sync.encode())
        await self._c.write_table(
            PropertyType.COMMISSION_SYNC, encode_table(elements), MAX_ECG
        )
        await self._c.run_command(DeviceCommand.PLUGIN_SYNC, timeout=180.0)
        if not await self._verify(by_slot):
            raise DaliCommissioningError(
                "commissioning verify mismatch: device read-back does not match the "
                "requested (ets, group) assignments"
            )
        return True

    @staticmethod
    def _assignment_plan(
        assignments: Sequence[EcgAssignment],
    ) -> dict[int, EcgAssignment]:
        by_slot = {a.slot: a for a in assignments}
        assigned = [
            a.ets_index for a in assignments if a.ets_index != SHORT_ADDR_UNASSIGNED
        ]
        if (
            len(by_slot) != len(assignments)
            or len(set(assigned)) != len(assigned)
            or any(
                not 0 <= a.slot < MAX_ECG
                or a.ets_index not in (*range(MAX_ECG), 255)
                or a.group_index
                not in (
                    *range(MAX_GROUP),
                    GROUP_SINGLE,
                    GROUP_UNASSIGNED,
                    GROUP_MISSING,
                )
                for a in assignments
            )
        ):
            raise DaliCommissioningError("invalid or duplicate ECG assignment")
        return by_slot

    async def _verify(self, by_slot: dict[int, EcgAssignment]) -> bool:
        """Re-read EVG_PROP and confirm every requested slot's ets-index AND group match."""
        props = decode_table(
            await self._c.read_table(PropertyType.EVG_PROP, MAX_ECG), 4
        )
        for slot, want in by_slot.items():
            if slot >= len(props):
                return False
            got = EcgProp.decode(props[slot])
            if (
                got.group_index != want.group_index
                or got.ets_ecg_index != want.ets_index
            ):
                return False
        return True

    # --- P4: scenes --------------------------------------------------------

    async def read_scenes(self) -> list[Scene]:
        """Read complete scene tables, retaining every target and colour."""
        fades = await self._c.read_table(PropertyType.SCENE_ASSIGN, MAX_SCENES)
        map_size = await self._c.read_table_size(PropertyType.SCENE_KNX_MAP)
        if map_size not in (None, 0, MAX_SCENES):
            raise DaliCommissioningError("invalid KNX scene map size")
        mapping = (
            await self._c.read_table(PropertyType.SCENE_KNX_MAP, MAX_SCENES)
            if map_size
            else bytes(MAX_SCENES)
        )
        scenes = [
            Scene(index, fade, mapping[index], knx_mapping_available=bool(map_size))
            for index, fade in enumerate(fades)
        ]
        data = await self._c.read_table(
            PropertyType.SCENE_GROUP_VALUE, _MAX_SCENE_VALUE_RECORDS, terminator=b"\xff"
        )
        for record in decode_table(data, 8):
            if record[0] == 255:
                break
            if record[0] >= MAX_SCENES or record[1] >= MAX_GROUP + MAX_ECG:
                raise DaliCommissioningError("invalid scene index or target received")
            scenes[record[0]].values.append(
                SceneValue(record[0], record[1], record[2], record[3:7], record[7])
            )
        return scenes

    async def write_scenes(
        self, scenes: Sequence[Scene], *, levels_only: bool = False
    ) -> None:
        """Merge-write scene fade times, per-target values, and the KNX scene map, then apply.

        Reads the current scene tables first and only overwrites the slots touched by ``scenes``,
        so programming a single edited scene does not erase fade times, KNX-scene numbers or value
        records of the untouched ones. With ``levels_only``, update only the supplied targets'
        levels, preserving all colours, other targets, fade times and KNX scene mappings.
        """
        if not scenes:
            return
        await self._write_scene_plan(
            await self._scene_plan(scenes, levels_only=levels_only)
        )

    async def _scene_plan(
        self, scenes: Sequence[Scene], *, levels_only: bool = False
    ) -> tuple[bytes, list[bytes], bytearray | None, bool]:
        touched = {s.index for s in scenes}
        if len(touched) != len(scenes):
            raise DaliCommissioningError("duplicate scene index")
        for scene in scenes:
            if not (
                0 <= scene.index < MAX_SCENES
                and 0 <= scene.fade_time <= 255
                and 0 <= scene.knx_scene <= 64
            ):
                raise DaliCommissioningError(
                    "invalid scene index, fade time or KNX scene"
                )
            targets = {v.target_index for v in scene.values}
            if len(targets) != len(scene.values):
                raise DaliCommissioningError("duplicate scene target")
            for value in scene.values:
                if not (
                    value.scene == scene.index
                    and 0 <= value.target_index < MAX_GROUP + MAX_ECG
                    and 0 <= value.level <= 255
                    and len(value.color) == 4
                    and 0 <= value.flags <= 255
                ):
                    raise DaliCommissioningError("invalid scene value")

        assign = (
            bytearray(await self._c.read_table(PropertyType.SCENE_ASSIGN, MAX_SCENES))
            if not levels_only
            else bytearray()
        )
        current_values = await self._c.read_table(
            PropertyType.SCENE_GROUP_VALUE,
            _MAX_SCENE_VALUE_RECORDS,
            terminator=SCENE_TERMINATOR[:1],
        )
        records: list[bytes] = []
        levels = {(s.index, v.target_index): v.level for s in scenes for v in s.values}
        for record in decode_table(current_values, 8):
            if record[0] == SCENE_TERMINATOR[0]:
                break
            if levels_only:
                level = levels.pop((record[0], record[1]), None)
                if level is not None:
                    record = (
                        record[:2]
                        + bytes((level,))
                        + record[3:7]
                        + bytes((record[7] & ~2,))
                    )
                records.append(record)
            elif record[0] not in touched:
                records.append(record)
        for scene in scenes:
            if levels_only:
                records.extend(
                    SceneItemValue(
                        scene.index, v.target_index, v.level, flags=1
                    ).encode()
                    for v in scene.values
                    if (scene.index, v.target_index) in levels
                )
                continue
            assign[scene.index] = scene.fade_time
            records.extend(
                SceneItemValue(
                    scene.index, v.target_index, v.level, v.color, v.flags
                ).encode()
                for v in sorted(scene.values, key=lambda v: v.target_index)
            )
        if len(records) > _MAX_SCENE_VALUE_RECORDS:
            raise DaliCommissioningError("scene table capacity exceeded")
        if len(records) < _MAX_SCENE_VALUE_RECORDS:
            records.append(SCENE_TERMINATOR)
        map_size = (
            None
            if levels_only
            else await self._c.read_table_size(PropertyType.SCENE_KNX_MAP)
        )
        knx_map: bytearray | None = None
        if map_size is not None:
            if map_size not in (0, MAX_SCENES):
                raise DaliCommissioningError("invalid KNX scene map size")
            knx_map = (
                bytearray(MAX_SCENES)
                if map_size == 0
                else bytearray(
                    await self._c.read_table(PropertyType.SCENE_KNX_MAP, MAX_SCENES)
                )
            )
            for scene in scenes:
                knx_map[scene.index] = scene.knx_scene

        return bytes(assign), records, knx_map, map_size == 0

    async def _write_scene_plan(
        self, plan: tuple[bytes, list[bytes], bytearray | None, bool]
    ) -> None:
        assign, records, knx_map, initialize_map = plan
        if knx_map is not None:
            if initialize_map:
                await self._c.write_table_size(PropertyType.SCENE_KNX_MAP, MAX_SCENES)
            await self._c.write_table(
                PropertyType.SCENE_KNX_MAP, bytes(knx_map), MAX_SCENES
            )
            await asyncio.sleep(0.5)
        if assign:
            await self._c.write_table(
                PropertyType.SCENE_ASSIGN, bytes(assign), MAX_SCENES
            )
            await asyncio.sleep(0.5)
        await self._c.write_table(
            PropertyType.SCENE_GROUP_VALUE, encode_table(records), len(records)
        )
        await self._c.run_command(DeviceCommand.SCENE_SYNC, timeout=180.0)

    # --- colour-control templates / effects --------------------------------

    async def read_effects(self) -> list[Effect]:
        from xknxeditor.dali.effects import read_effects

        return await read_effects(self._c)

    async def write_effects(self, effects: Sequence[Effect]) -> None:
        from xknxeditor.dali.effects import write_effects

        await write_effects(self._c, effects)

    async def read_templates(self) -> list[Template]:
        from xknxeditor.dali.effects import read_templates

        templates = await read_templates(self._c)
        self._template_plan(templates)
        return templates

    async def write_templates(self, templates: Sequence[Template]) -> None:
        """Write the template tables: COLOUR_C_EVENT (timed commands) + COLOUR_C_ASSIGN (members).

        Commands across all templates are flattened and ordered by (hour, minute, func).
        Invalid or oversized plans fail before any write; non-full tables get a terminator. Members are a per-template bitmap over
        the full 16-template table. Empty ``templates`` clears both tables.
        """
        await self._write_template_plan(self._template_plan(templates))

    def _template_plan(
        self, templates: Sequence[Template]
    ) -> tuple[list[bytes], list[bytes]]:
        commands: list[tuple[Template, TemplateCommand]] = [
            (t, c) for t in templates for c in t.commands
        ]
        commands.sort(key=lambda tc: (tc[1].hour, tc[1].minute, tc[1].func))
        if len(commands) > MAX_TPL_COMMANDS:
            raise DaliCommissioningError("template command capacity exceeded")
        if len({t.index for t in templates}) != len(templates):
            raise DaliCommissioningError("duplicate template index")
        extended = (
            self._c.profile is not None and self._c.profile.supports_extended_templates
        )
        if not extended and any(
            not t.reset_on_midnight or any(c.func == 18 for c in t.commands)
            for t in templates
        ):
            raise DaliCommissioningError(
                "template effect calls and midnight-reset control are unsupported by this profile"
            )
        for template in templates:
            if not (
                0 <= template.index < MAX_TEMPLATES
                and 0 <= template.mode <= 2
                and all(0 <= g < MAX_GROUP for g in template.groups)
                and all(0 <= e < MAX_ECG for e in template.ecgs)
            ):
                raise DaliCommissioningError("invalid template index, mode or member")
        for _, command in commands:
            if not (
                0 <= command.hour <= 23
                and 0 <= command.minute <= 59
                and 0 <= command.weekdays <= 127
                and len(command.value) == 4
                and 0 <= command.fade_time <= 255
                and command.func in (*range(1, 10), 16, 17, 18)
            ):
                raise DaliCommissioningError("invalid template command")
        events = [
            TemplateProp(
                t.index,
                c.func,
                c.value,
                c.fade_time,
                c.weekdays,
                c.hour,
                c.minute,
                t.mode,
                t.overwritable,
                t.reset_on_midnight,
            ).encode()
            for t, c in commands
        ]
        if len(commands) < MAX_TPL_COMMANDS:
            events.append(TEMPLATE_TERMINATOR)
        by_index = {t.index: t for t in templates}
        members = [
            encode_template_members(by_index[i].groups, by_index[i].ecgs)
            if i in by_index
            else b"\x00" * 10
            for i in range(MAX_TEMPLATES)
        ]
        return events, members

    async def _write_template_plan(self, plan: tuple[list[bytes], list[bytes]]) -> None:
        events, members = plan
        await self._c.write_table(
            PropertyType.COLOUR_C_EVENT, encode_table(events), len(events)
        )
        await asyncio.sleep(0.5)
        await self._c.write_table(
            PropertyType.COLOUR_C_ASSIGN, encode_table(members), MAX_TEMPLATES
        )
        await self._c.run_command(DeviceCommand.COLOR_SYNC, timeout=180.0)

    # --- config table writes / full-config restore ------------------------

    async def write_ecg_static(self, entries: Sequence[EcgStatic]) -> None:
        """Write the per-slot EVG_STATIC table (long address, min value, colour-temp bounds).

        ``entries`` is indexed by physical slot; shorter lists are padded to the full slot count
        with zeroed records (the DCA always writes the complete table).
        """
        await self._c.write_table(
            PropertyType.EVG_STATIC, self._static_data(entries), MAX_ECG
        )

    @staticmethod
    def _static_data(entries: Sequence[EcgStatic]) -> bytes:
        if len(entries) > MAX_ECG or any(
            not (
                0 <= e.long_address <= 0xFFFFFF
                and 0 <= e.min_value <= 255
                and 0 <= e.min_color_temp <= 65535
                and 0 <= e.max_color_temp <= 65535
            )
            for e in entries
        ):
            raise DaliCommissioningError("invalid ECG static table")
        elements = [
            (entries[slot] if slot < len(entries) else EcgStatic(0, 0, 0, 0)).encode()
            for slot in range(MAX_ECG)
        ]
        return encode_table(elements)

    async def write_ecg_props(self, entries: Sequence[EcgProp]) -> None:
        """Write the per-slot EVG_PROP table (type, subtype, group index, ETS ECG number).

        Shorter lists are padded to the full slot count with unassigned records.
        """
        await self._c.write_table(
            PropertyType.EVG_PROP, self._props_data(entries), MAX_ECG
        )

    @staticmethod
    def _props_data(entries: Sequence[EcgProp]) -> bytes:
        if len(entries) > MAX_ECG or any(
            not (
                -1 <= e.ecg_type <= 255
                and 0 <= e.sub_type <= 255
                and 0 <= e.group_index <= 255
                and 0 <= e.ets_ecg_index <= 255
            )
            for e in entries
        ):
            raise DaliCommissioningError("invalid ECG property table")
        elements = [
            (
                entries[slot]
                if slot < len(entries)
                else EcgProp(0, 0, GROUP_UNASSIGNED, slot)
            ).encode()
            for slot in range(MAX_ECG)
        ]
        return encode_table(elements)

    async def restore(
        self,
        *,
        static: Sequence[EcgStatic],
        props: Sequence[EcgProp],
        assignments: Sequence[EcgAssignment],
        scenes: Sequence[Scene],
        templates: Sequence[Template] = (),
    ) -> bool:
        """Write a full channel configuration to the device, mirroring the DCA restore order:
        EVG_STATIC, EVG_PROP, group/ECG commissioning (apply + verify), scenes, then templates.

        Returns the commissioning verify result. Empty ``templates`` clears the template tables.
        """
        static_data = self._static_data(static)
        props_data = self._props_data(props)
        self._assignment_plan(assignments)
        template_plan = self._template_plan(templates)
        scene_plan = await self._scene_plan(scenes) if scenes else None
        await self._c.write_table(PropertyType.EVG_STATIC, static_data, MAX_ECG)
        await self._c.write_table(PropertyType.EVG_PROP, props_data, MAX_ECG)
        ok = await self.commission(assignments)
        if scene_plan is not None:
            await self._write_scene_plan(scene_plan)
        await self._write_template_plan(template_plan)
        return ok

    # --- P5: new / post installation (bus renumbering — gate behind a confirm) ---

    async def replace_ecg(self) -> DaliBusState:
        """Replace a failed ballast and read its resulting physical inventory."""
        if self._c.profile is None or self._c.profile.key not in (
            "mdt",
            "theben",
            "ipas",
        ):
            raise DaliCommissioningError("replacement is unsupported by this profile")
        try:
            await self._c.run_command(DeviceCommand.EASY_REPLACEMENT, timeout=60)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self.abort()
            raise
        return await self.scan()

    async def new_installation(
        self,
        *,
        pre_assign_group: int = 0,
        timeout: float = 300.0,
        interval: float = 1.0,
        on_progress: Callable[[Command], None] | None = None,
    ) -> DaliBusState:
        """Full DALI bus scan with short-address (re)assignment, then read back the new state.

        Wipes and re-numbers every ballast on the bus (destructive) — always confirm with the user
        first. ``pre_assign_group`` optionally pre-assigns found ECGs to a group (firmware permitting).
        ``on_progress`` receives the live readback whose ``param1``/``param2`` are the found counters.
        """
        await self._c.run_command(
            DeviceCommand.NEW_INSTALLATION,
            0,
            pre_assign_group,
            timeout=timeout,
            interval=interval,
            on_progress=on_progress,
        )
        return await self.scan()

    async def post_installation(
        self,
        *,
        flags: int = 0,
        pre_assign_group: int = 0,
        timeout: float = 300.0,
        interval: float = 1.0,
        on_progress: Callable[[Command], None] | None = None,
    ) -> DaliBusState:
        """Add newly-found ECGs without renumbering the existing ones, then read back.

        ``flags`` is the raw MDT option byte (keep-unknown / delete-missing short addresses);
        ``on_progress`` readbacks carry found (param1) and removed (param2) counters.
        """
        await self._c.run_command(
            DeviceCommand.POST_INSTALLATION,
            flags,
            pre_assign_group,
            timeout=timeout,
            interval=interval,
            on_progress=on_progress,
        )
        return await self.scan()

    async def abort(self) -> None:
        """Abort a running installation/command."""
        await self._c.send_command(DeviceCommand.ABORT)


__all__ = ["MdtDaliCommissioner"]
