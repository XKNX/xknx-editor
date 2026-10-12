"""Reading and writing recordings as CommunicationLog XML (namespace telegrams/01)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from editor_gui.plugins.monitor.frames import MESSAGE_CODE_NAMES
from editor_gui.plugins.monitor.records import (
    TICKS_PER_SECOND,
    Mode,
    MonitorRecord,
    RecordKind,
    datetime_to_ticks,
    ticks_to_datetime,
)

NAMESPACE = "http://knx.org/xml/telegrams/01"
_TIMESTAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$"
)


def format_timestamp(ticks: int) -> str:
    """UTC ISO-8601 with up to 7 fractional digits (trailing zeros dropped) and ``Z``."""
    seconds, fraction = divmod(ticks, TICKS_PER_SECOND)
    moment = ticks_to_datetime(seconds * TICKS_PER_SECOND)
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    digits = f"{fraction:07d}".rstrip("0")
    if digits:
        text += f".{digits}"
    return text + "Z"


def parse_timestamp(text: str) -> int:
    """Ticks of an ``xs:dateTime``; one without a zone is taken as local time."""
    match = _TIMESTAMP.match(text.strip())
    if match is None:
        raise ValueError(f"invalid timestamp: {text!r}")
    year, month, day, hour, minute, second = (int(match[i]) for i in range(1, 7))
    fraction = (match[7] or "0")[:7].ljust(7, "0")
    zone = match[8]
    moment = datetime(year, month, day, hour, minute, second)
    if zone == "Z":
        moment = moment.replace(tzinfo=UTC)
    elif zone:
        sign = 1 if zone[0] == "+" else -1
        offset = timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
        moment = moment.replace(tzinfo=timezone(sign * offset))
    return datetime_to_ticks(moment) + int(fraction)


def export_xml(records: Iterable[MonitorRecord]) -> bytes:
    """The records as a CommunicationLog document."""
    ET.register_namespace("", NAMESPACE)
    root = ET.Element(f"{{{NAMESPACE}}}CommunicationLog")
    for record in records:
        element = _element(record)
        if element is not None:
            root.append(element)
    ET.indent(root, "  ")
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n").encode("utf-8")


def _element(record: MonitorRecord) -> ET.Element | None:
    def make(tag: str) -> ET.Element:
        element = ET.Element(f"{{{NAMESPACE}}}{tag}")
        element.set("Timestamp", format_timestamp(record.ticks))
        return element

    kind = record.kind
    if kind is RecordKind.TELEGRAM:
        if not record.raw:
            return None
        element = make("Telegram")
        if record.connection_name:
            element.set("ConnectionName", record.connection_name)
        service = MESSAGE_CODE_NAMES.get(record.raw[0])
        if service:
            element.set("Service", service)
        element.set("FrameFormat", "CommonEmi")
        element.set("RawData", record.raw.hex().upper())
        return element
    if kind is RecordKind.START:
        element = make("RecordStart")
        if record.connection_name:
            element.set("ConnectionName", record.connection_name)
        element.set("Mode", record.mode.value)
        for name, value in (
            ("Host", record.host),
            ("ConnectionOptions", record.connection_options),
            ("ConnectorType", record.connector_type),
            ("MediumType", record.medium_type),
        ):
            if value:
                element.set(name, value)
        return element
    if kind is RecordKind.STOP:
        element = make("RecordStop")
        if record.connection_name:
            element.set("ConnectionName", record.connection_name)
        return element
    if kind in (RecordKind.CONNECTION_ESTABLISHED, RecordKind.CONNECTION_LOST):
        element = make("Connection")
        if record.connection_name:
            element.set("ConnectionName", record.connection_name)
        element.set(
            "State",
            "Established" if kind is RecordKind.CONNECTION_ESTABLISHED else "Lost",
        )
        return element
    element = make("Comment")
    element.text = record.text
    return element


@dataclass(frozen=True, slots=True)
class ImportResult:
    records: list[MonitorRecord]
    # Telegrams in a frame format other than cEMI, which are not shown.
    skipped: int


def import_xml(data: bytes) -> ImportResult:
    """Read a CommunicationLog document. Unknown elements are ignored; raises ``ValueError``
    when the document is not a CommunicationLog."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as err:
        raise ValueError(str(err)) from err
    if _local(root.tag) != "CommunicationLog":
        raise ValueError("not a CommunicationLog document")
    records: list[MonitorRecord] = []
    skipped = 0
    for element in root:
        tag = _local(element.tag)
        stamp = element.get("Timestamp")
        if stamp is None:
            continue
        try:
            ticks = parse_timestamp(stamp)
        except ValueError:
            continue
        name = element.get("ConnectionName", "")
        if tag == "Telegram":
            raw_hex = element.get("RawData", "")
            if element.get("FrameFormat", "CommonEmi") != "CommonEmi" or not raw_hex:
                skipped += 1
                continue
            try:
                raw = bytes.fromhex(raw_hex)
            except ValueError:
                skipped += 1
                continue
            records.append(
                MonitorRecord(
                    kind=RecordKind.TELEGRAM, ticks=ticks, raw=raw, connection_name=name
                )
            )
        elif tag == "RecordStart":
            try:
                mode = Mode(element.get("Mode", "Unknown"))
            except ValueError:
                mode = Mode.UNKNOWN
            records.append(
                MonitorRecord(
                    kind=RecordKind.START,
                    ticks=ticks,
                    connection_name=name,
                    mode=mode,
                    host=element.get("Host", ""),
                    connection_options=element.get("ConnectionOptions", ""),
                    connector_type=element.get("ConnectorType", ""),
                    medium_type=element.get("MediumType", ""),
                )
            )
        elif tag == "RecordStop":
            records.append(
                MonitorRecord(kind=RecordKind.STOP, ticks=ticks, connection_name=name)
            )
        elif tag == "Connection":
            state = element.get("State", "")
            kind = (
                RecordKind.CONNECTION_ESTABLISHED
                if state == "Established"
                else RecordKind.CONNECTION_LOST
            )
            records.append(MonitorRecord(kind=kind, ticks=ticks, connection_name=name))
        elif tag == "Comment":
            records.append(
                MonitorRecord(
                    kind=RecordKind.COMMENT, ticks=ticks, text=element.text or ""
                )
            )
    return ImportResult(records, skipped)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
