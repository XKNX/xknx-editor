"""Read a PowerTool configuration back from what a device holds.

A download writes the compiled container (see :mod:`.codec`): the function blocks of the
channels with their settings and a function table into the memory of the application object
(object 4), the slots of the group objects into the object table (object 3). This module reads
both off a device and decodes them into a configuration that compiles to the same bytes: the
channels and their functions from the blocks' channel numbers and function codes, the hardware
(products, channel counts) that has those channels, each setting from its bits, the object flags
from the slots.

Not everything is in the bits: choices whose values share their bits, functions with the same
code, and settings kept only in the configuration (virtual ones) decide which settings and
objects exist. Those are searched: each alternative is compiled, and the one whose bytes come
closest to the device's is kept, until the configuration compiles to exactly what the device
holds. Names a configuration gives its channels are never downloaded and stay empty.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol

from .codec import CompiledPowerToolImage, float16_bytes
from .family import FamilyConfig, FamilyEvaluation, Node, Scalar, check_stored
from .family_codec import parameter_written, parameters
from .model import (
    ChannelConfig,
    EvaluatedChannel,
    Field,
    Number,
    PowerToolConfig,
    channel_exists,
    channel_functions,
    channel_number,
    float32,
    functions,
    timer_code,
)

if TYPE_CHECKING:
    from .native import Configuration

# The application object's memory holds the function blocks, the object table's the slots.
APPLICATION_OBJECT = 4
OBJECT_TABLE_OBJECT = 3
# How often decoding runs again: a setting can make others exist (or not).
_PASSES = 12
# How often the search goes through the choices the bits leave open.
_ROUNDS = 8
# How many hardware selections the search goes on from each time.
_BEAM = 12
# Up to how many values of a number kept in the configuration only are tried.
_FEW = 64
# The distance of a configuration that does not compile.
_FAR = 1 << 30
# Value kinds stored shifted into their bits.
_SHIFTED = (
    "V_UNSIGNED",
    "V_SIGNED",
    "P_SecondsShort",
    "P_SecondsLong",
    "P_MillisecondsShort",
    "P_MillisecondsLong",
)


class MemoryReader(Protocol):
    """What a device read needs: the start of an object's memory, and memory."""

    async def read_table_reference(self, object_index: int) -> int: ...

    async def read_memory(
        self, address: int, count: int, *, user: bool = False
    ) -> bytes: ...


@dataclass(frozen=True)
class DeviceImage:
    """What a PowerTool device holds of its configuration: the application object's memory
    (header, function blocks, function table) and the object table (count, then a flag and a
    size octet per slot)."""

    application: bytes
    object_table: bytes


async def read_device_image(device: MemoryReader) -> DeviceImage:
    """Read the configuration's memory off a device: as much of the application object's
    memory as its header and function table take, and the object table."""
    base = await device.read_table_reference(APPLICATION_OBJECT)
    header = await device.read_memory(base, 28)
    table_offset = struct.unpack(">7I", header)[6]
    if not 28 <= table_offset <= 0xFFFF:
        raise ValueError("No PowerTool configuration in the application object")
    count = int.from_bytes(await device.read_memory(base + table_offset, 2), "big")
    application = await device.read_memory(base, table_offset + 2 + 6 * count)
    objects = await device.read_table_reference(OBJECT_TABLE_OBJECT)
    slots = int.from_bytes(await device.read_memory(objects, 2), "big")
    object_table = await device.read_memory(objects, 2 + 2 * slots)
    return DeviceImage(application, object_table)


def image_of(compiled: CompiledPowerToolImage) -> DeviceImage:
    """What a device holds after a download of ``compiled``."""
    by_object = {r.object_index: r.data for r in compiled.resources}
    return DeviceImage(by_object[APPLICATION_OBJECT], by_object[OBJECT_TABLE_OBJECT])


