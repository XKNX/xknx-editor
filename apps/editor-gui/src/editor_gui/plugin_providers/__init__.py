"""Registered native configuration providers selected by shared plugin mappings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugin_params import PluginSectionView
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.download.prepared import PreparedDownload
    from xknxeditor.download.scope import DownloadScope
    from xknxeditor.prod import MasterData


@dataclass(frozen=True)
class NativeObject:
    key: str
    reference: str
    size: str
    dpt: str
    flags: Mapping[str, bool]
    priority: str = "Low"
    default_priority: str = "Low"


@dataclass(frozen=True)
class NativeConfiguration:
    data: bytes
    parameters: Mapping[str, str]
    objects: tuple[NativeObject, ...]
    source_objects: tuple[NativeObject, ...] | None = None
    binary_data: Mapping[str, bytes] = field(default_factory=dict[str, bytes])


@dataclass(frozen=True)
class PluginCapabilities:
    configured: bool
    supported: bool
    preflight: bool
    programming_error: str | None
    reason: str = ""
    importable_blob: str | None = None
    stored_configuration: bool = False
    readable: bool = False
    download_scopes: tuple[DownloadScope, ...] = ()


class PluginProvider(Protocol):
    id: str
    blob_name: str
    parameter_prefix: str

    def capabilities(self, device: Device) -> PluginCapabilities: ...
    def setup(self, device: Device) -> NativeConfiguration: ...
    def read(self, device: Device) -> NativeConfiguration: ...
    def decode(self, device: Device, data: bytes) -> NativeConfiguration: ...
    def edit(self, device: Device, ref_id: str, value: str) -> NativeConfiguration: ...
    def export(self, device: Device) -> bytes: ...
    def serialize(
        self,
        device: Device,
        config: NativeConfiguration,
        objects: tuple[NativeObject, ...],
    ) -> NativeConfiguration: ...
    def sections(self, device: Device) -> list[PluginSectionView]: ...
    def install_view(self, device: Device) -> None: ...
    def prepare(
        self, device: Device, communication: GroupCommunication | None
    ) -> PreparedDownload: ...


def compatible_dpt(expected: str, actual: str | None) -> bool:
    from xknxeditor.proj.core.dpt import normalize_datapoint_type

    if not actual or not expected:
        return True
    left = normalize_datapoint_type(expected)
    right = normalize_datapoint_type(actual)
    if left == right:
        return True
    if left is None or right is None:
        return False
    return left.split("-")[1] == right.split("-")[1] and (
        left.startswith("DPT-") or right.startswith("DPT-")
    )


def providers() -> tuple[PluginProvider, ...]:
    from .powertool import PROVIDER
    from .siemens_generic import PROVIDER as SIEMENS

    return (PROVIDER, SIEMENS)


def provider_named(name: str) -> PluginProvider:
    for provider in providers():
        if provider.id == name:
            return provider
    raise ValueError(f"Configuration provider {name!r} is unavailable")


def native_blob_names() -> frozenset[str]:
    return frozenset(p.blob_name for p in providers() if p.blob_name != "BLOB_STRING")


def provider_for(device: Device) -> PluginProvider | None:
    from editor_gui.plugin_params import mapping_for_device

    mapping = mapping_for_device(device)
    if mapping is not None and mapping.provider:
        return provider_named(mapping.provider)
    if any(r.name in native_blob_names() for r in getattr(device, "binary_data", ())):
        raise ValueError("Native configuration has no matching product provider")
    return None


class DeviceReader(Protocol):
    """A provider that reads its configuration back from a device (see the recovery)."""

    async def read_device(self, programmer: Any) -> object: ...
    def recover(self, device: Device, image: object) -> NativeConfiguration: ...
    def slot_references(self, device: Device) -> dict[int, str]: ...


def device_reader(application: object) -> DeviceReader | None:
    """The provider of a device of ``application`` that reads its configuration back from
    the device; None for an application without one."""
    from types import SimpleNamespace

    try:
        provider = provider_for(
            cast("Device", SimpleNamespace(app=application, binary_data=()))
        )
    except ValueError:
        return None
    if provider is None or not hasattr(provider, "read_device"):
        return None
    return cast(DeviceReader, provider)


def require_provider(device: Device) -> PluginProvider:
    provider = provider_for(device)
    if provider is None:
        raise ValueError("Device has no native configuration provider")
    return provider


def handles_parameter(device: Device, ref_id: str) -> bool:
    provider = provider_for(device)
    return provider is not None and ref_id.startswith(provider.parameter_prefix)


def programming_error(device: Device) -> str | None:
    try:
        provider = provider_for(device)
        return provider.capabilities(device).programming_error if provider else None
    except ValueError as exc:
        return str(exc)


def install_view(device: Device) -> None:
    if not any(
        r.name in native_blob_names() or r.name == "BLOB_STRING"
        for r in device.binary_data
    ):
        return
    try:
        provider = provider_for(device)
        if provider is not None:
            provider.install_view(device)
    except ValueError as exc:
        device.native_objects = []
        device.native_modules = []
        device.com_objects = []
        device.native_error = str(exc)


def apply_stored_object_dpts(device: Device) -> None:
    if any(r.name == "PT2EtsPlugin" for r in device.binary_data):
        from .powertool import apply_stored_object_dpts as apply_powertool_dpts

        apply_powertool_dpts(device)


def prepared_for(
    device: Device, communication: GroupCommunication | None = None
) -> PreparedDownload | None:
    provider = provider_for(device)
    if provider is None:
        return None
    capabilities = provider.capabilities(device)
    if not capabilities.preflight:
        raise ValueError(capabilities.reason or "Native preflight is unavailable")
    return provider.prepare(device, communication)


def preview(
    device: Device, prepared: PreparedDownload, master: MasterData | None = None
) -> dict[str, Any]:
    from dataclasses import fields, is_dataclass
    from enum import Enum

    from xknxeditor.download.download import prepared_controls

    assert device.app is not None
    secondary = device.second_application() if device.second_application else None
    controls = prepared_controls(device.app, prepared, master, secondary)

    def json_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (list, tuple)):
            return [json_value(v) for v in cast(list[Any], value)]
        if is_dataclass(value) and not isinstance(value, type):
            return {
                "type": type(value).__name__,
                **{f.name: json_value(getattr(value, f.name)) for f in fields(value)},
            }
        return value

    return {
        "memory": [
            {"address": s.address, "data": s.data.hex(), "mask": (s.mask or b"").hex()}
            for s in prepared.image.segments
        ],
        "resources": [
            {
                "object_index": s.object_index,
                "size": len(s.memory.data),
                "allocation_size": prepared.image.allocation_sizes.get(
                    s.object_index, len(s.memory.data)
                ),
                "data": s.memory.data.hex(),
                "mask": (s.memory.mask or b"").hex(),
            }
            for s in prepared.image.application_segments
        ]
        + [
            {
                "object_index": index,
                "address": address,
                "size": prepared.resource_sizes[index],
                "allocation_size": prepared.image.allocation_sizes.get(
                    index, prepared.resource_sizes[index]
                ),
                "data": segment.data.hex(),
                "mask": (segment.mask or b"").hex(),
            }
            for index, address in prepared.absolute_resources.items()
            for segment in prepared.image.segments
            if segment.address == address
        ],
        "properties": [
            {"object_index": p.object_index, "pid": p.property_id, "data": p.data.hex()}
            for p in prepared.image.properties
        ],
        "controls": [json_value(c) for c in controls],
        "allowed_scopes": [s.value for s in prepared.allowed_scopes],
        "programming_allowed": prepared.programming_allowed,
        "reason": prepared.programming_reason,
        "capacity_verified": False,
        "domain_address": prepared.domain_address,
    }
