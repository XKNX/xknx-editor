"""Events: each edit's reversible unit, run against a SQLAlchemy ``Session``.

An event changes the relational graph and can undo itself. On first ``apply`` it records every row
id it creates (the ``if self.x_id is not None`` guard) so a redo reuses those ids and foreign keys
elsewhere stay valid. ``to_dict``/``from_dict`` round-trip the whole payload — inputs, captured ids,
and any pre-change values ``revert`` needs — through the ``events`` JSON column, surviving reopen.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from xknxeditor.proj.core.ga_security import secure_commissioned
from xknxeditor.proj.core.identity import qualified_com_object_ref
from xknxeditor.proj.core.skeleton import MEDIUM_IP
from xknxeditor.proj.models import (
    Area,
    Base,
    ComObject,
    ComObjectLink,
    Device,
    DeviceAdditionalAddress,
    Function,
    FunctionGroupAddress,
    GroupAddress,
    GroupRange,
    Installation,
    Line,
    ModuleInstance,
    Parameter,
    Project,
    Segment,
    Space,
)

EVENT_TYPES: dict[str, type[Event]] = {}


def _register[E: Event](cls: type[E]) -> type[E]:
    EVENT_TYPES[cls.event_type] = cls
    return cls


def deserialize_event(event_type: str, data: dict[str, Any]) -> Event:
    cls = EVENT_TYPES.get(event_type)
    if cls is None:
        raise ValueError(f"Unknown event type: {event_type}")
    return cls.from_dict(data)


class Event(ABC):
    event_type: ClassVar[str]

    @abstractmethod
    def apply(self, session: Session) -> None: ...

    @abstractmethod
    def revert(self, session: Session) -> None: ...

    @abstractmethod
    def to_dict(self) -> dict[str, Any]: ...

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict[str, Any]) -> Event: ...


@_register
@dataclass
class AddInstallation(Event):
    event_type: ClassVar[str] = "AddInstallation"

    index: int
    name: str
    installation_id: int | None = None
    area_id: int | None = None
    line_id: int | None = None
    segment_id: int | None = None

    def apply(self, session: Session) -> None:
        inst = Installation(index=self.index, name=self.name)
        if self.installation_id is not None:
            inst.id = self.installation_id
        area = Area(address=0, name="")
        if self.area_id is not None:
            area.id = self.area_id
        line = Line(address=0, name="")
        if self.line_id is not None:
            line.id = self.line_id
        segment = Segment(number=0, medium_type=MEDIUM_IP)
        if self.segment_id is not None:
            segment.id = self.segment_id
        line.segments.append(segment)
        area.lines.append(line)
        inst.areas.append(area)
        session.add(inst)
        session.flush()
        self.installation_id = inst.id
        self.area_id = area.id
        self.line_id = line.id
        self.segment_id = segment.id

    def revert(self, session: Session) -> None:
        inst = session.get(Installation, self.installation_id)
        if inst is not None:
            session.delete(inst)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "installation_id": self.installation_id,
            "area_id": self.area_id,
            "line_id": self.line_id,
            "segment_id": self.segment_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AddInstallation:
        return cls(**data)


@_register
@dataclass
class CreateArea(Event):
    event_type: ClassVar[str] = "CreateArea"

    installation_id: int
    address: int
    name: str
    area_id: int | None = None

    def apply(self, session: Session) -> None:
        area = Area(
            installation_id=self.installation_id, address=self.address, name=self.name
        )
        if self.area_id is not None:
            area.id = self.area_id
        session.add(area)
        session.flush()
        self.area_id = area.id

    def revert(self, session: Session) -> None:
        area = session.get(Area, self.area_id)
        if area is not None:
            session.delete(area)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "address": self.address,
            "name": self.name,
            "area_id": self.area_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateArea:
        return cls(**data)


@_register
@dataclass
class CreateLine(Event):
    event_type: ClassVar[str] = "CreateLine"

    area_id: int
    address: int
    name: str
    medium_type: str
    line_id: int | None = None
    segment_id: int | None = None

    def apply(self, session: Session) -> None:
        line = Line(area_id=self.area_id, address=self.address, name=self.name)
        if self.line_id is not None:
            line.id = self.line_id
        segment = Segment(number=0, medium_type=self.medium_type)
        if self.segment_id is not None:
            segment.id = self.segment_id
        line.segments.append(segment)
        session.add(line)
        session.flush()
        self.line_id = line.id
        self.segment_id = segment.id

    def revert(self, session: Session) -> None:
        line = session.get(Line, self.line_id)
        if line is not None:
            session.delete(line)

    def to_dict(self) -> dict[str, Any]:
        return {
            "area_id": self.area_id,
            "address": self.address,
            "name": self.name,
            "medium_type": self.medium_type,
            "line_id": self.line_id,
            "segment_id": self.segment_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateLine:
        return cls(**data)


@_register
@dataclass
class CreateSegment(Event):
    event_type: ClassVar[str] = "CreateSegment"

    line_id: int
    number: int
    medium_type: str
    name: str
    segment_id: int | None = None

    def apply(self, session: Session) -> None:
        segment = Segment(
            line_id=self.line_id,
            number=self.number,
            medium_type=self.medium_type,
            name=self.name,
        )
        if self.segment_id is not None:
            segment.id = self.segment_id
        session.add(segment)
        session.flush()
        self.segment_id = segment.id

    def revert(self, session: Session) -> None:
        segment = session.get(Segment, self.segment_id)
        if segment is not None:
            session.delete(segment)

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_id": self.line_id,
            "number": self.number,
            "medium_type": self.medium_type,
            "name": self.name,
            "segment_id": self.segment_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateSegment:
        return cls(**data)


@_register
@dataclass
class AddDevice(Event):
    event_type: ClassVar[str] = "AddDevice"

    segment_id: int
    address: int | None
    name: str
    product_ref_id: str
    hardware2program_ref_id: str | None
    # The application-program ref (``M-XXXX_A-...``), used as a resolution fallback when the hardware
    # program is not in the catalog (see ProjectService._resolve_app). ``None`` = unknown.
    application_program_ref_id: str | None = None
    parameters: list[list[str]] = field(default_factory=list[list[str]])
    # [ref_id, channel_id] per row (channel_id optional)
    com_objects: list[list[str | None]] = field(default_factory=list[list[str | None]])
    # [instance_id, ref_id] per row, e.g. ["M-100_MI-1", "M-100"]
    module_instances: list[list[str]] = field(default_factory=list[list[str]])
    # Display fields of the product, as an imported device has them.
    product_name: str = ""
    hardware_name: str = ""
    order_number: str = ""
    manufacturer_name: str = ""
    # The Tool Key (an ``enc1:`` token) of a device added with secure commissioning activated.
    tool_key: str | None = None
    # The device's additional individual addresses (see :class:`SetDeviceAdditionalAddresses`).
    additional_addresses: list[dict[str, Any]] = field(
        default_factory=list[dict[str, Any]]
    )
    device_id: int | None = None
    parameter_ids: list[int] = field(default_factory=list[int])
    com_object_ids: list[int] = field(default_factory=list[int])
    module_instance_ids: list[int] = field(default_factory=list[int])

    def apply(self, session: Session) -> None:
        device = Device(
            segment_id=self.segment_id,
            address=self.address,
            name=self.name,
            product_ref_id=self.product_ref_id,
            hardware2program_ref_id=self.hardware2program_ref_id,
            application_program_ref_id=self.application_program_ref_id,
            product_name=self.product_name,
            hardware_name=self.hardware_name,
            order_number=self.order_number,
            manufacturer_name=self.manufacturer_name,
        )
        if self.tool_key is not None:
            _set_tool_key(device, self.tool_key)
        if self.device_id is not None:
            device.id = self.device_id
        for i, (instance_id, ref_id) in enumerate(self.module_instances):
            mi = ModuleInstance(instance_id=instance_id, ref_id=ref_id)
            if i < len(self.module_instance_ids):
                mi.id = self.module_instance_ids[i]
            device.module_instances.append(mi)
        for i, (ref_id, value) in enumerate(self.parameters):
            param = Parameter(ref_id=ref_id, value=value)
            if i < len(self.parameter_ids):
                param.id = self.parameter_ids[i]
            device.parameters.append(param)
        for i, (ref_id, channel_id) in enumerate(self.com_objects):
            assert ref_id is not None
            com_object = ComObject(ref_id=ref_id, channel_id=channel_id)
            if i < len(self.com_object_ids):
                com_object.id = self.com_object_ids[i]
            device.com_objects.append(com_object)
        _set_additional_entries(device, self.additional_addresses)
        session.add(device)
        session.flush()
        self.device_id = device.id
        self.module_instance_ids = [mi.id for mi in device.module_instances]
        self.parameter_ids = [p.id for p in device.parameters]
        self.com_object_ids = [c.id for c in device.com_objects]

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None:
            session.delete(device)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "address": self.address,
            "name": self.name,
            "product_ref_id": self.product_ref_id,
            "hardware2program_ref_id": self.hardware2program_ref_id,
            "application_program_ref_id": self.application_program_ref_id,
            "parameters": self.parameters,
            "com_objects": self.com_objects,
            "module_instances": self.module_instances,
            "product_name": self.product_name,
            "hardware_name": self.hardware_name,
            "order_number": self.order_number,
            "manufacturer_name": self.manufacturer_name,
            "tool_key": self.tool_key,
            "additional_addresses": self.additional_addresses,
            "device_id": self.device_id,
            "parameter_ids": self.parameter_ids,
            "com_object_ids": self.com_object_ids,
            "module_instance_ids": self.module_instance_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AddDevice:
        return cls(**data)


@_register
@dataclass
class UpdateDeviceApplication(Event):
    """Update a device to a newer version of the *same* application program, keeping parameter
    values and group-address links (like "Update Application Program").

    Repoints the device's ``product_ref_id``/``hardware2program_ref_id`` to the new version and
    re-prefixes every ``Parameter``/``ComObject`` ``ref_id`` from ``old_app_id`` to ``new_app_id``.
    A row whose re-prefixed ref does not exist in the new application (``valid_ref_ids``) is dropped
    (its ``ComObjectLink`` rows cascade). Rows that are not application-id-prefixed (module-instance
    parameters, foreign refs) are left untouched. Links of carried com-objects are preserved because
    they hang off ``com_object_id``, which does not change.
    """

    event_type: ClassVar[str] = "UpdateDeviceApplication"

    device_id: int
    new_product_ref_id: str
    new_hardware2program_ref_id: str | None
    old_app_id: str
    new_app_id: str
    valid_ref_ids: list[str]
    # Optional display columns to refresh from the new product (empty/None = leave unchanged).
    new_order_number: str | None = None
    new_hardware_name: str | None = None
    new_product_name: str | None = None
    new_manufacturer_name: str | None = None
    new_description: str | None = None
    # Captured on apply for revert.
    old_product_ref_id: str | None = None
    old_hardware2program_ref_id: str | None = None
    old_display: dict[str, str] | None = None
    renamed: list[list[str]] = field(default_factory=list[list[str]])
    deleted_params: list[list[Any]] = field(default_factory=list[list[Any]])
    deleted_com_objects: list[dict[str, Any]] = field(
        default_factory=list[dict[str, Any]]
    )
    old_loaded: tuple[list[bool], dict[str, str]] | None = None

    _CO_FLAGS: ClassVar[tuple[str, ...]] = (
        "read_flag",
        "write_flag",
        "communication_flag",
        "transmit_flag",
        "update_flag",
        "read_on_init_flag",
    )
    _DISPLAY_COLS: ClassVar[tuple[str, ...]] = (
        "order_number",
        "hardware_name",
        "product_name",
        "manufacturer_name",
        "description",
    )

    def _remap(self, ref: str, valid: set[str]) -> str | None:
        """New ref if the row should be kept, ``None`` if it should be dropped."""
        if not ref.startswith(self.old_app_id):
            return ref  # not application-prefixed -> carry unchanged
        candidate = self.new_app_id + ref[len(self.old_app_id) :]
        return candidate if candidate in valid else None

    def _snapshot_co(self, co: ComObject) -> dict[str, Any]:
        snap: dict[str, Any] = {
            "id": co.id,
            "ref_id": co.ref_id,
            "instance_ref_id": co.instance_ref_id,
            "channel_id": co.channel_id,
            "links": [
                [link.id, link.group_address_id, link.is_sending] for link in co.links
            ],
            "text_override": co.text_override,
            "function_text_override": co.function_text_override,
            "description_override": co.description_override,
        }
        for flag in self._CO_FLAGS:
            snap[flag] = getattr(co, flag)
        return snap

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        valid = set(self.valid_ref_ids)

        self.old_product_ref_id = device.product_ref_id
        self.old_hardware2program_ref_id = device.hardware2program_ref_id
        device.product_ref_id = self.new_product_ref_id
        device.hardware2program_ref_id = self.new_hardware2program_ref_id
        # Keep the application-ref fallback in step with the repoint, else a later failed
        # hardware-program lookup would resolve the OLD application against new parameter refs.
        device.application_program_ref_id = self.new_app_id

        self.old_display = {col: getattr(device, col) for col in self._DISPLAY_COLS}
        new_display = {
            "order_number": self.new_order_number,
            "hardware_name": self.new_hardware_name,
            "product_name": self.new_product_name,
            "manufacturer_name": self.new_manufacturer_name,
            "description": self.new_description,
        }
        for col, value in new_display.items():
            if value:
                setattr(device, col, value)
        self.old_loaded = _clear_application_loaded(device)

        self.renamed = []
        self.deleted_params = []
        self.deleted_com_objects = []

        for param in list(device.parameters):
            new_ref = self._remap(param.ref_id, valid)
            if new_ref is None:
                self.deleted_params.append([param.id, param.ref_id, param.value])
                session.delete(param)
            elif new_ref != param.ref_id:
                self.renamed.append(["P", str(param.id), param.ref_id, new_ref])
                param.ref_id = new_ref

        for co in list(device.com_objects):
            new_ref = self._remap(co.ref_id, valid)
            if new_ref is None:
                self.deleted_com_objects.append(self._snapshot_co(co))
                session.delete(co)
                continue
            # ETS4 can store a fully app-prefixed instance_ref_id; re-prefix it to the new
            # application too, or the qualified lookup would double-prefix (NEWAPP_OLDAPP_...).
            if co.instance_ref_id.startswith(self.old_app_id):
                new_inst = self.new_app_id + co.instance_ref_id[len(self.old_app_id) :]
                if new_inst != co.instance_ref_id:
                    self.renamed.append(
                        ["OI", str(co.id), co.instance_ref_id, new_inst]
                    )
                    co.instance_ref_id = new_inst
            if new_ref != co.ref_id:
                self.renamed.append(["O", str(co.id), co.ref_id, new_ref])
                co.ref_id = new_ref
        session.flush()

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        if self.old_product_ref_id is not None:
            device.product_ref_id = self.old_product_ref_id
        device.hardware2program_ref_id = self.old_hardware2program_ref_id
        device.application_program_ref_id = self.old_app_id
        if self.old_display is not None:
            for col, value in self.old_display.items():
                setattr(device, col, value)
        if self.old_loaded is not None:
            _restore_application_loaded(device, self.old_loaded)

        for kind, row_id, old_ref, _new_ref in self.renamed:
            if kind == "P":
                param = session.get(Parameter, int(row_id))
                if param is not None:
                    param.ref_id = old_ref
                continue
            co = session.get(ComObject, int(row_id))
            if co is None:
                continue
            if kind == "OI":
                co.instance_ref_id = old_ref
            else:
                co.ref_id = old_ref

        for pid, ref_id, value in self.deleted_params:
            param = Parameter(device_id=self.device_id, ref_id=ref_id, value=value)
            param.id = pid
            session.add(param)

        for snap in self.deleted_com_objects:
            co = ComObject(
                device_id=self.device_id,
                ref_id=snap["ref_id"],
                instance_ref_id=snap.get("instance_ref_id", ""),
                channel_id=snap["channel_id"],
                text_override=snap.get("text_override"),
                function_text_override=snap.get("function_text_override"),
                description_override=snap.get("description_override"),
            )
            co.id = snap["id"]
            for flag in self._CO_FLAGS:
                setattr(co, flag, snap[flag])
            session.add(co)
            session.flush()
            for lid, ga_id, is_sending in snap["links"]:
                link = ComObjectLink(
                    com_object_id=co.id,
                    group_address_id=ga_id,
                    is_sending=is_sending,
                )
                link.id = lid
                session.add(link)
        session.flush()

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "new_product_ref_id": self.new_product_ref_id,
            "new_hardware2program_ref_id": self.new_hardware2program_ref_id,
            "old_app_id": self.old_app_id,
            "new_app_id": self.new_app_id,
            "valid_ref_ids": self.valid_ref_ids,
            "new_order_number": self.new_order_number,
            "new_hardware_name": self.new_hardware_name,
            "new_product_name": self.new_product_name,
            "new_manufacturer_name": self.new_manufacturer_name,
            "new_description": self.new_description,
            "old_product_ref_id": self.old_product_ref_id,
            "old_hardware2program_ref_id": self.old_hardware2program_ref_id,
            "old_display": self.old_display,
            "renamed": self.renamed,
            "deleted_params": self.deleted_params,
            "deleted_com_objects": self.deleted_com_objects,
            "old_loaded": self.old_loaded,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpdateDeviceApplication:
        return cls(**data)


_APPLICATION_LOADED = (
    "application_program_loaded",
    "parameters_loaded",
    "communication_part_loaded",
)

# DeviceInstance commissioning blobs that identify the loaded image (captured verbatim into
# extra_attributes on import). An application change invalidates them — they belong to the OLD
# program — so clear them alongside the loaded flags; leaving them would attach the old program's
# image/checksums to the new one on export.
_LOADED_IMAGE_ATTRS = ("CheckSums", "LoadedImage")


def _clear_application_loaded(device: Device) -> tuple[list[bool], dict[str, str]]:
    """Clear the loaded-application flags and the now-stale loaded-image attributes; return the old
    values ``(flags, removed_attributes)`` for undo."""
    old = [getattr(device, name) for name in _APPLICATION_LOADED]
    for name in _APPLICATION_LOADED:
        setattr(device, name, False)
    attrs = dict(device.extra_attributes or {})
    removed = {k: attrs.pop(k) for k in _LOADED_IMAGE_ATTRS if k in attrs}
    if removed:
        device.extra_attributes = (
            attrs  # reassign: the JSON column is not mutation-tracked
        )
    return old, removed


def _restore_application_loaded(
    device: Device, saved: tuple[list[bool], dict[str, str]]
) -> None:
    values, removed = saved
    for name, value in zip(_APPLICATION_LOADED, values, strict=True):
        setattr(device, name, value)
    if removed:
        device.extra_attributes = {**(device.extra_attributes or {}), **removed}


@_register
@dataclass
class ChangeDeviceApplication(Event):
    """Switch a device to another application program of its hardware, starting from that
    application's defaults ("Change Application Program") or with values carried over from the
    old one ("Update Application Program Version").

    Parameters, module instances, com objects with their group-address links, binary data, the
    group-object tree and the channel instances are removed; the new application's module
    instances, ``parameters`` and com objects are added. ``com_object_data`` holds, per entry of
    ``com_objects``, the flags, priority, datapoint type and links to set, or ``None``. The
    application, parameter and communication loaded flags are cleared. The individual address,
    name, serial number, security and IP settings, additional addresses and bus interfaces stay.
    """

    event_type: ClassVar[str] = "ChangeDeviceApplication"

    device_id: int
    new_hardware2program_ref_id: str
    new_app_id: str
    # [ref_id, channel_id] per row
    com_objects: list[list[str | None]] = field(default_factory=list[list[str | None]])
    # [instance_id, ref_id] per row
    module_instances: list[list[str]] = field(default_factory=list[list[str]])
    # [ref_id, value] per row
    parameters: list[list[str]] = field(default_factory=list[list[str]])
    com_object_data: list[dict[str, Any] | None] = field(
        default_factory=list[dict[str, Any] | None]
    )
    com_object_ids: list[int] = field(default_factory=list[int])
    module_instance_ids: list[int] = field(default_factory=list[int])
    parameter_ids: list[int] = field(default_factory=list[int])
    link_ids: list[int] = field(default_factory=list[int])
    # Captured on apply for revert.
    old_hardware2program_ref_id: str | None = None
    old_app_id: str | None = None
    old_fields: dict[str, Any] = field(default_factory=dict[str, Any])
    removed: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    _SUBTREE_KEY: ClassVar[str] = "ChannelInstances"

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        self.old_hardware2program_ref_id = device.hardware2program_ref_id
        self.old_app_id = device.application_program_ref_id
        self.old_fields = {
            "loaded": _clear_application_loaded(device),
            "group_object_tree": device.group_object_tree,
            "extra_subtrees": device.extra_subtrees,
        }
        self.removed = []
        for rows in (
            device.parameters,
            device.module_instances,
            device.com_objects,
            device.binary_data,
        ):
            for row in list(rows):
                self.removed.extend(_snapshot_subtree(row))
                session.delete(row)
        session.flush()
        session.expire(
            device, ["parameters", "module_instances", "com_objects", "binary_data"]
        )

        device.hardware2program_ref_id = self.new_hardware2program_ref_id
        device.application_program_ref_id = self.new_app_id
        device.group_object_tree = ""
        if device.extra_subtrees and self._SUBTREE_KEY in device.extra_subtrees:
            device.extra_subtrees = {
                key: value
                for key, value in device.extra_subtrees.items()
                if key != self._SUBTREE_KEY
            } or None

        added_instances: list[ModuleInstance] = []
        for i, (instance_id, ref_id) in enumerate(self.module_instances):
            instance = ModuleInstance(
                device_id=self.device_id, instance_id=instance_id, ref_id=ref_id
            )
            if i < len(self.module_instance_ids):
                instance.id = self.module_instance_ids[i]
            session.add(instance)
            added_instances.append(instance)
        added_parameters: list[Parameter] = []
        for i, (ref_id, value) in enumerate(self.parameters):
            parameter = Parameter(device_id=self.device_id, ref_id=ref_id, value=value)
            if i < len(self.parameter_ids):
                parameter.id = self.parameter_ids[i]
            session.add(parameter)
            added_parameters.append(parameter)
        added_objects: list[ComObject] = []
        for i, (ref_id, channel_id) in enumerate(self.com_objects):
            assert ref_id is not None
            com_object = ComObject(
                device_id=self.device_id, ref_id=ref_id, channel_id=channel_id
            )
            data = self.com_object_data[i] if i < len(self.com_object_data) else None
            for name, value in (data or {}).get("fields", {}).items():
                setattr(com_object, name, value)
            if i < len(self.com_object_ids):
                com_object.id = self.com_object_ids[i]
            session.add(com_object)
            added_objects.append(com_object)
        session.flush()
        added_links: list[ComObjectLink] = []
        for com_object, data in zip(added_objects, self.com_object_data, strict=False):
            for group_address_id, is_sending in (data or {}).get("links", []):
                link = ComObjectLink(
                    com_object_id=com_object.id,
                    group_address_id=group_address_id,
                    is_sending=is_sending,
                )
                if len(added_links) < len(self.link_ids):
                    link.id = self.link_ids[len(added_links)]
                session.add(link)
                added_links.append(link)
        session.flush()
        self.module_instance_ids = [row.id for row in added_instances]
        self.parameter_ids = [row.id for row in added_parameters]
        self.com_object_ids = [row.id for row in added_objects]
        self.link_ids = [row.id for row in added_links]

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        for model, ids in (
            (ComObject, self.com_object_ids),
            (Parameter, self.parameter_ids),
            (ModuleInstance, self.module_instance_ids),
        ):
            for row_id in ids:
                row = session.get(model, row_id)
                if row is not None:
                    session.delete(row)
        session.flush()
        device.hardware2program_ref_id = self.old_hardware2program_ref_id
        device.application_program_ref_id = self.old_app_id
        if self.old_fields:
            _restore_application_loaded(device, self.old_fields["loaded"])
            device.group_object_tree = self.old_fields["group_object_tree"]
            # The device's Tool Keys may have changed since (a download); they stay.
            restored = dict(self.old_fields["extra_subtrees"] or {})
            security = (device.extra_subtrees or {}).get("Security")
            restored.pop("Security", None)
            if security:
                restored["Security"] = security
            device.extra_subtrees = restored or None
        _restore_rows(session, self.removed)
        session.expire(device)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "new_hardware2program_ref_id": self.new_hardware2program_ref_id,
            "new_app_id": self.new_app_id,
            "com_objects": self.com_objects,
            "module_instances": self.module_instances,
            "parameters": self.parameters,
            "com_object_data": self.com_object_data,
            "com_object_ids": self.com_object_ids,
            "module_instance_ids": self.module_instance_ids,
            "parameter_ids": self.parameter_ids,
            "link_ids": self.link_ids,
            "old_hardware2program_ref_id": self.old_hardware2program_ref_id,
            "old_app_id": self.old_app_id,
            "old_fields": self.old_fields,
            "removed": self.removed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangeDeviceApplication:
        return cls(**data)


@_register
@dataclass
class SyncDeviceComObjects(Event):
    """Reconcile a device's com-object rows to a target set of ``[ref_id, channel_id]`` — the
    parameter-driven "should exist" set after a structure-driving parameter change. Adds rows for new
    refs (default/inherited flags), removes rows whose ref is no longer wanted (their links cascade),
    and leaves survivors untouched (flags + links preserved). Reversible: added rows are dropped and
    removed rows re-created with their original ids, flags and links (mirrors UpdateDeviceApplication).
    """

    event_type: ClassVar[str] = "SyncDeviceComObjects"

    device_id: int
    target: list[list[str | None]] = field(default_factory=list[list[str | None]])
    # The app program id, so existing rows can be resolved to the same qualified per-instance ref the
    # ``target`` speaks (module instances collapse in ``ComObject.ref_id``; see
    # :func:`qualified_com_object_ref`). Empty only for legacy/non-module callers, where ``ref_id`` is
    # already the qualified id.
    app_program_id: str = ""
    added_ids: list[int] = field(default_factory=list[int])
    removed: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def _snapshot_co(self, co: ComObject) -> dict[str, Any]:
        snap: dict[str, Any] = {
            "id": co.id,
            "ref_id": co.ref_id,
            "instance_ref_id": co.instance_ref_id,
            "channel_id": co.channel_id,
            "links": [
                [link.id, link.group_address_id, link.is_sending] for link in co.links
            ],
            "text_override": co.text_override,
            "function_text_override": co.function_text_override,
            "description_override": co.description_override,
            "priority": co.priority,
            "datapoint_type_override": co.datapoint_type_override,
            "acknowledges": co.acknowledges,
        }
        for flag in COM_OBJECT_FLAGS:
            snap[flag] = getattr(co, flag)
            snap[f"{flag}_override"] = getattr(co, f"{flag}_override")
        return snap

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        target_refs = {row[0] for row in self.target}
        # Match on the qualified per-instance ref, not the stored (module-instance-stripped) ``ref_id``
        # — otherwise every imported module object mismatches ``target`` and would be deleted and
        # recreated, dropping its links and flag overrides.
        existing_by_qual = {
            qualified_com_object_ref(
                co.ref_id, co.instance_ref_id, self.app_program_id
            ): co
            for co in device.com_objects
        }

        self.removed = []
        for qual, co in existing_by_qual.items():
            if qual not in target_refs:
                self.removed.append(self._snapshot_co(co))
                session.delete(co)
        session.flush()

        existing_quals = set(existing_by_qual)
        to_add = [row for row in self.target if row[0] not in existing_quals]
        new_cos: list[ComObject] = []
        for i, row in enumerate(to_add):
            ref_id, channel_id = row[0], (row[1] if len(row) > 1 else None)
            co = ComObject(
                device_id=self.device_id, ref_id=ref_id, channel_id=channel_id
            )
            if i < len(self.added_ids):
                co.id = self.added_ids[i]
            session.add(co)
            new_cos.append(co)
        session.flush()
        if not self.added_ids:
            self.added_ids = [co.id for co in new_cos]

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        for cid in self.added_ids:
            co = session.get(ComObject, cid)
            if co is not None:
                session.delete(co)  # links cascade
        session.flush()
        for snap in self.removed:
            co = ComObject(
                device_id=self.device_id,
                ref_id=snap["ref_id"],
                instance_ref_id=snap.get("instance_ref_id", ""),
                channel_id=snap["channel_id"],
                text_override=snap.get("text_override"),
                function_text_override=snap.get("function_text_override"),
                description_override=snap.get("description_override"),
                priority=snap.get("priority"),
                datapoint_type_override=snap.get("datapoint_type_override"),
                acknowledges=snap.get("acknowledges"),
            )
            co.id = snap["id"]
            for flag in COM_OBJECT_FLAGS:
                setattr(co, flag, snap[flag])
                setattr(co, f"{flag}_override", snap.get(f"{flag}_override"))
            session.add(co)
            session.flush()
            for lid, ga_id, is_sending in snap["links"]:
                link = ComObjectLink(
                    com_object_id=co.id,
                    group_address_id=ga_id,
                    is_sending=is_sending,
                )
                link.id = lid
                session.add(link)
        session.flush()

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "target": self.target,
            "app_program_id": self.app_program_id,
            "added_ids": self.added_ids,
            "removed": self.removed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncDeviceComObjects:
        return cls(**data)


@_register
@dataclass
class SetParameter(Event):
    event_type: ClassVar[str] = "SetParameter"

    device_id: int
    ref_id: str
    value: str
    existed: bool | None = None
    old_value: str | None = None
    parameter_id: int | None = None

    def _find(self, session: Session) -> Parameter | None:
        return (
            session.query(Parameter)
            .filter_by(device_id=self.device_id, ref_id=self.ref_id)
            .first()
        )

    def apply(self, session: Session) -> None:
        param = self._find(session)
        if param is not None:
            self.existed = True
            self.old_value = param.value
            param.value = self.value
        else:
            self.existed = False
            param = Parameter(
                device_id=self.device_id, ref_id=self.ref_id, value=self.value
            )
            if self.parameter_id is not None:
                param.id = self.parameter_id
            session.add(param)
            session.flush()
            self.parameter_id = param.id

    def revert(self, session: Session) -> None:
        if self.existed:
            param = self._find(session)
            if param is not None and self.old_value is not None:
                param.value = self.old_value
        else:
            param = session.get(Parameter, self.parameter_id)
            if param is not None:
                session.delete(param)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "ref_id": self.ref_id,
            "value": self.value,
            "existed": self.existed,
            "old_value": self.old_value,
            "parameter_id": self.parameter_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetParameter:
        return cls(**data)


@_register
@dataclass
class CompositeEvent(Event):
    """A group of sub-events applied and reverted as a single undo step.

    Used when one user action must persist several events atomically — e.g. changing a structure
    parameter (:class:`SetParameter`) plus the com-object re-instantiation it triggers
    (:class:`SyncDeviceComObjects`) — so one undo/redo reverts/re-applies the whole action instead of
    leaving an inconsistent half-state. Sub-events apply in order and revert in reverse; each captures
    its own reversal state on apply (so re-serialising after apply persists it for reopen)."""

    event_type: ClassVar[str] = "Composite"

    events: list[Event] = field(default_factory=list[Event])
    label: str | None = None

    def apply(self, session: Session) -> None:
        for event in self.events:
            event.apply(session)

    def revert(self, session: Session) -> None:
        for event in reversed(self.events):
            event.revert(session)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "events": [
                {"type": event.event_type, "data": event.to_dict()}
                for event in self.events
            ]
        }
        if self.label is not None:
            data["label"] = self.label
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositeEvent:
        return cls(
            events=[
                deserialize_event(sub["type"], sub["data"]) for sub in data["events"]
            ],
            label=data.get("label"),
        )


@_register
@dataclass
class CreateGroupAddress(Event):
    """Insert a group address, creating any missing containing ranges, as a single undoable step.

    ``levels`` is the ``[range_start, range_end, name]`` chain (top → leaf) for the project style —
    see :func:`xknxeditor.proj.core.addressing.ranges_for`. ``range_ids`` and ``created``, filled
    on apply, hold each level's id and whether this event made it, so revert drops only what it added
    and redo rebuilds with identical ids."""

    event_type: ClassVar[str] = "CreateGroupAddress"

    installation_id: int
    address: int
    name: str
    levels: list[list[Any]] = field(default_factory=list[list[Any]])
    range_ids: list[int] = field(default_factory=list[int])
    created: list[bool] = field(default_factory=list[bool])
    ga_id: int | None = None

    def apply(self, session: Session) -> None:
        parent_id: int | None = None
        range_ids: list[int] = []
        created: list[bool] = []
        for i, (start, end, range_name) in enumerate(self.levels):
            existing = (
                session.query(GroupRange)
                .filter_by(
                    installation_id=self.installation_id,
                    parent_id=parent_id,
                    range_start=start,
                )
                .first()
            )
            if existing is None:
                group_range = GroupRange(
                    installation_id=self.installation_id,
                    parent_id=parent_id,
                    range_start=start,
                    range_end=end,
                    name=range_name,
                )
                if i < len(self.range_ids):
                    group_range.id = self.range_ids[i]
                session.add(group_range)
                session.flush()
                created.append(True)
            else:
                group_range = existing
                created.append(False)
            range_ids.append(group_range.id)
            parent_id = group_range.id
        self.range_ids = range_ids
        self.created = created

        ga = GroupAddress(
            group_range_id=parent_id, address=self.address, name=self.name
        )
        if self.ga_id is not None:
            ga.id = self.ga_id
        session.add(ga)
        session.flush()
        self.ga_id = ga.id

    def revert(self, session: Session) -> None:
        # Remove the topmost range this event created (cascade takes the deeper ones and the group
        # address); if none were created, delete just the group address.
        first_created = next((i for i, made in enumerate(self.created) if made), None)
        if first_created is not None:
            target = session.get(GroupRange, self.range_ids[first_created])
        else:
            target = session.get(GroupAddress, self.ga_id)
        if target is not None:
            session.delete(target)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "address": self.address,
            "name": self.name,
            "levels": self.levels,
            "range_ids": self.range_ids,
            "created": self.created,
            "ga_id": self.ga_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateGroupAddress:
        return cls(**data)


def _may_be_secured(
    session: Session, group_address: GroupAddress, device_id: int | None = None
) -> bool:
    """Whether the group address is or may become secured by a change of its links (with
    ``device_id`` joining): never with Security ``Off``; with ``On``, a key or a securely
    commissioned device on it (``Auto``, see :mod:`.ga_security`) it may."""
    if group_address.security_mode == "Off":
        return False
    if group_address.security_mode == "On" or group_address.security_key:
        return True
    devices = _devices_on(session, group_address.id)
    if device_id is not None:
        devices.add(device_id)
    return any(
        (device := session.get(Device, other)) is not None
        and secure_commissioned(device)
        for other in devices
    )


def _devices_on(session: Session, group_address_id: int) -> set[int]:
    """The devices with a group object linked to the group address."""
    return {
        device_id
        for (device_id,) in session.query(ComObject.device_id)
        .join(ComObjectLink, ComObjectLink.com_object_id == ComObject.id)
        .filter(ComObjectLink.group_address_id == group_address_id)
    }


def _link_affects(
    session: Session, com_object_id: int, group_address_id: int
) -> set[int]:
    """The devices whose group communication a change of this link changes: the group object's
    own device and, for a secured group address, every device linked to it (their Security
    Individual Address Tables list each other)."""
    affected: set[int] = set()
    com_object = session.get(ComObject, com_object_id)
    if com_object is not None:
        affected.add(com_object.device_id)
    group_address = session.get(GroupAddress, group_address_id)
    if group_address is not None and _may_be_secured(
        session, group_address, com_object.device_id if com_object else None
    ):
        affected |= _devices_on(session, group_address_id)
    return affected


def _clear_communication_loaded(
    session: Session, device_ids: set[int]
) -> dict[str, bool]:
    """Mark the group communication of ``device_ids`` as not loaded; the previous values (only of
    those that were loaded) for revert."""
    cleared: dict[str, bool] = {}
    for device_id in sorted(device_ids):
        device = session.get(Device, device_id)
        if device is not None and device.communication_part_loaded:
            cleared[str(device_id)] = True
            device.communication_part_loaded = False
    return cleared


def _restore_communication_loaded(session: Session, cleared: dict[str, bool]) -> None:
    for device_id, loaded in cleared.items():
        device = session.get(Device, int(device_id))
        if device is not None:
            device.communication_part_loaded = loaded


@_register
@dataclass
class LinkComObject(Event):
    event_type: ClassVar[str] = "LinkComObject"

    com_object_id: int
    group_address_id: int
    is_sending: bool = False
    link_id: int | None = None
    # Devices whose group communication was loaded before (see _clear_communication_loaded).
    cleared: dict[str, bool] = field(default_factory=dict[str, bool])

    def apply(self, session: Session) -> None:
        self.cleared = _clear_communication_loaded(
            session, _link_affects(session, self.com_object_id, self.group_address_id)
        )
        link = ComObjectLink(
            com_object_id=self.com_object_id,
            group_address_id=self.group_address_id,
            is_sending=self.is_sending,
        )
        if self.link_id is not None:
            link.id = self.link_id
        session.add(link)
        session.flush()
        self.link_id = link.id

    def revert(self, session: Session) -> None:
        link = session.get(ComObjectLink, self.link_id)
        if link is not None:
            session.delete(link)
        _restore_communication_loaded(session, self.cleared)

    def to_dict(self) -> dict[str, Any]:
        return {
            "com_object_id": self.com_object_id,
            "group_address_id": self.group_address_id,
            "is_sending": self.is_sending,
            "link_id": self.link_id,
            "cleared": self.cleared,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LinkComObject:
        return cls(**data)


COM_OBJECT_FLAGS = frozenset(
    {
        "read_flag",
        "write_flag",
        "communication_flag",
        "transmit_flag",
        "update_flag",
        "read_on_init_flag",
    }
)

# Valid per-instance ComObjectInstanceRef @Priority values (ComObjectPriority_t). ``None`` clears
# the override so the application/product default is inherited.
COM_OBJECT_PRIORITIES = ("Low", "High", "Alert")


@_register
@dataclass
class SetComObjectFlag(Event):
    """Force a com-object flag: ``True``/``False`` overrides it, ``None`` falls back to the product
    default."""

    event_type: ClassVar[str] = "SetComObjectFlag"

    com_object_id: int
    flag: str
    value: bool | None
    captured: bool = False
    old: bool | None = None
    old_override: bool | None = None

    def apply(self, session: Session) -> None:
        com_object = session.get(ComObject, self.com_object_id)
        if com_object is not None:
            self.old = getattr(com_object, self.flag)
            self.old_override = getattr(com_object, f"{self.flag}_override")
            self.captured = True
            # Forcing a flag is both the effective value the device download programs AND an explicit
            # override the export must re-emit, so set the effective column and its ``_override`` twin.
            setattr(com_object, self.flag, self.value)
            setattr(com_object, f"{self.flag}_override", self.value)

    def revert(self, session: Session) -> None:
        com_object = session.get(ComObject, self.com_object_id)
        if com_object is not None and self.captured:
            setattr(com_object, self.flag, self.old)
            setattr(com_object, f"{self.flag}_override", self.old_override)

    def to_dict(self) -> dict[str, Any]:
        return {
            "com_object_id": self.com_object_id,
            "flag": self.flag,
            "value": self.value,
            "captured": self.captured,
            "old": self.old,
            "old_override": self.old_override,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetComObjectFlag:
        return cls(**data)


@_register
@dataclass
class SetComObjectDatapointType(Event):
    """Pin (or clear with ``None``) the per-instance DatapointType of a group object
    (``ComObjectInstanceRef/@DatapointType``). ``None`` inherits the application/product default."""

    event_type: ClassVar[str] = "SetComObjectDatapointType"

    com_object_id: int
    datapoint_type: str | None
    captured: bool = False
    old: str | None = None

    def apply(self, session: Session) -> None:
        com_object = session.get(ComObject, self.com_object_id)
        if com_object is not None:
            self.old = com_object.datapoint_type_override
            self.captured = True
            com_object.datapoint_type_override = self.datapoint_type

    def revert(self, session: Session) -> None:
        com_object = session.get(ComObject, self.com_object_id)
        if com_object is not None and self.captured:
            com_object.datapoint_type_override = self.old

    def to_dict(self) -> dict[str, Any]:
        return {
            "com_object_id": self.com_object_id,
            "datapoint_type": self.datapoint_type,
            "captured": self.captured,
            "old": self.old,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetComObjectDatapointType:
        return cls(**data)


@_register
@dataclass
class SetComObjectPriority(Event):
    """Set (or clear with ``None``) the per-instance Priority of a group object
    (``ComObjectInstanceRef/@Priority``). ``None`` inherits the application/product default."""

    event_type: ClassVar[str] = "SetComObjectPriority"

    com_object_id: int
    priority: str | None
    captured: bool = False
    old: str | None = None

    def apply(self, session: Session) -> None:
        com_object = session.get(ComObject, self.com_object_id)
        if com_object is not None:
            self.old = com_object.priority
            self.captured = True
            com_object.priority = self.priority

    def revert(self, session: Session) -> None:
        com_object = session.get(ComObject, self.com_object_id)
        if com_object is not None and self.captured:
            com_object.priority = self.old

    def to_dict(self) -> dict[str, Any]:
        return {
            "com_object_id": self.com_object_id,
            "priority": self.priority,
            "captured": self.captured,
            "old": self.old,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetComObjectPriority:
        return cls(**data)


@_register
@dataclass
class SetComObjectDescription(Event):
    """Set (or clear with ``None``) the per-instance Description of a group object
    (``ComObjectInstanceRef/@Description``). ``None`` inherits the application/product default."""

    event_type: ClassVar[str] = "SetComObjectDescription"

    com_object_id: int
    description: str | None
    captured: bool = False
    old: str | None = None

    def apply(self, session: Session) -> None:
        com_object = session.get(ComObject, self.com_object_id)
        if com_object is not None:
            self.old = com_object.description_override
            self.captured = True
            com_object.description_override = self.description

    def revert(self, session: Session) -> None:
        com_object = session.get(ComObject, self.com_object_id)
        if com_object is not None and self.captured:
            com_object.description_override = self.old

    def to_dict(self) -> dict[str, Any]:
        return {
            "com_object_id": self.com_object_id,
            "description": self.description,
            "captured": self.captured,
            "old": self.old,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetComObjectDescription:
        return cls(**data)


@_register
@dataclass
class SetGroupAddressDatapointType(Event):
    event_type: ClassVar[str] = "SetGroupAddressDatapointType"

    group_address_id: int
    datapoint_type: str | None
    captured: bool = False
    old: str | None = None

    def apply(self, session: Session) -> None:
        ga = session.get(GroupAddress, self.group_address_id)
        if ga is not None:
            self.old = ga.datapoint_type
            self.captured = True
            ga.datapoint_type = self.datapoint_type

    def revert(self, session: Session) -> None:
        ga = session.get(GroupAddress, self.group_address_id)
        if ga is not None and self.captured:
            ga.datapoint_type = self.old

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_address_id": self.group_address_id,
            "datapoint_type": self.datapoint_type,
            "captured": self.captured,
            "old": self.old,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetGroupAddressDatapointType:
        return cls(**data)


@_register
@dataclass
class SetGroupAddressSecurity(Event):
    """Set a group address's KNX Data Secure state: its ``@Security`` mode (``"Auto"``/``"On"``/
    ``"Off"``, ``None`` = default), whether it is secured (``data_secure``) and its key (an
    ``enc1:`` token, kept once generated; ``security_key``/``old_security_key`` are encrypted
    like the column, see :mod:`project_secrets`). Devices linked to it then need a group download, so
    their group-communication loaded flag is cleared (restored on revert)."""

    event_type: ClassVar[str] = "SetGroupAddressSecurity"

    group_address_id: int
    security_mode: str | None
    data_secure: bool
    security_key: str | None
    captured: bool = False
    old_mode: str | None = None
    old_data_secure: bool = False
    old_security_key: str | None = None
    cleared: dict[str, bool] = field(default_factory=dict[str, bool])

    def apply(self, session: Session) -> None:
        ga = session.get(GroupAddress, self.group_address_id)
        if ga is None:
            return
        self.old_mode = ga.security_mode
        self.old_data_secure = ga.data_secure
        self.old_security_key = ga.security_key
        self.captured = True
        self.cleared = _clear_communication_loaded(
            session, _devices_on(session, self.group_address_id)
        )
        ga.security_mode = self.security_mode
        ga.data_secure = self.data_secure
        ga.security_key = self.security_key

    def revert(self, session: Session) -> None:
        ga = session.get(GroupAddress, self.group_address_id)
        if ga is not None and self.captured:
            ga.security_mode = self.old_mode
            ga.data_secure = self.old_data_secure
            ga.security_key = self.old_security_key
        _restore_communication_loaded(session, self.cleared)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_address_id": self.group_address_id,
            "security_mode": self.security_mode,
            "data_secure": self.data_secure,
            "security_key": self.security_key,
            "captured": self.captured,
            "old_mode": self.old_mode,
            "old_data_secure": self.old_data_secure,
            "old_security_key": self.old_security_key,
            "cleared": self.cleared,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetGroupAddressSecurity:
        data = dict(data)
        if "old_key" in data:  # the field name of earlier builds
            data["old_security_key"] = data.pop("old_key")
        return cls(**data)


@_register
@dataclass
class SetDeviceSecureCommissioning(Event):
    """Activate or deactivate a device's secure commissioning: set its Tool Key (an ``enc1:``
    token) or, with ``None``, remove it. Only ``<Security @ToolKey>`` changes: the key the device
    holds (``@LoadedToolKey``) stays, as the device keeps using it until it is switched to plain
    communication or reset. The device and the devices sharing a group address with it that may be
    secured need a group download, so their loaded flags are cleared (restored on revert).
    ``tool_key``/``old_tool_key`` are encrypted like the column (see :mod:`project_secrets`)."""

    event_type: ClassVar[str] = "SetDeviceSecureCommissioning"

    device_id: int
    tool_key: str | None
    captured: bool = False
    old_tool_key: str | None = None
    cleared: dict[str, bool] = field(default_factory=dict[str, bool])

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        affected = {self.device_id}
        group_addresses = (
            session.query(GroupAddress)
            .join(ComObjectLink, ComObjectLink.group_address_id == GroupAddress.id)
            .join(ComObject, ComObject.id == ComObjectLink.com_object_id)
            .filter(ComObject.device_id == self.device_id)
            .distinct()
        )
        for ga in group_addresses:
            if _may_be_secured(session, ga, self.device_id) or self.tool_key:
                affected |= _devices_on(session, ga.id)
        self.cleared = _clear_communication_loaded(session, affected)
        self.old_tool_key = _set_tool_key(device, self.tool_key)
        self.captured = True

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None and self.captured:
            _set_tool_key(device, self.old_tool_key)
        _restore_communication_loaded(session, self.cleared)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "tool_key": self.tool_key,
            "captured": self.captured,
            "old_tool_key": self.old_tool_key,
            "cleared": self.cleared,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetDeviceSecureCommissioning:
        return cls(**data)


def _set_tool_key(device: Device, tool_key: str | None) -> str | None:
    """Set (or with ``None`` remove) the device's ``<Security @ToolKey>``; the previous value."""
    subtrees = dict(device.extra_subtrees or {})
    raw = subtrees.get("Security")
    try:
        security = ET.fromstring(raw) if raw else ET.Element("Security")
    except ET.ParseError:
        security = ET.Element("Security")
    previous = security.get("ToolKey")
    if tool_key is None:
        security.attrib.pop("ToolKey", None)
    else:
        security.set("ToolKey", tool_key)
    if security.attrib or len(security):
        subtrees["Security"] = ET.tostring(security, encoding="unicode")
    else:
        subtrees.pop("Security", None)
    device.extra_subtrees = subtrees or None
    return previous