def decode(base: Configuration, image: DeviceImage) -> Configuration:
    """The configuration of the product ``base`` (its identity: the default configuration of
    the product, see :func:`.native.for_product`) that a device holding ``image`` was
    downloaded with. Raises ``ValueError`` when no configuration gives these bytes."""
    if isinstance(base, PowerToolConfig):
        return _decode_model(base, image)
    return _decode_family(base, image)


# -- the container -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Block:
    code: int
    data: bytes


def _blocks(application: bytes) -> list[_Block]:
    """The function blocks of an application object's memory, in table order."""
    if len(application) < 30:
        raise ValueError("PowerTool configuration is cut short")
    table_offset = struct.unpack(">7I", application[:28])[6]
    count = int.from_bytes(application[table_offset : table_offset + 2], "big")
    if len(application) < table_offset + 2 + 6 * count:
        raise ValueError("PowerTool configuration is cut short")
    entries = [
        struct.unpack(">IH", application[at : at + 6])
        for at in range(table_offset + 2, table_offset + 2 + 6 * count, 6)
    ]
    if any(not 28 <= offset < table_offset for offset, _ in entries):
        raise ValueError("Invalid PowerTool function table")
    ends = [offset for offset, _ in entries[1:]] + [table_offset] if entries else []
    return [
        _Block(code, application[offset:end])
        for (offset, code), end in zip(entries, ends, strict=True)
    ]


def _apart(a: bytes, b: bytes) -> int:
    return sum(x != y for x, y in zip(a, b, strict=False)) + 8 * abs(len(a) - len(b))


def _distance(config: Configuration, image: DeviceImage) -> int:
    """How many octets the image of ``config`` differs from ``image`` in."""
    from .native import compile_native

    try:
        compiled = image_of(compile_native(config))
    except ValueError:
        return _FAR
    return _apart(compiled.application, image.application) + _apart(
        compiled.object_table, image.object_table
    )


def _object_flags(
    compiled: CompiledPowerToolImage, object_table: bytes
) -> dict[str, int]:
    """The flags of the objects whose slot holds others than their defaults."""
    count = int.from_bytes(object_table[:2], "big")
    slots = {
        index + 1: object_table[2 + 2 * index]
        for index in range(count)
        if 3 + 2 * index < len(object_table)
    }
    return {
        role.key: slots[role.index]
        for role in compiled.objects
        if role.visible and slots.get(role.index, role.flags) != role.flags
    }


def _with_flags[C: (PowerToolConfig, FamilyConfig)](config: C, image: DeviceImage) -> C:
    """``config`` with the object flags the device's slots hold."""
    from .native import compile_native

    try:
        flags = _object_flags(compile_native(config), image.object_table)
    except ValueError:
        return config
    return replace(config, object_flags=flags)


def _search[S](
    start: S,
    build: Callable[[S], Configuration | None],
    choices: Callable[[S], list[tuple[str, list[object]]]],
    choose: Callable[[S, str, object], S],
    image: DeviceImage,
) -> Configuration | None:
    """From ``start``, take the alternative of one open choice after another that brings the
    configuration closer to ``image``, until it compiles to it (None when it does not)."""
    state = start
    config = build(state)
    best = _distance(config, image) if config is not None else _FAR
    for _ in range(_ROUNDS):
        if best == 0:
            return config
        improved = False
        for key, alternatives in choices(state):
            for alternative in alternatives:
                trial = choose(state, key, alternative)
                candidate = build(trial)
                if candidate is None:
                    continue
                distance = _distance(candidate, image)
                if distance < best:
                    state, config, best, improved = trial, candidate, distance, True
                    if best == 0:
                        return config
        if not improved:
            break
    return config if best == 0 else None


def _raw(data: bytes, address: int, size: int, mask: int) -> int:
    if address + size > len(data):
        raise ValueError("PowerTool setting lies outside its block")
    if size == 1:
        return data[address] & mask
    return int.from_bytes(data[address : address + size], "big")


