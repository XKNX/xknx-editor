from __future__ import annotations

import base64
import binascii
import ipaddress
import math
import re
import struct
from collections.abc import Callable, Mapping
from typing import NamedTuple

from xknxeditor.namespaces.intermediate import ApplicationProgram
from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_parameter import (
    ApplicationProgramStaticParametersParameter,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_parameters_union import (
    ApplicationProgramStaticParametersUnion,
)
from xknxeditor.namespaces.intermediate.download_behavior_t import DownloadBehavior
from xknxeditor.namespaces.intermediate.memory_parameter_t import MemoryParameter
from xknxeditor.namespaces.intermediate.memory_union_t import MemoryUnion
from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_parameter import (
    ModuleDefStaticParametersParameter,
)
from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_parameter_memory import (
    ModuleDefStaticParametersParameterMemory,
)
from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_parameter_property import (
    ModuleDefStaticParametersParameterProperty,
)
from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_union import (
    ModuleDefStaticParametersUnion,
)
from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_union_memory import (
    ModuleDefStaticParametersUnionMemory,
)
from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_union_property import (
    ModuleDefStaticParametersUnionProperty,
)
from xknxeditor.namespaces.intermediate.module_t_numeric_arg import ModuleNumericArg
from xknxeditor.namespaces.intermediate.parameter_type_t_type_color import (
    ParameterTypeTypeColor,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_color_space import (
    ParameterTypeTypeColorSpace,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_date import (
    ParameterTypeTypeDate,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_float import (
    ParameterTypeTypeFloat,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_float_encoding import (
    ParameterTypeTypeFloatEncoding,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_ipaddress import (
    ParameterTypeTypeIpaddress,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_ipaddress_version import (
    ParameterTypeTypeIpaddressVersion,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_number import (
    ParameterTypeTypeNumber,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_raw_data import (
    ParameterTypeTypeRawData,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction import (
    ParameterTypeTypeRestriction,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_text import (
    ParameterTypeTypeText,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_time import (
    ParameterTypeTypeTime,
)
from xknxeditor.namespaces.intermediate.property_parameter_t import PropertyParameter
from xknxeditor.namespaces.intermediate.property_union_t import PropertyUnion
from xknxeditor.namespaces.intermediate.union_parameter_t import UnionParameter

from ..errors import EncodingError
from .application_indexer import ApplicationIndexer
from .state import GlobalState, ModuleState


class MemWrite(NamedTuple):
    seg_id: str
    offset: int
    bit_offset: int
    param_id: str
    parameter_type: str
    value: str
    # Marks the range as written while keeping its current bytes (an inactive parameter
    # under ``DownloadInvisibleParameters="Background"``).
    keep: bool = False


class PropWrite(NamedTuple):
    object_index: int | None
    property_id: int
    occurrence: int
    offset: int
    bit_offset: int
    param_id: str
    parameter_type: str
    value: str
    # The object type of an object addressed by type (``ObjectType``) instead of index.
    object_type: int | None = None
    keep: bool = False


class Writes:
    __slots__ = ("mem", "prop")

    def __init__(self) -> None:
        self.mem: list[MemWrite] = []
        self.prop: list[PropWrite] = []


PropertyKey = tuple[int | None, int, int]  # keys: object_index, property_id, occurrence
# keys: object_index, object_type, property_id, occurrence
TypedPropertyKey = tuple[int | None, int | None, int, int]


def _program_little_endian(app: ApplicationProgram) -> bool:
    """Whether the application program encodes parameter values little-endian.

    Read from the static options' ``ParameterByteOrder`` (default big-endian).
    """
    options = getattr(app.static, "options", None)
    order = getattr(options, "parameter_byte_order", None)
    return order is not None and order.value == "LittleEndian"


# Bit-widths of the value types that have no ``size_in_bit`` field of their own because
# their encoding fixes the width (see ``type_size_in_bit``).
_FLOAT_ENCODING_SIZE_IN_BIT = {
    ParameterTypeTypeFloatEncoding.DPT_9: 16,  # KNX DPT 9: 2 octets
    ParameterTypeTypeFloatEncoding.IEEE_754_SINGLE: 32,
    ParameterTypeTypeFloatEncoding.IEEE_754_DOUBLE: 64,
}
_DATE_SIZE_IN_BIT = 24  # DPT 11: day/month/year octets (the only Date encoding)
_IPADDRESS_SIZE_IN_BIT = {
    ParameterTypeTypeIpaddressVersion.IPV4: 32,
    ParameterTypeTypeIpaddressVersion.IPV6: 128,
}
_COLOR_SIZE_IN_BIT = {
    ParameterTypeTypeColorSpace.RGB: 24,
    ParameterTypeTypeColorSpace.RGBW: 24,
    ParameterTypeTypeColorSpace.HSV: 24,
}


def type_size_in_bit(tc: object, *, for_property: bool = False) -> int | None:
    """Bit-width of a parameter type's encoded value, or ``None`` for an unknown type.

    Several types carry no ``size_in_bit`` attribute because their encoding fixes the
    width: Float (from its encoding), Date (DPT 11 is 3 octets), IP address (4 octets
    for IPv4, 16 for IPv6), Color (3 octets for every colour space) and RawData. RawData
    in a memory address space reserves 4 extra octets for a length prefix
    (``MaxSize + 4``); a property value has no prefix (``MaxSize``) - the framing is
    memory-only, so ``for_property`` selects between the two (see ``_encode_raw_data``).
    Every other type carries ``size_in_bit`` directly. Without this the memory/property
    writers gated on a plain ``getattr(tc, "size_in_bit", None)`` and silently skipped all
    five sizeless types, leaving their cells at the segment seed.
    """
    if isinstance(tc, ParameterTypeTypeFloat):
        return _FLOAT_ENCODING_SIZE_IN_BIT.get(tc.encoding)
    if isinstance(tc, ParameterTypeTypeDate):
        return _DATE_SIZE_IN_BIT
    if isinstance(tc, ParameterTypeTypeIpaddress):
        return _IPADDRESS_SIZE_IN_BIT.get(tc.version)
    if isinstance(tc, ParameterTypeTypeColor):
        return _COLOR_SIZE_IN_BIT.get(tc.space)
    if isinstance(tc, ParameterTypeTypeRawData):
        # MaxSize bounds the payload; a memory image reserves 4 extra octets for the
        # length prefix, a property value carries none.
        return tc.max_size * 8 if for_property else (tc.max_size + 4) * 8
    return getattr(tc, "size_in_bit", None)


def _write_bits(
    buf: bytearray, offset: int, bit_offset: int, size_in_bit: int, value: int
) -> None:
    """Pack value big-endian into buf at offset+bit_offset (bit 0 = byte MSB)."""
    start = offset * 8 + bit_offset
    for i in range(size_in_bit):
        pos = start + i
        bit_mask = 1 << (7 - pos % 8)
        if (value >> (size_in_bit - 1 - i)) & 1:
            buf[pos // 8] |= bit_mask
        else:
            buf[pos // 8] &= ~bit_mask


def _encode_value(
    str_value: str,
    size_in_bit: int,
    tc: object,
    *,
    little_endian: bool = False,
    for_property: bool = False,
) -> int | None:
    """Encode a parameter value string into the integer that ``_write_bits`` packs.

    Values are packed MSB-first. The default byte order is big-endian; when the
    application program selects little-endian (``ParameterByteOrder`` in its static
    options), the byte order of a multi-octet numeric value is reversed, as the
    KNX standard requires (the octets of integer and float values are reversed for
    a little-endian program). Implements the standard's per-type value encoders.

    ``for_property`` selects the RawData framing: a memory value carries a 4-octet
    length prefix, a property value carries none (see ``_encode_raw_data``).
    """
    try:
        str_value = validate_parameter_value(str_value, tc)
    except ValueError:
        return None
    if isinstance(tc, (ParameterTypeTypeNumber, ParameterTypeTypeTime)):
        # A time value is stored as a plain integer in the type's unit.
        return _apply_byte_order(
            _encode_number(str_value, size_in_bit), size_in_bit, little_endian
        )

    if isinstance(tc, ParameterTypeTypeRestriction):
        return _encode_restriction(str_value, size_in_bit, tc, little_endian)

    if isinstance(tc, ParameterTypeTypeFloat):
        return _apply_byte_order(
            _encode_float(str_value, size_in_bit, tc), size_in_bit, little_endian
        )

    if isinstance(tc, ParameterTypeTypeText):
        return _encode_text(str_value, size_in_bit)

    if isinstance(tc, ParameterTypeTypeDate):
        return _encode_date(str_value, tc)

    if isinstance(tc, ParameterTypeTypeIpaddress):
        return _encode_ipaddress(str_value, tc)

    if isinstance(tc, ParameterTypeTypeColor):
        return _encode_color(str_value, size_in_bit, tc)

    if isinstance(tc, ParameterTypeTypeRawData):
        return _encode_raw_data(
            str_value, tc.max_size, little_endian, with_prefix=not for_property
        )

    return None


def _apply_byte_order(
    value: int | None, size_in_bit: int, little_endian: bool
) -> int | None:
    """Reverse the octet order of a byte-aligned multi-octet numeric ``value``.

    Only applies for a little-endian program and a whole-octet field (at least two
    octets); single-octet and sub-octet fields are unaffected.
    """
    if value is None or not little_endian or size_in_bit < 16 or size_in_bit % 8 != 0:
        return value
    n = size_in_bit // 8
    return int.from_bytes(value.to_bytes(n, "big")[::-1], "big")


def _encode_restriction(
    str_value: str,
    size_in_bit: int,
    tc: ParameterTypeTypeRestriction,
    little_endian: bool,
) -> int | None:
    """Encode an enumeration value of a restricted parameter type.

    An enumeration entry may carry an explicit ``BinaryValue`` (the exact octets to
    store, e.g. a priority ordering); that is written verbatim, big-endian, and is
    not subject to the program byte order. Entries without a binary value fall back
    to encoding the enumeration value as an integer (honouring the byte order).
    """
    for enumeration in tc.enumeration:
        if str(enumeration.value) == str_value:
            if enumeration.binary_value:
                return int.from_bytes(enumeration.binary_value, "big")
            break
    return _apply_byte_order(
        _encode_number(str_value, size_in_bit), size_in_bit, little_endian
    )


def _encode_number(str_value: str, size_in_bit: int) -> int | None:
    """Signed/unsigned integer, masked to ``size_in_bit`` (two's complement)."""
    try:
        v = int(str_value, 0) if str_value[:2].lower() == "0x" else int(str_value)
    except (ValueError, TypeError):
        return None
    return v & ((1 << size_in_bit) - 1)


def validate_parameter_value(value: str, tc: object) -> str:
    """Validate integer domains and resolve restriction labels to numeric values."""
    if isinstance(tc, ParameterTypeTypeNumber):
        number = int(value, 16) if value[:2].lower() == "0x" else int(value)
        if not tc.min_inclusive <= number <= tc.max_inclusive:
            raise ValueError(
                f"value {value!r} outside {tc.min_inclusive}..{tc.max_inclusive}"
            )
    elif isinstance(tc, ParameterTypeTypeRestriction):
        try:
            numeric = int(value)
        except ValueError:
            numeric = -1
        if 0 <= numeric <= 0xFFFFFFFF:
            return value
        for entry in tc.enumeration:
            if entry.text == value:
                return str(entry.value)
        raise ValueError(f"invalid restriction value {value!r}")
    return value


def _to_single(x: float) -> float:
    """Round a double to IEEE-754 single precision (a 32-bit ``float``).

    A value beyond binary32's range yields signed infinity, not an error - the KNX
    standard scales in float32 and lets the DPT 9 exponent loop saturate the result, so
    returning infinity (rather than letting ``struct.pack`` raise) keeps a finite-but-huge
    input on the saturation path instead of failing to encode.
    """
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.inf if x > 0 else -math.inf


def _encode_float(
    str_value: str, size_in_bit: int, tc: ParameterTypeTypeFloat
) -> int | None:
    try:
        f = float(str_value)
    except (ValueError, TypeError):
        return None
    # nan/inf survive float() but blow up round() (DPT 9) or are silently accepted by
    # struct.pack (IEEE) - either way they are not encodable, so reject them up front and
    # let the write path raise EncodingError rather than programming a bogus image.
    if not math.isfinite(f):
        return None
    try:
        if tc.encoding == ParameterTypeTypeFloatEncoding.DPT_9:
            # KNX DPT 9 (2 octet float): value = 0.01 * m * 2^exp, m an 11 bit signed
            # two's complement (its sign is replicated into bit 15), exponent in bits 14..11.
            # The KNX standard picks the smallest exponent whose real scaled value fits
            # the mantissa range, then rounds once on that scaled value (banker's rounding).
            # The bound is asymmetric - a positive value may reach 2047, a negative one 2048 -
            # and a mantissa that rounds to zero carries no sign bit. Rounding the *signed*
            # value and deriving the sign from the result reproduces both: round(-0.1) == 0
            # -> no sign. The scaling is done in SINGLE precision (a float32 multiply by 100)
            # and only then widened to double for the exponent search and rounding. Scaling in
            # float64 instead diverges by an LSB or a whole exponent (0.295 -> 0x1D not 0x1E;
            # 20.4700001 -> 0x07FF not 0x0C00), so reproduce the single-precision scale before
            # widening. A finite input whose scaled value overflows binary32 becomes +/-inf
            # (see _to_single) and falls straight through the loop into the saturation branch.
            scaled = _to_single(_to_single(f) * 100.0)
            negative = scaled < 0.0
            threshold = 2048.0 if negative else 2047.0
            exp = 0
            while abs(scaled) / (1 << exp) > threshold:
                exp += 1
                if exp > 15:
                    # The KNX standard saturates rather than failing: the exponent clamps
                    # to 15 and the magnitude to its range limit, giving 0x7FFF for a positive
                    # overflow and 0xF800 for a negative one.
                    return (0x8000 | (15 << 11)) if negative else ((15 << 11) | 0x7FF)
            mantissa = round(scaled / (1 << exp))
            sign = 0x8000 if mantissa < 0 else 0
            return sign | (exp << 11) | (mantissa & 0x7FF)
        if tc.encoding == ParameterTypeTypeFloatEncoding.IEEE_754_SINGLE:
            return struct.unpack(">I", struct.pack(">f", f))[0]
        if tc.encoding == ParameterTypeTypeFloatEncoding.IEEE_754_DOUBLE:
            return struct.unpack(">Q", struct.pack(">d", f))[0]
    except (ValueError, OverflowError):
        return None
    return None


def _encode_text(str_value: str, size_in_bit: int) -> int:
    """Code-page (Latin-1) text, truncated and zero-padded to the field width.

    Zero padding also provides the null terminator implicitly (the KNX standard
    relies on the zero-initialised buffer for termination).
    """
    encoded = str_value.encode("latin-1", errors="replace")
    n_bytes = size_in_bit // 8
    padded = encoded[:n_bytes].ljust(n_bytes, b"\x00")
    return int.from_bytes(padded, "big")


def _encode_date(str_value: str, tc: ParameterTypeTypeDate) -> int | None:
    """KNX date: 3 octets day, month, year mod 100 (value ``YYYY-MM-DD``).

    When the type does not display the year, the year octet stays zero (the
    KNX standard writes only day and month for that formatting).
    """
    parts = str_value.split("-")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(p) for p in parts)
    except ValueError:
        return None
    year_octet = 0 if tc.display_the_year is False else year % 100
    return (day << 16) | (month << 8) | year_octet


def _encode_ipaddress(str_value: str, tc: ParameterTypeTypeIpaddress) -> int | None:
    """An IP address to its network-order octets: 4 for IPv4, 16 for IPv6.

    The address family must match the type's declared ``version`` (a v4 literal in a
    v6 field, or vice versa, does not encode).
    """
    try:
        addr = ipaddress.ip_address(str_value.strip())
    except ValueError:
        return None
    want = 16 if tc.version == ParameterTypeTypeIpaddressVersion.IPV6 else 4
    packed = addr.packed
    if len(packed) != want:
        return None
    return int.from_bytes(packed, "big")


def _encode_color(
    str_value: str, size_in_bit: int, tc: ParameterTypeTypeColor
) -> int | None:
    """Colour from a ``#RRGGBB`` hex string, per the type's space.

    Every colour space encodes to three octets per the KNX standard:

    - RGB: ``[R, G, B]``.
    - RGBW: ``[R, G, B]`` (the white channel is carried out of band, not in this field).
    - HSV: ``[H, S, V]`` converted from the RGB value (H scaled to a single octet), as
      the KNX standard's RGB-to-HSV conversion defines.
    """
    try:
        raw = bytes.fromhex(str_value.lstrip("#"))
    except ValueError:
        return None
    if len(raw) < 3:
        return None
    r, g, b = raw[0], raw[1], raw[2]
    if tc.space == ParameterTypeTypeColorSpace.HSV:
        h, s, v = _rgb_to_hsv(r, g, b)
        return (h << 16) | (s << 8) | v
    return (r << 16) | (g << 8) | b


def _rgb_to_hsv(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Convert an RGB triple to the KNX HSV octet triple (H scaled to 0..255)."""
    low = min(r, g, b)
    high = max(r, g, b)
    if low == high:
        hue = 0.0
    elif high == r:
        hue = 60.0 * (g - b) / (high - low)
    elif high == g:
        hue = 60.0 * (2.0 + (b - r) / (high - low))
    else:
        hue = 60.0 * (4.0 + (r - g) / (high - low))
    if hue < 0.0:
        hue += 360.0
    elif hue > 360.0:
        hue -= 360.0
    saturation = 0 if high == 0 else int(255.0 * (high - low) / high)
    return int(255.0 * hue / 360.0), saturation, high


# Whitespace characters a standard base64 decoder ignores (ASCII space/tab/CR/LF only).
_BASE64_IGNORED_WHITESPACE = {ord(c): None for c in " \t\r\n"}


def _encode_raw_data(
    str_value: str, max_size: int, little_endian: bool, *, with_prefix: bool
) -> int | None:
    """Variable-length data zero-padded to fill the field.

    ``MaxSize`` bounds the *payload* octets directly. A memory image reserves four extra
    octets for a length prefix (``with_prefix``), so the whole field is ``MaxSize + 4``
    octets; a property value has no prefix and the field is ``MaxSize`` octets (the ``+4``
    framing is applied only for memory address spaces, never for properties).

    The value is base64-encoded in the project XML. When present, the length prefix (the
    payload byte count) follows the program byte order - big-endian for a Motorola/BigEndian
    program, reversed for a LittleEndian one. The payload octets are opaque and never reversed.
    """
    # The value is base64-decoded ignoring only the ASCII whitespace characters
    # space/tab/CR/LF (not other Unicode whitespace), so strip exactly those - a
    # line-wrapped/pretty-printed value still encodes, an NBSP does not.
    try:
        data = base64.b64decode(
            str_value.translate(_BASE64_IGNORED_WHITESPACE), validate=True
        )
    except (binascii.Error, ValueError):
        return None
    if len(data) > max_size:  # the payload must fit MaxSize
        return None
    padded = data.ljust(max_size, b"\x00")
    if not with_prefix:
        return int.from_bytes(padded, "big")
    length_bytes = len(data).to_bytes(4, "little" if little_endian else "big")
    return int.from_bytes(length_bytes + padded, "big")


def _raw_data_written_octets(str_value: str, max_size: int) -> int | None:
    """Octets a RawData memory value actually writes: the 4-octet length prefix plus the
    real payload. The allocated tail (up to ``MaxSize``) is left untouched at the segment
    seed - the KNX standard writes only the prefix and the actual payload length, not the
    full reserved field. Returns ``None`` for an unencodable value (the caller raises)."""
    try:
        data = base64.b64decode(
            str_value.translate(_BASE64_IGNORED_WHITESPACE), validate=True
        )
    except (binascii.Error, ValueError):
        return None
    if len(data) > max_size:
        return None
    return 4 + len(data)


def resolve_param_values(idx: ApplicationIndexer, state: GlobalState) -> dict[str, str]:
    """Build {referenced_id: effective_value} for the ACTIVE ParameterRefs.

    The encoder writes each memory/property cell from the ParameterRef that is active in
    the resolved UI, taking its instance value, else its ref default, else the base
    Parameter default (``state.get`` resolves that whole chain). Parameters whose
    ref is not active are not encoded at all - their cells stay at the segment seed.

    Keys are the referenced ``Parameter``/``UnionParameter`` id (``pr.ref_id``),
    matching ``item.id`` in :func:`_collect_param` and ``up.id`` in a union pick.
    Module-qualified active refs are not in ``idx.parameter_refs``; the module
    collection path resolves those from its own instance state instead.
    """
    # When several active refs target the same parameter cell (the evaluator can
    # reach more than one in overlapping conditional branches), the KNX standard
    # keeps the first one in definition order. Iterate parameter_refs in
    # their (XML/insertion) order and take the first active ref per parameter.
    active = state.active_param_refs()
    overrides: dict[str, str] = {}
    for ref_id, pr in idx.parameter_refs.items():
        if ref_id not in active or pr.ref_id in overrides:
            continue
        value = state.get(ref_id)
        if value is not None:
            overrides[pr.ref_id] = value
    # Explicitly configured union alternatives drive the union selection even if
    # the evaluator did not mark their ref active; carry them too.
    for ref_id, _ in state.relative_param_values():
        pr = idx.parameter_refs.get(ref_id)
        if pr is None or not isinstance(idx.parameters.get(pr.ref_id), UnionParameter):
            continue
        value = state.get(ref_id)
        if value is not None:
            overrides[pr.ref_id] = value
    return overrides


def download_behavior(app: ApplicationProgram) -> DownloadBehavior:
    """How a download treats parameters without an active ParameterRef.

    ``DownloadInvisibleParameters`` applies from MinEtsVersion 5.0 with an Options element;
    older applications and applications without Options use ``Background``.
    """
    options = app.static.options
    # Parsed newer schemas keep the attribute as a plain string.
    version = str(getattr(app.min_ets_version, "value", app.min_ets_version) or "")
    match = re.match(r"(\d+)\.(\d+)", version)
    if options is None or match is None or (int(match[1]), int(match[2])) < (5, 0):
        return DownloadBehavior.BACKGROUND
    return options.download_invisible_parameters


def download_param_values(
    app: ApplicationProgram, idx: ApplicationIndexer, state: GlobalState
) -> dict[str, str]:
    """The parameter values a download writes.

    Those of the active ParameterRefs (:func:`resolve_param_values`) and, with
    ``DownloadInvisibleParameters="DefaultValue"``, the product default of every referenced
    parameter that has no active ParameterRef, and of the default alternative of every union
    none of whose alternatives is active.
    """
    values = resolve_param_values(idx, state)
    if (
        download_behavior(app) is not DownloadBehavior.DEFAULT_VALUE
        or app.static.parameters is None
    ):
        return values
    referenced = {ref.ref_id for ref in idx.parameter_refs.values()}
    for item in app.static.parameters.choice:
        if isinstance(item, ApplicationProgramStaticParametersParameter):
            if item.id in referenced and item.id not in values:
                values[item.id] = item.value
            continue
        members = item.parameter
        if any(up.id in values for up in members) or not any(
            up.id in referenced for up in members
        ):
            continue
        default = next((up for up in members if up.default_union_parameter), None)
        if default is not None:
            values[default.id] = default.value
    return values


def _resolve_base(base_id: str | None, ms: ModuleState) -> int | None:
    if base_id is None:
        return 0
    arg = ms.arguments.get(base_id)
    if not isinstance(arg, ModuleNumericArg) or arg.value is None:
        return None
    return arg.value


def _build_instance_overrides(
    ms: ModuleState, idx: ApplicationIndexer
) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for pr_id, value in ms.param_ref_id_to_value.items():
        pr = idx.parameter_refs.get(pr_id)
        if pr is not None:
            param = idx.parameters.get(pr.ref_id)
            if param is not None:
                overrides[param.id] = value
    return overrides


def _union_params_to_write(
    parameters: list[UnionParameter],
    overrides: dict[str, str],
    gated: bool = False,
) -> list[tuple[UnionParameter, str]]:
    """Return the union alternatives to write, each with its value.

    Union alternatives can sit at different bit offsets within the shared cell, so
    more than one may be active at once; each active alternative is written (their
    bits accumulate), as the KNX standard requires. When nothing is active: in a
    resolved UI (gated) the cell keeps its seed; a bare caller falls back to the
    union's default alternative so ``collect_writes`` still yields a value.
    """
    active = [up for up in parameters if up.id in overrides]
    if active:
        return [(up, overrides[up.id]) for up in active]
    if gated:
        return []
    default_up = next((up for up in parameters if up.default_union_parameter), None)
    return [(default_up, default_up.value)] if default_up is not None else []


def _collect_param(
    item: ApplicationProgramStaticParametersParameter
    | ModuleDefStaticParametersParameter,
    overrides: dict[str, str],
    ms: ModuleState | None,
    out: Writes,
    active_ids: set[str] | None = None,
) -> None:
    # When an active-ref set is given (resolved UI), encode only active parameters;
    # inactive cells are left at the segment seed, as the KNX standard does.
    if active_ids is not None and item.id not in active_ids:
        return
    choice = item.choice
    # An explicit override of "" (empty RawData/Text) is a real value, not "unset", so
    # membership decides the fallback - not truthiness, which would drop it to the default.
    value = overrides.get(item.id, item.value)
    # A module parameter's base_value offsets the encoded value by an arg-resolved amount.
    if (
        isinstance(item, ModuleDefStaticParametersParameter)
        and item.base_value is not None
        and ms is not None
    ):
        bv = _resolve_base(item.base_value, ms)
        if bv is not None and bv != 0:
            value = str(int(value) + bv)
    # Test subclasses first: module types derive from the top-level ones
    if isinstance(choice, ModuleDefStaticParametersParameterMemory):
        assert ms is not None
        base = _resolve_base(choice.base_offset, ms)
        if base is not None:
            out.mem.append(
                MemWrite(
                    choice.code_segment,
                    base + choice.offset,
                    choice.bit_offset,
                    item.id,
                    item.parameter_type,
                    value,
                )
            )
    elif isinstance(choice, MemoryParameter):
        out.mem.append(
            MemWrite(
                choice.code_segment,
                choice.offset,
                choice.bit_offset,
                item.id,
                item.parameter_type,
                value,
            )
        )
    elif isinstance(choice, ModuleDefStaticParametersParameterProperty):
        assert ms is not None
        bo = _resolve_base(choice.base_offset, ms)
        bi = _resolve_base(choice.base_index, ms)
        boc = _resolve_base(choice.base_occurrence, ms)
        if bo is not None and bi is not None and boc is not None:
            obj_idx = (choice.object_index or 0) + bi if bi else choice.object_index
            out.prop.append(
                PropWrite(
                    obj_idx,
                    choice.property_id,
                    choice.occurrence + boc,
                    bo + choice.offset,
                    choice.bit_offset,
                    item.id,
                    item.parameter_type,
                    value,
                    choice.object_type,
                )
            )
    elif isinstance(choice, PropertyParameter):
        out.prop.append(
            PropWrite(
                choice.object_index,
                choice.property_id,
                choice.occurrence,
                choice.offset,
                choice.bit_offset,
                item.id,
                item.parameter_type,
                value,
                choice.object_type,
            )
        )
    # skip IoPointParameter and None


def _collect_union(
    item: ApplicationProgramStaticParametersUnion | ModuleDefStaticParametersUnion,
    overrides: dict[str, str],
    ms: ModuleState | None,
    out: Writes,
    gated: bool = False,
) -> None:
    choice = item.choice
    if choice is None:
        return
    selected = _union_params_to_write(item.parameter, overrides, gated)
    # Test subclasses first: module types derive from the top-level ones
    if isinstance(choice, ModuleDefStaticParametersUnionMemory):
        assert ms is not None
        base = _resolve_base(choice.base_offset, ms)
        if base is not None:
            for up, value in selected:
                out.mem.append(
                    MemWrite(
                        choice.code_segment,
                        base + choice.offset + up.offset,
                        choice.bit_offset + up.bit_offset,
                        up.id,
                        up.parameter_type,
                        value,
                    )
                )
    elif isinstance(choice, MemoryUnion):
        for up, value in selected:
            out.mem.append(
                MemWrite(
                    choice.code_segment,
                    choice.offset + up.offset,
                    choice.bit_offset + up.bit_offset,
                    up.id,
                    up.parameter_type,
                    value,
                )
            )
    elif isinstance(choice, ModuleDefStaticParametersUnionProperty):
        assert ms is not None
        bo = _resolve_base(choice.base_offset, ms)
        bi = _resolve_base(choice.base_index, ms)
        boc = _resolve_base(choice.base_occurrence, ms)
        if bo is not None and bi is not None and boc is not None:
            obj_idx = (choice.object_index or 0) + bi if bi else choice.object_index
            for up, value in selected:
                out.prop.append(
                    PropWrite(
                        obj_idx,
                        choice.property_id,
                        choice.occurrence + boc,
                        bo + choice.offset + up.offset,
                        choice.bit_offset + up.bit_offset,
                        up.id,
                        up.parameter_type,
                        value,
                        choice.object_type,
                    )
                )
    else:
        assert isinstance(choice, PropertyUnion)
        for up, value in selected:
            out.prop.append(
                PropWrite(
                    choice.object_index,
                    choice.property_id,
                    choice.occurrence,
                    choice.offset + up.offset,
                    choice.bit_offset + up.bit_offset,
                    up.id,
                    up.parameter_type,
                    value,
                    choice.object_type,
                )
            )


def _collect_scope_writes(
    ms: ModuleState,
    idx: ApplicationIndexer,
    out: Writes,
    behavior: DownloadBehavior | None = None,
    keep: bool = False,
) -> None:
    """Collect one module instance scope's writes (not its children), resolved to
    that instance's offsets.

    Without ``behavior`` every parameter is collected with its instance value or default.
    With it, the instance's active ParameterRefs give the values, and inactive parameters
    are handled as the application's ``DownloadInvisibleParameters`` asks; ``keep`` adds
    the ranges of inactive parameters under ``Background``.
    """
    if ms.ref_id is None:
        return
    md = idx.module_defs.get(ms.ref_id)
    if md is None or md.static.parameters is None:
        return
    if behavior is None:
        instance_overrides = _build_instance_overrides(ms, idx)
        for item in md.static.parameters.choice:
            if isinstance(item, ModuleDefStaticParametersParameter):
                _collect_param(item, instance_overrides, ms, out)
            else:
                assert isinstance(item, ModuleDefStaticParametersUnion)
                _collect_union(item, instance_overrides, ms, out)
        return
    refs = md.static.parameter_refs.parameter_ref if md.static.parameter_refs else []
    referenced = {ref.ref_id for ref in refs}
    active = ms.local_active_param_refs()
    values: dict[str, str] = {}
    for ref in refs:
        if ref.id in active and ref.ref_id not in values:
            value = ms.get(ref.id)
            if value is not None:
                values[ref.ref_id] = value
    active_params = set(values)
    items = sorted(
        md.static.parameters.choice,
        key=lambda item: not _has_active_param(item, active_params),
    )
    defaults = behavior is DownloadBehavior.DEFAULT_VALUE
    for item in items:
        if isinstance(item, ModuleDefStaticParametersParameter):
            if item.id in values:
                _collect_param(item, values, ms, out)
            elif item.id in referenced and defaults:
                _collect_param(item, {}, ms, out)
            elif item.id in referenced and keep:
                _collect_kept(item, ms, out)
            continue
        assert isinstance(item, ModuleDefStaticParametersUnion)
        members = [up for up in item.parameter if up.id in referenced]
        if any(up.id in values for up in item.parameter):
            _collect_union(item, values, ms, out, gated=True)
        elif members and defaults:
            _collect_union(item, {}, ms, out)
        elif members and keep:
            _collect_kept(item, ms, out, members)


def _collect_kept(
    item: ApplicationProgramStaticParametersParameter
    | ModuleDefStaticParametersParameter
    | ApplicationProgramStaticParametersUnion
    | ModuleDefStaticParametersUnion,
    ms: ModuleState | None,
    out: Writes,
    members: list[UnionParameter] | None = None,
) -> None:
    """Collect the range of an inactive parameter (or of union ``members``), to be written
    with its current bytes."""
    kept = Writes()
    if isinstance(
        item,
        ApplicationProgramStaticParametersParameter
        | ModuleDefStaticParametersParameter,
    ):
        _collect_param(item, {}, ms, kept)
    else:
        _collect_union(item, {up.id: up.value for up in members or []}, ms, kept)
    out.mem.extend(w._replace(keep=True) for w in kept.mem)
    out.prop.extend(w._replace(keep=True) for w in kept.prop)


def _collect_module_writes(
    ms: ModuleState,
    idx: ApplicationIndexer,
    out: Writes,
    behavior: DownloadBehavior | None = None,
    keep: bool = False,
) -> None:
    _collect_scope_writes(ms, idx, out, behavior, keep)
    for child in ms.module_children():
        _collect_module_writes(child, idx, out, behavior, keep)


def collect_writes(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None = None,
    *,
    inactive_ranges: bool = False,
) -> Writes:
    """Gather every parameter write into a Writes bundle, split into mem and prop.

    With ``inactive_ranges``, an application downloading invisible parameters as
    ``Background`` also yields the ranges of inactive parameters, marked ``keep``.
    """
    out = Writes()
    s = app.static
    behavior = download_behavior(app)
    keep = inactive_ranges and behavior is DownloadBehavior.BACKGROUND
    referenced = {ref.ref_id for ref in idx.parameter_refs.values()}
    # With a resolved state, gate top-level parameters to the active set (regular
    # parameters present in overrides); inactive cells stay at the segment seed.
    # Without a state (direct callers), no gating: every static parameter encodes
    # as before. Unions keep their explicit-selection behaviour either way.
    active_ids = (
        {k for k in overrides if not isinstance(idx.parameters.get(k), UnionParameter)}
        if state is not None
        else None
    )
    if s.parameters is not None:
        items = list(s.parameters.choice)
        if state is not None:
            # Values of active ParameterRefs come first and the defaults of inactive
            # parameters after them, so a default wins where the two share bits.
            active_params = {
                idx.parameter_refs[ref].ref_id
                for ref in state.active_param_refs()
                if ref in idx.parameter_refs
            }
            items.sort(key=lambda item: not _has_active_param(item, active_params))
        for item in items:
            if isinstance(item, ApplicationProgramStaticParametersParameter):
                if active_ids is not None and item.id not in active_ids:
                    if keep and item.id in referenced:
                        _collect_kept(item, None, out)
                    continue
                _collect_param(item, overrides, None, out)
                continue
            assert isinstance(item, ApplicationProgramStaticParametersUnion)
            members = [up for up in item.parameter if up.id in referenced]
            if (
                keep
                and active_ids is not None
                and members
                and not any(up.id in overrides for up in item.parameter)
            ):
                _collect_kept(item, None, out, members)
                continue
            _collect_union(item, overrides, None, out, gated=active_ids is not None)
    if state is not None:
        for ms in state.module_children():
            _collect_module_writes(ms, idx, out, behavior, keep)
    return out


def _has_active_param(
    item: ApplicationProgramStaticParametersParameter
    | ApplicationProgramStaticParametersUnion
    | ModuleDefStaticParametersParameter
    | ModuleDefStaticParametersUnion,
    active_params: set[str],
) -> bool:
    if isinstance(
        item,
        ApplicationProgramStaticParametersParameter
        | ModuleDefStaticParametersParameter,
    ):
        return item.id in active_params
    return any(up.id in active_params for up in item.parameter)


def _written_width_in_bit(tc: object, value: str) -> int | None:
    """Bits a memory write actually drives, or ``None`` for an unknown type width.

    Equals the full type width, except RawData narrows to its 4-octet length prefix plus
    the real payload - the reserved tail up to ``MaxSize`` stays at the segment seed and is
    never written. Single source of truth shared by the packer and the bit-mask.
    """
    size_in_bit = type_size_in_bit(tc)
    if size_in_bit is None:
        return None
    if isinstance(tc, ParameterTypeTypeRawData):
        written = _raw_data_written_octets(value, tc.max_size)
        if written is not None and written * 8 < size_in_bit:
            return written * 8
    return size_in_bit


def _plan_memory_write(
    tc: object,
    value: str,
    param_id: str,
    parameter_type: str,
    *,
    little_endian: bool,
) -> tuple[int, int] | None:
    """Plan one memory cell write: return ``(value_to_pack, bits_to_write)`` or ``None``
    to skip the cell (unknown type width).

    Raises :class:`EncodingError` if the value cannot be encoded against its type. For
    RawData only the 4-octet length prefix plus the real payload is written; the reserved
    tail up to ``MaxSize`` stays at the segment seed, so the returned value is narrowed to
    those leading octets and ``bits_to_write`` shrinks to match.
    """
    size_in_bit = type_size_in_bit(tc)
    if size_in_bit is None:
        return None
    encoded = _encode_value(value, size_in_bit, tc, little_endian=little_endian)
    if encoded is None:
        raise EncodingError(
            f"cannot encode parameter {param_id} value {value!r} as {parameter_type}"
        )
    write_bits = _written_width_in_bit(tc, value) or size_in_bit
    if write_bits < size_in_bit:
        # Keep the leading prefix+payload octets (the padded tail is the low bits).
        return encoded >> (size_in_bit - write_bits), write_bits
    return encoded, size_in_bit


def encode_to_memory(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None = None,
) -> dict[str, bytes]:
    """Pack parameter values into per-segment byte buffers.

    Yields {segment_id: bytes} for each segment, seeded from seg.data when set.
    Layout: bit_offset 0 is a byte's MSB; values are big-endian.
    """
    writes = collect_writes(app, idx, overrides, state)
    little_endian = _program_little_endian(app)
    bufs: dict[str, bytearray] = {
        seg_id: bytearray(seg.data) if seg.data else bytearray(seg.size)
        for seg_id, seg in idx.code_segments.items()
    }
    for w in writes.mem:
        buf = bufs.get(w.seg_id)
        if buf is None:
            continue
        pt = idx.parameter_types.get(w.parameter_type)
        if pt is None:
            continue
        plan = _plan_memory_write(
            pt.choice,
            w.value,
            w.param_id,
            w.parameter_type,
            little_endian=little_endian,
        )
        if plan is None:
            continue
        encoded, write_bits = plan
        _write_bits(buf, w.offset, w.bit_offset, write_bits, encoded)
    return {seg_id: bytes(buf) for seg_id, buf in bufs.items()}


def encode_to_memory_masked(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None = None,
) -> dict[str, tuple[bytes, bytes]]:
    """Like :func:`encode_to_memory` but also return a write mask per segment.

    Returns ``{segment_id: (data, mask)}`` where ``mask`` has one byte per data
    byte: ``0xFF`` for bytes an encoded parameter actually wrote, ``0x00`` for
    bytes left at the segment seed. A downloader writes only masked bytes, so it
    never overwrites regions this encoder does not produce (e.g. the com object
    table or RAM) - as the KNX standard does, loading only touched
    bytes on a partial download.
    """
    writes = collect_writes(app, idx, overrides, state, inactive_ranges=True)
    little_endian = _program_little_endian(app)
    bufs: dict[str, bytearray] = {
        seg_id: bytearray(seg.data) if seg.data else bytearray(seg.size)
        for seg_id, seg in idx.code_segments.items()
    }
    masks: dict[str, bytearray] = {
        seg_id: bytearray(len(buf)) for seg_id, buf in bufs.items()
    }
    for w in writes.mem:
        buf = bufs.get(w.seg_id)
        if buf is None:
            continue
        pt = idx.parameter_types.get(w.parameter_type)
        if pt is None:
            continue
        if w.keep:
            # Written with the bytes the segment already holds.
            size_in_bit = type_size_in_bit(pt.choice)
            if size_in_bit:
                start_bit = w.offset * 8 + w.bit_offset
                mask = masks[w.seg_id]
                for b in range(start_bit // 8, (start_bit + size_in_bit - 1) // 8 + 1):
                    if b < len(mask):
                        mask[b] = 0xFF
            continue
        plan = _plan_memory_write(
            pt.choice,
            w.value,
            w.param_id,
            w.parameter_type,
            little_endian=little_endian,
        )
        if plan is None:
            continue
        encoded, write_bits = plan
        _write_bits(buf, w.offset, w.bit_offset, write_bits, encoded)
        start_bit = w.offset * 8 + w.bit_offset
        end_bit = start_bit + write_bits - 1
        mask = masks[w.seg_id]
        for b in range(start_bit // 8, end_bit // 8 + 1):
            if b < len(mask):
                mask[b] = 0xFF
    return {seg_id: (bytes(buf), bytes(masks[seg_id])) for seg_id, buf in bufs.items()}


def build_memory_param_map(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None = None,
) -> dict[str, dict[int, tuple[str, str]]]:
    """Map {seg_id: {byte_offset: (param_id, value)}} for hex-viewer hovers."""
    writes = collect_writes(app, idx, overrides, state)
    maps: dict[str, dict[int, tuple[str, str]]] = {
        seg_id: {} for seg_id in idx.code_segments
    }
    for w in writes.mem:
        seg_map = maps.get(w.seg_id)
        if seg_map is None:
            continue
        pt = idx.parameter_types.get(w.parameter_type)
        if pt is None:
            continue
        size = type_size_in_bit(pt.choice)
        if not size:
            continue
        start_bit = w.offset * 8 + w.bit_offset
        end_bit = start_bit + size - 1
        for b in range(start_bit // 8, end_bit // 8 + 1):
            seg_map[b] = (w.param_id, w.value)
    return maps


def written_bit_mask(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None = None,
) -> dict[str, bytes]:
    """Return ``{seg_id: bit_mask}`` marking which bits an active parameter writes.

    Unlike :func:`encode_to_memory_masked`, whose mask is byte granular (a byte is
    marked as soon as any parameter touches it), this marks the individual **bits**
    a parameter actually drives. Each mask byte holds, MSB first, a ``1`` for every
    bit position covered by a collected write (``1 << (7 - pos % 8)``); every other
    bit stays ``0``.

    A pre-flight uses this to tell apart, within one written byte, the bits that
    carry a real parameter value from the bits it only rewrites to the segment seed
    (the application default) because a neighbouring parameter shares the byte.
    """
    writes = collect_writes(app, idx, overrides, state)
    masks: dict[str, bytearray] = {
        seg_id: bytearray(seg.size if not seg.data else len(seg.data))
        for seg_id, seg in idx.code_segments.items()
    }
    for w in writes.mem:
        mask = masks.get(w.seg_id)
        if mask is None:
            continue
        pt = idx.parameter_types.get(w.parameter_type)
        size_in_bit = (
            _written_width_in_bit(pt.choice, w.value) if pt is not None else None
        )
        if not size_in_bit:
            continue
        start_bit = w.offset * 8 + w.bit_offset
        for pos in range(start_bit, start_bit + size_in_bit):
            byte_index = pos // 8
            if byte_index < len(mask):
                mask[byte_index] |= 1 << (7 - pos % 8)
    return {seg_id: bytes(mask) for seg_id, mask in masks.items()}


def _read_bits(data: bytes, offset: int, bit_offset: int, size_in_bit: int) -> int:
    """Read ``size_in_bit`` bits MSB-first from ``data`` - the inverse of ``_write_bits``."""
    start = offset * 8 + bit_offset
    value = 0
    for i in range(size_in_bit):
        pos = start + i
        byte_index = pos // 8
        bit = (data[byte_index] >> (7 - pos % 8)) & 1 if byte_index < len(data) else 0
        value = (value << 1) | bit
    return value


def _is_signed_number(tc: ParameterTypeTypeNumber) -> bool:
    """Whether a number type encodes signed values (a negative lower bound)."""
    try:
        return int(tc.min_inclusive) < 0
    except (TypeError, ValueError):
        return False


def _field_bytes(raw: int, size_in_bit: int) -> bytes | None:
    """The big-endian octets of a byte-aligned field, or ``None`` if sub-octet."""
    if size_in_bit <= 0 or size_in_bit % 8:
        return None
    return raw.to_bytes(size_in_bit // 8, "big")


def _decode_float(raw: int, size_in_bit: int, tc: ParameterTypeTypeFloat) -> str | None:
    """Inverse of :func:`_encode_float` (DPT9 two-octet float and IEEE single/double)."""
    if tc.encoding == ParameterTypeTypeFloatEncoding.DPT_9 and size_in_bit == 16:
        exponent = (raw >> 11) & 0xF
        mantissa = raw & 0x7FF
        if raw & 0x8000:
            mantissa -= 0x800  # 11-bit two's complement
        return str(0.01 * mantissa * (1 << exponent))
    data = _field_bytes(raw, size_in_bit)
    if data is None:
        return None
    if tc.encoding == ParameterTypeTypeFloatEncoding.IEEE_754_SINGLE and len(data) == 4:
        value = struct.unpack(">f", data)[0]
        # Mirror the encoder's finite check: nan/inf cannot be re-encoded, so a device
        # holding such bytes has no recoverable value (returning "inf"/"nan" would make
        # recover record a value that then fails EncodingError on the next image build).
        return str(value) if math.isfinite(value) else None
    if tc.encoding == ParameterTypeTypeFloatEncoding.IEEE_754_DOUBLE and len(data) == 8:
        value = struct.unpack(">d", data)[0]
        return str(value) if math.isfinite(value) else None
    return None


def _decode_value(
    raw: int,
    size_in_bit: int,
    tc: object,
    *,
    little_endian: bool,
    for_property: bool = False,
) -> str | None:
    """Best-effort inverse of :func:`_encode_value`; ``None`` when not recoverable.

    Enumerations are matched by re-encoding each candidate (so an explicit
    ``BinaryValue`` or byte order is handled by the same forward path). Integer,
    time, float, text, date, IPv4, RGB/RGBW colour and raw-data types are decoded
    directly. The result is a canonical string (e.g. a normalised number/float),
    not necessarily the exact original text. Non-injective encodings (HSV colour,
    a date whose year the type does not display, sub-octet byte types) return
    ``None`` - the original cannot be reconstructed from the bytes alone.

    ``for_property`` selects the RawData framing (memory has a 4-octet length
    prefix, a property value has none - see :func:`_encode_raw_data`).
    """
    if isinstance(tc, ParameterTypeTypeRestriction):
        for enumeration in tc.enumeration:
            encoded = _encode_value(
                str(enumeration.value),
                size_in_bit,
                tc,
                little_endian=little_endian,
                for_property=for_property,
            )
            if encoded is not None and encoded == raw:
                return str(enumeration.value)
        return None
    if isinstance(tc, ParameterTypeTypeNumber):
        logical = _apply_byte_order(raw, size_in_bit, little_endian) or 0
        if _is_signed_number(tc) and logical >= (1 << (size_in_bit - 1)):
            logical -= 1 << size_in_bit
        return str(logical)
    if isinstance(tc, ParameterTypeTypeTime):
        return str(_apply_byte_order(raw, size_in_bit, little_endian) or 0)
    if isinstance(tc, ParameterTypeTypeFloat):
        # Mirror the encoder: a little-endian program reverses the float octets, so undo
        # that before interpreting them (Number/Time above do the same).
        logical = _apply_byte_order(raw, size_in_bit, little_endian)
        return _decode_float(raw if logical is None else logical, size_in_bit, tc)
    if isinstance(tc, ParameterTypeTypeText):
        data = _field_bytes(raw, size_in_bit)
        return None if data is None else data.rstrip(b"\x00").decode("latin-1")
    if isinstance(tc, ParameterTypeTypeIpaddress):
        data = _field_bytes(raw, size_in_bit)
        if data is None or len(data) not in (4, 16):
            return None
        return str(ipaddress.ip_address(data))
    if isinstance(tc, ParameterTypeTypeDate):
        if tc.display_the_year is False:  # year not stored -> not reconstructable
            return None
        day, month, year = (raw >> 16) & 0xFF, (raw >> 8) & 0xFF, raw & 0xFF
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        # KNX DPT 11.001 century rule: a stored year of 90..99 maps to 1990..1999,
        # 0..89 to 2000..2089.
        century = 1900 if year >= 90 else 2000
        return f"{century + year:04d}-{month:02d}-{day:02d}"
    if isinstance(tc, ParameterTypeTypeColor):
        data = _field_bytes(raw, size_in_bit)
        if data is None or tc.space == ParameterTypeTypeColorSpace.HSV:
            return None  # HSV<-RGB is lossy; not inverted
        if len(data) >= 3:
            return "#" + data[:3].hex().upper()
        return None
    if isinstance(tc, ParameterTypeTypeRawData):
        data = _field_bytes(raw, size_in_bit)
        if data is None:
            return None
        if for_property:
            # A property value has no length prefix; the payload fills the whole field,
            # zero-padded. Drop the trailing zero padding and re-encode what remains.
            return base64.b64encode(data.rstrip(b"\x00")).decode("ascii")
        if len(data) < 4:
            return None
        # The length prefix (payload byte count) follows the program byte order; see
        # _encode_raw_data. The payload octets are opaque and never reversed.
        length = int.from_bytes(data[:4], "little" if little_endian else "big")
        if length > len(data) - 4:  # framed length claims more than the field can hold
            return None
        return base64.b64encode(data[4 : 4 + length]).decode("ascii")
    return None


def decode_memory_parameters(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    segments: Mapping[str, bytes],
    state: GlobalState | None = None,
) -> dict[str, str | None]:
    """Best-effort inverse of :func:`encode_to_memory` for recovering parameter values.

    Given the current bytes of each code segment (read off a device), decodes each
    top-level static memory-backed parameter's value using the application's field
    layout. Returns ``{parameter_id: value}`` where ``value`` is ``None`` for a
    parameter whose type cannot be reconstructed from bytes (see
    :func:`_decode_value`).

    Only top-level static memory parameters are handled. Module-instanced
    parameters are deliberately skipped: a module definition's parameter id repeats
    across every instance at different offsets, so decoding it would collapse the
    instances and the values could not be mapped back to instance-qualified project
    references. Recovering those correctly needs the resolved per-instance layout
    and is left to a caller that has it. Property-backed parameters are omitted too.
    """
    little_endian = _program_little_endian(app)
    result: dict[str, str | None] = {}
    # Resolving against the evaluated state (not empty overrides) is what makes union
    # members appear in the write set: a union only contributes its active alternative
    # once the parameter values are resolved. Only the field layout (segment/offset/
    # size) is used for decoding; the resolved values themselves are irrelevant here.
    overrides = resolve_param_values(idx, state) if state is not None else {}
    for w in collect_writes(app, idx, overrides, state).mem:
        data = segments.get(w.seg_id)
        if data is None:
            continue
        pt = idx.parameter_types.get(w.parameter_type)
        if pt is None:
            continue
        size_in_bit = type_size_in_bit(pt.choice)
        if not size_in_bit:
            continue
        if w.offset * 8 + w.bit_offset + size_in_bit > len(data) * 8:
            continue  # field does not fit the bytes we read (do not decode zero-fill)
        raw = _read_bits(data, w.offset, w.bit_offset, size_in_bit)
        result[w.param_id] = _decode_value(
            raw, size_in_bit, pt.choice, little_endian=little_endian
        )
    return result


def decode_property_parameters(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    properties: Mapping[PropertyKey, bytes],
    state: GlobalState | None = None,
) -> dict[str, str | None]:
    """Best-effort inverse of :func:`encode_to_properties` for property parameters.

    ``properties`` maps ``(object_index, property_id, occurrence)`` to the property
    value bytes read off the device. Decodes each top-level static property-backed
    parameter's field from those bytes. Falls back to any occurrence of the same
    ``(object_index, property_id)`` when the exact occurrence is absent. Fields that
    do not fit the read bytes are skipped. Module-instanced parameters are omitted
    for the same reason as :func:`decode_memory_parameters`.
    """
    little_endian = _program_little_endian(app)
    result: dict[str, str | None] = {}
    overrides = resolve_param_values(idx, state) if state is not None else {}
    for w in collect_writes(app, idx, overrides, state).prop:
        data = properties.get((w.object_index, w.property_id, w.occurrence))
        if data is None:
            data = next(
                (
                    value
                    for (obj, pid, _occ), value in properties.items()
                    if obj == w.object_index and pid == w.property_id
                ),
                None,
            )
        if data is None:
            continue
        pt = idx.parameter_types.get(w.parameter_type)
        if pt is None:
            continue
        size_in_bit = type_size_in_bit(pt.choice, for_property=True)
        if not size_in_bit:
            continue
        if w.offset * 8 + w.bit_offset + size_in_bit > len(data) * 8:
            continue  # field does not fit the bytes we read
        raw = _read_bits(data, w.offset, w.bit_offset, size_in_bit)
        result[w.param_id] = _decode_value(
            raw, size_in_bit, pt.choice, little_endian=little_endian, for_property=True
        )
    return result


def _decode_field(
    w: MemWrite | PropWrite,
    data: bytes,
    idx: ApplicationIndexer,
    *,
    little_endian: bool,
    for_property: bool,
) -> str | None:
    """Decode one write's field from ``data`` (its segment/property bytes)."""
    pt = idx.parameter_types.get(w.parameter_type)
    if pt is None:
        return None
    size_in_bit = type_size_in_bit(pt.choice, for_property=for_property)
    if not size_in_bit:
        return None
    if w.offset * 8 + w.bit_offset + size_in_bit > len(data) * 8:
        return None
    raw = _read_bits(data, w.offset, w.bit_offset, size_in_bit)
    return _decode_value(
        raw,
        size_in_bit,
        pt.choice,
        little_endian=little_endian,
        for_property=for_property,
    )


def decode_module_parameters(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    state: GlobalState,
    segments: Mapping[str, bytes],
    properties: Mapping[PropertyKey, bytes],
) -> dict[str, str | None]:
    """Decode module-instance parameter values from device memory and properties.

    ``state`` must be an evaluated state whose module instances match the device
    (seed it from the recovered top-level parameters first). For every module
    instance scope this resolves that instance's field offsets, decodes each field,
    and returns ``{qualified_parameter_ref_id: value}`` - the same instance-qualified
    reference ids a project stores - with ``None`` for unreconstructable types.
    """
    little_endian = _program_little_endian(app)
    param_to_refs: dict[str, list[str]] = {}
    for ref_id, ref in idx.parameter_refs.items():
        param_to_refs.setdefault(ref.ref_id, []).append(ref_id)

    result: dict[str, str | None] = {}

    def visit(scope: ModuleState) -> None:
        writes = Writes()
        _collect_scope_writes(scope, idx, writes)
        for w in writes.mem:
            data = segments.get(w.seg_id)
            if data is not None:
                value = _decode_field(
                    w, data, idx, little_endian=little_endian, for_property=False
                )
                for ref_id in param_to_refs.get(w.param_id, []):
                    result[scope.qualify(ref_id)] = value
        for w in writes.prop:
            data = properties.get((w.object_index, w.property_id, w.occurrence))
            if data is None:
                data = next(
                    (
                        value
                        for (obj, pid, _occ), value in properties.items()
                        if obj == w.object_index and pid == w.property_id
                    ),
                    None,
                )
            if data is not None:
                value = _decode_field(
                    w, data, idx, little_endian=little_endian, for_property=True
                )
                for ref_id in param_to_refs.get(w.param_id, []):
                    result[scope.qualify(ref_id)] = value
        for child in scope.module_children():
            visit(child)

    for child in state.module_children():
        visit(child)
    return result


def encode_to_properties(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None = None,
) -> dict[PropertyKey, bytes]:
    """Pack PropertyParameter-backed values into interface-object property data.

    Yields {(object_index, property_id, occurrence): bytes}.
    Layout: bit_offset 0 is a byte's MSB; values are big-endian. Buffers grow to fit.
    """
    return _encode_properties(
        app,
        idx,
        overrides,
        state,
        lambda w: (w.object_index, w.property_id, w.occurrence),
    )


def encode_to_typed_properties(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None = None,
) -> dict[TypedPropertyKey, bytes]:
    """:func:`encode_to_properties` keyed by ``(object_index, object_type, property_id,
    occurrence)``, so properties of objects addressed by type stay apart per type."""
    return _encode_properties(
        app,
        idx,
        overrides,
        state,
        lambda w: (w.object_index, w.object_type, w.property_id, w.occurrence),
    )


def _encode_properties[K](
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None,
    key_of: Callable[[PropWrite], K],
) -> dict[K, bytes]:
    writes = collect_writes(app, idx, overrides, state, inactive_ranges=True)
    little_endian = _program_little_endian(app)
    bufs: dict[K, bytearray] = {}
    for w in writes.prop:
        pt = idx.parameter_types.get(w.parameter_type)
        if pt is None:
            continue
        tc = pt.choice
        size_in_bit = type_size_in_bit(tc, for_property=True)
        if not size_in_bit:
            continue
        if w.keep:
            # A kept range is part of the property value; its bytes stay zero.
            needed = w.offset + (w.bit_offset + size_in_bit + 7) // 8
            buf = bufs.setdefault(key_of(w), bytearray())
            buf.extend(bytearray(max(0, needed - len(buf))))
            continue
        encoded = _encode_value(
            w.value, size_in_bit, tc, little_endian=little_endian, for_property=True
        )
        if encoded is None:
            raise EncodingError(
                f"cannot encode parameter {w.param_id} value {w.value!r} "
                f"as {w.parameter_type}"
            )
        key = key_of(w)
        needed = w.offset + (w.bit_offset + size_in_bit + 7) // 8
        buf = bufs.get(key)
        if buf is None:
            bufs[key] = buf = bytearray(needed)
        elif len(buf) < needed:
            buf.extend(bytearray(needed - len(buf)))
        _write_bits(buf, w.offset, w.bit_offset, size_in_bit, encoded)
    return {key: bytes(buf) for key, buf in bufs.items()}


def build_property_param_map(
    app: ApplicationProgram,
    idx: ApplicationIndexer,
    overrides: dict[str, str],
    state: GlobalState | None = None,
) -> dict[PropertyKey, dict[int, tuple[str, str]]]:
    """Map {(object_index, property_id, occurrence): {byte_offset: (param_id, value)}}."""
    writes = collect_writes(app, idx, overrides, state)
    maps: dict[PropertyKey, dict[int, tuple[str, str]]] = {}
    for w in writes.prop:
        pt = idx.parameter_types.get(w.parameter_type)
        if pt is None:
            continue
        size = type_size_in_bit(pt.choice, for_property=True)
        if not size:
            continue
        key: PropertyKey = (w.object_index, w.property_id, w.occurrence)
        byte_map = maps.setdefault(key, {})
        start_bit = w.offset * 8 + w.bit_offset
        end_bit = start_bit + size - 1
        for b in range(start_bit // 8, end_bit // 8 + 1):
            byte_map[b] = (w.param_id, w.value)
    return maps