def additional_address_entries(device: Device) -> list[dict[str, Any]]:
    """The device's additional individual addresses as event entries, in order."""
    return [
        {
            "address": row.address,
            "name": row.name,
            "description": row.description,
            "comment": row.comment,
        }
        for row in device.additional_addresses
    ]


def _set_additional_entries(device: Device, entries: list[dict[str, Any]]) -> None:
    """Make the device's additional individual addresses ``entries``, updating the existing rows in
    place (their order is the entry number) and adding or removing rows at the end."""
    rows = device.additional_addresses
    for index, entry in enumerate(entries):
        if index < len(rows):
            row = rows[index]
        else:
            row = DeviceAdditionalAddress()
            rows.append(row)
        address = entry.get("address")
        row.address = int(address) if address is not None else None
        row.name = str(entry.get("name") or "")
        row.description = str(entry.get("description") or "")
        row.comment = str(entry.get("comment") or "")
    for row in list(rows[len(entries) :]):
        rows.remove(row)


@_register
@dataclass
class SetDeviceAdditionalAddresses(Event):
    """Set a device's complete list of additional individual addresses (the tunnelling addresses
    of an IP interface, the addresses a coupler claims). Each entry holds ``address`` (the device
    number 1-255 in the device's line, or ``None``), ``name``, ``description`` and ``comment``;
    revert restores the previous list. ``label`` names the change in the history."""

    event_type: ClassVar[str] = "SetDeviceAdditionalAddresses"

    device_id: int
    entries: list[dict[str, Any]]
    label: str | None = None
    old_entries: list[dict[str, Any]] | None = None

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        self.old_entries = additional_address_entries(device)
        _set_additional_entries(device, self.entries)
        session.flush()

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None or self.old_entries is None:
            return
        _set_additional_entries(device, self.old_entries)
        session.flush()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "device_id": self.device_id,
            "entries": self.entries,
            "old_entries": self.old_entries,
        }
        if self.label is not None:
            data["label"] = self.label
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetDeviceAdditionalAddresses:
        return cls(**data)