def _float(data: bytes, address: int, size: int) -> float:
    """A float setting: four octets IEEE 754, two the 16-bit float of a hundredth."""
    chunk = data[address : address + size]
    if len(chunk) != size:
        raise ValueError("PowerTool setting lies outside its block")
    if size == 4:
        return float(struct.unpack(">f", chunk)[0])
    raw = int.from_bytes(chunk, "big")
    mantissa = raw & 0x7FF
    if raw & 0x8000:
        mantissa -= 0x800
    hundredths = mantissa * (1 << ((raw >> 11) & 0x0F))
    # The value with the fewest decimals that encodes so.
    for decimals in range(3):
        candidate = round(hundredths / 100, decimals)
        if float16_bytes(candidate) == chunk:
            return candidate
    return hundredths / 100


def _unsigned(raw: int, factor: float) -> int:
    """The stored value an unsigned setting with ``factor`` writes as ``raw``."""
    if factor == 1:
        return raw
    guess = raw / factor
    for candidate in sorted(
        {round(guess), math.floor(guess), math.ceil(guess)}
        | {round(guess) + d for d in (-2, -1, 1, 2)},
        key=lambda c: abs(c - guess),
    ):
        product = candidate * factor
        if int(float32(product) if factor != int(factor) else product) == raw:
            return candidate
    return round(guess)


def _timer(code: int) -> int:
    return (code & 255) * (5 << (code >> 8))


def _choices_with_bits(
    options: list[int], shift: int, mask: int, raw: int
) -> list[int]:
    """The indexes of the options whose bits are ``raw``."""
    return [i for i, value in enumerate(options) if (value << shift) & mask == raw]


# -- push buttons (model) --------------------------------------------------------------------


def _model_value(f: Field, data: bytes) -> tuple[Number, list[int]]:
    """The value of the push-button field ``f`` in its block ``data``, and for a choice the
    other choices with the same bits."""
    if f.kind == "float":
        return float32(_float(data, f.address, f.size)), []
    raw = _raw(
        data, f.address, f.size, f.mask if f.size == 1 else (1 << f.size * 8) - 1
    )
    if f.kind == "enum":
        found = _choices_with_bits([v for _, v in f.options], f.shift, f.mask, raw)
        default = int(f.default or 0)
        if default in found:
            return default, [i for i in found if i != default]
        return (found[0], found[1:]) if found else (default, [])
    if f.kind == "freetimer":
        return _timer(raw), []
    if f.kind == "unsigned":
        raw = _unsigned(raw, float32(f.constraints.get("factor", 1)))
    value = (raw & f.mask) >> f.shift
    if f.kind == "signed":
        width = (f.mask >> f.shift).bit_length()
        if width and value >= 1 << (width - 1):
            value -= 1 << width
    return value, []


@dataclass(frozen=True)
class _ModelState:
    functions: tuple[str, ...]
    fixed: tuple[tuple[int, str, Number], ...] = ()


