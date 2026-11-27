"""Validate edits and preserve both configuration XML dialects."""

from __future__ import annotations

import base64
import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from .blob import PluginSettings, decode_text, parse_settings
from .codec import encode
from .description import (
    INTEGER_KINDS,
    CommunicationObject,
    Description,
    TypeKind,
    integer,
)
from .view import Evaluator, same_application


@dataclass(frozen=True)
class Evaluation:
    values: dict[str, str]
    active_refs: tuple[str, ...]
    objects: tuple[CommunicationObject, ...]
    object_channels: dict[str, int]
    assigned: frozenset[str]
    memory_values: tuple[tuple[str, str], ...]


def evaluate(
    description: Description,
    settings: PluginSettings,
    globals_: dict[str, str] | None = None,
) -> Evaluation:
    if description.unsupported:
        raise ValueError(
            f"Unsupported dynamic elements: {', '.join(description.unsupported)}"
        )
    if not settings.application_id or not same_application(
        settings.application_id, description.id
    ):
        raise ValueError("Stored configuration belongs to another application")
    unknown = settings.values.keys() - description.refs.keys()
    if unknown:
        raise ValueError(f"Unknown stored parameter reference: {min(unknown)}")
    for ref, value in settings.values.items():
        if not description.refs[ref].global_variable:
            validate_value(description, ref, value, stored=True)
    builder = Evaluator(description, settings, {}, globals_ or {})
    walk = builder.run()
    refs = tuple(
        dict.fromkeys(
            r for _, r, fitted in walk.shown if fitted and not r.startswith("\0")
        )
    )
    values = {**settings.values, **walk.assigned}
    values.update((r, builder.value(r, walk)) for r in refs)
    objects: dict[int, CommunicationObject] = {}
    channels: dict[str, int] = {}
    for ref, channel in walk.objects:
        obj = description.objects.get(ref)
        if obj is None:
            raise ValueError(f"Unknown communication object reference: {ref}")
        previous = objects.get(obj.number)
        if previous is not None and (previous.parent_value, previous.size) != (
            obj.parent_value,
            obj.size,
        ):
            raise ValueError(f"Conflicting active communication objects: {obj.number}")
        objects.setdefault(obj.number, obj)
        channels[obj.id] = channel
    return Evaluation(
        values,
        refs,
        tuple(objects.values()),
        channels,
        frozenset(walk.assigned),
        tuple(walk.memory_values),
    )


def validate_value(
    description: Description, ref_id: str, value: str, *, stored: bool = False
) -> None:
    ref = description.refs.get(ref_id)
    if ref is None:
        raise ValueError(f"Unknown parameter reference: {ref_id}")
    if ref.global_variable:
        raise ValueError("This parameter is supplied by the product")
    parameter = description.parameters[ref.parameter_id]
    ptype = description.types[parameter.type_id]
    if ptype.kind is TypeKind.ENUM:
        enum = ptype.enumeration(value)
        if enum is None or (
            not stored
            and (
                enum.name in ref.hidden_enum_values
                or str(enum.value) in ref.hidden_enum_values
            )
        ):
            raise ValueError(f"Invalid choice for {ref_id}: {value}")
    elif ptype.kind is TypeKind.TEXT:
        if (
            any(ord(c) > 255 or ord(c) < 32 for c in value)
            or len(value) * 8 > ptype.size_bits
        ):
            raise ValueError("Text exceeds the parameter's character range or length")
    elif ptype.kind in INTEGER_KINDS:
        try:
            number = integer(value)
        except ValueError as exc:
            raise ValueError("An integer value is required") from exc
        minimum = (
            ptype.minimum
            if ptype.minimum is not None
            else -(1 << (ptype.size_bits - 1))
            if ptype.signed
            else 0
        )
        maximum = (
            ptype.maximum
            if ptype.maximum is not None
            else (1 << (ptype.size_bits - int(ptype.signed))) - 1
        )
        if not minimum <= number <= maximum:
            raise ValueError(f"Value must be between {minimum} and {maximum}")
    else:
        raise ValueError("This parameter type is not implemented")
    if encode(value, ptype) is None:
        raise ValueError("The parameter value cannot be encoded")


