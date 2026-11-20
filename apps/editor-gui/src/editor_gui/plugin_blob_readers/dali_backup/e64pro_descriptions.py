"""Standalone XML and line-based text exchange for controller descriptions."""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from editor_gui.plugins.project.strings import S

ROOTS = {
    "groups": "GRP_TEXT",
    "ecgs": "ECG_TEXT",
    "mds": "MD_TEXT",
    "pbs": "PB_TEXT",
    "gis": "GI_TEXT",
}


def entries(root: ET.Element, section: str) -> dict[int, ET.Element]:
    if section not in ROOTS:
        raise ValueError(S.E64_INVALID_SELECTION)
    result: dict[int, ET.Element] = {}
    for item in root.findall(section + "/*"):
        index = item.get("index", "")
        if not index.isascii() or not index.isdecimal() or int(index) in result:
            raise ValueError(S.E64_INVALID_SELECTION)
        result[int(index)] = item
    return dict(sorted(result.items()))


def export_descriptions(data: bytes, section: str, format: str = "xml") -> bytes:
    """Export one controller category; XML numbers are one-based."""
    from .e64pro import document

    items = entries(document(data), section)
    if format == "xml":
        root = ET.Element(ROOTS[section])
        for index, item in items.items():
            ET.SubElement(
                root,
                "text",
                index=str(index + 1),
                description=item.get("description", ""),
            )
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if format != "txt":
        raise ValueError(S.E64_INVALID_VALUE)
    if list(items) != list(range(len(items))):
        raise ValueError(S.E64_DESCRIPTION_TEXT_LOSS)
    lines: list[str] = []
    for item in items.values():
        value = item.get("description", "")
        if value == "''" or "\n" in value or "\r" in value or value != value.rstrip():
            raise ValueError(S.E64_DESCRIPTION_TEXT_LOSS)
        lines.append(value or "''")
    return ("\r\n".join(lines) + ("\r\n" if lines else "")).encode("utf-8-sig")


def parse_descriptions(
    root: ET.Element, section: str, text: str, format: str
) -> dict[str, str]:
    """Read a category's imported descriptions for validation by the shared editor."""

    items = entries(root, section)
    changes: dict[int, str] = {}
    if format == "xml":
        try:
            source = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ValueError(S.E64_INVALID_VALUE) from exc
        if source.tag != ROOTS[section]:
            raise ValueError(S.E64_INVALID_SELECTION)
        for item in source:
            index = item.get("index", "")
            if item.tag != "text" or not index.isascii() or not index.isdecimal():
                raise ValueError(S.E64_INVALID_SELECTION)
            number = int(index) - 1
            if number not in items or number in changes:
                raise ValueError(S.E64_INVALID_SELECTION)
            changes[number] = item.get("description", "")
    elif format == "txt":
        content = text.removeprefix("\ufeff")
        lines = re.split(r"\r\n|\r|\n", content) if content else []
        if lines and lines[-1] == "":
            lines.pop()
        for index, line in enumerate(lines):
            if index not in items:
                raise ValueError(S.E64_INVALID_SELECTION)
            value = line.rstrip()
            changes[index] = "" if value == "''" or not value.strip() else value
    else:
        raise ValueError(S.E64_INVALID_VALUE)
    return {str(index): value for index, value in changes.items()}