def _decode_model(base: PowerToolConfig, image: DeviceImage) -> PowerToolConfig:
    by_code: dict[int, list[str]] = {}
    for function in functions().values():
        by_code.setdefault(function.code, []).append(function.key)
    blocks = {int.from_bytes(b.data[:3], "big"): b for b in _blocks(image.application)}
    found: dict[int, _Block] = {}
    alternatives: dict[int, list[str]] = {}
    selected: list[str] = []
    for index, current in enumerate(base.channels):
        block = blocks.pop(channel_number(base.variant, index), None)
        if block is None:
            # No block: the channel is inactive (or absent, its function then as it is).
            available = channel_functions(base.variant, index)
            selected.append("inactive" if "inactive" in available else current.function)
            continue
        available = [
            key
            for key in by_code.get(block.code, [])
            if key in channel_functions(base.variant, index)
        ]
        if not available:
            raise ValueError(f"Unknown PowerTool function {block.code}")
        # Functions with the same code: the channel's own first (what its block holds may
        # not tell them apart).
        available.sort(key=lambda key: key != current.function)
        selected.append(available[0])
        alternatives[index] = available
        found[index] = block
    if blocks:
        raise ValueError("PowerTool channel the product does not have")

    def build(state: _ModelState) -> PowerToolConfig | None:
        channels: list[ChannelConfig] = []
        for index, function in enumerate(state.functions):
            block = found.get(index)
            if block is None:
                channels.append(ChannelConfig(function))
                continue
            fixed = {k: v for i, k, v in state.fixed if i == index}
            try:
                values = _model_values(function, block.data, fixed)[0]
                channels.append(ChannelConfig(function, values))
            except ValueError:
                return None
        try:
            config = replace(base, channels=tuple(channels), object_flags={})
        except ValueError:
            return None
        if any(
            not channel_exists(config, i) and c.function != "inactive"
            for i, c in enumerate(config.channels)
        ):
            return None
        return _with_flags(config, image)

    def choices(state: _ModelState) -> list[tuple[str, list[object]]]:
        open_: list[tuple[str, list[object]]] = [
            (f"function:{index}", list(keys))
            for index, keys in alternatives.items()
            if len(keys) > 1
        ]
        for index, function in enumerate(state.functions):
            block = found.get(index)
            if block is None:
                continue
            fixed = {k: v for i, k, v in state.fixed if i == index}
            try:
                _, others = _model_values(function, block.data, fixed)
            except ValueError:
                continue
            open_.extend((f"{index}/{key}", list(values)) for key, values in others)
        return open_

    def choose(state: _ModelState, key: str, value: object) -> _ModelState:
        if key.startswith("function:"):
            channel = int(key.removeprefix("function:"))
            assert isinstance(value, str)
            selected = list(state.functions)
            selected[channel] = value
            fixed = tuple(f for f in state.fixed if f[0] != channel)
            return _ModelState(tuple(selected), fixed)
        index, name = key.split("/", 1)
        assert isinstance(value, (int, float))
        kept = tuple(f for f in state.fixed if (f[0], f[1]) != (int(index), name))
        return replace(state, fixed=(*kept, (int(index), name, value)))

    config = _search(_ModelState(tuple(selected)), build, choices, choose, image)
    if not isinstance(config, PowerToolConfig):
        raise ValueError("No push-button configuration gives what the device holds")
    return config


def _model_values(
    function: str, data: bytes, fixed: Mapping[str, Number]
) -> tuple[dict[str, Number], list[tuple[str, list[int]]]]:
    """The values of a channel of ``function`` its block ``data`` holds (``fixed`` where the
    bits leave the choice open), and the choices left open: aliases among the choices, and the
    choices that are kept in the configuration only."""
    values: dict[str, Number] = {}
    open_: list[tuple[str, list[int]]] = []
    for _ in range(_PASSES):
        evaluated = EvaluatedChannel(ChannelConfig(function, {**values, **fixed}))
        found: dict[str, Number] = {}
        open_ = []
        for i, f in enumerate(evaluated.function.fields):
            if f.kind == "page" or not f.visible or f.default is None:
                continue
            if not evaluated.exists(i):
                continue
            if f.storage != "offset":
                if f.kind == "enum":
                    open_.append((f.key, list(range(len(f.options)))))
                continue
            value, others = _model_value(f, data)
            if others:
                open_.append((f.key, [int(value), *others]))
            try:
                # Bits read where, with other settings, another field lies: no value.
                f.validate(value)
            except ValueError:
                continue
            if value != f.default:
                found[f.key] = value
        found.update(fixed)
        found = {k: v for k, v in found.items() if v != _model_default(function, k)}
        if found == values:
            return values, open_
        values = found
    raise ValueError("PowerTool settings do not settle")


def _model_default(function: str, key: str) -> Number | None:
    return next((f.default for f in functions()[function].fields if f.key == key), None)


# -- product families --------------------------------------------------------------------------


