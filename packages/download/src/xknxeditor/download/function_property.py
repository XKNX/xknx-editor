"""Preserve function property responses that carry no result octets."""

from __future__ import annotations

from xknx.telegram.apci import FunctionPropertyStateResponse, encode_cmd_and_payload

_from_knx = FunctionPropertyStateResponse.from_knx


class EmptyFunctionPropertyStateResponse(FunctionPropertyStateResponse):
    """A response with an object and property, but no return code or state data."""

    __slots__ = ()

    def calculated_length(self) -> int:
        return 3

    def to_knx(self) -> bytearray:
        return encode_cmd_and_payload(
            self.CODE, appended_payload=bytes((self.object_index, self.property_id))
        )

    def __str__(self) -> str:
        return (
            f'<EmptyFunctionPropertyStateResponse object_index="{self.object_index}" '
            f'property_id="{self.property_id}" />'
        )


def _parse_response(raw: bytes) -> FunctionPropertyStateResponse:
    if len(raw) == 4:
        return EmptyFunctionPropertyStateResponse(
            object_index=raw[2], property_id=raw[3]
        )
    return _from_knx(raw)


def install() -> None:
    """Let xknx decode empty responses without inventing a result octet."""
    FunctionPropertyStateResponse.from_knx = staticmethod(_parse_response)  # type: ignore[method-assign]