@_register
@dataclass
class RenameGroupAddress(Event):
    event_type: ClassVar[str] = "RenameGroupAddress"

    group_address_id: int
    name: str
    old_name: str | None = None

    def apply(self, session: Session) -> None:
        ga = session.get(GroupAddress, self.group_address_id)
        if ga is not None:
            self.old_name = ga.name
            ga.name = self.name

    def revert(self, session: Session) -> None:
        ga = session.get(GroupAddress, self.group_address_id)
        if ga is not None and self.old_name is not None:
            ga.name = self.old_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_address_id": self.group_address_id,
            "name": self.name,
            "old_name": self.old_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenameGroupAddress:
        return cls(**data)


@_register
@dataclass
class SetGroupAddressAddress(Event):
    """Renumber a group address: change its numeric address and re-home it under the range chain
    for the new address, creating any missing main/middle ranges. The ``GroupAddress`` row (and
    its id) is kept, so com-object links -- which reference the group address by id -- stay valid.
    This mirrors ETS's "change the address of a group address": the links move with it, nothing is
    re-wired. One undoable step; ``levels`` is the ``[range_start, range_end, name]`` chain for the
    new address (see :func:`ranges_for`), like :class:`CreateGroupAddress`.
    """

    event_type: ClassVar[str] = "SetGroupAddressAddress"

    group_address_id: int
    installation_id: int
    address: int
    levels: list[list[Any]] = field(default_factory=list[list[Any]])
    range_ids: list[int] = field(default_factory=list[int])
    created: list[bool] = field(default_factory=list[bool])
    old_address: int | None = None
    old_group_range_id: int | None = None
    # Devices whose group communication was loaded before (the address is in their tables).
    cleared: dict[str, bool] = field(default_factory=dict[str, bool])

    def apply(self, session: Session) -> None:
        ga = session.get(GroupAddress, self.group_address_id)
        if ga is None:
            return
        self.old_address = ga.address
        self.old_group_range_id = ga.group_range_id
        self.cleared = _clear_communication_loaded(
            session, _devices_on(session, self.group_address_id)
        )

        # Ensure the target range chain exists (same walk as CreateGroupAddress), tracking which
        # ranges this event created so revert drops exactly those and redo rebuilds identical ids.
        parent_range: GroupRange | None = None
        range_ids: list[int] = []
        created: list[bool] = []
        for i, (start, end, range_name) in enumerate(self.levels):
            parent_id = parent_range.id if parent_range is not None else None
            existing = (
                session.query(GroupRange)
                .filter_by(
                    installation_id=self.installation_id,
                    parent_id=parent_id,
                    range_start=start,
                )
                .first()
            )
            if existing is None:
                group_range = GroupRange(
                    installation_id=self.installation_id,
                    parent_id=parent_id,
                    range_start=start,
                    range_end=end,
                    name=range_name,
                )
                if i < len(self.range_ids):
                    group_range.id = self.range_ids[i]
                session.add(group_range)
                session.flush()
                created.append(True)
            else:
                group_range = existing
                created.append(False)
            range_ids.append(group_range.id)
            parent_range = group_range
        self.range_ids = range_ids
        self.created = created

        # Move the existing group address: new address + new containing range. Its links are not
        # touched (they hang off group_address_id, which does not change). Assign the relationship
        # (not the raw FK) so the ORM collections stay in sync -- otherwise reverting could leave
        # the GA in a to-be-deleted range's loaded collection and cascade-delete it. Empty levels
        # (Free style) keep the GA under its current range: Free folders are not address-derived.
        ga.address = self.address
        if parent_range is not None:
            ga.group_range = parent_range

    def revert(self, session: Session) -> None:
        ga = session.get(GroupAddress, self.group_address_id)
        if ga is not None and self.old_address is not None:
            ga.address = self.old_address
            if self.old_group_range_id is not None:
                # Set the relationship (not the FK) so ``ga`` leaves the created range's loaded
                # collection; deleting that empty range below then cannot cascade the GA away.
                old_range = session.get(GroupRange, self.old_group_range_id)
                if old_range is not None:
                    ga.group_range = old_range
        session.flush()
        # Drop the topmost range this event created (cascade removes the deeper ones); the moved GA
        # is already back under its old range, so a created range is empty and safe to delete.
        first_created = next((i for i, made in enumerate(self.created) if made), None)
        if first_created is not None:
            target = session.get(GroupRange, self.range_ids[first_created])
            if target is not None:
                session.delete(target)
        _restore_communication_loaded(session, self.cleared)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_address_id": self.group_address_id,
            "installation_id": self.installation_id,
            "address": self.address,
            "levels": self.levels,
            "range_ids": self.range_ids,
            "created": self.created,
            "old_address": self.old_address,
            "old_group_range_id": self.old_group_range_id,
            "cleared": self.cleared,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetGroupAddressAddress:
        return cls(**data)


