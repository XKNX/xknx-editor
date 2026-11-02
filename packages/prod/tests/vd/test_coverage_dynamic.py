"""Communication objects and refs, and the dynamic part: pages, conditions, chooses."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from .test_coverage_helpers import (
    APP,
    Tables,
    base,
    com_object,
    every,
    find,
    param,
    ptype,
    run,
    tag,
    text,
    translations,
    value,
    xml,
)


def objects_tables() -> Tables:
    t = base()
    t["parameter_type"] = [ptype(100, 4, "t_enum", 2)]
    t["parameter_list_of_values"] = [
        value(100, 110, 0, "Off", 0),
        value(100, 111, 1, "On", 1),
    ]
    t["parameter"] = [
        param(200, 0, "Page", None, space=0, order=1),
        param(201, 1, "Mode", 100, space=2, address=0x4400, bit=0, order=2),
    ]
    t["communication_object"] = [
        com_object(300, 0, 1, "Switch", 0, dpt=(1, 1), priority=0),
        com_object(
            301,
            0,
            2,
            "Switch",
            7,
            dpt=(5, 1),
            priority=1,
            read=1,
            parameter=201,
            value=1,
        ),
        com_object(302, 0, 3, "Switch", 0, priority=2, trans=1, update=1, init=1),
        com_object(303, 1, 4, "Status", 15, dpt=(1, 9), function="Feedback"),
        com_object(304, 1, 5, "Status", 15, dpt=(9, -1), comm=0, write=0),
        com_object(305, 2, 6, "Odd", 20),
        com_object(306, 3, 7, "Plain", 8, parameter=201, value=0),
        com_object(307, None, 8, "Numberless", 1),
    ]
    t["text_attribute"] = [
        text(1, 1033, 20, 300, "Switch EN"),
        text(2, 1033, 22, 300, "On/off EN"),
        text(3, 1031, 20, 300, "Schalten"),
        text(4, 1033, 20, 301, "Switch 2 EN"),
        text(5, 1031, 20, 301, "Schalten 2"),
        text(6, 1031, 20, 302, "Schalten"),
        text(7, 1031, 22, 303, "Rückmeldung"),
    ]
    t["device_template_object"] = [
        {
            "DEVICE_OBJECT_ID": 1,
            "DEVICE_ID": 70,
            "OBJECT_ID": 300,
            "EIB_DATA_TYPE_CODE": 1,
            "EIB_DATA_SUBTYPE_CODE": 2,
        },
        {
            "DEVICE_OBJECT_ID": 2,
            "DEVICE_ID": 70,
            "OBJECT_ID": 306,
            "EIB_DATA_TYPE_CODE": None,
            "EIB_DATA_SUBTYPE_CODE": None,
        },
        {
            "DEVICE_OBJECT_ID": 3,
            "DEVICE_ID": 70,
            "OBJECT_ID": 302,
            "EIB_DATA_TYPE_CODE": 1,
            "EIB_DATA_SUBTYPE_CODE": 1,
        },
    ]
    return t


def _cond(
    ident: int,
    number: int,
    name: str,
    order: int,
    parent: int | None,
    cond: Any,
    **kw: Any,
) -> dict[str, Any]:
    """An unsigned byte parameter at 0x4400 + number."""
    return param(
        ident,
        number,
        name,
        100,
        space=2,
        address=0x4400 + number,
        bit=0,
        order=order,
        parent=parent,
        value=cond,
        **kw,
    )


def dynamic_tables() -> Tables:
    t = base()
    t["parameter_type"] = [ptype(100, 1, "t_u8", 8), ptype(101, 0, "t_page", 0)]
    t["parameter"] = [
        _cond(201, 1, "Before pages", 0, None, None),
        _cond(202, 2, "Hidden before pages", 0, None, None, high=0),
        param(210, 10, "Main", 101, space=0, order=10),
        _cond(211, 11, "Selector", 11, None, None),
        _cond(212, 12, "Shown on 1", 12, 211, 1),
        _cond(213, 13, "Shown on 2", 13, 211, 2),
        _cond(214, 14, "Also on 1", 14, 211, 1),
        _cond(215, 15, "Nested", 15, 212, 5),
        _cond(216, 16, "Default branch", 16, 211, None),
        param(220, 20, "Extra page", 101, space=0, order=20, parent=211, value=1),
        _cond(221, 21, "On extra page", 21, 211, 1),
        _cond(222, 22, "Deeper on extra page", 22, 214, 3),
        param(230, 30, "Deep page", 101, space=0, order=30, parent=212, value=4),
        _cond(231, 31, "On deep page", 31, 212, 4),
        param(240, 40, "Second deep page", 101, space=0, order=40, parent=212, value=4),
        _cond(250, 50, "Unordered", None, None, None),
        _cond(251, 51, "Unordered child", None, 250, 1),
    ]
    t["communication_object"] = [
        com_object(300, 0, 1, "Always", 0),
        com_object(301, 1, 2, "On 1", 0, parameter=211, value=1),
        com_object(302, 2, 3, "Nested", 0, parameter=215, value=7),
        com_object(303, 3, 4, "On extra page", 0, parameter=221, value=2),
        com_object(304, 4, 5, "Unordered", 0, parameter=251, value=0),
        com_object(305, 5, 6, "Default", 0, parameter=211, value=None),
    ]
    return t


def _app(tables: Tables) -> tuple[ET.Element, list[str], list[str]]:
    files, warnings, errors = run(tables)
    return xml(files, f"M-0083/{APP}.xml"), warnings, errors


def _tree(el: ET.Element) -> list[Any]:
    """The dynamic part as nested lists: (tag, id or test, children)."""
    out: list[Any] = []
    for c in el:
        key = (
            c.get("RefId")
            or c.get("ParamRefId")
            or c.get("test")
            or c.get("default")
            or ""
        )
        key = key.replace(f"{APP}_", "")
        out.append((tag(c), key, _tree(c)) if len(c) else (tag(c), key))
    return out


def _short(attrs: dict[str, str]) -> dict[str, str]:
    return {k: v.replace(f"{APP}_", "") for k, v in attrs.items()}


_FLAGS = {
    "ReadFlag": "Disabled",
    "WriteFlag": "Enabled",
    "CommunicationFlag": "Enabled",
    "TransmitFlag": "Disabled",
    "UpdateFlag": "Disabled",
    "ReadOnInitFlag": "Disabled",
}


def _object(number: int, name: str, size: str, **extra: str) -> dict[str, str]:
    attrs = {
        "Id": f"O-{number}",
        "Name": name,
        "Text": name,
        "Number": str(number),
        "FunctionText": extra.pop("FunctionText", f"{name} function"),
        "ObjectSize": size,
        **_FLAGS,
    }
    return attrs | extra


def test_com_objects() -> None:
    root, warnings, errors = _app(objects_tables())
    assert warnings == []
    assert [e.split(":")[0] for e in errors] == ["ComObjIllegalObjectType"] * 2
    table = find(root, ".//{}ComObjectTable")
    assert _short(dict(table.attrib)) == {"CodeSegment": "AS-4000", "Offset": "512"}
    # an object without number counts as number 0; of the rows of one number the biggest
    # type gives the object's size and datapoint type
    assert [_short(dict(o.attrib)) for o in table] == [
        _object(0, "Numberless", "1 Byte", DatapointType="DPST-5-1"),
        _object(
            1, "Status", "LegacyVarData", FunctionText="Feedback", DatapointType="DPT-1"
        ),
        _object(2, "Odd", "1 Bit"),
        _object(3, "Plain", "2 Bytes"),
    ]
    refs = [_short(dict(r.attrib)) for r in find(root, ".//{}ComObjectRefs")]
    assert refs == [
        {
            "Id": "O-0_R-8",
            "RefId": "O-0",
            "Tag": "8",
            "ObjectSize": "2 Bit",
            "DatapointType": "",
        },
        # the datapoint type of the device template is added
        {
            "Id": "O-0_R-1",
            "RefId": "O-0",
            "Text": "Switch EN",
            "Tag": "1",
            "FunctionText": "On/off EN",
            "Priority": "Alert",
            "ObjectSize": "1 Bit",
            "DatapointType": "DPST-1-1 DPST-1-2",
        },
        {
            "Id": "O-0_R-2",
            "RefId": "O-0",
            "Text": "Switch 2 EN",
            "Tag": "2",
            "FunctionText": "Switch function",
            "Priority": "High",
            "ReadFlag": "Enabled",
        },
        {
            "Id": "O-0_R-3",
            "RefId": "O-0",
            "Text": "Switch",
            "Tag": "3",
            "FunctionText": "Switch function",
            "Priority": "Alert",
            "ObjectSize": "1 Bit",
            "TransmitFlag": "Enabled",
            "UpdateFlag": "Enabled",
            "ReadOnInitFlag": "Enabled",
            "DatapointType": " DPST-1-1",
        },
        {"Id": "O-1_R-4", "RefId": "O-1", "Tag": "4"},
        {
            "Id": "O-1_R-5",
            "RefId": "O-1",
            "Tag": "5",
            "FunctionText": "Status function",
            "WriteFlag": "Disabled",
            "CommunicationFlag": "Disabled",
            "DatapointType": "DPT-9",
        },
        {"Id": "O-2_R-6", "RefId": "O-2", "Tag": "6"},
        {"Id": "O-3_R-7", "RefId": "O-3", "Tag": "7"},
    ]
    assert _tree(find(root, ".//{}Dynamic")) == [
        (
            "Channel",
            "",
            [
                ("ComObjectRefRef", "O-0_R-8"),
                ("ComObjectRefRef", "O-0_R-1"),
                ("ComObjectRefRef", "O-0_R-3"),
                ("ComObjectRefRef", "O-1_R-4"),
                ("ComObjectRefRef", "O-1_R-5"),
                ("ComObjectRefRef", "O-2_R-6"),
                (
                    "ParameterBlock",
                    "P-0_R-0",
                    [
                        ("ParameterRefRef", "P-1_R-1"),
                        (
                            "choose",
                            "P-1_R-1",
                            [
                                ("when", "1", [("ComObjectRefRef", "O-0_R-2")]),
                                ("when", "0", [("ComObjectRefRef", "O-3_R-7")]),
                            ],
                        ),
                    ],
                ),
            ],
        )
    ]
    assert translations(root, "de-DE") == {
        f"{APP}_O-0_R-1": {"Text": "Schalten"},
        f"{APP}_O-0_R-2": {"Text": "Schalten 2"},
        f"{APP}_O-0_R-3": {"Text": "Schalten"},
        f"{APP}_O-1": {"FunctionText": "Rückmeldung"},
    }
    assert translations(root, "en-US") == {
        f"{APP}_O-0_R-1": {"Text": "Switch EN", "FunctionText": "On/off EN"},
        f"{APP}_O-0_R-2": {"Text": "Switch 2 EN"},
    }


def system_b_tables() -> Tables:
    t = objects_tables()
    t["mask"][0]["MASK_VERSION"] = 0x7B0
    return t


def test_com_object_sizes_of_system_b() -> None:
    t = system_b_tables()
    root, _warnings, _errors = _app(t)
    sizes = [o.get("ObjectSize") for o in find(root, ".//{}ComObjectTable")]
    assert sizes == ["1 Byte", "5 Bytes", "1 Bit", "2 Bytes"]


def test_dynamic_part() -> None:
    root, warnings, errors = _app(dynamic_tables())
    assert (warnings, errors) == ([], [])
    blocks = {b.get("Id"): dict(b.attrib) for b in every(root, "ParameterBlock")}
    # parameters before the first page (and without display order) go to an extra block
    assert blocks[f"{APP}_PB-41"] == {
        "Id": f"{APP}_PB-41",
        "Name": "Added by Converter",
        "Text": "",
    }
    assert blocks[f"{APP}_PB-10"]["Name"] == "Main"
    assert _tree(find(root, ".//{}Dynamic")) == [
        (
            "Channel",
            "",
            [
                ("ComObjectRefRef", "O-0_R-1"),
                (
                    "ParameterBlock",
                    "",
                    [
                        ("ParameterRefRef", "P-1_R-1"),
                        ("ParameterRefRef", "P-50_R-50"),
                        (
                            "choose",
                            "P-50_R-50",
                            [
                                (
                                    "when",
                                    "1",
                                    [
                                        ("ParameterRefRef", "P-51_R-51"),
                                        (
                                            "choose",
                                            "P-51_R-51",
                                            [
                                                (
                                                    "when",
                                                    "0",
                                                    [("ComObjectRefRef", "O-4_R-5")],
                                                )
                                            ],
                                        ),
                                    ],
                                )
                            ],
                        ),
                    ],
                ),
                (
                    "ParameterBlock",
                    "P-10_R-10",
                    [
                        # a hidden parameter before the first page belongs to that page
                        ("ParameterRefRef", "P-2_R-2"),
                        ("ParameterRefRef", "P-11_R-11"),
                        (
                            "choose",
                            "P-11_R-11",
                            [
                                (
                                    "when",
                                    "1",
                                    [
                                        ("ComObjectRefRef", "O-1_R-2"),
                                        ("ParameterRefRef", "P-12_R-12"),
                                        ("ParameterRefRef", "P-14_R-14"),
                                        (
                                            "choose",
                                            "P-12_R-12",
                                            [
                                                (
                                                    "when",
                                                    "5",
                                                    [
                                                        (
                                                            "ParameterRefRef",
                                                            "P-15_R-15",
                                                        ),
                                                        (
                                                            "choose",
                                                            "P-15_R-15",
                                                            [
                                                                (
                                                                    "when",
                                                                    "7",
                                                                    [
                                                                        (
                                                                            "ComObjectRefRef",
                                                                            "O-2_R-3",
                                                                        )
                                                                    ],
                                                                )
                                                            ],
                                                        ),
                                                    ],
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                                ("when", "2", [("ParameterRefRef", "P-13_R-13")]),
                            ],
                        ),
                        # without a value the condition is a default branch of its own choose
                        (
                            "choose",
                            "P-11_R-11",
                            [
                                (
                                    "when",
                                    "true",
                                    [
                                        ("ComObjectRefRef", "O-5_R-6"),
                                        ("ParameterRefRef", "P-16_R-16"),
                                    ],
                                )
                            ],
                        ),
                    ],
                ),
                # conditional pages are chooses of the channel
                (
                    "choose",
                    "P-11_R-11",
                    [
                        (
                            "when",
                            "1",
                            [
                                (
                                    "ParameterBlock",
                                    "P-20_R-20",
                                    [
                                        ("ParameterRefRef", "P-21_R-21"),
                                        (
                                            "choose",
                                            "P-21_R-21",
                                            [
                                                (
                                                    "when",
                                                    "2",
                                                    [("ComObjectRefRef", "O-3_R-4")],
                                                )
                                            ],
                                        ),
                                        (
                                            "choose",
                                            "P-14_R-14",
                                            [
                                                (
                                                    "when",
                                                    "3",
                                                    [("ParameterRefRef", "P-22_R-22")],
                                                )
                                            ],
                                        ),
                                    ],
                                ),
                                (
                                    "choose",
                                    "P-12_R-12",
                                    [
                                        (
                                            "when",
                                            "4",
                                            [
                                                (
                                                    "ParameterBlock",
                                                    "P-30_R-30",
                                                    [("ParameterRefRef", "P-31_R-31")],
                                                ),
                                                ("ParameterBlock", "P-40_R-40"),
                                            ],
                                        )
                                    ],
                                ),
                            ],
                        )
                    ],
                ),
            ],
        )
    ]