def _family_value(node: Node, data: bytes, text_base: int) -> tuple[Scalar, list[int]]:
    """The stored value of the family parameter ``node`` in its block ``data`` (strings behind
    a pointer start at ``text_base`` plus the pointer), and for a choice the other choices with
    the same bits."""
    spec = node.spec
    kind, size, address = spec["value_kind"], spec["size"], spec["address"]
    panel = bool(node.application and node.application.spec["smart_touch"])
    if spec["extension"] == "DatapointConfig":
        return spec["default"], []
    if kind == "V_FLOAT":
        return float32(_float(data, address, size)), []
    if kind == "V_STRING":
        chunk = data[address : address + size]
        if panel:
            return chunk.split(b"\0", 1)[0].decode("utf-8", "replace"), []
        if chunk[:2] != b"\xfe\xff":
            return "", []
        return _utf16(chunk[2:]), []
    if kind == "V_STRINGPTR":
        pointer = int.from_bytes(data[address : address + 2], "big")
        if panel:
            start = text_base + (spec.get("fix_string_ptr", 0) or pointer)
            text = data[start : start + size].split(b"\0", 1)[0]
            return text.decode("utf-8", "replace"), []
        start = text_base + pointer
        if data[start : start + 2] != b"\xfe\xff":
            return "", []
        return _utf16(data[start + 2 : start + size]), []
    raw = _raw(data, address, size, spec["mask"] if size == 1 else (1 << size * 8) - 1)
    if kind == "V_ENUM":
        options = [int(option["value"]) for option in spec["options"]]
        found = _choices_with_bits(options, spec["shift"], spec["mask"], raw)
        default = spec["default"]
        if default in found:
            return default, [i for i in found if i != default]
        return (found[0], found[1:]) if found else (default, [])
    if kind == "V_UNSIGNED":
        return _unsigned(raw, float32(spec["constraints"].get("factor", 1))), []
    return raw, []


def _kept(node: Node, value: Scalar) -> bool:
    """Whether a configuration keeps ``value`` for ``node``: not its default (an empty text
    where it has none), and one it can hold (bits read where, with other settings, another
    setting lies hold none)."""
    default = _default(node)
    if value == default or (value == "" and default is None):
        return False
    try:
        check_stored(node, value)
    except ValueError:
        return False
    return True


def _default(node: Node) -> Scalar:
    """The value a family parameter stores without one of its own."""
    spec = node.spec
    value = spec["default"]
    if spec["value_kind"] in _SHIFTED:
        return (int(value) << spec["shift"]) & spec["mask"]
    if spec["value_kind"] == "V_FREETIMER":
        return timer_code(int(value))
    return value


def _utf16(data: bytes) -> str:
    end = next(
        (i for i in range(0, len(data) - 1, 2) if data[i : i + 2] == b"\0\0"),
        len(data) - len(data) % 2,
    )
    return data[:end].decode("utf-16-be", "replace")


def _matches(evaluated: FamilyEvaluation, channel: Node, block: _Block) -> list[int]:
    """The function options of ``channel`` whose application ``block`` is."""
    number = evaluated.channel_number(channel)
    whole = int.from_bytes(block.data[:3], "big")
    panel = int.from_bytes(block.data[1:3], "big")
    found: list[tuple[bool, int]] = []
    for option, key in enumerate(channel.spec["applications"]):
        spec = evaluated.schema["nodes"][key]
        if spec["inactive"] or spec.get("function") != block.code:
            continue
        if panel == number & 0xFFFF if spec["smart_touch"] else whole == number:
            found.append((not _identified(evaluated, spec, block, number), option))
    # Functions with the same code: first those whose layout holds the channel's own number
    # where a panel element keeps its identity.
    return [option for _, option in sorted(found)]


def _identified(
    evaluated: FamilyEvaluation, app: dict[str, Any], block: _Block, number: int
) -> bool:
    """Whether ``block``, read as the application ``app``, holds ``number`` in its identity
    field (an application without one: True)."""
    nodes: dict[str, dict[str, Any]] = evaluated.schema["nodes"]
    pending: list[str] = list(app.get("parameters", []))
    while pending:
        spec = nodes[pending.pop()]
        if spec.get("extension") == "ControlId" and spec.get("storage") == "Offset":
            size, address = spec["size"], spec["address"]
            if address + size > len(block.data):
                return False
            raw = int.from_bytes(block.data[address : address + size], "big")
            return raw == number & ((1 << 8 * size) - 1)
        pending.extend(spec.get("parameters", []))
    return True