@_register
@dataclass
class SetComObjectSending(Event):
    """Mark one link as sending and clear the bit on the com-object's other links (at most one
    sends). ``previous`` stores each sibling's earlier bit for revert."""

    event_type: ClassVar[str] = "SetComObjectSending"

    link_id: int
    captured: bool = False
    previous: dict[str, bool] = field(default_factory=dict[str, bool])
    cleared: dict[str, bool] = field(default_factory=dict[str, bool])

    def apply(self, session: Session) -> None:
        link = session.get(ComObjectLink, self.link_id)
        if link is None:
            return
        com_object = session.get(ComObject, link.com_object_id)
        self.cleared = _clear_communication_loaded(
            session, {com_object.device_id} if com_object is not None else set()
        )
        siblings = (
            session.query(ComObjectLink)
            .filter_by(com_object_id=link.com_object_id)
            .all()
        )
        self.previous = {str(s.id): s.is_sending for s in siblings}
        self.captured = True
        for sibling in siblings:
            sibling.is_sending = sibling.id == self.link_id

    def revert(self, session: Session) -> None:
        if not self.captured:
            return
        for sibling_id, was_sending in self.previous.items():
            sibling = session.get(ComObjectLink, int(sibling_id))
            if sibling is not None:
                sibling.is_sending = was_sending
        _restore_communication_loaded(session, self.cleared)

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "captured": self.captured,
            "previous": self.previous,
            "cleared": self.cleared,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetComObjectSending:
        return cls(**data)


