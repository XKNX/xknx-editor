"""A coupler used as Security Proxy: its Security Proxy Group Key Table and router settings."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from xknxeditor.download.image import (
    ImageError,
    MemorySegment,
    _keep_proxy_memory,  # pyright: ignore[reportPrivateUsage]
    _validate_security_capacity,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.download.secure_config import (
    PID_SECURITY_PROXY_GRP_KEY_TABLE,
    ROUTER_OBJECT,
    GroupSecurity,
    ProxyEntry,
    proxy_element,
    security_tables,
    with_security_proxy,
)
from xknxeditor.namespaces.intermediate import (
    LdCtrlDisconnect,
    LdCtrlLoad,
    LdCtrlLoadCompleted,
    LdCtrlRestart,
    LdCtrlUnload,
    LdCtrlWriteProp,
)
from xknxeditor.prod import Application

_KEY = bytes(range(16))
_ENTRIES = [ProxyEntry(0x0A03, _KEY, primary=False), ProxyEntry(0x0801, _KEY, True)]


def test_an_element_holds_address_key_and_side() -> None:
    assert proxy_element(_ENTRIES[1]) == b"\x08\x01" + _KEY + b"\x83"
    assert proxy_element(_ENTRIES[0]) == b"\x0a\x03" + _KEY + b"\x43"


def test_a_proxy_gets_tables_without_group_keys_of_its_own() -> None:
    tables = security_tables(
        [], [], GroupSecurity(keys={}, senders=[(0x1105, 4)], proxy=_ENTRIES)
    )
    assert tables is not None
    # In group address order; its senders go into its Security Individual Address Table.
    assert tables.proxy == (_ENTRIES[1], _ENTRIES[0])
    assert tables.individual_addresses == ((0x1105, 4),)
    assert security_tables([], [], GroupSecurity(keys={})) is None


def _router_config(prop_id: int, value: int) -> LdCtrlWriteProp:
    return LdCtrlWriteProp(
        obj_type=ROUTER_OBJECT,
        occurrence=0,
        prop_id=prop_id,
        start_element=1,
        count=1,
        verify=True,
        inline_data=bytes([value]),
    )


def test_the_table_goes_before_the_first_load_completed() -> None:
    controls: list[object] = [
        LdCtrlUnload(obj_type=1),
        _router_config(52, 0x11),
        _router_config(55, 0x22),
        LdCtrlLoadCompleted(obj_type=1),
        LdCtrlRestart(),
    ]
    result = with_security_proxy(controls, [ProxyEntry(0x0801, _KEY, True)])
    kinds = [
        (type(c).__name__, getattr(c, "obj_type", None), getattr(c, "occurrence", None))
        for c in result
    ]
    assert kinds[:3] == [
        ("LdCtrlUnload", 1, 0),
        ("LdCtrlUnload", ROUTER_OBJECT, 0),
        ("LdCtrlUnload", ROUTER_OBJECT, 1),
    ]
    first_completed = next(
        i for i, c in enumerate(result) if isinstance(c, LdCtrlLoadCompleted)
    )
    table = result[first_completed - 4 : first_completed]
    assert [type(c) for c in table[:2]] == [LdCtrlLoad, LdCtrlLoad]
    clear, element = cast(list[LdCtrlWriteProp], table[2:])
    assert (clear.prop_id, clear.start_element, clear.inline_data) == (
        PID_SECURITY_PROXY_GRP_KEY_TABLE,
        0,
        b"\x00\x00",
    )
    assert (element.start_element, element.inline_data) == (
        1,
        proxy_element(ProxyEntry(0x0801, _KEY, True)),
    )
    # The coupler routes group telegrams as a proxy, whatever its parameters say.
    configs = {
        c.prop_id: c.inline_data
        for c in result
        if isinstance(c, LdCtrlWriteProp) and c.prop_id in (52, 55)
    }
    assert configs == {52: b"\x77", 55: b"\x1f"}


def test_router_objects_unloaded_by_the_procedure_are_not_unloaded_again() -> None:
    controls: list[object] = [
        LdCtrlUnload(obj_type=ROUTER_OBJECT),
        LdCtrlDisconnect(),
    ]
    result = with_security_proxy(controls, [])
    assert sum(isinstance(c, LdCtrlUnload) for c in result) == 1
    assert not any(isinstance(c, LdCtrlLoad) for c in result)
    # Without a LoadCompleted: before the disconnect; an empty table is cleared only.
    assert isinstance(result[-1], LdCtrlDisconnect)
    assert cast(LdCtrlWriteProp, result[-2]).start_element == 0


def test_the_configuration_memory_is_kept() -> None:
    segments = [
        MemorySegment(0x0100, bytes(0x30)),
        MemorySegment(0x0200, bytes(4)),
    ]
    kept = _keep_proxy_memory(segments)
    assert kept[1] is segments[1]
    mask = kept[0].mask
    assert mask is not None
    assert mask[0x1B] == 0 and mask[0x1C] == 0
    assert mask.count(0) == 2


def test_the_table_must_fit_the_device() -> None:
    tables = security_tables(
        [], [], GroupSecurity(keys={}, proxy=[_ENTRIES[0], _ENTRIES[1]])
    )
    assert tables is not None
    application = cast(
        Application,
        SimpleNamespace(
            program=SimpleNamespace(
                max_security_group_key_table_entries=0,
                max_security_individual_address_entries=0,
                max_security_p2_pkey_table_entries=0,
                max_security_proxy_group_key_table_entries=1,
            )
        ),
    )
    with pytest.raises(ImageError, match="Security Proxy Group Key Table"):
        _validate_security_capacity(application, tables)
