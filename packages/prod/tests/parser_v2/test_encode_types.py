"""Unit tests for the per-type value encoders (_encode_value).

Values are checked against the KNX Datapoint Type byte encodings; the integer the
encoder returns is packed big-endian (MSB first) by _write_bits.
"""

from __future__ import annotations

import base64

from xknxeditor.namespaces.intermediate.parameter_type_t_type_color import (
    ParameterTypeTypeColor,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_color_space import (
    ParameterTypeTypeColorSpace,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_date import (
    ParameterTypeTypeDate,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_date_encoding import (
    ParameterTypeTypeDateEncoding,
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
from xknxeditor.namespaces.intermediate.parameter_type_t_type_ipaddress_address_type import (
    ParameterTypeTypeIpaddressAddressType,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_ipaddress_version import (
    ParameterTypeTypeIpaddressVersion,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_number import (
    ParameterTypeTypeNumber,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_number_type import (
    ParameterTypeTypeNumberType,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_raw_data import (
    ParameterTypeTypeRawData,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction import (
    ParameterTypeTypeRestriction,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction_enumeration import (
    ParameterTypeTypeRestrictionEnumeration,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_text import (
    ParameterTypeTypeText,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_time import (
    ParameterTypeTypeTime,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_time_unit import (
    ParameterTypeTypeTimeUnit,
)
from xknxeditor.prod.parser_v2.encode import _encode_value


def _number(bits: int = 8, *, signed: bool = False) -> ParameterTypeTypeNumber:
    return ParameterTypeTypeNumber(
        size_in_bit=bits,
        type_value=ParameterTypeTypeNumberType.SIGNED_INT
        if signed
        else ParameterTypeTypeNumberType.UNSIGNED_INT,
        min_inclusive=-(1 << (bits - 1)) if signed else 0,
        max_inclusive=(1 << (bits - int(signed))) - 1,
    )


def _float(encoding: ParameterTypeTypeFloatEncoding) -> ParameterTypeTypeFloat:
    return ParameterTypeTypeFloat(encoding=encoding, min_inclusive=0, max_inclusive=0)


def _text() -> ParameterTypeTypeText:
    return ParameterTypeTypeText(size_in_bit=0)


def _date(display_the_year: bool = True) -> ParameterTypeTypeDate:
    return ParameterTypeTypeDate(
        encoding=ParameterTypeTypeDateEncoding.DPT_11,
        display_the_year=display_the_year,
    )


def _ipaddress(
    version: ParameterTypeTypeIpaddressVersion = ParameterTypeTypeIpaddressVersion.IPV4,
) -> ParameterTypeTypeIpaddress:
    return ParameterTypeTypeIpaddress(
        address_type=ParameterTypeTypeIpaddressAddressType.HOST_ADDRESS,
        version=version,
    )


def _color(space: ParameterTypeTypeColorSpace) -> ParameterTypeTypeColor:
    return ParameterTypeTypeColor(space=space)


def _raw_data() -> ParameterTypeTypeRawData:
    return ParameterTypeTypeRawData(max_size=16)


def test_number_unsigned() -> None:
    assert _encode_value("15", 8, _number()) == 0x0F


def test_number_negative_twos_complement() -> None:
    assert _encode_value("-1", 8, _number(signed=True)) == 0xFF
    assert _encode_value("-2", 16, _number(16, signed=True)) == 0xFFFE


def test_number_invalid_returns_none() -> None:
    assert _encode_value("abc", 8, _number()) is None


def test_number_outside_its_range_is_written_while_it_fits() -> None:
    # A product's own value may lie outside the type's range (a hidden parameter's default of 1
    # for a 129..129 type): written as given, as long as it fits the size.
    only_129 = ParameterTypeTypeNumber(
        size_in_bit=8,
        type_value=ParameterTypeTypeNumberType.UNSIGNED_INT,
        min_inclusive=129,
        max_inclusive=129,
    )
    assert _encode_value("1", 8, only_129) == 0x01
    assert _encode_value("256", 8, only_129) is None


def test_float_dpt9() -> None:
    # 21.0 -> 0.01 * 1050 * 2^1; encoded 0x0C1A
    tc = _float(ParameterTypeTypeFloatEncoding.DPT_9)
    assert _encode_value("21.0", 16, tc) == 0x0C1A
    assert _encode_value("0", 16, tc) == 0x0000


def test_float_dpt9_negative() -> None:
    tc = _float(ParameterTypeTypeFloatEncoding.DPT_9)
    # -1.0 -> m = -100, exp 0, sign bit set, 11-bit two's complement of -100
    assert _encode_value("-1.0", 16, tc) == (0x8000 | (-100 & 0x7FF))


def test_float_ieee_single() -> None:
    tc = _float(ParameterTypeTypeFloatEncoding.IEEE_754_SINGLE)
    assert _encode_value("1.0", 32, tc) == 0x3F800000


def test_float_dpt9_rounds_scaled_mantissa() -> None:
    # The KNX standard selects the exponent on the real scaled magnitude then rounds
    # once. 21.03 -> scaled 2103 -> /2 = 1051.5 at exp 1 -> round to 1052 (0x0C1C), i.e.
    # 21.04. Truncating (the old >>) would give 1051 (0x0C1B) / 21.02, which diverges.
    tc = _float(ParameterTypeTypeFloatEncoding.DPT_9)
    assert _encode_value("21.03", 16, tc) == 0x0C1C


def test_float_dpt9_zero_mantissa_has_no_sign_bit() -> None:
    # A tiny negative value whose scaled mantissa rounds to 0 must encode as 0x0000, not
    # 0x8000. The KNX standard special-cases a zero mantissa to the no-sign pack:
    # -20.48 (= 0.01 * -2048 * 2^0) is the smallest representable value, so -0.001 -> 0.
    tc = _float(ParameterTypeTypeFloatEncoding.DPT_9)
    assert _encode_value("-0.001", 16, tc) == 0x0000


def test_float_dpt9_negative_reaches_minus_2048() -> None:
    # The mantissa bound is asymmetric per the KNX standard: a negative value may
    # reach magnitude 2048, a positive one only 2047. -20.48 stays at exp 0 (m = -2048),
    # while +20.48 must bump to exp 1 (m = 1024).
    tc = _float(ParameterTypeTypeFloatEncoding.DPT_9)
    assert _encode_value("-20.48", 16, tc) == (0x8000 | (-2048 & 0x7FF))  # 0x8000
    assert _encode_value("20.48", 16, tc) == (1 << 11) | 1024  # 0x0C00


def test_float_dpt9_scales_in_single_precision() -> None:
    # The KNX standard scales in SINGLE precision (round to float32, then multiply by
    # 100 in float32) before widening to double for the exponent search and rounding.
    # 0.295 -> single(0.295) = 0.29499998... -> single(* 100) = 29.4999980...
    # -> round 29 (0x001D). Scaling in float64 gives exactly 29.5 -> banker's round to
    # 30 (0x001E), which diverges.
    tc = _float(ParameterTypeTypeFloatEncoding.DPT_9)
    assert _encode_value("0.295", 16, tc) == 0x001D


def test_float_dpt9_overflow_saturates() -> None:
    # A finite value too large for DPT 9 saturates per the KNX standard: exponent clamped
    # to 15, magnitude to its limit -> 0x7FFF positive, 0xF800 negative.
    # (nan/inf still return None via the finite guard - see test_float_nan_and_inf.)
    tc = _float(ParameterTypeTypeFloatEncoding.DPT_9)
    assert _encode_value("670761", 16, tc) == 0x7FFF
    assert _encode_value("-671089", 16, tc) == 0xF800
    # A finite value so large its scaled form overflows binary32 (the float32 scale
    # -> +/-inf) must still saturate, not fall out as unencodable: 1e37 is finite.
    assert _encode_value("1e37", 16, tc) == 0x7FFF
    assert _encode_value("-1e37", 16, tc) == 0xF800


def test_float_nan_and_inf_return_none() -> None:
    # nan/inf survive float() but blow up round() (DPT 9) or are silently accepted by
    # struct.pack (IEEE); they must not leak - the writer relies on None to raise
    # EncodingError instead of programming a bogus image.
    for encoding in (
        ParameterTypeTypeFloatEncoding.DPT_9,
        ParameterTypeTypeFloatEncoding.IEEE_754_SINGLE,
        ParameterTypeTypeFloatEncoding.IEEE_754_DOUBLE,
    ):
        tc = _float(encoding)
        assert _encode_value("nan", 32, tc) is None
        assert _encode_value("inf", 32, tc) is None
        assert _encode_value("-inf", 32, tc) is None


def test_text_latin1_padded() -> None:
    assert _encode_value("AB", 24, _text()) == 0x414200


def test_text_truncated() -> None:
    assert _encode_value("ABCD", 16, _text()) == 0x4142


def test_date_dpt11() -> None:
    # 2024-03-15 -> day 15, month 3, year%100 = 24
    assert _encode_value("2024-03-15", 24, _date()) == 0x0F0318


def test_ipaddress_v4() -> None:
    assert _encode_value("192.168.1.1", 32, _ipaddress()) == 0xC0A80101


def test_ipaddress_invalid() -> None:
    assert _encode_value("192.168.1", 32, _ipaddress()) is None
    assert _encode_value("1.2.3.999", 32, _ipaddress()) is None


def test_color_rgb() -> None:
    assert _encode_value("#FF8000", 24, _color(ParameterTypeTypeColorSpace.RGB)) == (
        0xFF8000
    )


def test_color_hsv() -> None:
    # #FF8000 (255,128,0) -> H=30.12deg -> 21, S=255, V=255
    assert _encode_value("#FF8000", 24, _color(ParameterTypeTypeColorSpace.HSV)) == (
        0x15FFFF
    )


def test_color_rgbw() -> None:
    # RGBW encodes to three octets like RGB per the KNX standard; the white channel is
    # not carried in this field, so a trailing #..WW octet is ignored, not written.
    assert (
        _encode_value("#FF800040", 24, _color(ParameterTypeTypeColorSpace.RGBW))
        == 0xFF8000
    )


# RawData value is base64-encoded in the XML; the memory layout is a four octet length
# prefix (the payload byte count) followed by the payload, zero-padded to fill the field.
# Per the KNX standard SizeInBits = 8 * MaxSize and RawData reserves +4 octets, so MaxSize
# bounds the *payload* and the whole field is MaxSize + 4 octets. With MaxSize 16 the field
# is 20 octets (160 bit), the payload capacity is 16, and the length prefix follows the
# program byte order (payload never reversed).
_RAW_DATA_BITS = (16 + 4) * 8


def test_raw_data_base64_big_endian_length_prefix() -> None:
    # "Cgs=" decodes to b"\x0a\x0b" (2 octets) -> big-endian length prefix 0x00000002
    encoded = _encode_value("Cgs=", _RAW_DATA_BITS, _raw_data())
    assert encoded is not None
    assert encoded.to_bytes(20, "big") == b"\x00\x00\x00\x02\x0a\x0b" + b"\x00" * 14


def test_raw_data_base64_little_endian_length_prefix() -> None:
    # only the length prefix follows the program byte order; the payload is never reversed
    encoded = _encode_value("Cgs=", _RAW_DATA_BITS, _raw_data(), little_endian=True)
    assert encoded is not None
    assert encoded.to_bytes(20, "big") == b"\x02\x00\x00\x00\x0a\x0b" + b"\x00" * 14


def test_raw_data_payload_over_max_size_returns_none() -> None:
    # payload capacity is MaxSize = 16; 17 octets no longer fit
    too_long = base64.b64encode(b"\x00" * 17).decode("ascii")
    assert _encode_value(too_long, _RAW_DATA_BITS, _raw_data()) is None


def test_raw_data_invalid_base64_returns_none() -> None:
    assert _encode_value("not valid base64 @@@", _RAW_DATA_BITS, _raw_data()) is None


def test_raw_data_base64_whitespace_is_ignored() -> None:
    # Standard base64 decoding ignores only ASCII space/tab/CR/LF; a line-wrapped value
    # must encode identically to its compact form, but other Unicode whitespace (e.g.
    # NBSP) is rejected.
    compact = _encode_value("Cgs=", _RAW_DATA_BITS, _raw_data())
    wrapped = _encode_value("Cg\n s=\t", _RAW_DATA_BITS, _raw_data())
    assert wrapped is not None
    assert wrapped == compact
    assert _encode_value("Cg\u00a0s=", _RAW_DATA_BITS, _raw_data()) is None


def test_raw_data_property_has_no_length_prefix() -> None:
    # A property value carries no length prefix (the +4 framing is memory-only per the
    # KNX standard). The field is MaxSize octets: the payload zero-padded, no prefix.
    encoded = _encode_value("Cgs=", 16 * 8, _raw_data(), for_property=True)
    assert encoded is not None
    assert encoded.to_bytes(16, "big") == b"\x0a\x0b" + b"\x00" * 14


def test_decode_raw_data_property_round_trip() -> None:
    # Without a prefix the decoder strips the trailing zero padding and re-encodes the rest.
    tc = _raw_data()
    raw = _encode_value("Cgs=", 16 * 8, tc, for_property=True)
    assert raw is not None
    assert (
        _decode_value(raw, 16 * 8, tc, little_endian=False, for_property=True) == "Cgs="
    )


def test_number_little_endian_byte_swap() -> None:
    # 500 = 0x01F4 big-endian; little-endian reverses the two octets
    assert _encode_value("500", 16, _number(16)) == 0x01F4
    assert _encode_value("500", 16, _number(16), little_endian=True) == 0xF401


def test_date_without_year_zeroes_year_octet() -> None:
    assert _encode_value("2024-03-17", 24, _date(display_the_year=False)) == (
        (17 << 16) | (3 << 8)
    )


def test_time_encodes_as_integer() -> None:
    tc = ParameterTypeTypeTime(
        size_in_bit=16,
        unit=ParameterTypeTypeTimeUnit.SECONDS,
        min_inclusive=0,
        max_inclusive=65535,
    )
    assert _encode_value("1000", 16, tc) == 1000
    assert _encode_value("1000", 16, tc, little_endian=True) == 0xE803


def test_restriction_uses_enumeration_binary_value() -> None:
    # An enumeration with an explicit binary value writes those octets verbatim.
    tc = ParameterTypeTypeRestriction(
        base="BinaryValue",
        size_in_bit=24,
        enumeration=[
            ParameterTypeTypeRestrictionEnumeration(
                id="EN-0", value=0, binary_value=b"\x01\x00\x02"
            )
        ],
    )
    assert _encode_value("0", 24, tc) == 0x010002
    # even for a little-endian program the binary value is written as-is
    assert _encode_value("0", 24, tc, little_endian=True) == 0x010002


def test_restriction_without_binary_value_is_numeric() -> None:
    tc = ParameterTypeTypeRestriction(
        base="Value",
        size_in_bit=8,
        enumeration=[ParameterTypeTypeRestrictionEnumeration(id="EN-5", value=5)],
    )
    assert _encode_value("5", 8, tc) == 5


# --- decode round-trips (recover): _decode_value must invert _encode_value -----

from xknxeditor.prod.parser_v2.encode import _decode_value  # noqa: E402


def _signed_number() -> ParameterTypeTypeNumber:
    return ParameterTypeTypeNumber(
        size_in_bit=16,
        type_value=ParameterTypeTypeNumberType.SIGNED_INT,
        min_inclusive=-100,
        max_inclusive=100,
    )


def test_decode_number_signed() -> None:
    tc = _signed_number()
    raw = _encode_value("-2", 16, tc)
    assert raw is not None
    assert _decode_value(raw, 16, tc, little_endian=False) == "-2"


def test_decode_float_dpt9() -> None:
    tc = _float(ParameterTypeTypeFloatEncoding.DPT_9)
    raw = _encode_value("21.0", 16, tc)
    assert raw is not None
    assert _decode_value(raw, 16, tc, little_endian=False) == "21.0"


def test_decode_float_ieee_single() -> None:
    tc = _float(ParameterTypeTypeFloatEncoding.IEEE_754_SINGLE)
    raw = _encode_value("1.5", 32, tc)
    assert raw is not None
    assert _decode_value(raw, 32, tc, little_endian=False) == "1.5"


def test_decode_text() -> None:
    tc = _text()
    raw = _encode_value("Aus", 24, tc)
    assert raw is not None
    assert _decode_value(raw, 24, tc, little_endian=False) == "Aus"


def test_decode_ipaddress() -> None:
    tc = _ipaddress()
    raw = _encode_value("192.168.1.5", 32, tc)
    assert raw is not None
    assert _decode_value(raw, 32, tc, little_endian=False) == "192.168.1.5"


def test_decode_date_with_year() -> None:
    tc = _date(display_the_year=True)
    raw = _encode_value("2024-03-07", 24, tc)
    assert raw is not None
    assert _decode_value(raw, 24, tc, little_endian=False) == "2024-03-07"


def test_decode_date_without_year_is_unknown() -> None:
    tc = _date(display_the_year=False)
    raw = _encode_value("2024-03-07", 24, tc)
    assert raw is not None
    assert _decode_value(raw, 24, tc, little_endian=False) is None


def test_decode_date_1990s_century() -> None:
    # KNX DPT 11.001: a stored two-digit year of 90..99 is the 1990s, not the 2090s.
    # The KNX standard reconstructs the same way (year >= 90 -> 1900 + year).
    tc = _date(display_the_year=True)
    raw = _encode_value("1995-06-20", 24, tc)
    assert raw is not None
    assert _decode_value(raw, 24, tc, little_endian=False) == "1995-06-20"


def test_decode_color_rgb() -> None:
    tc = _color(ParameterTypeTypeColorSpace.RGB)
    raw = _encode_value("#0A0B0C", 24, tc)
    assert raw is not None
    assert _decode_value(raw, 24, tc, little_endian=False) == "#0A0B0C"


def test_decode_color_hsv_is_unknown() -> None:
    tc = _color(ParameterTypeTypeColorSpace.HSV)
    raw = _encode_value("#0A0B0C", 24, tc)
    assert raw is not None
    assert _decode_value(raw, 24, tc, little_endian=False) is None


def test_decode_raw_data_round_trip_big_endian() -> None:
    tc = _raw_data()
    raw = _encode_value("Cgs=", _RAW_DATA_BITS, tc)
    assert raw is not None
    # decode recovers the base64 payload (using the length prefix, dropping the padding)
    assert _decode_value(raw, _RAW_DATA_BITS, tc, little_endian=False) == "Cgs="


def test_decode_raw_data_round_trip_little_endian() -> None:
    tc = _raw_data()
    raw = _encode_value("Cgs=", _RAW_DATA_BITS, tc, little_endian=True)
    assert raw is not None
    assert _decode_value(raw, _RAW_DATA_BITS, tc, little_endian=True) == "Cgs="


def test_decode_raw_data_length_over_capacity_returns_none() -> None:
    # A framed length that claims more octets than the field can hold is corrupt: the
    # decoder must reject it rather than return a truncated (plausible) base64 payload.
    tc = _raw_data()  # MaxSize 16 -> 20 octet field, payload capacity 16
    field = (100).to_bytes(4, "big") + b"\x0a\x0b" + b"\x00" * 14
    raw = int.from_bytes(field, "big")
    assert _decode_value(raw, _RAW_DATA_BITS, tc, little_endian=False) is None


def test_ipaddress_v6_round_trip() -> None:
    tc = _ipaddress(ParameterTypeTypeIpaddressVersion.IPV6)
    raw = _encode_value("2001:db8::1", 128, tc)
    assert raw is not None
    assert _decode_value(raw, 128, tc, little_endian=False) == "2001:db8::1"


def test_ipaddress_v6_literal_in_v4_field_returns_none() -> None:
    tc = _ipaddress(ParameterTypeTypeIpaddressVersion.IPV4)
    assert _encode_value("2001:db8::1", 32, tc) is None
