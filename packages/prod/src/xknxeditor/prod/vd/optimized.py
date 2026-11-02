"""The optimized download plugins of Hager, which the converter replaces.

The load procedures of such a program reset the device instead of calling its function property
52, the plugin's baggages leave the program, and the default data the plugin shipped
(``Data_<number>v<version>_….xml`` next to its first baggage) becomes the ex-factory image of the
program's Hardware2Programs (``CheckSums``, ``LoadedImage``).
"""

from __future__ import annotations

import base64
import re
import struct
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from .deflate import deflate
from .xmlout import El

if TYPE_CHECKING:
    from .baggage import BaggageDoc
    from .context import Context
    from .hardware import HardwareDoc
    from .replacers import Replacer

# the object of the function property per mask
_OBJECT_INDEX = {"MV-0701": "5", "MV-07B0": "6"}
_SERIALIZE_VERSION = 65537


def replace_load_controls(procedures: list[El], mask: str) -> None:
    """The calls of function property 52 that the plugin handled become master resets."""
    index = _OBJECT_INDEX.get(mask)
    if index is None:
        raise ValueError("Unexpected mask version while replacing load controls")
    for proc in procedures:
        for i, item in enumerate(proc.children):
            if (
                item.tag != "LdCtrlInvokeFunctionProp"
                or (item.get("ObjIdx") or "0") != index
                or item.get("PropId") != "52"
            ):
                continue
            data = item.get("InlineData")
            applies = item.get("AppliesTo") or "auto"
            if data == "04" and applies == "full,grp":
                code = "6"
            elif data == "10" and applies == "full,par":
                code = "5"
            else:
                continue
            proc.children[i] = El(
                "LdCtrlMasterReset",
                {"AppliesTo": applies, "EraseCode": code, "ChannelNumber": "0"},
            )


def _hex(text: str, maximum: int) -> int:
    """A hexadecimal number as the converter parses it (an optional ``0x`` prefix)."""
    digits = text[2:] if text[:2].lower() == "0x" else text
    if not re.fullmatch(r"[0-9A-Fa-f]+", digits) or int(digits, 16) > maximum:
        raise ValueError(f"Could not parse {text!r} as a hexadecimal number")
    return int(digits, 16)


def _hex16(text: str | None) -> int:
    """A hexadecimal attribute; none is 0."""
    return 0 if text is None else _hex(text, 0xFFFF)


def _number(text: str | None) -> int:
    """A decimal attribute, cut to 16 bits."""
    if text is None or not re.fullmatch(r"\s*\d+\s*", text) or int(text) > 0xFFFFFFFF:
        raise ValueError(f"Not a number: {text!r}")
    return int(text) & 0xFFFF


def ex_factory_image(data: bytes) -> tuple[bytes, bytes]:
    """``CheckSums`` and ``LoadedImage`` of a ``DefaultData`` file of the plugin."""
    root = ET.fromstring(data)
    # (the root in any namespace; its segments only without one)
    if root.tag.rpartition("}")[2] != "DefaultData":
        raise ValueError("Not the expected XML format")
    checksums = struct.pack(
        "<IiiIHHIi",
        _SERIALIZE_VERSION,
        3,
        1,
        5,
        0,
        53,
        0,
        4,
    ) + struct.pack(
        ">HH", _hex16(root.get("ParameterCRC")), _hex16(root.get("ObjectTableCRC"))
    )
    segments = [s for s in root if s.tag == "Segment"]
    image = bytearray(struct.pack("<Ii", _SERIALIZE_VERSION, len(segments)))
    for segment in segments:
        space = segment.get("AddressSpace")
        if space == "StandardMemory":
            image += struct.pack("<IHH", 1, 0, 0)
        elif space == "RelativeMemory":
            image += struct.pack("<IHH", 12, _number(segment.get("ObjectIdx")), 7)
        elif space == "AppProperty":
            image += struct.pack(
                "<IHH",
                5,
                _number(segment.get("ObjectIdx")),
                _number(segment.get("PropertyId")),
            )
        else:
            raise ValueError(f"Unsupported AddressSpace {space or ''}")
        image += struct.pack("<I", _hex16(segment.get("Offset")))
        element = segment.find("Data")
        if element is None:
            raise ValueError("Segment without Data")
        text = "".join(element.itertext())
        values = bytes(_hex(part, 0xFF) for part in text.split(" "))
        image += struct.pack("<iB", len(values), 1) + values
    return checksums, deflate(bytes(image))


def _target_path(
    replacer: Replacer, application_id: str, doc: BaggageDoc | None
) -> str:
    """The folder of the program's first removed baggage, where its data is."""
    removed = replacer.removed.get(application_id)
    if not removed:
        raise ValueError(
            f"application program extension was not patched {application_id}"
        )
    baggages = doc.baggages if doc is not None else []
    baggage = next((b for b in baggages if b.get("Id") == removed[0]), None)
    if baggage is None:
        raise ValueError(f"Baggage {removed[0]} cannot be found")
    return baggage.get("TargetPath") or ""


def _data_file(doc: BaggageDoc, target_path: str, prefix: str) -> bytes | None:
    """The first file (in file system order) of the folder whose name starts with
    ``prefix``. (The folder exists: the baggage's file was written into it.)"""
    key = "/".join(p for p in re.split(r"[\\/]+", target_path) if p).lower()
    folder = doc.folders.get(key, key)
    base = f"{doc.manufacturer}/Baggages/{folder + '/' if folder else ''}"
    files = {
        path[len(base) :]: content
        for path, content in doc.files.items()
        if path.lower().startswith(base.lower()) and "/" not in path[len(base) :]
    }
    for name in sorted(files, key=str.upper):
        if name.lower().startswith(prefix.lower()):
            return files[name]
    return None


def _hardware2programs(hardware: HardwareDoc, application_id: str) -> list[El]:
    found: list[El] = []
    for hw in hardware.hardware:
        container = hw.find("Hardware2Programs")
        for h2p in container.children if container is not None else []:
            if any(
                c.tag == "ApplicationProgramRef" and c.get("RefId") == application_id
                for c in h2p.children
            ):
                found.append(h2p)
    return found


def add_ex_factory_images(
    ctx: Context,
    replacer: Replacer,
    baggage_docs: list[BaggageDoc],
    hardware_docs: list[HardwareDoc],
) -> None:
    """Writes the plugin's default data of each replaced program into the Hardware2Programs
    that refer to it (in the hardware of the program's manufacturer).

    Where the converter stops the whole conversion (no plugin baggage, no folder, data it cannot
    read), only the image is left out and an error reported."""
    for hardware in hardware_docs:
        doc = next(
            (
                d
                for d in baggage_docs
                if d.manufacturer.lower() == hardware.manufacturer.lower()
            ),
            None,
        )
        for application_id in replacer.applications:
            if application_id[:6] != hardware.manufacturer:
                continue
            h2ps = _hardware2programs(hardware, application_id)
            try:
                target = _target_path(replacer, application_id, doc)
                assert doc is not None
                prefix = f"Data_{application_id[9:13]}v{application_id[14:16]}_"
                data = _data_file(doc, target, prefix)
                if data is None or not h2ps:
                    continue
                checksums, image = ex_factory_image(data)
            except (ValueError, ET.ParseError) as error:
                ctx.errors.append(
                    f"OptimizedDownloadDataFailed: no ex-factory image for {application_id}: "
                    f"{error}"
                )
                continue
            for h2p in h2ps:
                h2p["CheckSums"] = base64.b64encode(checksums).decode()
                h2p["LoadedImage"] = base64.b64encode(image).decode()
