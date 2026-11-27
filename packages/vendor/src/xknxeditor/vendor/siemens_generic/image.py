"""Compile the complete parameter memory and its whole-byte write mask."""

from dataclasses import dataclass

from .blob import PluginSettings
from .codec import byte_size, encode
from .configuration import Evaluation, evaluate
from .description import Description, Parameter, locked_parameters


@dataclass(frozen=True)
class CompiledImage:
    """The parameter memory: ``data`` and ``mask`` hold the address ``base`` + i at i (the
    origin of the plug-in's stored image), ``start`` is the lowest parameter address.
    ``relative_offset`` (when not None) is subtracted from an address to write it relative
    to the application's segment."""

    base: int
    data: bytes
    mask: bytes
    allocation_size: int
    state: Evaluation
    start: int = 0
    relative_offset: int | None = None

    @property
    def used_length(self) -> int:
        return self.mask.rfind(b"\xff") + 1


def compile_image(
    description: Description,
    settings: PluginSettings,
    globals_: dict[str, str] | None = None,
) -> CompiledImage:
    """Encode defaults, active controls and assignments in the original memory map."""
    if description.image_unsupported:
        raise ValueError(
            "Unsupported memory layout: " + ", ".join(description.image_unsupported)
        )
    start = description.memory_base
    base = description.image_origin
    if base is None or start is None or not 0 < description.allocation_size <= 65535:
        raise ValueError("Missing parameter memory base or allocation size")
    relative = description.download_offset
    if relative is not None and start < relative:
        raise ValueError("Parameter memory lies before its relative download offset")
    state = evaluate(description, settings, globals_)
    data = bytearray(b"\xab" * 65535)
    mask = bytearray(65535)

    def write(parameter: Parameter, value: str) -> tuple[int, bytes] | None:
        if not parameter.offset:
            return None
        raw = encode(value, description.types[parameter.type_id])
        if raw is None:
            raise ValueError(f"Cannot encode parameter {parameter.id}")
        offset = parameter.offset - base
        if offset < 0 or offset + len(raw) > len(data):
            raise ValueError(f"Parameter outside memory map: {parameter.id}")
        data[offset : offset + len(raw)] = raw
        mask[offset : offset + len(raw)] = b"\xff" * len(raw)
        return offset, raw

    defaults: dict[int, int] = {}
    for parameter in description.parameters.values():
        written = write(parameter, parameter.default)
        if written is not None:
            offset, raw = written
            for index, value in enumerate(raw, offset):
                if index in defaults and defaults[index] != value:
                    raise ValueError("Conflicting default parameter memory")
                defaults[index] = value
    for ref_id, value in state.memory_values:
        parameter = description.parameters[description.refs[ref_id].parameter_id]
        write(parameter, value)
    # A setting whose download condition is 0 keeps what the device holds.
    for parameter_id in locked_parameters(
        description, state.values, [r for r, _ in state.memory_values]
    ):
        parameter = description.parameters[parameter_id]
        size = byte_size(description.types[parameter.type_id]) or 0
        if parameter.offset:
            offset = parameter.offset - base
            mask[offset : offset + size] = bytes(size)
    length = mask.rfind(b"\xff") + 1
    first = mask.find(b"\xff")
    if (
        not length
        or length - first > description.allocation_size
        or base + length > 65536
        or (
            relative is not None
            and base + length - relative > description.allocation_size
        )
    ):
        raise ValueError(
            "Parameter memory exceeds the supported allocation or address range"
        )
    return CompiledImage(
        base,
        bytes(data),
        bytes(mask),
        description.allocation_size,
        state,
        start,
        relative,
    )
