"""Compile the complete parameter memory and its whole-byte write mask."""

from dataclasses import dataclass

from .blob import PluginSettings
from .codec import encode
from .configuration import Evaluation, evaluate
from .description import Description, Parameter


@dataclass(frozen=True)
class CompiledImage:
    base: int
    data: bytes
    mask: bytes
    allocation_size: int
    state: Evaluation

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
    base = description.memory_base
    if base is None or not 0 < description.allocation_size <= 65535:
        raise ValueError("Missing parameter memory base or allocation size")
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
    length = mask.rfind(b"\xff") + 1
    if not length or length > description.allocation_size or base + length > 65536:
        raise ValueError(
            "Parameter memory exceeds the supported allocation or address range"
        )
    return CompiledImage(
        base, bytes(data), bytes(mask), description.allocation_size, state
    )
