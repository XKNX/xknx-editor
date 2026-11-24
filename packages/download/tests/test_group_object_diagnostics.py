"""Group telegrams sent by a device through Group Object Diagnostics (PID_GO_DIAGNOSTICS)."""

from __future__ import annotations

from typing import Any

import pytest
from xknx.dpt.payload import DPTArray, DPTBinary
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import (
    APCI,
    FunctionPropertyExtCommand,
    FunctionPropertyExtStateResponse,
    ReturnCode,
)

from xknxeditor.download import session
from xknxeditor.download.errors import LoadStateError
from xknxeditor.download.group_object_diagnostics import (
    read_command,
    send_command,
    write_command,
)

_DEVICE = IndividualAddress("1.1.5")


def test_the_commands() -> None:
    # Reserved, WriteServiceID, flags (conf + auth; Selector for a value in its own octets),
    # the group address and the value (03_05_01 4.8.1.3.3 and 4.8.1.3.5).
    assert write_command(0x0801, DPTBinary(1)) == bytes.fromhex("00 01 03 08 01 01")
    assert write_command(0x0801, DPTArray((0x0C, 0x1A))) == bytes.fromhex(
        "00 01 83 08 01 0C 1A"
    )
    assert read_command(0x0801) == bytes.fromhex("00 03 03 08 01")


class _Manager:
    def __init__(self, code: ReturnCode, answer: bool = True) -> None:
        self.requests: list[APCI] = []
        self.closed = False
        self._code = code
        self._answer = answer

    async def open(self) -> _Manager:
        return self

    async def close(self) -> None:
        self.closed = True

    async def request(self, payload: APCI, expected: Any) -> Telegram:
        self.requests.append(payload)
        assert isinstance(payload, FunctionPropertyExtCommand)
        response: APCI = (
            FunctionPropertyExtStateResponse(
                interface_object_type=payload.interface_object_type,
                object_instance=payload.object_instance,
                property_id=payload.property_id,
                return_code=self._code,
            )
            if self._answer
            else payload
        )
        return Telegram(destination_address=_DEVICE, payload=response)


class _Sessions:
    """Stands in for ``management_session``: hands out ``manager``, records the calls."""

    def __init__(self) -> None:
        self.manager = _Manager(ReturnCode.E_SUCCESS)
        self.calls: list[tuple[Any, Any, bool]] = []

    def __call__(
        self, xknx: Any, address: Any, security: Any, *, connectionless: bool = False
    ) -> _Manager:
        self.calls.append((address, security, connectionless))
        return self.manager


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> _Sessions:
    fake = _Sessions()
    monkeypatch.setattr(session, "management_session", fake)
    return fake


async def test_the_device_takes_the_command(sessions: _Sessions) -> None:
    manager = sessions.manager
    command = write_command(0x0801, DPTBinary(1))
    await send_command(object(), _DEVICE, None, command)  # type: ignore[arg-type]
    # Connectionless, to the Group Object Table Object (type 9, instance 1), PID 66.
    assert sessions.calls == [(_DEVICE, None, True)]
    [request] = manager.requests
    assert isinstance(request, FunctionPropertyExtCommand)
    assert (request.interface_object_type, request.object_instance) == (9, 1)
    assert (request.property_id, request.data) == (66, command)
    assert manager.closed


async def test_a_refused_command(sessions: _Sessions) -> None:
    manager = sessions.manager = _Manager(ReturnCode.E_COMMAND_INVALID)
    with pytest.raises(LoadStateError, match="return code F2h"):
        await send_command(object(), _DEVICE, None, read_command(0x0801))  # type: ignore[arg-type]
    assert manager.closed


async def test_no_response(sessions: _Sessions) -> None:
    sessions.manager = _Manager(ReturnCode.E_SUCCESS, answer=False)
    with pytest.raises(LoadStateError, match="no Group Object Diagnostics response"):
        await send_command(object(), _DEVICE, None, read_command(0x0801))  # type: ignore[arg-type]
