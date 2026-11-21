"""Connect Siemens XML configurations to native project editing."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import fields, is_dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast

from editor_gui.device import ComObject, ComObjectFlags
from editor_gui.device_channels import ComObjectModule
from editor_gui.dpt import lookup_or_make_dpt
from editor_gui.languages import product_language
from editor_gui.plugin_params import PluginParameter, PluginSectionView
from xknxeditor.prod.errors import ArchiveError
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.vendor.siemens_generic import (
    Sources,
    build_view,
    find_sources,
    parse_settings,
    pick_language,
)
from xknxeditor.vendor.siemens_generic.configuration import (
    default_config,
    edit,
    evaluate,
    export_config,
    validate_value,
    write_values,
)
from xknxeditor.vendor.siemens_generic.presentation import value_format
from xknxeditor.vendor.siemens_generic.recover import (
    DeviceImage,
    MemoryReader,
    decode_device,
    read_device_image,
)
from xknxeditor.vendor.siemens_generic.view import same_application

from . import NativeConfiguration, NativeObject, PluginCapabilities

if TYPE_CHECKING:
    from editor_gui.device import Device
    from xknxeditor.download.image import GroupCommunication
    from xknxeditor.download.prepared import PreparedDownload


def sources(device: Device) -> Sources:
    if device.app is None or device.plugin_baggage is None:
        raise ValueError("Siemens application description is unavailable")
    extension = device.app.program.static.extension
    baggage_ids = tuple(b.ref_id for b in extension.baggage) if extension else ()
    try:
        payloads = tuple(
            (ref, device.plugin_baggage(ref))
            for ref in baggage_ids
            if ref.lower().endswith((".xml", ".2exml"))
        )
    except (OSError, ArchiveError, zipfile.BadZipFile) as exc:
        raise ValueError("Siemens application description is unavailable") from exc
    found = _sources(payloads)
    identity = re.fullmatch(
        r"M-([0-9A-F]{4})_A-([0-9A-F]{4})-([0-9A-F]{2})-[0-9A-F]{4}",
        device.app.id,
        re.IGNORECASE,
    )
    if identity is None or not same_application(
        "id-" + "_".join(identity.groups()), found.description.id
    ):
        raise ValueError("Siemens description belongs to another catalog application")
    return found


@lru_cache(maxsize=16)
def _sources(payloads: tuple[tuple[str, bytes | None], ...]) -> Sources:
    files = dict(payloads)
    found = find_sources(files, files.get)
    if found is None:
        raise ValueError("Siemens application description is unavailable")
    return found


def blob(device: Device) -> bytes:
    entries = [bytes(r.data) for r in device.binary_data if r.name == "BLOB_STRING"]
    if len(entries) != 1:
        raise ValueError("One Siemens BLOB_STRING configuration is required")
    return entries[0]


def _globals(device: Device) -> dict[str, str]:
    return {"DeviceOrderNumber": device.order_number} if device.order_number else {}


def _conditions(root: object) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}

    def visit(value: object, parameter: str = "", test: str = "") -> None:
        if isinstance(value, list):
            for child in cast(list[object], value):
                visit(child, parameter, test)
        elif is_dataclass(value) and not isinstance(value, type):
            if hasattr(value, "param_ref_id") and hasattr(value, "when"):
                parameter = str(getattr(value, "param_ref_id", "") or "")
            if hasattr(value, "test"):
                test = str(getattr(value, "test", "") or "")
            if type(value).__name__ == "ComObjectRefRef" and parameter and test:
                result[str(getattr(value, "ref_id", ""))] = (parameter, test)
            for field in fields(value):
                visit(getattr(value, field.name), parameter, test)

    visit(root)
    return result


class SiemensGenericProvider:
    id = "siemens-generic"
    blob_name = "BLOB_STRING"
    parameter_prefix = "siemens-generic:"
    # The configuration holds no object flags: a device read sets them from its tables.
    keeps_object_flags = False

    def capabilities(self, device: Device) -> PluginCapabilities:
        try:
            if device.native_error:
                raise ValueError(device.native_error)
            configured = any(r.name == self.blob_name for r in device.binary_data)
            self.read(device) if configured else self.setup(device)
        except ValueError as exc:
            return PluginCapabilities(False, False, False, str(exc), str(exc))
        from xknxeditor.download.scope import DownloadScope

        reason = ""
        try:
            from xknxeditor.vendor.siemens_generic.image import compile_image

            data = self.read(device).data if configured else self.setup(device).data
            settings = parse_settings(data)
            assert settings is not None
            compile_image(sources(device).description, settings, _globals(device))
            assert device.app is not None
            from xknxeditor.vendor.siemens_generic.prepare import validate_profile

            validate_profile(device.app, sources(device).description)
        except ValueError as exc:
            reason = str(exc)
        return PluginCapabilities(
            configured,
            True,
            not reason,
            reason or None,
            reason,
            readable=configured,
            download_scopes=(DownloadScope.FULL,) if not reason else (),
        )

    def setup(self, device: Device) -> NativeConfiguration:
        if any(r.name == self.blob_name for r in device.binary_data):
            return self.read(device)
        return self._state(
            device, default_config(sources(device).description, _globals(device))
        )

    def _state(self, device: Device, data: bytes) -> NativeConfiguration:
        source = sources(device)
        settings = parse_settings(data)
        if settings is None:
            raise ValueError("Invalid Siemens configuration XML")
        state = evaluate(source.description, settings, _globals(device))
        assert device.app is not None
        catalog = ApplicationIndexer(device.app.program)
        conditions = _conditions(device.app.program.dynamic)
        params = {ref: "0" for ref, _ in conditions.values()}
        bindings: list[NativeObject] = []
        for obj in state.objects:
            candidates = [
                ref
                for ref in catalog.com_object_refs.values()
                if catalog.com_objects[ref.ref_id].number == obj.number
                and (
                    ref.object_size or catalog.com_objects[ref.ref_id].object_size
                ).value
                == obj.size
                and conditions.get(ref.id, ("", ""))[1] == obj.parent_value
            ]
            if len(candidates) != 1:
                raise ValueError(
                    f"Group object {obj.number} has no unique catalog reference"
                )
            ref = candidates[0]
            base = catalog.com_objects[ref.ref_id]
            dpts = ref.datapoint_type or base.datapoint_type
            dpt = dpts[0] if dpts else ""
            params[conditions[ref.id][0]] = obj.parent_value
            bindings.append(NativeObject(obj.id, ref.id, obj.size, dpt, obj.flags))
        return NativeConfiguration(data, params, tuple(bindings))

    def read(self, device: Device) -> NativeConfiguration:
        return self._state(device, blob(device))

    def decode(self, device: Device, data: bytes) -> NativeConfiguration:
        if data.lstrip().startswith(b"{"):
            config: dict[str, Any] = json.loads(data)
            source = sources(device)
            if (
                config.get("format") != self.id
                or config.get("version") != 1
                or config.get("application") != source.description.id
            ):
                raise ValueError("Incompatible Siemens configuration")
            raw_values = config.get("values")
            values = (
                cast(dict[object, object], raw_values)
                if isinstance(raw_values, dict)
                else {}
            )
            if not isinstance(raw_values, dict) or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in values.items()
            ):
                raise ValueError("Configuration values must be strings")
            existing = (
                blob(device)
                if any(r.name == self.blob_name for r in device.binary_data)
                else self.setup(device).data
            )
            current = parse_settings(existing)
            if current is None:
                raise ValueError("Invalid Siemens configuration XML")
            typed_values = cast(dict[str, str], values)
            for ref, value in typed_values.items():
                if current.values.get(ref) != value:
                    validate_value(source.description, ref, value)
            state = evaluate(
                source.description,
                replace(current, values=typed_values),
                _globals(device),
            )
            data = write_values(existing, state.values, replace_current=True)
        result = self._state(device, data)
        settings = parse_settings(data)
        assert settings is not None
        state = evaluate(sources(device).description, settings, _globals(device))
        return replace(result, data=write_values(data, state.values))

    def edit(self, device: Device, ref_id: str, value: str) -> NativeConfiguration:
        if not ref_id.startswith(self.parameter_prefix):
            raise ValueError("Invalid Siemens parameter reference")
        source = sources(device)
        raw = blob(device)
        settings = parse_settings(raw)
        if settings is None:
            raise ValueError("Invalid Siemens configuration XML")
        ref = ref_id.removeprefix(self.parameter_prefix)
        state = evaluate(source.description, settings, _globals(device))
        if ref not in state.active_refs:
            raise ValueError(
                "This parameter is not editable in the current configuration"
            )
        value = value_format(source.description, ref).parse(value, state.values[ref])
        data = edit(
            source.description,
            raw,
            ref,
            value,
            _globals(device),
        )
        return self._state(device, data)

    def serialize(
        self,
        device: Device,
        config: NativeConfiguration,
        objects: tuple[NativeObject, ...],
    ) -> NativeConfiguration:
        return config

    def export(self, device: Device) -> bytes:
        return export_config(
            sources(device).description, blob(device), _globals(device)
        )

    def sections(self, device: Device) -> list[PluginSectionView]:
        if not any(r.name == self.blob_name for r in device.binary_data):
            return []
        self.read(device)
        source = sources(device)
        settings = parse_settings(blob(device))
        if settings is None:
            return []
        texts = pick_language(source.translations, product_language())
        view = build_view(source.description, settings, texts, _globals(device))
        sections: list[PluginSectionView] = []
        for page in view.pages:
            parameters: list[PluginParameter] = []
            for index, row in enumerate(page.rows):
                if row.separator:
                    continue
                ref = source.description.refs[row.ref_id]
                param = source.description.parameters[ref.parameter_id]
                ptype = source.description.types[param.type_id]
                enum = ptype.enumeration(row.value)
                value = (
                    row.value
                    if enum is None
                    else (
                        enum.name
                        if ptype.enum_by_name(param.default)
                        else str(enum.value)
                    )
                )
                choices = tuple(
                    (
                        e.name if ptype.enum_by_name(param.default) else str(e.value),
                        texts.get(e.id) or e.text,
                    )
                    for e in ptype.enumerations
                    if e.name not in ref.hidden_enum_values
                    and str(e.value) not in ref.hidden_enum_values
                )
                formatting = value_format(source.description, row.ref_id)
                parameters.append(
                    PluginParameter(
                        self.parameter_prefix + row.ref_id,
                        row.parameter,
                        row.label,
                        formatting.display(value),
                        formatting.display(row.default_value),
                        page.title,
                        index,
                        not row.assigned and not ref.global_variable,
                        choices,
                        raw_value=value
                        if formatting.percent or formatting.time
                        else None,
                    )
                )
            sections.append(PluginSectionView(page.title, parameters))
        return sections

    def install_view(self, device: Device) -> None:
        state = self.read(device)
        source = sources(device)
        settings = parse_settings(state.data)
        assert settings is not None and device.app is not None
        evaluated = evaluate(source.description, settings, _globals(device))
        catalog = ApplicationIndexer(device.app.program)
        by_number = {obj.number: obj for obj in evaluated.objects}
        overrides = {ref.ref_id: ref for ref in device.com_object_instance_refs}
        device.native_objects = []
        device.native_modules = []
        texts = pick_language(source.translations, product_language())
        for role in state.objects:
            co = catalog.com_objects[catalog.com_object_refs[role.reference].ref_id]
            original = by_number[co.number]
            override = overrides.get(role.reference)
            flags = dict(role.flags)
            for name in flags:
                value = getattr(override, name + "_flag", None)
                if value is not None:
                    flags[name] = value.value == "Enabled"
            device.native_objects.append(
                ComObject(
                    id=role.reference,
                    name=texts.get(original.id) or original.text or co.text or co.name,
                    function_text=co.function_text or "",
                    number=co.number,
                    object_size=role.size,
                    dpt=lookup_or_make_dpt(role.dpt),
                    flags=ComObjectFlags(**flags),
                )
            )
        channels: dict[int, set[str]] = {}
        for role in state.objects:
            number = catalog.com_objects[
                catalog.com_object_refs[role.reference].ref_id
            ].number
            original = by_number[number]
            channels.setdefault(evaluated.object_channels[original.id], set()).add(
                role.reference
            )
        view = build_view(source.description, settings, texts, _globals(device))
        titles = {
            page.channel: page.title for page in reversed(view.pages) if page.title
        }
        device.native_modules = [
            ComObjectModule(
                str(channel), titles.get(channel, str(channel)), None, frozenset(refs)
            )
            for channel, refs in channels.items()
        ]
        device.com_objects = device.native_objects

    def prepare(
        self, device: Device, communication: GroupCommunication | None
    ) -> PreparedDownload:
        from xknxeditor.download.tables import com_object_flag_byte, size_code
        from xknxeditor.vendor.siemens_generic.image import compile_image
        from xknxeditor.vendor.siemens_generic.prepare import prepare_image

        if device.native_error:
            raise ValueError(device.native_error)
        if communication is None:
            raise ValueError(
                "Set a valid individual address before preview or preflight"
            )
        state = self.read(device)
        references = {o.reference for o in state.objects}
        if any(
            link.com_object_ref_id not in references for link in communication.links
        ):
            raise ValueError("Stored group link has no compatible native object role")
        linked = {link.com_object_ref_id for link in communication.links}
        objects = {c.id: c for c in device.get_visible_com_objects()}
        if objects.keys() != references:
            raise ValueError("Native Siemens object bindings are incomplete")
        descriptors: dict[int, tuple[int, int]] = {}
        for ref, obj in objects.items():
            flags = {
                name: getattr(obj.flags, name)
                for name in (
                    "communication",
                    "read",
                    "write",
                    "transmit",
                    "update",
                    "read_on_init",
                )
            }
            if ref not in linked:
                flags["communication"] = False
            descriptors[obj.number] = (
                com_object_flag_byte(priority=obj.priority, **flags),
                size_code(obj.object_size),
            )
        settings = parse_settings(state.data)
        assert settings is not None and device.app is not None
        source = sources(device)
        compiled = compile_image(source.description, settings, _globals(device))
        prepared = prepare_image(
            device.app,
            source.description,
            compiled,
            state.parameters,
            replace(communication, group_object_descriptors=descriptors),
        )
        from collections.abc import Mapping

        from xknxeditor.vendor.siemens_generic.configuration import downloaded_config

        def completion(
            properties: Mapping[tuple[int, int, int], bytes],
        ) -> Mapping[str, tuple[bytes, bytes]]:
            segment_info = properties.get((4, 27, 1), b"")
            return {
                self.blob_name: (
                    state.data,
                    downloaded_config(
                        state.data, compiled.data, compiled.mask, segment_info
                    ),
                )
            }

        return replace(prepared, completion=completion)

    async def read_device(
        self, programmer: MemoryReader, device: Device
    ) -> DeviceImage:
        """The parameter memory a device of ``device``'s product holds (see
        :func:`xknxeditor.vendor.siemens_generic.recover.read_device_image`)."""
        return await read_device_image(programmer, sources(device).description)

    def recover(self, device: Device, image: object) -> NativeConfiguration:
        """The configuration a device read off the bus (``image``, see
        :meth:`read_device`) was downloaded with, for ``device`` (of the same product):
        the product's default configuration with the values the memory holds."""
        if not isinstance(image, DeviceImage):
            raise ValueError("No Siemens configuration was read off the device")
        description = sources(device).description
        data = decode_device(
            description,
            default_config(description, _globals(device)),
            image,
            _globals(device),
        )
        return self._state(device, data)

    def slot_references(self, device: Device) -> dict[int, str]:
        """The group object reference of each group object number, as the device's
        configuration places its objects (an association names the number)."""
        assert device.app is not None
        catalog = ApplicationIndexer(device.app.program)
        return {
            catalog.com_objects[catalog.com_object_refs[o.reference].ref_id].number: (
                o.reference
            )
            for o in self.read(device).objects
        }


PROVIDER = SiemensGenericProvider()
