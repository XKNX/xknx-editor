"""Native push-button object view and complete-image preparation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from xknx.exceptions import CouldNotParseAddress
from xknx.telegram import IndividualAddress

from editor_gui.device import ComObject, ComObjectFlags
from editor_gui.device_channels import ComObjectModule
from editor_gui.dpt import lookup_or_make_dpt
from xknxeditor.download.scope import DownloadScope
from xknxeditor.download.tables import com_object_flag_byte, priority_from_bits
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.vendor.powertool import (
    BLOB_NAME,
    Association,
    HostProfile,
)
from xknxeditor.vendor.powertool.binding import SIZE_TEXT, bind_catalog
from xknxeditor.vendor.powertool.native import (
    Configuration,
    channel_labels,
    default,
    edit_native,
    for_product,
    profile,
    rebase,
)
from xknxeditor.vendor.powertool.native import (
    compile_native as compile_image,
)
from xknxeditor.vendor.powertool.native import (
    dump as to_json_bytes,
)
from xknxeditor.vendor.powertool.native import (
    from_original as config_from_project_blob,
)
from xknxeditor.vendor.powertool.native import (
    load as from_json_bytes,
)
from xknxeditor.vendor.powertool.native import (
    product_profile as profile_for,
)
from xknxeditor.vendor.powertool.prepare import prepare_image, product_parameters
from xknxeditor.vendor.powertool.project_blob import (
    PLUGIN_BLOB_NAME,
    read_project_blob,
)
from xknxeditor.vendor.powertool.project_writer import write_project_blob

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugin_params import PluginSectionView
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.download.prepared import PreparedDownload

from . import NativeConfiguration, NativeObject, PluginCapabilities, compatible_dpt

FLAGS = {
    "communication": 4,
    "read": 8,
    "write": 16,
    "transmit": 64,
    "update": 128,
    "read_on_init": 32,
}


def configuration(device: Device) -> Configuration:
    entries = [r for r in device.binary_data if r.name == BLOB_NAME]
    if not entries:
        originals = [r for r in device.binary_data if r.name == PLUGIN_BLOB_NAME]
        if len(originals) == 1:
            return config_from_project_blob(bytes(originals[0].data))
    if len(entries) != 1:
        raise ValueError("Set up a native PowerTool configuration first")
    return from_json_bytes(bytes(entries[0].data))


def install_view(device: Device) -> None:
    if not any(r.name == BLOB_NAME for r in device.binary_data):
        return
    device.native_objects = []
    device.native_modules = []
    device.native_error = None
    try:
        config = configuration(device)
        assert device.app is not None
        secondary = device.second_application() if device.second_application else None
        config = for_product(config, device.app, secondary)
        compiled = compile_image(config)
        bindings = bind_catalog(device.app, compiled).references
        if dict(config.bindings) != bindings:
            raise ValueError("native object bindings disagree with the product")
        catalog = ApplicationIndexer(device.app.program)
        overrides = {r.ref_id: r for r in device.com_object_instance_refs}
        for role in compiled.objects:
            if not role.visible:
                continue
            ref = bindings[role.key]
            override = overrides.get(ref)
            flags = ComObjectFlags(
                **{name: bool(role.flags & bit) for name, bit in FLAGS.items()}
            )
            for name in FLAGS:
                value = getattr(override, name + "_flag", None)
                if value is not None:
                    setattr(flags, name, value.value == "Enabled")
            device.native_objects.append(
                ComObject(
                    id=ref,
                    name=role.name,
                    dpt=lookup_or_make_dpt(role.dpt),
                    flags=flags,
                    number=catalog.com_objects[
                        catalog.com_object_refs[ref].ref_id
                    ].number,
                    object_size=SIZE_TEXT[role.size_code],
                    supported_dpts=[lookup_or_make_dpt(role.dpt)],
                    priority=override.priority.value
                    if override is not None and override.priority is not None
                    else priority_from_bits(role.flags),
                )
            )
        for channel, label in enumerate(channel_labels(config)):
            refs = frozenset(
                bindings[o.key]
                for o in compiled.objects
                if o.visible and o.channel == channel
            )
            if refs:
                device.native_modules.append(
                    ComObjectModule(f"channel_{channel}", label, None, refs)
                )
        device.com_objects = device.native_objects
    except ValueError as exc:
        device.native_error = str(exc)
        device.com_objects = []


def _stored_objects(
    device: Device, objects: tuple[NativeObject, ...]
) -> tuple[NativeObject, ...]:
    assert device.app is not None
    catalog = ApplicationIndexer(device.app.program)

    def identity(reference: str) -> tuple[int, str] | None:
        ref = catalog.com_object_refs.get(reference)
        if ref is None:
            return None
        obj = catalog.com_objects[ref.ref_id]
        return obj.number, (ref.object_size or obj.object_size).value

    sources: dict[tuple[int, str], list[str]] = {}
    for ref in device.com_object_instance_refs:
        key = identity(ref.ref_id)
        if key is not None:
            sources.setdefault(key, []).append(ref.ref_id)
    matched: list[NativeObject] = []
    for obj in objects:
        key = identity(obj.reference)
        candidates = sources.get(key, []) if key is not None else []
        if len(candidates) != 1:
            continue
        source = next(
            r for r in device.com_object_instance_refs if r.ref_id == candidates[0]
        )
        if source.datapoint_type and any(
            not compatible_dpt(obj.dpt, token) for token in source.datapoint_type
        ):
            continue
        matched.append(replace(obj, reference=candidates[0]))
    return tuple(matched)


def _prepare(
    device: Device, communication: GroupCommunication | None = None
) -> PreparedDownload:
    if device.native_error:
        raise ValueError(device.native_error)
    if communication is not None and (
        communication.security is not None or communication.filter_table is not None
    ):
        raise ValueError("unsupported native communication profile")
    state = PROVIDER.read(device)
    config = from_json_bytes(state.data)
    assert device.app is not None
    secondary = device.second_application() if device.second_application else None
    config = for_product(config, device.app, secondary)
    sources = state.objects if state.source_objects is None else state.source_objects
    reverse = {obj.reference: obj.key for obj in sources}
    compiled = compile_image(config)
    if bind_catalog(device.app, compiled).references != dict(config.bindings):
        raise ValueError("native object bindings disagree with the product")
    associations: list[Association] = []
    for link in communication.links if communication else ():
        if link.com_object_ref_id not in reverse:
            raise ValueError(
                "Stored group link has no unique compatible native object role"
            )
        associations.append(
            Association(
                reverse[link.com_object_ref_id],
                link.group_address,
                link.sending,
                link.group_speaker,
            )
        )
    native = any(r.name == BLOB_NAME for r in device.binary_data)
    objects = {co.id: co for co in device.get_visible_com_objects()}
    if native:
        flags = {
            role: com_object_flag_byte(
                priority=objects[ref].priority,
                **{name: getattr(objects[ref].flags, name) for name in FLAGS},
            )
            for role, ref in config.bindings.items()
        }
    else:
        overrides = {r.ref_id: r for r in device.com_object_instance_refs}
        flags = {}
        for obj in sources:
            override = overrides[obj.reference]
            values = dict(obj.flags)
            if device.com_object_flag_overrides is not None:
                values.update(device.com_object_flag_overrides.get(obj.reference, {}))
            else:
                for name in FLAGS:
                    value = getattr(override, name + "_flag")
                    if value is not None:
                        values[name] = value.value == "Enabled"
            flags[obj.key] = com_object_flag_byte(
                priority=override.priority.value
                if override.priority is not None
                else obj.priority,
                **values,
            )
    try:
        individual_address = IndividualAddress(device.individual_address).raw
    except CouldNotParseAddress as exc:
        raise ValueError(
            "Set a valid individual address before preview or preflight"
        ) from exc
    if profile(config) != profile_for(device.app, secondary):
        raise ValueError("Configuration does not match the product")
    parameters = product_parameters(device.app)
    mask = int(str(device.app.program.mask_version).upper().removeprefix("MV-"), 16)
    if mask == 0x17B0 and (
        communication is None or communication.domain_address is None
    ):
        raise ValueError("Powerline domain address is missing")
    host = HostProfile(
        individual_address=individual_address,
        routing_count=int(parameters["RoutingCount"]),
        retry_count=int(parameters["MaxRetryCount"]),
        rate_time=int(parameters["TelegramRateLimitationTimeBase"]),
        rate_count=int(parameters["TelegramRateLimitationNumber"]),
        mask_version=mask,
        property_order="windows" if mask == 0x07B0 else "mono",
        domain_address=communication.domain_address or 0 if communication else 0,
        repeater_present=communication.repeater_present if communication else False,
    )
    return prepare_image(
        compile_image(config, host, associations, flags),
        secondary_application=secondary,
        application=device.app,
    )


class PowerToolProvider:
    id = "powertool"
    blob_name = BLOB_NAME
    parameter_prefix = "powertool:"

    def _variant(self, device: Device) -> str:
        if device.app is None:
            raise ValueError("Device has no application")
        secondary = device.second_application() if device.second_application else None
        return profile_for(device.app, secondary)

    def capabilities(self, device: Device) -> PluginCapabilities:
        native = any(r.name == self.blob_name for r in device.binary_data)
        originals = [r for r in device.binary_data if r.name == PLUGIN_BLOB_NAME]
        stored = bool(originals) and not native
        readable = False
        if stored:
            try:
                if len(originals) != 1:
                    raise ValueError("Duplicate PowerTool project blobs")
                read_project_blob(bytes(originals[0].data))
                readable = True
            except ValueError as exc:
                return PluginCapabilities(
                    True,
                    False,
                    False,
                    str(exc),
                    str(exc),
                    stored_configuration=True,
                )
        configured = native or stored
        try:
            self._variant(device)
        except ValueError as exc:
            return PluginCapabilities(
                configured,
                False,
                False,
                str(exc),
                str(exc),
                stored_configuration=stored,
                readable=readable,
            )
        try:
            self.read(device)
        except ValueError as exc:
            importable = None
            if len(originals) == 1:
                try:
                    self.decode(device, bytes(originals[0].data))
                    importable = PLUGIN_BLOB_NAME
                except ValueError:
                    pass
            return PluginCapabilities(
                configured,
                True,
                False,
                str(exc),
                str(exc),
                importable,
                stored,
                readable,
            )
        return PluginCapabilities(
            configured,
            True,
            True,
            None,
            importable_blob=PLUGIN_BLOB_NAME if stored else None,
            stored_configuration=stored,
            readable=True,
            download_scopes=(DownloadScope.FULL,),
        )

    def _state(
        self, device: Device, config: Configuration, *, verify: bool = False
    ) -> NativeConfiguration:
        if profile(config) != self._variant(device):
            raise ValueError("Configuration does not match the device")
        assert device.app is not None
        secondary = device.second_application() if device.second_application else None
        config = for_product(config, device.app, secondary)
        compiled = compile_image(config)
        binding = bind_catalog(device.app, compiled)
        defaults = {
            obj.key: priority_from_bits(obj.flags)
            for obj in compile_image(replace(config, object_flags={})).objects
        }
        if verify and dict(config.bindings) != binding.references:
            raise ValueError("Native object bindings disagree with the product")
        return NativeConfiguration(
            to_json_bytes(replace(config, bindings=binding.references)),
            binding.parameters,
            tuple(
                NativeObject(
                    role.key,
                    binding.references[role.key],
                    SIZE_TEXT[role.size_code],
                    role.dpt,
                    {name: bool(role.flags & bit) for name, bit in FLAGS.items()},
                    priority_from_bits(role.flags),
                    defaults[role.key],
                )
                for role in compiled.objects
                if role.visible
            ),
        )

    def setup(self, device: Device) -> NativeConfiguration:
        if any(r.name == PLUGIN_BLOB_NAME for r in device.binary_data):
            return self.read(device)
        return self._state(device, default(self._variant(device)))

    def read(self, device: Device) -> NativeConfiguration:
        native = any(r.name == self.blob_name for r in device.binary_data)
        state = self._state(
            device,
            configuration(device),
            verify=native,
        )
        return (
            state
            if native
            else replace(state, source_objects=_stored_objects(device, state.objects))
        )

    def serialize(
        self,
        device: Device,
        config: NativeConfiguration,
        objects: tuple[NativeObject, ...],
    ) -> NativeConfiguration:
        native = from_json_bytes(config.data)
        flags = dict(native.object_flags)
        flags.update(
            (obj.key, com_object_flag_byte(priority=obj.priority, **obj.flags))
            for obj in objects
        )
        native = replace(native, object_flags=flags)
        source = next(
            (
                bytes(row.data)
                for row in device.binary_data
                if row.name == PLUGIN_BLOB_NAME
            ),
            None,
        )
        payload = write_project_blob(source, native)
        return replace(
            self._state(device, rebase(native)),
            binary_data={PLUGIN_BLOB_NAME: payload},
        )

    def decode(self, device: Device, data: bytes) -> NativeConfiguration:
        if data.startswith(b"\x00"):
            return self._state(device, config_from_project_blob(data))
        return self._state(device, from_json_bytes(data))

    def edit(self, device: Device, ref_id: str, value: str) -> NativeConfiguration:
        config = from_json_bytes(self.read(device).data)
        return self._state(device, edit_native(config, ref_id, value))

    def sections(self, device: Device) -> list[PluginSectionView]:
        from editor_gui.plugin_blob_readers.powertool import read, read_original
        from xknxeditor.vendor.powertool.project_blob import PLUGIN_BLOB_NAME

        if not any(r.name == self.blob_name for r in device.binary_data):
            originals = [r for r in device.binary_data if r.name == PLUGIN_BLOB_NAME]
            if len(originals) > 1:
                raise ValueError("Duplicate PowerTool project blobs")
            if not originals:
                return []
            try:
                return read(self.read(device).data)
            except ValueError:
                return read_original(bytes(originals[0].data))
        try:
            return read(self.read(device).data)
        except ValueError:
            return []

    def install_view(self, device: Device) -> None:
        install_view(device)

    def prepare(
        self, device: Device, communication: GroupCommunication | None
    ) -> PreparedDownload:
        self.read(device)
        return _prepare(device, communication)


PROVIDER = PowerToolProvider()