def _structures(base: FamilyConfig, blocks: list[_Block]) -> Iterator[FamilyConfig]:
    """Hardware selections whose channels take every block, the fewest changes from the
    product's own first. The selections are changed one after another, the most promising
    kept each time (those whose channels take the most blocks in their order): a device's
    subsystems only take blocks once the device is chosen, so selections that take no more
    blocks than before are kept as well. Channel counts follow the blocks."""
    start = _counted(base, blocks)
    beam = [start]
    seen = {_structure_key(start)}
    for _ in range(_ROUNDS * 2):
        full = [c for c in beam if _aligned(c, blocks) == len(blocks)]
        if full:
            yield from full
            # Hardware whose channels take the same blocks may differ in their settings: the
            # selections one change away that take every block too.
            for config in full:
                for candidate in _neighbours(config, blocks):
                    key = _structure_key(candidate)
                    if key not in seen and _aligned(candidate, blocks) == len(blocks):
                        seen.add(key)
                        yield candidate
            return
        scored: list[tuple[int, FamilyConfig]] = []
        for config in beam:
            for candidate in _neighbours(config, blocks):
                key = _structure_key(candidate)
                if key not in seen:
                    seen.add(key)
                    scored.append((_aligned(candidate, blocks), candidate))
        if not scored:
            return
        scored.sort(key=lambda item: -item[0])
        beam = [config for _, config in scored[:_BEAM]]


def _structure_key(config: FamilyConfig) -> tuple[object, ...]:
    return (
        tuple(sorted(config.products.items())),
        tuple(sorted(config.channel_counts.items())),
    )


def _neighbours(config: FamilyConfig, blocks: list[_Block]) -> Iterator[FamilyConfig]:
    """``config`` with one hardware selection changed (and what that selection brings with
    it, see :func:`.family.edit`), channel counts following the blocks."""
    from .family import edit

    state = FamilyEvaluation(config)
    for path, options in state.product_choices.items():
        current = options.index(state.nodes[path].key)
        for option in range(len(options)):
            if option == current:
                continue
            try:
                yield _counted(edit(config, path, str(option)), blocks)
            except ValueError:
                continue


def _aligned(config: FamilyConfig, blocks: list[_Block]) -> int:
    """How many of the blocks, in their order, the channels of ``config`` take."""
    state = FamilyEvaluation(config)
    at = 0
    for channel in state.channels:
        if at < len(blocks) and _matches(state, channel, blocks[at]):
            at += 1
    return at


def _counted(config: FamilyConfig, blocks: list[_Block]) -> FamilyConfig:
    """``config`` with each device of a variable channel count holding as many channels as
    the blocks have of its address."""
    state = FamilyEvaluation(config)
    counts = dict(config.channel_counts)
    for path, limit in state.count_limits.items():
        address = state.nodes[path].spec["address"]
        used = sum(
            1 for b in blocks if int.from_bytes(b.data[:3], "big") >> 16 == address
        )
        counts[path] = min(max(used, 1), limit)
    if counts == dict(config.channel_counts):
        return config
    try:
        candidate = replace(config, channel_counts=counts)
        FamilyEvaluation(candidate)
    except ValueError:
        return config
    return candidate


def _assigned(
    config: FamilyConfig, blocks: list[_Block]
) -> tuple[FamilyConfig, dict[str, _Block], dict[str, list[int]]] | None:
    """``config`` with each channel's function the one its block names, the block of each
    channel, and the functions with that block's code; None when the blocks do not fit the
    channels. A download writes the blocks in the order of the channels, one per active
    channel: a channel whose turn the next block does not fit has none (an inactive function
    where the channel exists)."""
    state = FamilyEvaluation(config)
    options = dict(config.options)
    found: dict[str, _Block] = {}
    alternatives: dict[str, list[int]] = {}
    at = 0
    for channel in state.channels:
        matches = _matches(state, channel, blocks[at]) if at < len(blocks) else []
        if not matches:
            if state.channel_exists(channel):
                # Any of its inactive functions writes no block: which one, other channels
                # may tell (the search tries each).
                inactive = [
                    i
                    for i, key in enumerate(channel.spec["applications"])
                    if state.schema["nodes"][key]["inactive"]
                ]
                if inactive:
                    options[channel.path] = inactive[0]
                    alternatives[channel.path] = inactive
            continue
        options[channel.path] = matches[0]
        found[channel.path] = blocks[at]
        alternatives[channel.path] = matches
        at += 1
    if at != len(blocks):
        return None
    candidate = replace(config, options={k: v for k, v in options.items() if v})
    try:
        FamilyEvaluation(candidate)
    except ValueError:
        return None
    return candidate, found, alternatives


