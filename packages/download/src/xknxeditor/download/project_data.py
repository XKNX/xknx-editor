"""Adapt project state into download inputs.

Turns a configured device (parameter values and group address assignments) into
the inputs a download needs: a parameter value map that feeds the download image,
and the structured group communication links a device transmits and receives on.

The device is consumed structurally (see the protocols below), so this module has
no hard dependency on the project store; any object with the same shape works,
including ``xknxeditor.proj`` ORM devices.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
    ComObjectInstanceRef,
)
from xknxeditor.namespaces.intermediate.enable_t import Enable
from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance
from xknxeditor.namespaces.intermediate.parameter_instance_ref_t import (
    ParameterInstanceRef,
)


class _Parameter(Protocol):
    ref_id: str
    value: str


class _ModuleInstance(Protocol):
    instance_id: str
    ref_id: str


class _GroupAddress(Protocol):
    address: int


class _ComObjectLink(Protocol):
    is_sending: bool

    @property
    def group_address(self) -> _GroupAddress: ...


class _ComObject(Protocol):
    ref_id: str

    @property
    def links(self) -> Sequence[_ComObjectLink]: ...


class _ComObjectConfig(Protocol):
    ref_id: str
    channel_id: str | None
    read_flag: bool | None
    write_flag: bool | None
    communication_flag: bool | None
    transmit_flag: bool | None
    update_flag: bool | None
    read_on_init_flag: bool | None


class _Device(Protocol):
    @property
    def parameters(self) -> Sequence[_Parameter]: ...

    @property
    def com_objects(self) -> Sequence[_ComObject]: ...


class SeedDevice(Protocol):
    @property
    def parameters(self) -> Sequence[_Parameter]: ...

    @property
    def module_instances(self) -> Sequence[_ModuleInstance]: ...

    @property
    def com_objects(self) -> Sequence[_ComObjectConfig]: ...


def _enable(value: bool | None) -> Enable | None:
    """Map a project boolean flag override to the model's Enable enum."""
    if value is None:
        return None
    return Enable.ENABLED if value else Enable.DISABLED


@dataclass(frozen=True, slots=True)
class GroupObjectLink:
    """A link between a device com object and a group address."""

    com_object_ref_id: str
    group_address: int
    sending: bool
    group_speaker: bool = False


def parameter_values_from_device(device: _Device) -> dict[str, str]:
    """Collect the parameter reference id to value map of a configured device."""
    return {parameter.ref_id: parameter.value for parameter in device.parameters}


def group_communication_from_device(device: _Device) -> tuple[GroupObjectLink, ...]:
    """Collect the group address links of a device's com objects."""
    return tuple(
        GroupObjectLink(
            com_object_ref_id=com_object.ref_id,
            group_address=link.group_address.address,
            sending=link.is_sending,
        )
        for com_object in device.com_objects
        for link in com_object.links
    )


def group_address_table(links: Sequence[GroupObjectLink]) -> tuple[int, ...]:
    """Return the sorted, unique group addresses a device uses.

    This is the content of the group address table. Encoding it (and the
    association table) into device memory is realisation-type specific and is
    performed by the Load Procedure's table segments; this helper provides the
    ordered address set those tables are built from.
    """
    return tuple(sorted({link.group_address for link in links}))


def parameter_instance_refs_from_device(
    device: SeedDevice,
) -> list[ParameterInstanceRef]:
    """Build the application evaluator's parameter instance refs from a device."""
    return [
        ParameterInstanceRef(ref_id=parameter.ref_id, value=parameter.value)
        for parameter in device.parameters
    ]


def module_instances_from_device(device: SeedDevice) -> list[ModuleInstance]:
    """Build the application evaluator's module instances from a device."""
    return [
        ModuleInstance(id=instance.instance_id, ref_id=instance.ref_id)
        for instance in device.module_instances
    ]


def com_object_instance_refs_from_device(
    device: SeedDevice,
) -> list[ComObjectInstanceRef]:
    """Build the evaluator's com object instance refs (flags, channel) from a device.

    Only configuration that influences the parameter memory image is carried
    (flag overrides, channel); group address links belong to the group
    communication tables, not the parameter part.
    """
    return [
        ComObjectInstanceRef(
            ref_id=com_object.ref_id,
            channel_id=com_object.channel_id,
            read_flag=_enable(com_object.read_flag),
            write_flag=_enable(com_object.write_flag),
            communication_flag=_enable(com_object.communication_flag),
            transmit_flag=_enable(com_object.transmit_flag),
            update_flag=_enable(com_object.update_flag),
            read_on_init_flag=_enable(com_object.read_on_init_flag),
        )
        for com_object in device.com_objects
    ]
