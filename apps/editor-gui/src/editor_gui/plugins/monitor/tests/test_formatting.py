"""Tests for the monitor's column texts: Info values, DPT names, names and building structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from xknx.dpt import DPTArray, DPTBinary

from editor_gui.plugins.monitor.formatting import (
    COLUMN_INDEX,
    BitFormat,
    EnumFormat,
    NumberFormat,
    ProjectIndex,
    build_index,
    decoded_value_text,
    dpt_text,
    format_cells,
    master_formats,
    medium_info_text,
    raw_value_text,
    record_cells,
)
from editor_gui.plugins.monitor.records import Mode, MonitorRecord, RecordKind


def test_raw_value_text() -> None:
    assert raw_value_text(DPTBinary(1)) == "$01"
    assert raw_value_text(DPTBinary(0x3F)) == "$3F"
    assert raw_value_text(DPTArray((0xFF,))) == "$FF"
    assert raw_value_text(DPTArray((0x42, 0x0F, 0x79, 0x32))) == "42 0F 79 32"


def test_decoded_values_with_unit_and_locale() -> None:
    assert decoded_value_text(DPTBinary(1), "DPST-1-1") == "On"
    assert decoded_value_text(DPTBinary(0), "DPST-1-1") == "Off"
    assert decoded_value_text(DPTArray((0xFF,)), "DPST-5-1") == "100,0%"
    assert decoded_value_text(DPTArray((0x80,)), "DPST-5-1", decimal=".") == "50.2%"
    assert (
        decoded_value_text(DPTArray((0x42, 0x0F, 0x79, 0x32)), "DPST-14-27")
        == "35,868 V"
    )
    assert decoded_value_text(DPTArray((0x0C, 0x1A)), "DPST-9-1") == "21,00 °C"
    assert decoded_value_text(DPTArray((0x01, 0x00)), "DPST-7-1") == "256 pulses"
    assert decoded_value_text(DPTArray((0x01,)), None) == ""


def test_master_data_formats_win() -> None:
    formats = {
        "DPST-1-8": BitFormat("Up", "Down"),
        "DPST-20-102": EnumFormat({1: "Comfort"}),
        "DPST-5-3": NumberFormat("°", 1.4117647),
    }
    assert decoded_value_text(DPTBinary(1), "DPST-1-8", formats) == "Down"
    assert decoded_value_text(DPTArray((1,)), "DPST-20-102", formats) == "Comfort"
    assert decoded_value_text(DPTArray((255,)), "DPST-5-3", formats) == "360,0 °"


def test_dpt_text() -> None:
    assert dpt_text("DPST-1-1") == "1.001 Switch"
    assert dpt_text("DPST-14-27") == "14.027 Electric Potential"
    assert dpt_text("DPT-9").startswith("9.xxx")
    assert dpt_text(None) == ""


@dataclass
class _Bit:
    cleared: str
    set: str


class FakeFormatBit(_Bit):
    pass


@dataclass
class _Format:
    choice: list[Any]


@dataclass
class _Subtype:
    id: str
    format: _Format


@dataclass
class _Subtypes:
    datapoint_subtype: list[_Subtype]


@dataclass
class _Type:
    datapoint_subtypes: _Subtypes


@dataclass
class _Types:
    datapoint_type: list[_Type]


@dataclass
class _Raw:
    datapoint_types: _Types


@dataclass
class _Master:
    raw: _Raw


def test_master_formats_reads_bit_formats() -> None:
    master = _Master(
        _Raw(
            _Types(
                [
                    _Type(
                        _Subtypes(
                            [
                                _Subtype(
                                    "DPST-1-1", _Format([FakeFormatBit("Aus", "Ein")])
                                )
                            ]
                        )
                    )
                ]
            )
        )
    )
    assert master_formats(master) == {"DPST-1-1": BitFormat("Aus", "Ein")}
    assert master_formats(None) == {}


@dataclass
class _Ga:
    id: int
    address: str
    name: str
    datapoint_type: str | None
    raw: int
    description: str = ""


@dataclass
class _Device:
    node_id: int
    individual_address: str
    display_name: str
    description: str = ""


@dataclass
class _Link:
    group_address_id: int


@dataclass
class _Function:
    name: str
    group_addresses: list[_Link]


@dataclass
class _SpaceDevice:
    id: int


@dataclass
class _Space:
    name: str
    functions: list[_Function] = field(default_factory=list[_Function])
    devices: list[_SpaceDevice] = field(default_factory=list[_SpaceDevice])
    children: list[_Space] = field(default_factory=list["_Space"])


def _index() -> ProjectIndex:
    room = _Space(
        "Living room",
        functions=[_Function("Ceiling light", [_Link(7)])],
        devices=[_SpaceDevice(3)],
    )
    return build_index(
        revision=1,
        group_addresses=[_Ga(7, "3/0/32", "PM 1, Output 1", "DPST-1-1", 0x1820)],
        devices=[_Device(3, "1.0.47", "RaumController")],
        spaces=[_Space("House", children=[room])],  # type: ignore[list-item]
    )


def test_cells_of_a_group_telegram() -> None:
    record = MonitorRecord(
        kind=RecordKind.TELEGRAM, ticks=0, raw=bytes.fromhex("2900BCE0102F1820010081")
    )
    record.number = 2
    cells = format_cells(record, _index())
    col = COLUMN_INDEX
    assert cells[col["number"]] == "2"
    assert cells[col["service"]] == "from bus"
    assert cells[col["prio"]] == "Low"
    assert cells[col["source_address"]] == "1.0.47"
    assert cells[col["source_name"]] == "RaumController"
    assert cells[col["destination_address"]] == "3/0/32"
    assert cells[col["destination_name"]] == "PM 1, Output 1"
    assert cells[col["building_part"]] == "Living room"
    assert cells[col["building_function"]] == "Ceiling light"
    assert cells[col["hop_count"]] == "6"
    assert cells[col["type"]] == "GroupValueWrite"
    assert cells[col["dpt"]] == "1.001 Switch"
    assert cells[col["info"]] == "On"
    assert cells[col["raw_value"]] == "$01"
    assert cells[col["message_code"]] == "L_Data.ind"
    assert cells[col["frame_format"]] == "Standard"


def test_building_function_precedes_building_part() -> None:
    assert COLUMN_INDEX["building_function"] < COLUMN_INDEX["building_part"]
    record = MonitorRecord(
        kind=RecordKind.TELEGRAM, ticks=0, raw=bytes.fromhex("2900BCE0102F1820010081")
    )
    cells = format_cells(record, _index())
    assert cells[COLUMN_INDEX["building_function"]] == "Ceiling light"
    assert cells[COLUMN_INDEX["building_part"]] == "Living room"
    sent = MonitorRecord(
        kind=RecordKind.TELEGRAM, ticks=0, raw=bytes.fromhex("1100B0600000102F010300")
    )
    cells = format_cells(sent, _index())
    assert cells[COLUMN_INDEX["building_function"]] == ""
    assert cells[COLUMN_INDEX["building_part"]] == "Living room"


def test_cells_of_an_individual_telegram_and_events() -> None:
    index = _index()
    sent = MonitorRecord(
        kind=RecordKind.TELEGRAM, ticks=0, raw=bytes.fromhex("1100B0600000102F010300")
    )
    cells = format_cells(sent, index)
    assert cells[COLUMN_INDEX["service"]] == "to bus"
    assert cells[COLUMN_INDEX["prio"]] == "System"
    assert cells[COLUMN_INDEX["destination_name"]] == "RaumController"
    assert cells[COLUMN_INDEX["building_part"]] == "Living room"
    start = MonitorRecord(
        kind=RecordKind.START,
        ticks=0,
        connection_name="IP Tunneling",
        mode=Mode.LINK_LAYER,
        host="HOST",
    )
    assert format_cells(start, index)[COLUMN_INDEX["info"]] == (
        "Recording was started, Host=HOST, Connection=IP Tunneling, Mode=LinkLayer"
    )
    stop = MonitorRecord(kind=RecordKind.STOP, ticks=0)
    assert format_cells(stop, index)[COLUMN_INDEX["info"]] == "Recording was stopped"


def test_cells_are_cached_per_key() -> None:
    record = MonitorRecord(
        kind=RecordKind.TELEGRAM, ticks=0, raw=bytes.fromhex("2900BCE0102F1820010081")
    )
    index = _index()
    first = record_cells(record, index, "a")
    assert record_cells(record, index, "a") is first
    assert "pm 1" in record.haystack
    assert record_cells(record, ProjectIndex(), "b") is not first


def test_optional_columns() -> None:
    index = build_index(
        revision=1,
        group_addresses=[
            _Ga(7, "3/0/32", "PM 1, Output 1", "DPST-1-1", 0x1820, "Hallway lights")
        ],
        devices=[_Device(3, "1.0.47", "RaumController", "Flush-mounted")],
        spaces=[],
    )
    record = MonitorRecord(
        kind=RecordKind.TELEGRAM,
        ticks=0,
        raw=bytes.fromhex("2900BCE0102F1820010081"),
        connection_name="IP Tunneling (192.168.1.10:3671 Udp)",
    )
    cells = format_cells(record, index)
    col = COLUMN_INDEX
    assert cells[col["interface"]] == "IP Tunneling (192.168.1.10:3671 Udp)"
    assert cells[col["source_description"]] == "Flush-mounted"
    assert cells[col["destination_description"]] == "Hallway lights"
    assert cells[col["medium_info"]] == ""
    assert medium_info_text(bytes.fromhex("2904010200AB")) == "00 AB"


@dataclass
class _UnloadedDevice:
    node_id: int
    individual_address: str
    display_name: str


def test_device_without_description() -> None:
    index = build_index(
        revision=1,
        group_addresses=[],
        devices=[_UnloadedDevice(3, "1.0.47", "RaumController")],
        spaces=[],
    )
    assert index.devices[0x102F].name == "RaumController"
    assert index.devices[0x102F].description == ""
