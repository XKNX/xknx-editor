"""Reading a PowerTool configuration back from what a device holds: every profile's default
configuration, and configurations edited at random, decode to a configuration that compiles
to the same bytes; what a device holds is read off it as far as its header and tables say."""

from __future__ import annotations

import random
from dataclasses import replace
from typing import Any

import pytest

from xknxeditor.vendor.powertool.family import (
    FamilyConfig,
    FamilyEvaluation,
    edit,
    schemas,
)
from xknxeditor.vendor.powertool.model import (
    EvaluatedChannel,
    PowerToolConfig,
    Variant,
    channel_exists,
    channel_functions,
    default_config,
    edit_config,
)
from xknxeditor.vendor.powertool.native import Configuration, compile_native
from xknxeditor.vendor.powertool.recover import (
    DeviceImage,
    decode,
    image_of,
    read_device_image,
)

PROGRAMS = ("0007610840", "0007610850")
PROFILES = [(s["code"], name) for s in schemas() for name in s["products"]]
VARIANTS: list[Variant] = ["01", "02"]


def _family(code: int, name: str) -> FamilyConfig:
    return FamilyConfig(name, database_code=code, program_ids=PROGRAMS)


def _reads_back(base: Configuration, config: Configuration) -> None:
    image = image_of(compile_native(config))
    assert image_of(compile_native(decode(base, image))) == image


@pytest.mark.parametrize(("code", "name"), PROFILES)
def test_every_profile_reads_back_its_default(code: int, name: str) -> None:
    base = _family(code, name)
    _reads_back(base, base)


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_push_button_reads_back_its_default(variant: Variant) -> None:
    _reads_back(default_config(variant), default_config(variant))


def _edited_family(base: FamilyConfig, rnd: random.Random) -> FamilyConfig:
    """``base`` with hardware, functions and settings chosen at random (each a user's edit)."""
    config = base
    for _ in range(40):
        state = FamilyEvaluation(config)
        try:
            pick = rnd.random()
            if pick < 0.15 and state.product_choices:
                path, choices = rnd.choice(list(state.product_choices.items()))
                config = edit(config, path, str(rnd.randrange(len(choices))))
            elif pick < 0.5 and state.channels:
                channel = rnd.choice(state.channels)
                options = channel.spec["applications"]
                config = edit(config, channel.path, str(rnd.randrange(len(options))))
            else:
                config = _edit_setting(config, state, rnd)
        except ValueError:
            continue
    roles = [o for o in compile_native(config).objects if o.visible]
    if roles:
        role = rnd.choice(roles)
        config = replace(config, object_flags={role.key: 0x5C})
    return config


def _edit_setting(
    config: FamilyConfig, state: FamilyEvaluation, rnd: random.Random
) -> FamilyConfig:
    nodes = [
        n
        for n in state.nodes.values()
        if n.spec["kind"] == "parameter"
        and n.spec["value_kind"] != "V_PAGE"
        and state.property(n, "Visible")
    ]
    if not nodes:
        return config
    node = rnd.choice(nodes)
    spec: dict[str, Any] = node.spec
    kind = spec["value_kind"]
    if kind in ("V_ENUM", "V_SYMBOL"):
        text = str(rnd.randrange(len(spec["options"])))
    elif kind in ("V_STRING", "V_STRINGPTR"):
        text = "".join(rnd.choice("abcÄü1 ") for _ in range(rnd.randint(0, 5)))
    else:
        bounds = spec["constraints"]
        low, high = bounds.get("min", 0), bounds.get("max", 100)
        if kind == "V_FLOAT":
            text = str(round(rnd.uniform(low, high), 1))
        else:
            text = str(
                rnd.randint(int(low), int(high)) + bounds.get("displayoffset", 0)
            )
    return edit(config, node.path, text)


@pytest.mark.parametrize(
    ("code", "name"),
    [
        (20, "SmartTouch"),
        (21, "GrandTower"),
        (1, "priOn"),
        (5, "priOn_3"),
        (2, "SoloTasterKomfort"),
        (7, "SidusRTR2"),
        (10, "BuTouchSensor3fIR"),
        (11, "PM_Mini"),
        (1, "SidusTaster"),
    ],
)
@pytest.mark.parametrize("seed", range(3))
def test_edited_configurations_read_back(code: int, name: str, seed: int) -> None:
    base = _family(code, name)
    config = _edited_family(base, random.Random(f"{name}/{seed}"))
    try:
        compile_native(config)
    except ValueError:
        pytest.skip("the random edits gave no configuration that compiles")
    _reads_back(base, config)


def _edited_buttons(variant: Variant, rnd: random.Random) -> PowerToolConfig:
    config = default_config(variant)
    for _ in range(30):
        index = rnd.randrange(len(config.channels))
        try:
            if rnd.random() < 0.3 and channel_exists(config, index):
                functions = channel_functions(config.variant, index)
                config = edit_config(config, index, "function", rnd.choice(functions))
                continue
            evaluated = EvaluatedChannel(config.channels[index])
            fields = [
                f
                for i, f in enumerate(evaluated.function.fields)
                if f.visible and f.kind != "page" and evaluated.exists(i)
            ]
            if not fields:
                continue
            f = rnd.choice(fields)
            if f.kind == "enum":
                text = str(rnd.randrange(len(f.options)))
            elif f.kind == "float":
                low, high = f.constraints["min"], f.constraints["max"]
                text = str(round(rnd.uniform(low, high), 1))
            else:
                low, high = f.constraints["min"], f.constraints["max"]
                text = str(rnd.randint(int(low), int(high)))
            config = edit_config(config, index, f.key, text)
        except ValueError:
            continue
    return config


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", range(6))
def test_edited_push_buttons_read_back(variant: Variant, seed: int) -> None:
    config = _edited_buttons(variant, random.Random(f"{variant}/{seed}"))
    _reads_back(default_config(variant), config)


def test_flags_read_back() -> None:
    config = default_config("01")
    role = next(o for o in compile_native(config).objects if o.visible)
    flagged = replace(config, object_flags={role.key: 0x14})
    decoded = decode(config, image_of(compile_native(flagged)))
    assert dict(decoded.object_flags) == {role.key: 0x14}


def test_a_block_of_no_function_is_refused() -> None:
    config = default_config("02")
    image = image_of(compile_native(config))
    table = int.from_bytes(image.application[24:28], "big")
    broken = bytearray(image.application)
    broken[table + 6 : table + 8] = b"\xff\xff"
    with pytest.raises(ValueError, match="function"):
        decode(config, DeviceImage(bytes(broken), image.object_table))


async def test_the_device_is_read_as_far_as_its_tables_say() -> None:
    image = image_of(compile_native(default_config("01")))
    memory = {0x4000: image.application, 0x5000: image.object_table}
    reads: list[tuple[int, int]] = []

    class Device:
        async def read_table_reference(self, object_index: int) -> int:
            return {4: 0x4000, 3: 0x5000}[object_index]

        async def read_memory(
            self, address: int, count: int, *, user: bool = False
        ) -> bytes:
            reads.append((address, count))
            for start, data in memory.items():
                if start <= address < start + 0x1000:
                    return (data + bytes(0x1000))[
                        address - start : address - start + count
                    ]
            raise AssertionError(address)

    assert await read_device_image(Device()) == image
    assert (0x4000, len(image.application)) in reads
    assert (0x5000, len(image.object_table)) in reads
