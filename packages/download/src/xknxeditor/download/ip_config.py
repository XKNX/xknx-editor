"""IP settings a full download writes to the KNXnet/IP parameter object of an IP device.

For an IP device whose IP configuration the tool manages, a full download reads the device's MAC
address and writes the project installation id, the routing multicast address, the TTL, the
friendly name, the IP assignment method, for a fixed assignment the IP address, subnet mask and
default gateway, and the additional individual addresses. The controls are inserted before the
procedure's last restart.
"""

from __future__ import annotations

import ipaddress
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.application_program_ipconfig_t import (
    ApplicationProgramIpconfig,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_disconnect_t import LdCtrlDisconnect
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_master_reset_t import LdCtrlMasterReset
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

if TYPE_CHECKING:
    from xknxeditor.prod import Application

IP_PARAMETER_OBJECT = 11
PID_PROJECT_INSTALLATION_ID = 51
PID_ADDITIONAL_INDIVIDUAL_ADDRESSES = 53
PID_IP_ASSIGNMENT_METHOD = 55
PID_IP_ADDRESS = 60
PID_SUBNET_MASK = 61
PID_DEFAULT_GATEWAY = 62
PID_MAC_ADDRESS = 64
PID_ROUTING_MULTICAST_ADDRESS = 66
PID_TTL = 67
PID_FRIENDLY_NAME = 76

# Element sizes of the properties above, so no description has to be read.
IP_PROPERTY_WIDTHS = {
    PID_PROJECT_INSTALLATION_ID: 2,
    PID_ADDITIONAL_INDIVIDUAL_ADDRESSES: 2,
    PID_IP_ASSIGNMENT_METHOD: 1,
    PID_IP_ADDRESS: 4,
    PID_SUBNET_MASK: 4,
    PID_DEFAULT_GATEWAY: 4,
    PID_MAC_ADDRESS: 6,
    PID_ROUTING_MULTICAST_ADDRESS: 4,
    PID_TTL: 1,
    PID_FRIENDLY_NAME: 1,
}

DEFAULT_ROUTING_MULTICAST_ADDRESS = "224.0.23.12"
_PROJECT_INSTALLATION_ID = 0
_TTL = 32
_FRIENDLY_NAME_LENGTH = 30
_ASSIGN_FIXED = 0x01
_ASSIGN_DHCP = 0x04
_LINE_COUPLER_MASK = "MV-091A"
_LINE_COUPLER_OBJECT = 6
_LINE_COUPLER_PIDS = (52, 53, 54, 55)


@dataclass(frozen=True)
class IpDeviceConfig:
    """The project's IP settings of an IP device (hardware with ``IsIPEnabled``)."""

    individual_address: int
    friendly_name: str = ""
    routing_multicast_address: str | None = DEFAULT_ROUTING_MULTICAST_ADDRESS
    fixed_address: bool = False
    ip_address: str | None = None
    subnet_mask: str | None = None
    default_gateway: str | None = None
    # Device numbers (last address octet) of the additional individual addresses.
    additional_addresses: Sequence[int] = field(default_factory=tuple[int, ...])


def applies(application: Application) -> bool:
    """Whether the tool manages the IP settings of the application's device."""
    return application.program.ipconfig is not ApplicationProgramIpconfig.CUSTOM


def _ipv4(text: str | None) -> bytes | None:
    if not text:
        return None
    try:
        address = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    return address.packed if address.version == 4 else None


def _friendly_name(name: str) -> bytes:
    """The name in ISO 8859-1, transliterated where needed, cut or padded with zeros to 30."""
    text = name.strip()
    try:
        data = text.encode("latin-1")
    except UnicodeEncodeError:
        ascii_text = (
            unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        )
        data = ascii_text.strip().encode("latin-1")
    return data[:_FRIENDLY_NAME_LENGTH].ljust(_FRIENDLY_NAME_LENGTH, b"\x00")


def _write(property_id: int, data: bytes, count: int = 1) -> LdCtrlWriteProp:
    return LdCtrlWriteProp(
        obj_type=IP_PARAMETER_OBJECT,
        prop_id=property_id,
        start_element=1,
        count=count,
        verify=True,
        inline_data=data,
    )


def ip_config_controls(
    application: Application, config: IpDeviceConfig
) -> list[object]:
    """The controls that read the MAC address and write the IP settings."""
    controls: list[object] = [
        LdCtrlLoadImageProp(
            obj_type=IP_PARAMETER_OBJECT,
            prop_id=PID_MAC_ADDRESS,
            start_element=1,
            count=1,
        ),
        _write(
            PID_PROJECT_INSTALLATION_ID, _PROJECT_INSTALLATION_ID.to_bytes(2, "big")
        ),
    ]
    multicast = _ipv4(config.routing_multicast_address)
    if multicast is not None:
        controls.append(_write(PID_ROUTING_MULTICAST_ADDRESS, multicast))
    controls.append(_write(PID_TTL, bytes([_TTL])))
    controls.append(
        _write(
            PID_FRIENDLY_NAME,
            _friendly_name(config.friendly_name),
            _FRIENDLY_NAME_LENGTH,
        )
    )
    controls.append(
        _write(
            PID_IP_ASSIGNMENT_METHOD,
            bytes([_ASSIGN_FIXED if config.fixed_address else _ASSIGN_DHCP]),
        )
    )
    if config.fixed_address:
        for property_id, text in (
            (PID_IP_ADDRESS, config.ip_address),
            (PID_SUBNET_MASK, config.subnet_mask),
            (PID_DEFAULT_GATEWAY, config.default_gateway),
        ):
            value = _ipv4(text)
            if value is not None:
                controls.append(_write(property_id, value))
    count = application.program.additional_addresses_count or 0
    if count > 0:
        line = (config.individual_address >> 8) & 0xFF
        entries = bytearray(b"\xff" * (2 * count))
        for i, number in enumerate(list(config.additional_addresses)[:count]):
            entries[2 * i : 2 * i + 2] = bytes([line, number & 0xFF])
        controls.append(
            _write(PID_ADDITIONAL_INDIVIDUAL_ADDRESSES, bytes(entries), count)
        )
    return controls


def insert_position(application: Application, controls: Sequence[object]) -> int:
    """Where controls that belong before the final restart go into ``controls``."""
    if application.program.mask_version == _LINE_COUPLER_MASK:
        loads = [
            i
            for i, control in enumerate(controls)
            if isinstance(control, LdCtrlLoad)
            and getattr(control, "obj_type", None) == _LINE_COUPLER_OBJECT
        ]
        if loads:
            position = loads[-1] + 1
            while position < len(controls):
                control = controls[position]
                if not (
                    isinstance(control, LdCtrlWriteProp)
                    and control.obj_type == _LINE_COUPLER_OBJECT
                    and control.prop_id in _LINE_COUPLER_PIDS
                ):
                    break
                position += 1
            return position
    for kind in (LdCtrlRestart, LdCtrlMasterReset):
        found = [
            i
            for i, control in enumerate(controls)
            if isinstance(control, kind)
            and (kind is LdCtrlRestart or getattr(control, "erase_code", None) == 1)
        ]
        if found:
            return found[-1]
    disconnects = [
        i for i, control in enumerate(controls) if isinstance(control, LdCtrlDisconnect)
    ]
    if disconnects:
        return disconnects[-1]
    return len(controls)


def with_ip_config_controls(
    application: Application, controls: Sequence[object], config: IpDeviceConfig
) -> list[object]:
    """``controls`` with the IP settings inserted before the last restart."""
    position = insert_position(application, controls)
    return [
        *controls[:position],
        *ip_config_controls(application, config),
        *controls[position:],
    ]
