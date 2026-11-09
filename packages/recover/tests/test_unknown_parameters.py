"""Parameters read without a value: those the device holds no value of (a lossy type, or bytes
that are no value of the type while the result fits the device), and those another configuration
may still read (the result differs from the device)."""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import (
    ParameterTypeTypeColor,
    ParameterTypeTypeDate,
    ParameterTypeTypeText,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_color_space import (
    ParameterTypeTypeColorSpace,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_date_encoding import (
    ParameterTypeTypeDateEncoding,
)
from xknxeditor.prod.parser_v2.encode import decodable_type
from xknxeditor.recover.parameters import RecoveredParameters


def test_lossy_types_give_back_no_value() -> None:
    assert decodable_type(ParameterTypeTypeText(size_in_bit=32), 32)
    # Text that does not fill whole octets, a date without its year.
    assert not decodable_type(ParameterTypeTypeText(size_in_bit=12), 12)
    assert not decodable_type(
        ParameterTypeTypeDate(
            encoding=ParameterTypeTypeDateEncoding.DPT_11, display_the_year=False
        ),
        24,
    )
    # An HSV colour gives back one of the colours that convert to its octets.
    assert decodable_type(
        ParameterTypeTypeColor(space=ParameterTypeTypeColorSpace.HSV), 24
    )
    assert decodable_type(
        ParameterTypeTypeColor(space=ParameterTypeTypeColorSpace.RGB), 24
    )
    assert not decodable_type(object(), 8)


def test_a_result_that_differs_has_parameters_not_found_yet() -> None:
    result = RecoveredParameters(
        values={}, unknown=["P-1", "P-2", "P-3"], lossy=["P-2"], differing_bytes=4
    )
    assert result.not_found == ["P-1", "P-3"]
    assert result.unrecoverable == ["P-2"]


def test_a_result_that_fits_holds_no_value_of_its_unknown_parameters() -> None:
    # The bytes are the device's: no other configuration reads them otherwise.
    result = RecoveredParameters(values={}, unknown=["P-1", "P-2"], lossy=["P-2"])
    assert result.not_found == []
    assert result.unrecoverable == ["P-1", "P-2"]