# --- reversible deletes (snapshot subtree on delete, re-insert on revert) ----------


@dataclass
class _SubtreeDelete(Event):
    """Delete a row plus its cascade-owned descendants, snapshotting them first so revert can
    re-insert with the original ids and keep external foreign keys valid."""

    _model: ClassVar[type[Base]]

    target_id: int
    rows: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def apply(self, session: Session) -> None:
        obj = session.get(self._model, self.target_id)
        if obj is not None:
            self.rows = _snapshot_subtree(obj)
            session.delete(obj)

    def revert(self, session: Session) -> None:
        _restore_rows(session, self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {"target_id": self.target_id, "rows": self.rows}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _SubtreeDelete:
        return cls(**data)


@_register
@dataclass
class RemoveDevice(_SubtreeDelete):
    event_type: ClassVar[str] = "RemoveDevice"
    _model: ClassVar[type[Base]] = Device


@_register
@dataclass
class RemoveArea(_SubtreeDelete):
    event_type: ClassVar[str] = "RemoveArea"
    _model: ClassVar[type[Base]] = Area


@_register
@dataclass
class RemoveLine(_SubtreeDelete):
    event_type: ClassVar[str] = "RemoveLine"
    _model: ClassVar[type[Base]] = Line


@_register
@dataclass
class RemoveSegment(_SubtreeDelete):
    event_type: ClassVar[str] = "RemoveSegment"
    _model: ClassVar[type[Base]] = Segment


@dataclass
class _LoadedSubtreeDelete(_SubtreeDelete):
    """A delete that also changes the group communication of devices (see ``_affected``)."""

    cleared: dict[str, bool] = field(default_factory=dict[str, bool])

    def _affected(self, session: Session) -> set[int]:
        raise NotImplementedError

    def apply(self, session: Session) -> None:
        self.cleared = _clear_communication_loaded(session, self._affected(session))
        super().apply(session)

    def revert(self, session: Session) -> None:
        super().revert(session)
        _restore_communication_loaded(session, self.cleared)

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "cleared": self.cleared}


