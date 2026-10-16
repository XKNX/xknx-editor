"""Device snapshots: what a device's application derived the last time it was evaluated (its group
objects), stored in the project so it opens without loading every application.

A snapshot's key covers everything the device was built from: the application (its stored product
file, which is named by its content hash), the language, the device's parameters, module instances
and group objects, and the code that evaluates them. A device whose key changed is evaluated again.
The data is JSON of plain values only, so a project from elsewhere cannot make the editor run code.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from editor_gui.device import ComObject, ComObjectFlags
from editor_gui.dpt import DPT

if TYPE_CHECKING:
    from xknxeditor.prod import Application

# Bump when the snapshot data or what goes into the key changes.
SNAPSHOT_FORMAT = 1

_FLAG_FIELDS = tuple(f.name for f in dataclasses.fields(ComObjectFlags))
_CO_FIELDS = tuple(f.name for f in dataclasses.fields(ComObject))


@dataclasses.dataclass(frozen=True)
class DeviceSnapshot:
    """A device's application facts and its group objects as they were built."""

    application_name: str
    application_version: str
    manufacturer_id: str
    com_objects: list[ComObject]
    # The positions of the group objects visible with the device's parameters.
    visible: list[int]


@cache
def code_fingerprint() -> str:
    """Identifies the code that evaluates applications into group objects: the editor version and,
    when it runs from its sources, their content (so a changed evaluation is never served from an
    older snapshot)."""
    import editor_gui
    import xknxeditor.prod

    digest = hashlib.sha256(editor_gui.__version__.encode())
    gui = Path(editor_gui.__file__).parent
    sources = [
        *sorted(Path(xknxeditor.prod.__file__).parent.rglob("*.py")),
        gui / "device.py",
        gui / "dpt.py",
        gui / "device_snapshot.py",
    ]
    for source in sources:
        try:
            digest.update(source.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


def snapshot_key(source: tuple[Any, ...], application_source: str) -> str:
    """The key of the snapshot of a device built from ``source`` (its program refs, then the
    column values of its parameters, module instances and group objects, each row starting with
    its id) with the application read from ``application_source``."""
    program_refs = source[:-3]
    rows = [sorted(table, key=lambda values: values[0]) for table in source[-3:]]
    content = [
        SNAPSHOT_FORMAT,
        code_fingerprint(),
        application_source,
        list(program_refs),
        rows,
    ]
    encoded = json.dumps(content, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _dpt(dpt: DPT) -> list[Any]:
    return [dpt.major, dpt.minor, dpt.name, dpt.label, dpt.has_subtype]


def _from_dpt(value: list[Any]) -> DPT:
    major, minor, name, label, has_subtype = value
    return DPT(major, minor, name, label, has_subtype)


def _encode_co(co: ComObject) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _CO_FIELDS:
        value = getattr(co, name)
        if name == "dpt":
            value = _dpt(value)
        elif name == "supported_dpts":
            value = [_dpt(d) for d in value]
        elif name == "flags":
            value = [getattr(value, f) for f in _FLAG_FIELDS]
        out[name] = value
    return out


def _decode_co(data: dict[str, Any]) -> ComObject:
    values: dict[str, Any] = {}
    for name in _CO_FIELDS:
        value = data[name]
        if name == "dpt":
            value = _from_dpt(value)
        elif name == "supported_dpts":
            value = [_from_dpt(d) for d in value]
        elif name == "flags":
            value = ComObjectFlags(**dict(zip(_FLAG_FIELDS, value, strict=True)))
        values[name] = value
    return ComObject(**values)


def encode_snapshot(
    app: Application, com_objects: list[ComObject], visible: list[ComObject]
) -> str:
    """The snapshot data of a device built with ``app``; ``visible`` are objects of
    ``com_objects``."""
    shown = {id(co) for co in visible}
    return json.dumps(
        {
            "format": SNAPSHOT_FORMAT,
            "application": [app.name, app.version, app.manufacturer_id],
            "com_objects": [_encode_co(co) for co in com_objects],
            "visible": [i for i, co in enumerate(com_objects) if id(co) in shown],
        },
        separators=(",", ":"),
    )


def decode_snapshot(data: str) -> DeviceSnapshot | None:
    """The snapshot stored as ``data``; ``None`` when it is not one this editor can use."""
    try:
        content = json.loads(data)
        if not isinstance(content, dict) or content.get("format") != SNAPSHOT_FORMAT:
            return None
        name, version, manufacturer_id = content["application"]
        return DeviceSnapshot(
            application_name=str(name),
            application_version=str(version),
            manufacturer_id=str(manufacturer_id),
            com_objects=[_decode_co(co) for co in content["com_objects"]],
            visible=[int(i) for i in content["visible"]],
        )
    except (ValueError, KeyError, TypeError, IndexError):
        return None