def write_values(
    data: bytes, values: Mapping[str, str], *, replace_current: bool = False
) -> bytes:
    settings = parse_settings(data)
    if settings is None:
        raise ValueError("Invalid Siemens configuration XML")
    changes = {
        key: value for key, value in values.items() if settings.values.get(key) != value
    }
    removed: set[str] = (
        settings.values.keys() - values.keys() if replace_current else set()
    )
    if not changes and not removed:
        return data
    text = decode_text(data)
    assert text is not None
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(text, parser=parser)
    current = root.find("CurrentSettings")
    assert current is not None
    container = current.find("ParameterInstanceRefs")
    if container is None:
        container = ET.SubElement(current, "ParameterInstanceRefs")
    tag, attr, child = (
        ("P", "R", "V")
        if settings.compact
        else ("ParameterInstanceRef", "RefId", "Value")
    )
    by_ref = {e.get(attr): e for e in container if e.tag == tag}
    for ref in removed:
        element = by_ref.get(ref)
        if element is not None:
            container.remove(element)
    for ref, value in changes.items():
        element = by_ref.get(ref)
        if element is None:
            element = ET.SubElement(container, tag, {"Id": "idInst_" + ref, attr: ref})
        target = element.find(child)
        if target is None:
            target = ET.SubElement(element, child)
        target.text = value
    current.set(
        "LastModified",
        datetime.now().isoformat(timespec="milliseconds"),
    )
    declaration = text[: text.index("?>") + 2] if text.startswith("<?xml") else ""
    return (declaration + ET.tostring(root, encoding="unicode")).encode("utf-16-le")


def default_config(
    description: Description, globals_: dict[str, str] | None = None
) -> bytes:
    root = ET.Element("ApplicationProgramInstance")
    current = ET.SubElement(
        root,
        "CurrentSettings",
        {
            "Id": "V01_00",
            "Name": description.id,
            "Description": "Created by Generic PlugIn",
        },
    )
    container = ET.SubElement(current, "ParameterInstanceRefs")
    ET.SubElement(
        container,
        "P" if description.compact_blob else "ParameterInstanceRef",
        {"R" if description.compact_blob else "RefId": ""},
    )
    ET.SubElement(
        root,
        "DownloadedSettings",
        {
            "Id": "V01_00",
            "Name": "invalid segment Info",
            "LastModified": "0001-01-01T00:00:00.123",
            "LastDownload": "0001-01-01T00:00:00.123",
        },
    )
    data = (
        '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")
    ).encode("utf-16-le")
    settings = parse_settings(data)
    assert settings is not None
    state = evaluate(description, settings, globals_)
    return write_values(data, state.values)


def edit(
    description: Description,
    data: bytes,
    ref_id: str,
    value: str,
    globals_: dict[str, str] | None = None,
) -> bytes:
    settings = parse_settings(data)
    if settings is None:
        raise ValueError("Invalid Siemens configuration XML")
    state = evaluate(description, settings, globals_)
    if ref_id not in state.active_refs or ref_id in state.assigned:
        raise ValueError("This parameter is not editable in the current configuration")
    validate_value(description, ref_id, value)
    parameter = description.parameters[description.refs[ref_id].parameter_id]
    ptype = description.types[parameter.type_id]
    if encode(state.values.get(ref_id, ""), ptype) == encode(value, ptype):
        return data
    if ptype.kind is TypeKind.ENUM and (enum := ptype.enumeration(value)) is not None:
        value = enum.name if ptype.enum_by_name(parameter.default) else str(enum.value)
    next_settings = replace(settings, values={**settings.values, ref_id: value})
    next_state = evaluate(description, next_settings, globals_)
    return write_values(data, next_state.values)


def export_config(
    description: Description, data: bytes, globals_: dict[str, str] | None = None
) -> bytes:
    settings = parse_settings(data)
    if settings is None:
        raise ValueError("Invalid Siemens configuration XML")
    evaluate(description, settings, globals_)
    return json.dumps(
        {
            "format": "siemens-generic",
            "version": 1,
            "application": description.id,
            "values": settings.values,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()


def downloaded_config(
    data: bytes,
    memory: bytes,
    mask: bytes,
    segment_info: bytes,
    *,
    downloaded: bool = True,
) -> bytes:
    """Store the completed image and the device's own segment record. Without
    ``downloaded`` (the memory was read off the device) the times of the record stay as
    they are: when the device was downloaded is not known then."""
    if len(memory) != 65535 or len(mask) != len(memory) or len(segment_info) != 8:
        raise ValueError("Invalid completed Siemens image or segment record")
    text = decode_text(data)
    if text is None or parse_settings(data) is None:
        raise ValueError("Invalid Siemens configuration XML")
    root = ET.fromstring(text)
    current = root.find("CurrentSettings")
    assert current is not None
    record = root.find("DownloadedSettings")
    if record is None:
        record = ET.SubElement(root, "DownloadedSettings")
    record.attrib.update(
        Id=current.get("Id", "V01_00"), Name=segment_info.hex().upper()
    )
    if downloaded:
        now = datetime.now().isoformat(timespec="milliseconds")
        record.attrib.update(LastModified=now, LastDownload=now)
    image = record.find("MemoryImage")
    if image is None:
        image = ET.SubElement(record, "MemoryImage")
    image.set("Data", base64.b64encode(memory).decode())
    image.set("Mask", base64.b64encode(mask).decode())
    declaration = text[: text.index("?>") + 2] if text.startswith("<?xml") else ""
    return (declaration + ET.tostring(root, encoding="unicode")).encode("utf-16-le")