@_register
@dataclass
class RemoveGroupAddress(_LoadedSubtreeDelete):
    event_type: ClassVar[str] = "RemoveGroupAddress"
    _model: ClassVar[type[Base]] = GroupAddress

    def _affected(self, session: Session) -> set[int]:
        return _devices_on(session, self.target_id)


@_register
@dataclass
class UnlinkComObject(_LoadedSubtreeDelete):
    event_type: ClassVar[str] = "UnlinkComObject"
    _model: ClassVar[type[Base]] = ComObjectLink

    def _affected(self, session: Session) -> set[int]:
        link = session.get(ComObjectLink, self.target_id)
        if link is None:
            return set()
        return _link_affects(session, link.com_object_id, link.group_address_id)


# --- field changes (stash the prior value, put it back on revert) --------------


@_register
@dataclass
class RenameArea(Event):
    event_type: ClassVar[str] = "RenameArea"

    area_id: int
    name: str
    old_name: str | None = None

    def apply(self, session: Session) -> None:
        area = session.get(Area, self.area_id)
        if area is not None:
            self.old_name = area.name
            area.name = self.name

    def revert(self, session: Session) -> None:
        area = session.get(Area, self.area_id)
        if area is not None and self.old_name is not None:
            area.name = self.old_name

    def to_dict(self) -> dict[str, Any]:
        return {"area_id": self.area_id, "name": self.name, "old_name": self.old_name}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenameArea:
        return cls(**data)


@_register
@dataclass
class SetProjectName(Event):
    event_type: ClassVar[str] = "SetProjectName"

    project_id: str
    name: str
    old_name: str | None = None

    def apply(self, session: Session) -> None:
        project = session.get(Project, self.project_id)
        if project is not None:
            self.old_name = project.name
            project.name = self.name

    def revert(self, session: Session) -> None:
        project = session.get(Project, self.project_id)
        if project is not None and self.old_name is not None:
            project.name = self.old_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "old_name": self.old_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetProjectName:
        return cls(**data)