@dataclass(frozen=True)
class _FamilyState:
    options: tuple[tuple[str, int], ...]
    fixed: tuple[tuple[str, Scalar], ...] = ()


def _decode_family(base: FamilyConfig, image: DeviceImage) -> FamilyConfig:
    blocks = _blocks(image.application)
    start = replace(base, options={}, values={}, object_flags={})
    for structure in _structures(start, blocks):
        assigned = _assigned(structure, blocks)
        if assigned is None:
            continue
        config, found, alternatives = assigned
        result = _search_family(config, found, alternatives, image)
        if result is not None:
            return result
    raise ValueError("No PowerTool configuration gives what the device holds")


def _search_family(
    config: FamilyConfig,
    found: Mapping[str, _Block],
    alternatives: Mapping[str, list[int]],
    image: DeviceImage,
) -> FamilyConfig | None:
    def configured(state: _FamilyState) -> FamilyConfig:
        return replace(config, options={k: v for k, v in state.options if v})

    def build(state: _FamilyState) -> FamilyConfig | None:
        try:
            decoded = _family_values(configured(state), found, dict(state.fixed))[0]
            return _with_flags(decoded, image)
        except ValueError:
            return None

    def choices(state: _FamilyState) -> list[tuple[str, list[object]]]:
        open_: list[tuple[str, list[object]]] = [
            (f"option:{path}", list(values))
            for path, values in alternatives.items()
            if len(values) > 1
        ]
        try:
            _, others = _family_values(configured(state), found, dict(state.fixed))
        except ValueError:
            return open_
        return open_ + [(f"value:{path}", list(values)) for path, values in others]

    def choose(state: _FamilyState, key: str, value: object) -> _FamilyState:
        kind, path = key.split(":", 1)
        if kind == "option":
            assert isinstance(value, int)
            options = tuple((k, v) for k, v in state.options if k != path)
            # Another function of the channel: its settings start anew.
            fixed = tuple(f for f in state.fixed if not f[0].startswith(path + "/"))
            return _FamilyState((*options, (path, value)), fixed)
        assert isinstance(value, (int, float, str)) or value is None
        kept = tuple(f for f in state.fixed if f[0] != path)
        return replace(state, fixed=(*kept, (path, value)))

    result = _search(
        _FamilyState(tuple(config.options.items())), build, choices, choose, image
    )
    return result if isinstance(result, FamilyConfig) else None


def _family_values(
    config: FamilyConfig, found: Mapping[str, _Block], fixed: Mapping[str, Scalar]
) -> tuple[FamilyConfig, list[tuple[str, list[int]]]]:
    """``config`` with the settings its channels' blocks hold (``fixed`` where the bits leave
    the choice open), decoded until they settle, and the choices left open: aliases among the
    choices, and the choices kept in the configuration only."""
    values: dict[str, Scalar] = {}
    open_: list[tuple[str, list[int]]] = []
    for _ in range(_PASSES):
        state = FamilyEvaluation(replace(config, values={**values, **fixed}))
        decoded: dict[str, Scalar] = {}
        open_ = []
        for channel in state.channels:
            block = found.get(channel.path)
            if block is None or not state.active(channel):
                continue
            _channel_values(state, channel, block.data, decoded, open_)
        # Choices of the hardware and its channels kept in the configuration only (which
        # channels a device has, for example).
        for root in (*state.devices, *state.channels):
            for node in parameters(root):
                if node.spec["storage"] != "Offset" and _held(state, node):
                    _virtual(state, node, open_)
        decoded.update(
            (k, v) for k, v in fixed.items() if k in state.nodes and v is not None
        )
        decoded = {k: v for k, v in decoded.items() if _kept(state.nodes[k], v)}
        if decoded == values:
            return replace(config, values=values), open_
        values = decoded
    raise ValueError("PowerTool settings do not settle")


