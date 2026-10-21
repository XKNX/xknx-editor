from __future__ import annotations

import pytest

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
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction_base import (
    ParameterTypeTypeRestrictionBase,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction_enumeration import (
    ParameterTypeTypeRestrictionEnumeration,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_text import (
    ParameterTypeTypeText,
)
from xknxeditor.prod.script.values import (
    check_value,
    dotnet_number_to_string,
    from_js,
    to_js,
)

NUMBER = ParameterTypeTypeNumber(
    size_in_bit=8,
    type_value=ParameterTypeTypeNumberType.UNSIGNED_INT,
    min_inclusive=0,
    max_inclusive=200,
)
ENUM = ParameterTypeTypeRestriction(
    base=ParameterTypeTypeRestrictionBase.VALUE,
    size_in_bit=8,
    enumeration=[
        ParameterTypeTypeRestrictionEnumeration(value=0, id="E0", text="Off"),
        ParameterTypeTypeRestrictionEnumeration(value=3, id="E3", text="On"),
    ],
)
FLOAT = ParameterTypeTypeFloat(
    encoding=ParameterTypeTypeFloatEncoding.IEEE_754_SINGLE,
    min_inclusive=-10.0,
    max_inclusive=10.0,
)
TEXT = ParameterTypeTypeText(size_in_bit=40, pattern="^[a-z]*$")
RAW = ParameterTypeTypeRawData(max_size=8)
IP = ParameterTypeTypeIpaddress(
    address_type=ParameterTypeTypeIpaddressAddressType.HOST_ADDRESS,
    version=ParameterTypeTypeIpaddressVersion.IPV4,
)


def test_to_js() -> None:
    assert to_js("12", NUMBER) == 12
    assert to_js("0x10", NUMBER) == 16
    assert to_js("3", ENUM) == 3
    assert to_js("1.5", FLOAT) == 1.5
    assert to_js("2.0", FLOAT) == 2
    assert to_js("abc", TEXT) == "abc"
    assert to_js(None, NUMBER) is None
    assert to_js("1", object()) is None


@pytest.mark.parametrize(
    ("js", "tc", "expected"),
    [
        (True, NUMBER, "1"),
        (False, ENUM, "0"),
        (7, NUMBER, "7"),
        (2.5, NUMBER, "2"),
        (3.5, NUMBER, "4"),
        ("12", NUMBER, "12"),
        (1.25, FLOAT, "1.25"),
        (4, FLOAT, "4"),
        ("x", TEXT, "x"),
        (1.5, TEXT, "1.5"),
        (0.1 + 0.2, TEXT, "0.3"),
        (1e21, TEXT, "1E+21"),
        (True, TEXT, "True"),
        (None, TEXT, ""),
        # Data a script read from a device: an array of octets.
        ([1, 2, 255], RAW, "AQL/"),
        ([], RAW, ""),
    ],
)
def test_from_js(js: object, tc: object, expected: str) -> None:
    assert from_js(js, tc) == expected


@pytest.mark.parametrize(
    ("js", "tc"),
    [
        ("1.5", NUMBER),
        ("abc", NUMBER),
        ("x", FLOAT),
        (float("nan"), NUMBER),
        ([256], RAW),
        (["a"], RAW),
    ],
)
def test_from_js_rejects(js: object, tc: object) -> None:
    with pytest.raises(ValueError):
        from_js(js, tc)


def test_check_value() -> None:
    assert check_value("200", NUMBER) == "200"
    assert check_value("3", ENUM) == "3"
    assert check_value("abcde", TEXT) == "abcde"
    assert check_value("192.168.1.1", IP) == "192.168.1.1"
    for value, tc in [
        ("201", NUMBER),
        ("2", ENUM),
        ("11", FLOAT),
        ("abcdef", TEXT),
        ("ABC", TEXT),
        ("::1", IP),
    ]:
        with pytest.raises(ValueError):
            check_value(value, tc)


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (1.0, "1"),
        (-0.0, "0"),
        (1.5, "1.5"),
        (0.1 + 0.2, "0.3"),
        (123456789012345.6, "123456789012346"),
        (1e-5, "1E-05"),
        (0.0001, "0.0001"),
        (float("nan"), "NaN"),
        (float("-inf"), "-Infinity"),
    ],
)
def test_dotnet_number_to_string(x: float, expected: str) -> None:
    assert dotnet_number_to_string(x) == expected
