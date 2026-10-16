"""The KNX Data Secure configuration of a device: Tool Key, Security Mode and the security tables.

A secure device keeps them in its Security Interface Object (object type 17, KNX Standard v3.0.0,
3/5/1 6.3). They are written as ordinary load controls next to the application's own procedure:

* commissioning (the first secure download, still secured with the FDSK): Security Mode on, then the
  Tool Key (3/5/1 6.1.5). The device answers the Tool Key write already with the new key
  (6.3.10.4), so the runner announces the change to the secured connection first;
* the tables, before the final restart: the Security Individual Address Table and the Group Key
  Table under the object's own load state (6.3.7.4, 6.3.8.4), then the Group Object Security
  Flags (6.3.15). Each table is cleared by writing 0 to element 0 and then filled element by
  element from index 1.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from xknxeditor.namespaces.intermediate import (
    LdCtrlInvokeFunctionProp,
    LdCtrlLoad,
    LdCtrlLoadCompleted,
    LdCtrlUnload,
    LdCtrlWriteProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType

SECURITY_OBJECT: Final = 17
PID_SECURITY_MODE: Final = 51
PID_P2P_KEY_TABLE: Final = 52
PID_GRP_KEY_TABLE: Final = 53
PID_SECURITY_INDIVIDUAL_ADDRESS_TABLE: Final = 54
PID_TOOL_KEY: Final = 56
PID_SEQUENCE_NUMBER_SENDING: Final = 59
PID_GO_SECURITY_FLAGS: Final = 61
#: Element width of the security object's writable array properties (3/5/1 Table 99).
SECURITY_PROPERTY_WIDTHS: Final = {
    PID_P2P_KEY_TABLE: 20,
    PID_GRP_KEY_TABLE: 18,
    PID_SECURITY_INDIVIDUAL_ADDRESS_TABLE: 8,
    PID_TOOL_KEY: 16,
    PID_SEQUENCE_NUMBER_SENDING: 6,
    PID_GO_SECURITY_FLAGS: 1,
}
#: Group Object Security Flags of an object linked to a secured group address: authentication and
#: confidentiality (3/5/1 6.3.15.2, b0 = authentication, b1 = confidentiality). Every object on a
#: secured group address gets the same flags: a device accepts a group telegram only with exactly
#: the security its flags require (3/5/1 6.3.15), and a group address's ``@Security`` has no
#: authentication-only level, so all its senders use both.
GO_AUTHENTICATION_AND_CONFIDENTIALITY: Final = 0x03
# A_FunctionPropertyCommand data enabling / disabling the Security Mode: reserved, service
# "write", on / off (3/5/1 6.3.5.1).
_SECURITY_MODE_ON: Final = b"\x00\x00\x01"
_SECURITY_MODE_OFF: Final = b"\x00\x00\x00"
# Flags per write: a few elements stay within the shortest APDU a secure device negotiates.
_FLAGS_PER_WRITE: Final = 8
_TABLES: Final = LdCtrlProcType.FULL_GRP


@dataclass(frozen=True, slots=True)
class GroupSecurity:
    """The project's KNX Data Secure data for one device's communication.

    ``keys`` maps each secured group address (raw) to its 16-octet key. ``senders`` are the
    individual addresses (raw) that send secured telegrams this device must accept, with the last
    valid Sequence Number known for each (0 if unknown, 3/5/1 6.3.8.5). ``p2p`` are the device's
    secure
    point-to-point / broadcast partners as ``(partner individual address, 16-octet key, roles)``
    (3/5/1 6.3.6)."""

    keys: Mapping[int, bytes]
    senders: Sequence[tuple[int, int]] = ()
    p2p: Sequence[tuple[int, bytes, int]] = ()


@dataclass(frozen=True, slots=True)
class SecurityTables:
    """The security tables of a device, derived from the group addresses its address table holds.

    ``group_keys`` are ``(address table index, key)`` in index order, ``individual_addresses``
    ``(individual address, last valid sequence number)`` in address order, ``go_flags`` one octet
    per group object number from 0, and ``p2p_keys`` ``(individual-address-table index, key,
    roles)`` in index order (3/5/1 6.3.6)."""

    group_keys: tuple[tuple[int, bytes], ...]
    individual_addresses: tuple[tuple[int, int], ...]
    go_flags: bytes
    p2p_keys: tuple[tuple[int, bytes, int], ...] = ()


def security_tables(
    group_addresses: Sequence[int],
    links: Sequence[tuple[int, int]],
    security: GroupSecurity,
) -> SecurityTables | None:
    """The security tables for an address table of ``group_addresses`` (in table order) and the
    active ``(group address, group object number)`` links, or ``None`` when the device has no
    secured group address and no secure point-to-point partner.

    The Security Individual Address Table holds every secured sender and every point-to-point
    partner; a point-to-point key references the partner by its 1-based index in that table
    (3/5/1 6.3.6.2, 6.3.8)."""
    index_of = {address: i + 1 for i, address in enumerate(group_addresses)}
    group_keys = tuple(
        sorted(
            (index_of[address], key)
            for address, key in security.keys.items()
            if address in index_of
        )
    )
    if not group_keys and not security.p2p:
        return None
    secured = {address for address in security.keys if address in index_of}
    numbers = {number for address, number in links if address in secured}
    flags = bytearray(max(numbers) + 1 if numbers else 0)
    for number in numbers:
        flags[number] = GO_AUTHENTICATION_AND_CONFIDENTIALITY
    sequence_of: dict[int, int] = {}
    for address, sequence in security.senders:
        sequence_of[address] = max(sequence, sequence_of.get(address, 0))
    for address, _key, _roles in security.p2p:
        sequence_of.setdefault(address, 0)
    individual_addresses = tuple(sorted(sequence_of.items()))
    ia_index = {address: i + 1 for i, (address, _) in enumerate(individual_addresses)}
    p2p_keys = tuple(
        sorted((ia_index[address], key, roles) for address, key, roles in security.p2p)
    )
    return SecurityTables(
        group_keys=group_keys,
        individual_addresses=individual_addresses,
        go_flags=bytes(flags),
        p2p_keys=p2p_keys,
    )


def sequence_number_control(value: int) -> LdCtrlWriteProp:
    """Write the device's Sequence Number Sending (3/5/1 6.3.13); the device secures its answer
    with the new value already."""
    return _write(
        PID_SEQUENCE_NUMBER_SENDING,
        1,
        value.to_bytes(6, "big"),
        applies_to=LdCtrlProcType.ALL,
    )


def _write(
    prop_id: int,
    start: int,
    data: bytes,
    count: int = 1,
    applies_to: LdCtrlProcType = _TABLES,
) -> LdCtrlWriteProp:
    return LdCtrlWriteProp(
        obj_type=SECURITY_OBJECT,
        prop_id=prop_id,
        start_element=start,
        count=count,
        # Confirmed: the device's answer to the Tool Key write is what switches the session to
        # the new key, and a rejected table write must not go unnoticed.
        verify=True,
        inline_data=data,
        applies_to=applies_to,
    )


def _clear(prop_id: int, applies_to: LdCtrlProcType = _TABLES) -> LdCtrlWriteProp:
    return _write(prop_id, 0, b"\x00\x00", applies_to=applies_to)


def commissioning_controls(tool_key: bytes) -> list[object]:
    """Security Mode on, then the Tool Key: the start of every download of a device the project has
    a Tool Key for. The runner skips the Tool Key write when the device already uses that key."""
    return [
        LdCtrlInvokeFunctionProp(
            obj_type=SECURITY_OBJECT,
            prop_id=PID_SECURITY_MODE,
            inline_data=_SECURITY_MODE_ON,
            applies_to=LdCtrlProcType.ALL,
        ),
        _write(PID_TOOL_KEY, 1, tool_key, applies_to=LdCtrlProcType.ALL),
    ]


#: The Router Object of a coupler and its routing control of point-to-point and broadcast
#: messages (KNX AN192 2.3.1.1, 2.3.1.2): BLOCK (0) or RULE (1, the default) in b1..b0.
ROUTER_OBJECT: Final = 6
PID_PTP_CONFIG: Final = 80
PID_BC_CONFIG: Final = 82
_ROUTE_BLOCK: Final = 0
_ROUTE_RULE: Final = 1


def routing_control_controls(*, block: bool, router_objects: int = 2) -> list[object]:
    """Block (or route by the rules) point-to-point and broadcast messages on each side of a
    coupler with the security extensions: its two Router Objects (primary and secondary side),
    written on every full and group download over the secured connection (AN192 2.5.2, 2.5.3)."""
    value = bytes([_ROUTE_BLOCK if block else _ROUTE_RULE])
    return [
        LdCtrlWriteProp(
            obj_type=ROUTER_OBJECT,
            occurrence=occurrence,
            prop_id=prop_id,
            start_element=1,
            count=1,
            verify=True,
            inline_data=value,
            applies_to=_TABLES,
        )
        for occurrence in range(router_objects)
        for prop_id in (PID_PTP_CONFIG, PID_BC_CONFIG)
    ]


def plain_controls(*, p2p: bool) -> list[object]:
    """Switch a device that was commissioned securely to plain communication, over the secured
    connection with its Tool Key: its security tables are emptied (the Point-to-point Key Table
    only with ``p2p``, a device that has one), its group objects lose their security flags and the
    Security Mode is switched off (3/5/1 6.3.5) - the last step, so the rest of the download is
    still secured."""
    controls: list[object] = [
        LdCtrlUnload(obj_type=SECURITY_OBJECT, applies_to=LdCtrlProcType.ALL),
        LdCtrlLoad(obj_type=SECURITY_OBJECT, applies_to=LdCtrlProcType.ALL),
        _clear(PID_SECURITY_INDIVIDUAL_ADDRESS_TABLE, LdCtrlProcType.ALL),
        _clear(PID_GRP_KEY_TABLE, LdCtrlProcType.ALL),
    ]
    if p2p:
        controls.append(_clear(PID_P2P_KEY_TABLE, LdCtrlProcType.ALL))
    controls += [
        LdCtrlLoadCompleted(obj_type=SECURITY_OBJECT, applies_to=LdCtrlProcType.ALL),
        _clear(PID_GO_SECURITY_FLAGS, LdCtrlProcType.ALL),
        LdCtrlInvokeFunctionProp(
            obj_type=SECURITY_OBJECT,
            prop_id=PID_SECURITY_MODE,
            inline_data=_SECURITY_MODE_OFF,
            applies_to=LdCtrlProcType.ALL,
        ),
    ]
    return controls


def table_controls(tables: SecurityTables) -> list[object]:
    """The load controls writing ``tables``."""
    controls: list[object] = [
        LdCtrlUnload(obj_type=SECURITY_OBJECT, applies_to=_TABLES),
        LdCtrlLoad(obj_type=SECURITY_OBJECT, applies_to=_TABLES),
        _clear(PID_SECURITY_INDIVIDUAL_ADDRESS_TABLE),
    ]
    for index, (address, sequence) in enumerate(tables.individual_addresses, start=1):
        element = address.to_bytes(2, "big") + sequence.to_bytes(6, "big")
        controls.append(_write(PID_SECURITY_INDIVIDUAL_ADDRESS_TABLE, index, element))
    controls.append(_clear(PID_GRP_KEY_TABLE))
    for index, (table_index, key) in enumerate(tables.group_keys, start=1):
        controls.append(
            _write(PID_GRP_KEY_TABLE, index, table_index.to_bytes(2, "big") + key)
        )
    if tables.p2p_keys:
        controls.append(_clear(PID_P2P_KEY_TABLE))
        for index, (ia_index, key, roles) in enumerate(tables.p2p_keys, start=1):
            element = ia_index.to_bytes(2, "big") + key + roles.to_bytes(2, "big")
            controls.append(_write(PID_P2P_KEY_TABLE, index, element))
    controls.append(LdCtrlLoadCompleted(obj_type=SECURITY_OBJECT, applies_to=_TABLES))
    controls.append(_clear(PID_GO_SECURITY_FLAGS))
    for start in range(0, len(tables.go_flags), _FLAGS_PER_WRITE):
        chunk = tables.go_flags[start : start + _FLAGS_PER_WRITE]
        controls.append(_write(PID_GO_SECURITY_FLAGS, start + 1, chunk, len(chunk)))
    return controls
