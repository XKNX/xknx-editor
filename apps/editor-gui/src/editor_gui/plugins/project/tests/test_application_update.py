"""Matching parameters and group objects by number on an application update."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.project.application_update import (
    carry_com_objects,
    carry_parameters,
)

OLD = "M-0083_A-00A4-32-234A"
NEW = "M-0083_A-00A4-33-1111"


class _Indexer:
    def __init__(self, refs: dict[str | None, dict[int, str]], none: set[str]) -> None:
        self.refs_by_number = refs
        self._none = none

    def type_of(self, ref: str) -> object:
        return object() if ref in self._none else SimpleNamespace()


def test_parameters_are_matched_by_ref_number() -> None:
    indexer = _Indexer(
        {
            None: {43: f"{NEW}_P-950_R-43", 7: f"{NEW}_P-7_R-7"},
            f"{NEW}_MD-1": {26: f"{NEW}_MD-1_P-21_R-26"},
        },
        none={f"{NEW}_P-7_R-7"},
    )
    carried = carry_parameters(
        [
            (f"{OLD}_P-943_R-43", "0"),
            (f"{OLD}_MD-1_M-1_MI-1_P-20_R-26", "1"),
            (f"{OLD}_P-7_R-7", "5"),
            (f"{OLD}_P-1_R-99", "2"),
            ("M-0083_A-0000-01-0000_P-1_R-1", "3"),
        ],
        OLD,
        NEW,
        cast(Any, indexer),
    )
    assert carried.values == [
        (f"{NEW}_P-950_R-43", "0"),
        (f"{NEW}_MD-1_M-1_MI-1_P-21_R-26", "1"),
    ]
    assert carried.dropped == 3


def _row(
    ref: str, instance: str, *, links: list[tuple[int, bool]], **fields: Any
) -> Any:
    return SimpleNamespace(
        ref_id=ref,
        instance_ref_id=instance,
        links=[SimpleNamespace(group_address_id=g, is_sending=s) for g, s in links],
        read_flag=None,
        write_flag=True,
        communication_flag=True,
        transmit_flag=False,
        update_flag=None,
        read_on_init_flag=None,
        read_flag_override=None,
        write_flag_override=True,
        communication_flag_override=None,
        transmit_flag_override=None,
        update_flag_override=None,
        read_on_init_flag_override=None,
        priority="High",
        datapoint_type_override="DPST-1-1",
        text_override="kept text",
        **fields,
    )


def test_group_objects_keep_settings_when_number_and_size_match() -> None:
    rows = [
        _row(f"{OLD}_O-144_R-1", "O-144_R-1", links=[(5, False), (6, True)]),
        _row(f"{OLD}_MD-1_O-2-0_R-1", "MD-1_M-1_MI-1_O-2-0_R-1", links=[(7, True)]),
        _row(f"{OLD}_O-146_R-2", "O-146_R-2", links=[(8, True)]),
        _row(f"{OLD}_O-150_R-3", "O-150_R-3", links=[(9, True)]),
    ]
    old_sizes = {
        f"{OLD}_O-144_R-1": "1 Bit",
        f"{OLD}_MD-1_M-1_MI-1_O-2-0_R-1": "1 Byte",
        f"{OLD}_O-146_R-2": "1 Bit",
    }
    carried = carry_com_objects(
        rows,
        old_sizes,
        OLD,
        [
            (f"{NEW}_O-200_R-1", "1 Bit"),
            (f"{NEW}_MD-1_M-1_MI-1_O-2-0_R-1", "1 Byte"),
            (f"{NEW}_O-146_R-2", "2 Bytes"),
            (f"{NEW}_O-150_R-3", "1 Bit"),
        ],
        NEW,
    )
    first, module, resized, inactive = carried.data
    assert first is not None and module is not None
    assert first["links"] == [[6, True], [5, False]]
    assert first["fields"]["priority"] == "High"
    assert first["fields"]["datapoint_type_override"] == "DPST-1-1"
    assert first["fields"]["write_flag_override"] is True
    assert "text_override" not in first["fields"]
    assert module["links"] == [[7, True]]
    assert resized is None and inactive is None
    assert (carried.kept, carried.dropped) == (2, 1)