@_register
@dataclass
class SetDeviceCertificates(Event):
    """Replace the project's ``<DeviceCertificates>`` (the FDSKs, kept encrypted as they are)."""

    event_type: ClassVar[str] = "SetDeviceCertificates"

    project_id: str
    certificates: str | None
    old_certificates: str | None = None

    def apply(self, session: Session) -> None:
        project = session.get(Project, self.project_id)
        if project is not None:
            self.old_certificates = (project.info_subtrees or {}).get(
                "DeviceCertificates"
            )
            _set_info_subtree(project, "DeviceCertificates", self.certificates)

    def revert(self, session: Session) -> None:
        project = session.get(Project, self.project_id)
        if project is not None:
            _set_info_subtree(project, "DeviceCertificates", self.old_certificates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "certificates": self.certificates,
            "old_certificates": self.old_certificates,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetDeviceCertificates:
        return cls(**data)


def _set_info_subtree(project: Project, name: str, xml: str | None) -> None:
    subtrees = {k: v for k, v in (project.info_subtrees or {}).items() if k != name}
    if xml:
        subtrees[name] = xml
    project.info_subtrees = subtrees or None


@_register
@dataclass
class RenameLine(Event):
    event_type: ClassVar[str] = "RenameLine"

    line_id: int
    name: str
    old_name: str | None = None

    def apply(self, session: Session) -> None:
        line = session.get(Line, self.line_id)
        if line is not None:
            self.old_name = line.name
            line.name = self.name

    def revert(self, session: Session) -> None:
        line = session.get(Line, self.line_id)
        if line is not None and self.old_name is not None:
            line.name = self.old_name

    def to_dict(self) -> dict[str, Any]:
        return {"line_id": self.line_id, "name": self.name, "old_name": self.old_name}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenameLine:
        return cls(**data)


@_register
@dataclass
class SetDeviceName(Event):
    event_type: ClassVar[str] = "SetDeviceName"

    device_id: int
    name: str
    old_name: str | None = None

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None:
            self.old_name = device.name
            device.name = self.name

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None and self.old_name is not None:
            device.name = self.old_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "old_name": self.old_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetDeviceName:
        return cls(**data)


@_register
@dataclass
class SetDeviceDescription(Event):
    event_type: ClassVar[str] = "SetDeviceDescription"

    device_id: int
    description: str
    old_description: str | None = None

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None:
            self.old_description = device.description
            device.description = self.description

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None and self.old_description is not None:
            device.description = self.old_description

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "description": self.description,
            "old_description": self.old_description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetDeviceDescription:
        return cls(**data)


@_register
@dataclass
class MoveDevice(Event):
    """Relocate a device to a different segment and/or set a new individual-address octet."""

    event_type: ClassVar[str] = "MoveDevice"

    device_id: int
    segment_id: int
    address: int | None
    old_segment_id: int | None = None
    old_address: int | None = None

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None:
            self.old_segment_id = device.segment_id
            self.old_address = device.address
            device.segment_id = self.segment_id
            device.address = self.address

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None and self.old_segment_id is not None:
            device.segment_id = self.old_segment_id
            device.address = self.old_address

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "segment_id": self.segment_id,
            "address": self.address,
            "old_segment_id": self.old_segment_id,
            "old_address": self.old_address,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MoveDevice:
        return cls(**data)


# --- subtree snapshot/restore helpers -------------------------------------------------

# Every mapped model, keyed by class name, for snapshot/restore. Derived from the SQLAlchemy
# registry (not a hand-kept list) so a newly added cascade-owned model — e.g. DeviceAdditionalAddress
# or Trade — is covered automatically; a stale list here silently breaks undo with a KeyError in
# _restore_rows when such a row appears in a snapshotted subtree.
_MODELS: dict[str, type[Base]] = {
    mapper.class_.__name__: mapper.class_ for mapper in Base.registry.mappers
}


def _row_to_dict(obj: Base) -> dict[str, Any]:
    mapper = sa_inspect(type(obj))
    data: dict[str, Any] = {"__model__": type(obj).__name__}
    for column in mapper.columns:
        value = getattr(obj, column.name)
        # The whole snapshot is round-tripped through the ``events`` JSON column, which cannot hold
        # raw bytes (e.g. ``DeviceBinaryData.data`` for an MDT DALI DCA backup). Base64-encode them
        # behind a marker so ``_restore_rows`` can turn them back into bytes verbatim.
        if isinstance(value, bytes):
            value = {"__bytes_b64__": base64.b64encode(value).decode("ascii")}
        data[column.name] = value
    return data


def _cascades_on_delete(rel: Any) -> bool:
    """True if the child rows of a ONETOMANY relationship carry a DB-level ``ON DELETE CASCADE`` FK.

    Such rows vanish when the parent is deleted even without an ORM ``delete-orphan`` cascade (e.g.
    ``TradeDevice.device_id``, where ``Trade`` owns the association but a device delete still drops
    it). They must be snapshotted so undo can restore them."""
    return any(
        (fk.ondelete or "").upper() == "CASCADE"
        for col in rel.remote_side
        for fk in col.foreign_keys
    )


def _snapshot_subtree(obj: Base) -> list[dict[str, Any]]:
    """Snapshot ``obj`` and all cascade-owned descendants, parents first so restore order is safe."""
    rows = [_row_to_dict(obj)]
    mapper = sa_inspect(type(obj))
    for rel in mapper.relationships:
        if rel.direction.name == "ONETOMANY" and (
            rel.cascade.delete_orphan or _cascades_on_delete(rel)
        ):
            for child in getattr(obj, rel.key):
                rows.extend(_snapshot_subtree(child))
    return rows


def _restore_rows(session: Session, rows: list[dict[str, Any]]) -> None:
    for data in rows:
        payload = dict(data)
        model = _MODELS[payload.pop("__model__")]
        for key, value in payload.items():
            # Reverse the base64 marker written by ``_row_to_dict`` for bytes columns.
            if isinstance(value, dict) and "__bytes_b64__" in value:
                encoded = cast(str, value["__bytes_b64__"])
                payload[key] = base64.b64decode(encoded)
        session.add(model(**payload))
    session.flush()


# --- building functions (Function + its group-address roles) ------------------


@_register
@dataclass
class CreateFunction(Event):
    """Add a building function (a named grouping of group addresses by role) to a space."""

    event_type: ClassVar[str] = "CreateFunction"

    space_id: int
    function_type: str
    name: str
    order: int = 0
    function_id: int | None = None

    def apply(self, session: Session) -> None:
        fn = Function(
            space_id=self.space_id,
            function_type=self.function_type,
            name=self.name,
            order=self.order,
        )
        if self.function_id is not None:
            fn.id = self.function_id
        session.add(fn)
        session.flush()
        self.function_id = fn.id

    def revert(self, session: Session) -> None:
        fn = session.get(Function, self.function_id)
        if fn is not None:
            session.delete(fn)

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "function_type": self.function_type,
            "name": self.name,
            "order": self.order,
            "function_id": self.function_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateFunction:
        return cls(**data)


@_register
@dataclass
class RemoveFunction(_SubtreeDelete):
    event_type: ClassVar[str] = "RemoveFunction"
    _model: ClassVar[type[Base]] = Function


@_register
@dataclass
class RenameFunction(Event):
    event_type: ClassVar[str] = "RenameFunction"

    function_id: int
    name: str
    old_name: str | None = None

    def apply(self, session: Session) -> None:
        fn = session.get(Function, self.function_id)
        if fn is not None:
            self.old_name = fn.name
            fn.name = self.name

    def revert(self, session: Session) -> None:
        fn = session.get(Function, self.function_id)
        if fn is not None and self.old_name is not None:
            fn.name = self.old_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_id": self.function_id,
            "name": self.name,
            "old_name": self.old_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenameFunction:
        return cls(**data)


@_register
@dataclass
class SetFunctionType(Event):
    event_type: ClassVar[str] = "SetFunctionType"

    function_id: int
    function_type: str
    old_type: str | None = None

    def apply(self, session: Session) -> None:
        fn = session.get(Function, self.function_id)
        if fn is not None:
            self.old_type = fn.function_type
            fn.function_type = self.function_type

    def revert(self, session: Session) -> None:
        fn = session.get(Function, self.function_id)
        if fn is not None and self.old_type is not None:
            fn.function_type = self.old_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_id": self.function_id,
            "function_type": self.function_type,
            "old_type": self.old_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetFunctionType:
        return cls(**data)


@_register
@dataclass
class AddFunctionGroupAddress(Event):
    """Assign a group address to a function under a role (empty role = unassigned)."""

    event_type: ClassVar[str] = "AddFunctionGroupAddress"

    function_id: int
    group_address_id: int
    role: str = ""
    link_id: int | None = None

    def apply(self, session: Session) -> None:
        link = FunctionGroupAddress(
            function_id=self.function_id,
            group_address_id=self.group_address_id,
            role=self.role,
        )
        if self.link_id is not None:
            link.id = self.link_id
        session.add(link)
        session.flush()
        self.link_id = link.id

    def revert(self, session: Session) -> None:
        link = session.get(FunctionGroupAddress, self.link_id)
        if link is not None:
            session.delete(link)

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_id": self.function_id,
            "group_address_id": self.group_address_id,
            "role": self.role,
            "link_id": self.link_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AddFunctionGroupAddress:
        return cls(**data)


@_register
@dataclass
class RemoveFunctionGroupAddress(_SubtreeDelete):
    event_type: ClassVar[str] = "RemoveFunctionGroupAddress"
    _model: ClassVar[type[Base]] = FunctionGroupAddress


@_register
@dataclass
class CreateFunctionWithGroupAddresses(Event):
    """Create a function and assign group addresses to it as one undoable step.

    The group-address links depend on the function id, which only exists after the function is
    created, so this cannot be a :class:`CompositeEvent` of the two separate events. Reverting
    deletes the function; its ``group_addresses`` cascade removes the links."""

    event_type: ClassVar[str] = "CreateFunctionWithGroupAddresses"

    space_id: int
    function_type: str
    name: str
    group_address_ids: list[int] = field(default_factory=list[int])
    order: int = 0
    function_id: int | None = None
    link_ids: list[int] | None = None

    def apply(self, session: Session) -> None:
        fn = Function(
            space_id=self.space_id,
            function_type=self.function_type,
            name=self.name,
            order=self.order,
        )
        if self.function_id is not None:
            fn.id = self.function_id
        session.add(fn)
        session.flush()
        self.function_id = fn.id
        new_link_ids: list[int] = []
        for i, ga_id in enumerate(self.group_address_ids):
            link = FunctionGroupAddress(function_id=fn.id, group_address_id=ga_id)
            if self.link_ids is not None and i < len(self.link_ids):
                link.id = self.link_ids[i]
            session.add(link)
            session.flush()
            new_link_ids.append(link.id)
        self.link_ids = new_link_ids

    def revert(self, session: Session) -> None:
        fn = session.get(Function, self.function_id)
        if fn is not None:
            session.delete(fn)

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "function_type": self.function_type,
            "name": self.name,
            "group_address_ids": self.group_address_ids,
            "order": self.order,
            "function_id": self.function_id,
            "link_ids": self.link_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateFunctionWithGroupAddresses:
        return cls(**data)


# --- building spaces (location tree: building / floor / room / …) -------------


@_register
@dataclass
class CreateSpace(Event):
    """Add a node to the building/location tree (a building, floor, room, …). ``parent_id`` is
    ``None`` for a top-level space."""

    event_type: ClassVar[str] = "CreateSpace"

    installation_id: int
    space_type: str
    name: str
    parent_id: int | None = None
    number: str = ""
    order: int = 0
    space_id: int | None = None

    def apply(self, session: Session) -> None:
        space = Space(
            installation_id=self.installation_id,
            parent_id=self.parent_id,
            space_type=self.space_type,
            name=self.name,
            number=self.number,
            order=self.order,
        )
        if self.space_id is not None:
            space.id = self.space_id
        session.add(space)
        session.flush()
        self.space_id = space.id

    def revert(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None:
            session.delete(space)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "space_type": self.space_type,
            "name": self.name,
            "parent_id": self.parent_id,
            "number": self.number,
            "order": self.order,
            "space_id": self.space_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateSpace:
        return cls(**data)


@_register
@dataclass
class RenameSpace(Event):
    event_type: ClassVar[str] = "RenameSpace"

    space_id: int
    name: str
    old_name: str | None = None

    def apply(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None:
            self.old_name = space.name
            space.name = self.name

    def revert(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None and self.old_name is not None:
            space.name = self.old_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "name": self.name,
            "old_name": self.old_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenameSpace:
        return cls(**data)


@_register
@dataclass
class SetSpaceNumber(Event):
    """Set a space's number, a free text (e.g. a room number)."""

    event_type: ClassVar[str] = "SetSpaceNumber"

    space_id: int
    number: str
    old_number: str | None = None

    def apply(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None:
            self.old_number = space.number
            space.number = self.number

    def revert(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None and self.old_number is not None:
            space.number = self.old_number

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "number": self.number,
            "old_number": self.old_number,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetSpaceNumber:
        return cls(**data)


@_register
@dataclass
class SetSpaceDescription(Event):
    """Set a space's description."""

    event_type: ClassVar[str] = "SetSpaceDescription"

    space_id: int
    description: str
    old_description: str | None = None

    def apply(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None:
            self.old_description = space.description
            space.description = self.description

    def revert(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None and self.old_description is not None:
            space.description = self.old_description

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "description": self.description,
            "old_description": self.old_description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetSpaceDescription:
        return cls(**data)


@_register
@dataclass
class SetSpaceType(Event):
    event_type: ClassVar[str] = "SetSpaceType"

    space_id: int
    space_type: str
    old_type: str | None = None

    def apply(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None:
            self.old_type = space.space_type
            space.space_type = self.space_type

    def revert(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None and self.old_type is not None:
            space.space_type = self.old_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "space_type": self.space_type,
            "old_type": self.old_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetSpaceType:
        return cls(**data)


@_register
@dataclass
class MoveSpace(Event):
    """Re-parent a space (``new_parent_id`` ``None`` = move to top level). The caller guards against
    cycles; this only records the move so it can be reverted."""

    event_type: ClassVar[str] = "MoveSpace"

    space_id: int
    new_parent_id: int | None
    old_parent_id: int | None = None
    _had_old: bool = False

    def apply(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None:
            self.old_parent_id = space.parent_id
            self._had_old = True
            space.parent_id = self.new_parent_id

    def revert(self, session: Session) -> None:
        space = session.get(Space, self.space_id)
        if space is not None and self._had_old:
            space.parent_id = self.old_parent_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "new_parent_id": self.new_parent_id,
            "old_parent_id": self.old_parent_id,
            "_had_old": self._had_old,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MoveSpace:
        return cls(**data)


@_register
@dataclass
class SetDeviceSpace(Event):
    """Assign a device to a space (room), or unassign it (``space_id`` ``None``)."""

    event_type: ClassVar[str] = "SetDeviceSpace"

    device_id: int
    space_id: int | None
    old_space_id: int | None = None
    _had_old: bool = False

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None:
            self.old_space_id = device.space_id
            self._had_old = True
            device.space_id = self.space_id

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is not None and self._had_old:
            device.space_id = self.old_space_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "space_id": self.space_id,
            "old_space_id": self.old_space_id,
            "_had_old": self._had_old,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetDeviceSpace:
        return cls(**data)


_COMMISSIONING_FIELDS = (
    "serial_number",
    "last_download",
    "individual_address_loaded",
    "application_program_loaded",
    "communication_part_loaded",
    "medium_config_loaded",
    "parameters_loaded",
)


@_register
@dataclass
class SetDeviceCommissioning(Event):
    """Update a device's commissioning state ("loaded" ticks + serial / last download, and
    the MAC address read from an IP device, kept in its IP configuration).

    Each field is optional; ``None`` leaves it unchanged (so e.g. programming can set only the
    flags for its scope). The previous values of the changed fields are captured for undo.
    """

    event_type: ClassVar[str] = "SetDeviceCommissioning"

    device_id: int
    serial_number: str | None = None
    last_download: str | None = None
    individual_address_loaded: bool | None = None
    application_program_loaded: bool | None = None
    communication_part_loaded: bool | None = None
    medium_config_loaded: bool | None = None
    parameters_loaded: bool | None = None
    mac_address: str | None = None
    _old: dict[str, Any] = field(default_factory=dict[str, Any])

    def apply(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        self._old = {}
        for name in _COMMISSIONING_FIELDS:
            new = getattr(self, name)
            if new is not None:
                self._old[name] = getattr(device, name)
                setattr(device, name, new)
        if self.mac_address is not None:
            self._old["ip_config"] = device.ip_config
            device.ip_config = {
                **(device.ip_config or {}),
                "MACAddress": self.mac_address,
            }

    def revert(self, session: Session) -> None:
        device = session.get(Device, self.device_id)
        if device is None:
            return
        for name, old in self._old.items():
            setattr(device, name, old)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            **{name: getattr(self, name) for name in _COMMISSIONING_FIELDS},
            "mac_address": self.mac_address,
            "_old": self._old,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SetDeviceCommissioning:
        return cls(**data)


def _devices_in_subtree(space: Space) -> list[Device]:
    """Every device assigned to ``space`` or any space below it (used to snapshot/restore
    device→space assignments when a space subtree is deleted)."""
    devices = list(space.devices)
    for child in space.children:
        devices.extend(_devices_in_subtree(child))
    return devices


@_register
@dataclass
class RemoveSpace(_SubtreeDelete):
    """Delete a space and its cascade-owned children/functions. Devices are NOT owned by a space
    (deleting a space only unassigns them), so their ``space_id`` is captured and restored on revert
    — otherwise undo would re-create the spaces but leave the devices orphaned."""

    event_type: ClassVar[str] = "RemoveSpace"
    _model: ClassVar[type[Base]] = Space

    device_space: dict[int, int] = field(default_factory=dict[int, int])

    def apply(self, session: Session) -> None:
        space = session.get(Space, self.target_id)
        if space is None:
            return
        # Capture each device's original space before the delete nulls the foreign key.
        self.device_space = {
            d.id: d.space_id
            for d in _devices_in_subtree(space)
            if d.space_id is not None
        }
        for device in _devices_in_subtree(space):
            device.space_id = None
        session.flush()
        self.rows = _snapshot_subtree(space)
        session.delete(space)

    def revert(self, session: Session) -> None:
        _restore_rows(session, self.rows)
        for device_id, space_id in self.device_space.items():
            device = session.get(Device, device_id)
            if device is not None:
                device.space_id = space_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "rows": self.rows,
            "device_space": self.device_space,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RemoveSpace:
        payload = dict(data)
        # JSON object keys are strings; device ids must round-trip back to int.
        raw: dict[Any, int] = payload.get("device_space") or {}
        payload["device_space"] = {int(k): v for k, v in raw.items()}
        return cls(**payload)


# --- group-address folders (GroupRange: main / middle groups) ------------------


@_register
@dataclass
class CreateGroupRange(Event):
    """Add an (initially empty) group-range folder — a main or middle group. ``range_start`` /
    ``range_end`` define its address span (derived by the service from the next free number for the
    project's style); ``parent_id`` is ``None`` for a main group, the main group's id for a middle
    group."""

    event_type: ClassVar[str] = "CreateGroupRange"

    installation_id: int
    range_start: int
    range_end: int
    name: str
    parent_id: int | None = None
    range_id: int | None = None

    def apply(self, session: Session) -> None:
        group_range = GroupRange(
            installation_id=self.installation_id,
            parent_id=self.parent_id,
            range_start=self.range_start,
            range_end=self.range_end,
            name=self.name,
        )
        if self.range_id is not None:
            group_range.id = self.range_id
        session.add(group_range)
        session.flush()
        self.range_id = group_range.id

    def revert(self, session: Session) -> None:
        group_range = session.get(GroupRange, self.range_id)
        if group_range is not None:
            session.delete(group_range)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "name": self.name,
            "parent_id": self.parent_id,
            "range_id": self.range_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateGroupRange:
        return cls(**data)


@_register
@dataclass
class RenameGroupRange(Event):
    event_type: ClassVar[str] = "RenameGroupRange"

    range_id: int
    name: str
    old_name: str | None = None

    def apply(self, session: Session) -> None:
        group_range = session.get(GroupRange, self.range_id)
        if group_range is not None:
            self.old_name = group_range.name
            group_range.name = self.name

    def revert(self, session: Session) -> None:
        group_range = session.get(GroupRange, self.range_id)
        if group_range is not None and self.old_name is not None:
            group_range.name = self.old_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "range_id": self.range_id,
            "name": self.name,
            "old_name": self.old_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RenameGroupRange:
        return cls(**data)


@_register
@dataclass
class RemoveGroupRange(_SubtreeDelete):
    """Delete a group-range folder and everything it owns (child ranges, the group addresses in it,
    and their com-object/function links — all cascade-owned, so the snapshot restores them on undo)."""

    event_type: ClassVar[str] = "RemoveGroupRange"
    _model: ClassVar[type[Base]] = GroupRange


# Group address fields an import sets.
_IMPORT_FIELDS = (
    "name",
    "datapoint_type",
    "description",
    "security_mode",
    "central",
    "unfiltered",
)


@_register
@dataclass
class ImportGroupAddresses(Event):
    """Import group address folders and group addresses as one undoable step.

    ``ranges`` are ``[range_start, range_end, name, parent_start]`` in parent-first order
    (``parent_start`` ``None`` for a top level folder); an existing folder with the same window
    under the same parent is renamed when the import has a name for it, else a new one is made.
    ``addresses`` are dicts with ``address`` and the fields of :data:`_IMPORT_FIELDS` that the
    import sets (missing or ``None`` = keep). A new address goes into the smallest folder that
    contains it; an existing one keeps its folder and gets the given fields.

    Filled on apply so revert/redo restore exactly: ``range_ids`` (id per entry of ``ranges``,
    ``created_ranges`` marks the new ones), ``renamed`` (``[range_id, old_name]``), ``ga_ids`` (id per
    address, ``created_gas`` marks the new ones) and ``old_values`` (previous values of updated
    addresses)."""

    event_type: ClassVar[str] = "ImportGroupAddresses"

    installation_id: int
    ranges: list[list[Any]] = field(default_factory=list[list[Any]])
    addresses: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    range_ids: list[int] = field(default_factory=list[int])
    created_ranges: list[bool] = field(default_factory=list[bool])
    renamed: list[list[Any]] = field(default_factory=list[list[Any]])
    ga_ids: list[int] = field(default_factory=list[int])
    created_gas: list[bool] = field(default_factory=list[bool])
    old_values: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    label: str | None = None

    def apply(self, session: Session) -> None:
        reuse_ranges = list(self.range_ids)
        reuse_gas = list(self.ga_ids)
        self.range_ids, self.created_ranges, self.renamed = [], [], []
        self.ga_ids, self.created_gas, self.old_values = [], [], []

        by_start: dict[tuple[int | None, int], GroupRange] = {
            (gr.parent_id, gr.range_start): gr
            for gr in session.query(GroupRange).filter_by(
                installation_id=self.installation_id
            )
        }
        ids_by_start: dict[int, int] = {}
        for index, (start, end, name, parent_start) in enumerate(self.ranges):
            parent_id = None if parent_start is None else ids_by_start.get(parent_start)
            existing = by_start.get((parent_id, start))
            if existing is not None and existing.range_end == end:
                if name and existing.name != name:
                    self.renamed.append([existing.id, existing.name])
                    existing.name = name
                group_range, created = existing, False
            else:
                group_range = GroupRange(
                    installation_id=self.installation_id,
                    parent_id=parent_id,
                    range_start=start,
                    range_end=end,
                    name=name,
                )
                if index < len(reuse_ranges):
                    group_range.id = reuse_ranges[index]
                session.add(group_range)
                session.flush()
                by_start[(parent_id, start)] = group_range
                created = True
            ids_by_start[start] = group_range.id
            self.range_ids.append(group_range.id)
            self.created_ranges.append(created)

        all_ranges = (
            session.query(GroupRange)
            .filter_by(installation_id=self.installation_id)
            .all()
        )
        existing_gas = {
            ga.address: ga
            for ga in session.query(GroupAddress)
            .join(GroupRange)
            .filter(GroupRange.installation_id == self.installation_id)
        }
        for index, item in enumerate(self.addresses):
            values = {k: item[k] for k in _IMPORT_FIELDS if item.get(k) is not None}
            ga = existing_gas.get(item["address"])
            if ga is not None:
                old: dict[str, Any] = {}
                for key, value in values.items():
                    if getattr(ga, key) != value:
                        old[key] = getattr(ga, key)
                        setattr(ga, key, value)
                self.ga_ids.append(ga.id)
                self.created_gas.append(False)
                self.old_values.append(old)
                continue
            container = _smallest_range(all_ranges, item["address"])
            if container is None:
                self.ga_ids.append(0)
                self.created_gas.append(False)
                self.old_values.append({})
                continue
            ga = GroupAddress(group_range_id=container.id, address=item["address"])
            for key, value in values.items():
                setattr(ga, key, value)
            if index < len(reuse_gas) and reuse_gas[index]:
                ga.id = reuse_gas[index]
            session.add(ga)
            session.flush()
            existing_gas[ga.address] = ga
            self.ga_ids.append(ga.id)
            self.created_gas.append(True)
            self.old_values.append({})

    def revert(self, session: Session) -> None:
        for ga_id, created, old in zip(
            reversed(self.ga_ids),
            reversed(self.created_gas),
            reversed(self.old_values),
            strict=True,
        ):
            ga = session.get(GroupAddress, ga_id) if ga_id else None
            if ga is None:
                continue
            if created:
                session.delete(ga)
            else:
                for key, value in old.items():
                    setattr(ga, key, value)
        session.flush()
        for range_id, created in zip(
            reversed(self.range_ids), reversed(self.created_ranges), strict=True
        ):
            if (
                created
                and (group_range := session.get(GroupRange, range_id)) is not None
            ):
                session.delete(group_range)
                session.flush()
        for range_id, old_name in reversed(self.renamed):
            if (group_range := session.get(GroupRange, range_id)) is not None:
                group_range.name = old_name

    @property
    def added(self) -> int:
        return sum(self.created_gas)

    @property
    def updated(self) -> int:
        return sum(1 for old in self.old_values if old)

    @property
    def skipped(self) -> int:
        return sum(1 for ga_id in self.ga_ids if not ga_id)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "installation_id": self.installation_id,
            "ranges": self.ranges,
            "addresses": self.addresses,
            "range_ids": self.range_ids,
            "created_ranges": self.created_ranges,
            "renamed": self.renamed,
            "ga_ids": self.ga_ids,
            "created_gas": self.created_gas,
            "old_values": self.old_values,
        }
        if self.label is not None:
            data["label"] = self.label
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImportGroupAddresses:
        return cls(**data)


def _smallest_range(ranges: list[GroupRange], address: int) -> GroupRange | None:
    """The narrowest folder whose window contains ``address``."""
    best: GroupRange | None = None
    for gr in ranges:
        if gr.range_start <= address <= gr.range_end and (
            best is None
            or gr.range_end - gr.range_start < best.range_end - best.range_start
        ):
            best = gr
    return best
