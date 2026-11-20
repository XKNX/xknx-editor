"""Effect tables and readback of scheduled template commands."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.model import Effect, EffectStep, Template, TemplateCommand
from xknxeditor.dali.properties import DeviceCommand, PropertyType
from xknxeditor.dali.structs import SceneItemValue, decode_table

MAX_EFFECTS = 16
MAX_EFFECT_STEPS = 500


def require_effects(connection: MdtDaliConnection) -> None:
    if (
        connection.profile is None
        or connection.profile.backup_blob_base != "Dalie64-Backup-Store"
    ):
        raise DaliCommissioningError("effects are unsupported by this gateway profile")


def effect_plan(effects: Sequence[Effect]) -> tuple[bytes, int, bytes]:
    if len({effect.index for effect in effects}) != len(effects):
        raise DaliCommissioningError("duplicate effect index")
    modes = bytearray(MAX_EFFECTS)
    records: list[bytes] = []
    for effect in effects:
        if not 0 <= effect.index < MAX_EFFECTS or not 0 <= effect.mode <= 255:
            raise DaliCommissioningError("invalid effect index or mode")
        modes[effect.index] = effect.mode
        for step in effect.steps:
            if not (
                0 <= step.target_index < 80
                and 0 <= step.level <= 255
                and len(step.color) == 4
                and 0 <= step.flags <= 255
                and step.delay in (*range(11), *range(20, 201, 10))
                and step.fade_time in (0, 7, 9, 11, 12, 14, 15)
            ):
                raise DaliCommissioningError("invalid effect step")
            records.append(
                SceneItemValue(
                    effect.index, step.target_index, step.level, step.color, step.flags
                ).encode()
                + bytes((step.delay, step.fade_time))
            )
    if len(records) > MAX_EFFECT_STEPS:
        raise DaliCommissioningError("effect step capacity exceeded")
    if len(records) < MAX_EFFECT_STEPS:
        records.append(b"\xff" * 10)
    return b"".join(records), len(records), bytes(modes)


async def write_effects(
    connection: MdtDaliConnection, effects: Sequence[Effect]
) -> None:
    """Replace all effect slots; validate the complete table before writing."""
    require_effects(connection)
    data, count, modes = effect_plan(effects)
    await connection.write_table(PropertyType.EFFECT_STEP, data, count)
    await asyncio.sleep(0.5)
    await connection.write_table(PropertyType.EFFECT_MODE, modes, MAX_EFFECTS)
    await connection.run_command(DeviceCommand.UPDATE_EFFECT_TABLE, timeout=180.0)


async def read_effects(connection: MdtDaliConnection) -> list[Effect]:
    require_effects(connection)
    modes = await connection.read_table(PropertyType.EFFECT_MODE, MAX_EFFECTS)
    effects = [Effect(index, mode) for index, mode in enumerate(modes)]
    data = await connection.read_table(
        PropertyType.EFFECT_STEP, MAX_EFFECT_STEPS, terminator=b"\xff"
    )
    for record in decode_table(data, 10):
        if record[0] == 255:
            break
        if record[0] >= MAX_EFFECTS or record[1] >= 80:
            raise DaliCommissioningError("invalid effect target or index received")
        effects[record[0]].steps.append(
            EffectStep(
                record[1], record[2], record[3:7], record[7], record[8], record[9]
            )
        )
    effect_plan(effects)
    return effects


async def read_templates(connection: MdtDaliConnection) -> list[Template]:
    """Read all template slots; empty slots have no mode metadata on the bus."""
    memberships = decode_table(
        await connection.read_table(PropertyType.COLOUR_C_ASSIGN, 16), 10
    )
    templates = [
        Template(
            index,
            mode=1,
            groups=tuple(g for g in range(16) if members[g // 8] & (1 << (g % 8))),
            ecgs=tuple(e for e in range(64) if members[2 + e // 8] & (1 << (e % 8))),
        )
        for index, members in enumerate(memberships)
    ]
    data = await connection.read_table(
        PropertyType.COLOUR_C_EVENT, 300, terminator=b"\xff"
    )
    for record in decode_table(data, 10):
        if record[0] == 255:
            break
        if record[0] >= 16:
            raise DaliCommissioningError("invalid template index received")
        template = templates[record[0]]
        metadata = (record[9] >> 6, bool(record[9] & 32), not bool(record[9] & 16))
        if (
            template.commands
            and (template.mode, template.overwritable, template.reset_on_midnight)
            != metadata
        ):
            raise DaliCommissioningError("inconsistent template metadata received")
        template.mode, template.overwritable, template.reset_on_midnight = metadata
        template.commands.append(
            TemplateCommand(
                record[1],
                record[8] & 31,
                (record[9] & 7) | ((record[8] & 224) >> 2),
                record[2:6],
                record[6],
                record[7],
            )
        )
    return templates
