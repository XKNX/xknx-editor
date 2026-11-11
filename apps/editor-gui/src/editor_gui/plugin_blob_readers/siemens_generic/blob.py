"""Reader for the settings the Siemens ETS3 Generic Plug-in stores in a device's BinaryData.

The plug-in keeps one XML document per device (UTF-16, usually without a byte order mark):

- ``CurrentSettings``: every setting as a ``ParameterInstanceRef`` with a symbolic ``Value``
  (enumeration names such as ``yn_Yes``, or plain numbers);
- ``DownloadedSettings``: what was last downloaded, with a ``MemoryImage`` (base64) of the
  parameter memory and the time of that download.
"""

from __future__ import annotations

import base64
import binascii
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PluginSettings:
    """The parsed BinaryData of one device."""

    application_id: str  # CurrentSettings Name: the description's ApplicationProgram Id
    values: dict[str, str]  # ParameterRef id -> stored value
    memory_image: bytes | None  # the last downloaded parameter memory, None if never
    modified: str = ""  # CurrentSettings LastModified
    last_download: str = ""  # DownloadedSettings LastDownload


def decode_text(data: bytes) -> str | None:
    """The XML text of ``data``: UTF-16 (with or without BOM) or UTF-8; None if neither."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    elif len(data) >= 2 and data[1] == 0 and data[0] != 0:
        encodings = ("utf-16-le",)
    elif len(data) >= 2 and data[0] == 0 and data[1] != 0:
        encodings = ("utf-16-be",)
    else:
        encodings = ("utf-8-sig",)
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def parse_settings(data: bytes) -> PluginSettings | None:
    """The plug-in settings in ``data``, or None when it holds none."""
    text = decode_text(data)
    if text is None:
        return None
    # ElementTree refuses a str that still declares an encoding; drop the declaration.
    if text.startswith("<?xml"):
        end = text.find("?>")
        text = text[end + 2 :] if end != -1 else text
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    current = root.find("CurrentSettings")
    if current is None:
        return None
    values: dict[str, str] = {}
    for pir in current.iterfind("ParameterInstanceRefs/ParameterInstanceRef"):
        ref = pir.get("RefId", "")
        value = pir.findtext("Value")
        if ref and value is not None:
            values[ref] = value
    image: bytes | None = None
    last_download = ""
    downloaded = root.find("DownloadedSettings")
    if downloaded is not None:
        last_download = downloaded.get("LastDownload", "")
        memory = downloaded.find("MemoryImage")
        encoded = memory.get("Data") if memory is not None else None
        if encoded:
            try:
                image = base64.b64decode(encoded, validate=False)
            except (binascii.Error, ValueError):
                image = None
    return PluginSettings(
        application_id=current.get("Name", ""),
        values=values,
        memory_image=image,
        modified=current.get("LastModified", ""),
        last_download=last_download,
    )