def _channel_values(
    state: FamilyEvaluation,
    channel: Node,
    data: bytes,
    values: dict[str, Scalar],
    open_: list[tuple[str, list[int]]],
) -> None:
    app = channel.application
    assert app is not None and channel.product is not None
    spec = app.spec
    generic = spec["compiler"] == "GenericApplication"
    panel = bool(spec["smart_touch"])
    device_size = 0
    if not panel and (not generic or len(data) > spec["param_block_size"]):
        offset = spec["config_offset"]
        device_size = int.from_bytes(data[offset : offset + 2], "big")
    if panel:
        text_base = spec["param_block_size"] if generic else spec["config_offset"]
    elif generic:
        text_base = spec["param_block_size"] + (device_size + 2 if device_size else 0)
    else:
        length = spec["config_offset"] + 2 + device_size
        text_base = length + (device_size + 2 if device_size else 0)
    for node in parameters(app):
        if not _held(state, node):
            continue
        if node.spec["storage"] != "Offset":
            _virtual(state, node, open_)
            continue
        if not parameter_written(state, node):
            continue
        value, others = _family_value(node, data, text_base)
        if others:
            open_.append((node.path, [int(value or 0), *others]))
        if node.spec["extension"] in ("ControlId", "ParentId") and isinstance(
            value, int
        ):
            value = _channel_reference(state, channel, node, value)
        values[node.path] = value
    if device_size:
        start = spec["config_offset"] + 2
        device = data[start : start + device_size]
        for root in (channel.product, channel, app):
            for node in parameters(root):
                if (
                    node.spec["extension"] == "DeviceConfiguration"
                    and _held(state, node)
                    and state.property(node, "Exists")
                ):
                    values[node.path] = _family_value(node, device, 0)[0]


def _virtual(
    state: FamilyEvaluation, node: Node, open_: list[tuple[str, list[int]]]
) -> None:
    """Note the values of a setting kept in the configuration only as a choice left open: a
    choice's options, a number's values where there are few."""
    if not state.property(node, "Exists"):
        return
    spec = node.spec
    if spec["value_kind"] == "V_ENUM":
        open_.append((node.path, list(range(len(spec["options"])))))
    elif spec["value_kind"] == "V_UNSIGNED":
        low, high = int(spec["constraints"]["min"]), int(spec["constraints"]["max"])
        if 0 <= high - low < _FEW:
            open_.append(
                (
                    node.path,
                    [(v << spec["shift"]) & spec["mask"] for v in range(low, high + 1)],
                )
            )


def _channel_reference(
    state: FamilyEvaluation, channel: Node, node: Node, raw: int
) -> int:
    """The channel number a panel element's identity (``ControlId``: its own channel) or
    parent (``ParentId``: its parent channel, else another) holds: the field keeps only the
    bits that fit it."""
    bits = (1 << (8 * node.spec["size"])) - 1
    preferred = channel if node.spec["extension"] == "ControlId" else channel.parent
    numbers = [state.channel_number(c) for c in state.channels]
    if preferred is not None and preferred.spec["kind"] == "channel":
        numbers.insert(0, state.channel_number(preferred))
    return next((n for n in numbers if n & bits == raw), raw)


def _held(state: FamilyEvaluation, node: Node) -> bool:
    """Whether a configuration holds a value of ``node`` (else its default applies)."""
    return node.spec["value_kind"] != "V_PAGE" and bool(
        state.property(node, "Visible")
        or node.spec["extension"] in ("ControlId", "ParentId", "Position")
    )
