"""Registered native configuration providers selected by shared plugin mappings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True)
class NativeConfiguration:
    data: bytes
    parameters: Mapping[str, str]
    objects: tuple[NativeObject, ...]


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
    def sections(self, device: Device) -> list[PluginSectionView]: ...
    def install_view(self, device: Device) -> None: ...
    def prepare(
        self, device: Device, communication: GroupCommunication | None
    ) -> PreparedDownload: ...


def providers() -> tuple[PluginProvider, ...]:
    from .powertool import PROVIDER

    return (PROVIDER,)


def provider_named(name: str) -> PluginProvider:
    for provider in providers():
        if provider.id == name:
            return provider
    raise ValueError(f"Configuration provider {name!r} is unavailable")


def native_blob_names() -> frozenset[str]:
    return frozenset(p.blob_name for p in providers())


def provider_for(device: Device) -> PluginProvider | None:
    from editor_gui.plugin_params import mapping_for_device

    mapping = mapping_for_device(device)
    if mapping is not None and mapping.provider:
        return provider_named(mapping.provider)
    if any(r.name in native_blob_names() for r in getattr(device, "binary_data", ())):
        raise ValueError("Native configuration has no matching product provider")
    return None


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
    if not any(r.name in native_blob_names() for r in device.binary_data):
        return
    try:
        require_provider(device).install_view(device)
    except ValueError as exc:
        device.native_objects = []
        device.native_modules = []
        device.com_objects = []
        device.native_error = str(exc)


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
        "resources": [
            {
                "object_index": s.object_index,
                "size": len(s.memory.data),
                "data": s.memory.data.hex(),
                "mask": (s.memory.mask or b"").hex(),
            }
            for s in prepared.image.application_segments
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
