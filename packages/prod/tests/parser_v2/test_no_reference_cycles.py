"""Parsing an application, building its evaluation tree and evaluating a device leave no reference
cycles: what they make is freed when it is no longer used, without the garbage collector, which
leaves long-lived objects alone (see xknxeditor.prod.gc_pause)."""

from __future__ import annotations

import collections
import copy
import gc
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .test_module_instance_comes_back import _XML as _MODULES_XML

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _applications() -> list[tuple[str, bytes]]:
    found = [
        (p.name, p.read_bytes()) for p in sorted(_FIXTURES.glob("*_application.xml"))
    ]
    # An application with module instances in module instances.
    found.append(("modules", _MODULES_XML.encode()))
    return found


def _use(xml: bytes) -> None:
    match = re.search(rb'RefId="(M-[0-9A-F]{4})"', xml)
    assert match is not None
    for app in parse_application_xml(xml, match.group(1).decode()):
        program, tree = app.resolved()
        ui = DynamicUI(program, tree_builder=tree)
        ui.ui()
        ui.active_parameter_driven_com_object_ref_ids()
        ui.parameter_ref_ids()
        ui.calculate_anew()


@pytest.fixture
def collector_off() -> Iterator[None]:
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


@pytest.mark.usefixtures("collector_off")
@pytest.mark.parametrize(
    ("name", "xml"), [pytest.param(*a, id=a[0]) for a in _applications()]
)
def test_using_an_application_leaves_no_cycles(name: str, xml: bytes) -> None:
    _use(xml)  # caches and imports of the first use
    gc.collect()
    gc.set_debug(gc.DEBUG_SAVEALL)
    try:
        _use(xml)
        gc.collect()
        left = collections.Counter(type(o).__qualname__ for o in gc.garbage)
    finally:
        gc.set_debug(0)
        gc.garbage.clear()
    assert not left, f"{name}: {left.most_common(10)}"


def test_a_copied_state_has_its_module_instances_in_the_copy() -> None:
    xml = dict(_applications())["modules"]
    match = re.search(rb'RefId="(M-[0-9A-F]{4})"', xml)
    assert match is not None
    program, tree = parse_application_xml(xml, match.group(1).decode())[0].resolved()
    ui = DynamicUI(program, tree_builder=tree)
    ui.ui()
    state = ui._state  # pyright: ignore[reportPrivateUsage]
    copied = copy.deepcopy(state)
    children = copied.module_children()
    assert children
    for child in children:
        assert child._parent is copied  # pyright: ignore[reportPrivateUsage]
        for grandchild in child.module_children():
            assert grandchild._parent is child  # pyright: ignore[reportPrivateUsage]
