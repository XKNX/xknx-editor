"""Group telegrams sent by a device: Group Object Diagnostics (PID_GO_DIAGNOSTICS).

A group address secured with KNX Data Secure is written or read through a device commissioned
securely that sends on it: the device transmits the A_GroupValue_Write or A_GroupValue_Read
itself, secured with its own Sequence Number and the key of the group address in its Group Key
Table, so its receivers need not know the sender of the command (KNX Standard v3.0.2, 03_05_01
Resources §4.8.1.3.3 and §4.8.1.3.5). The command is the Function Property PID_GO_DIAGNOSTICS
(PID 66) of the Group Object Table Object (Object Type 9, §4.8.1), reached connectionless (the
device accepts both, §4.8.1.1.4) with A_FunctionPropertyExtCommand, secured with the device's
Tool Key when it has one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from xknx.dpt.payload import DPTArray, DPTBinary
from xknx.profile.const import ResourceObjectType
from xknx.telegram.apci import (
    FunctionPropertyExtCommand,
    FunctionPropertyExtStateResponse,
)

from .errors import LoadStateError

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram import IndividualAddress

    from .data_secure import DeviceSecurity

#: The property of the Group Object Table Object (03_05_01 §4.8.1).
PID_GO_DIAGNOSTICS: Final = 66
#: WriteServiceID "Send A_GroupValue_Write" (§4.8.1.3.3).
SEND_GROUP_VALUE_WRITE: Final = 0x01
#: WriteServiceID "Send A_GroupValue_Read" (§4.8.1.3.5).
SEND_GROUP_VALUE_READ: Final = 0x03
# Flags: secured with confidentiality and authentication (bits 1 and 0), and the Selector
# (bit 7) that sends a value of one octet below 64 in its own octet, not in the 6 bits after the
# APCI (§4.8.1.3.3).
_CONF_AUTH: Final = 0x03
_SELECTOR: Final = 0x80


def write_command(group_address: int, value: DPTBinary | DPTArray) -> bytes:
    """The command that makes a device send an A_GroupValue_Write of ``value`` to the group
    address (raw): a short value in the 6 bits after the APCI, any other in octets."""
    flags = _CONF_AUTH
    if isinstance(value, DPTBinary):
        data = bytes((value.value,))
    else:
        flags |= _SELECTOR
        data = bytes(value.value)
    return (
        bytes((0x00, SEND_GROUP_VALUE_WRITE, flags))
        + group_address.to_bytes(2, "big")
        + data
    )


def read_command(group_address: int) -> bytes:
    """The command that makes a device send an A_GroupValue_Read to the group address (raw)."""
    return bytes((0x00, SEND_GROUP_VALUE_READ, _CONF_AUTH)) + group_address.to_bytes(
        2, "big"
    )


async def send_command(
    xknx: XKNX,
    address: IndividualAddress,
    security: DeviceSecurity | None,
    command: bytes,
) -> None:
    """Give the device at ``address`` the Group Object Diagnostics ``command``, connectionless
    and secured with ``security`` (its Tool Key) when set; :class:`LoadStateError` when it
    refuses it (a return code other than 00h)."""
    from .session import management_session

    manager = management_session(xknx, address, security, connectionless=True)
    connection = await manager.open()
    try:
        telegram = await connection.request(
            FunctionPropertyExtCommand(
                interface_object_type=ResourceObjectType.OBJECT_GROUP_OBJECT_TABLE.value,
                object_instance=1,
                property_id=PID_GO_DIAGNOSTICS,
                data=command,
            ),
            FunctionPropertyExtStateResponse,
        )
    finally:
        await manager.close()
    payload = telegram.payload
    if not isinstance(payload, FunctionPropertyExtStateResponse):
        raise LoadStateError(f"{address} gave no Group Object Diagnostics response")
    if payload.return_code.value != 0:
        raise LoadStateError(
            f"{address} refused the Group Object Diagnostics command "
            f"{command.hex(' ')}: return code {payload.return_code.value:02X}h"
        )
