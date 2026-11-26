"""The parse cache writes the objects of this package's classes compactly (a call with their
attribute values instead of a state dict, which the unpickler keeps until the end of the load):
what is read back is the same graph, and an application read from the cache behaves as a parsed
one."""

from __future__ import annotations

import io
import pickle
import pickletools
import re
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from xknxeditor.namespaces import detect_version
from xknxeditor.namespaces.intermediate.knx import Knx
from xknxeditor.prod import parse_cache
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.data import to_ir
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

_FIXTURES = Path(__file__).parent / "fixtures"
_APPLICATIONS = sorted(_FIXTURES.glob("*_application.xml"))


class Base:
    __slots__ = ("__secret", "name")

    def __init__(self, name: str, secret: int) -> None:
        self.name = name
        self.__secret = secret

    def secret(self) -> int:
        return self.__secret


class Child(Base):
    __slots__ = ("extra", "other")


class Loose:
    """A class with a ``__dict__``, as the nodes of the evaluation trees."""

    def __init__(self, value: object) -> None:
        self.value = value


class Mixed(Loose):
    __slots__ = ("slot",)


class Colour(Enum):
    RED = "red"


class Reduced:
    __slots__ = ("value",)
    reduced = 0

    def __init__(self, value: int) -> None:
        self.value = value

    def __reduce__(self) -> tuple[type[Reduced], tuple[int]]:
        Reduced.reduced += 1
        return (Reduced, (self.value,))


class Stated:
    __slots__ = ("value",)

    def __getstate__(self) -> dict[str, object]:
        return {"value": self.value, "stated": True}

    def __setstate__(self, state: dict[str, object]) -> None:
        self.value = (state["value"], state["stated"])


@pytest.fixture(autouse=True)
def _this_module_is_compact(monkeypatch: pytest.MonkeyPatch) -> None:
    """The classes of this module are written compactly, as those of the package."""
    monkeypatch.setattr(parse_cache, "_PACKAGES", (*parse_cache._PACKAGES, __name__))  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(parse_cache, "_compact", {})


def _dumps(value: object) -> bytes:
    buffer = io.BytesIO()
    parse_cache._CompactPickler(buffer).dump(value)  # pyright: ignore[reportPrivateUsage]
    return buffer.getvalue()


def _round_trip(value: object) -> Any:
    return pickle.loads(_dumps(value))


def _opcodes(data: bytes) -> set[str]:
    return {opcode.name for opcode, _arg, _pos in pickletools.genops(data)}


def test_slots_come_back_without_a_state_dict() -> None:
    """Inherited and private slots are written, and the pickle holds no state to build from."""
    child = Child("child", 7)
    child.extra = [1, 2]
    child.other = None
    data = _dumps(child)
    assert not _opcodes(data) & {"BUILD", "EMPTY_DICT", "NEWOBJ"}

    loaded = pickle.loads(data)
    assert type(loaded) is Child
    assert (loaded.name, loaded.secret(), loaded.extra, loaded.other) == (
        "child",
        7,
        [1, 2],
        None,
    )


def test_a_slot_without_a_value_stays_without_one() -> None:
    child = Child("child", 1)
    child.other = "set"
    loaded = _round_trip(child)
    assert not hasattr(loaded, "extra")
    assert loaded.other == "set"
    assert loaded.secret() == 1


def test_objects_of_a_class_and_its_subclass_come_back_as_their_classes() -> None:
    loaded = _round_trip([Base("base", 1), Child("child", 2), Base("again", 3)])
    assert [type(item) for item in loaded] == [Base, Child, Base]
    assert [item.name for item in loaded] == ["base", "child", "again"]


def test_attributes_in_a_dict_and_in_slots_beside_it_come_back() -> None:
    mixed = Mixed([1])
    mixed.slot = "slot"
    loose = Loose({"key": mixed})
    loose.added = "later"  # pyright: ignore[reportAttributeAccessIssue]
    data = _dumps([loose, Loose(None), Mixed(2)])
    assert "BUILD" not in _opcodes(data)

    first, second, third = pickle.loads(data)
    assert type(first) is Loose and vars(first).keys() == {"value", "added"}
    assert first.added == "later"
    inner = first.value["key"]
    assert type(inner) is Mixed
    assert (inner.value, inner.slot) == ([1], "slot")
    assert second.value is None
    assert third.value == 2 and not hasattr(third, "slot")


def test_an_object_referenced_twice_is_one_object_after_loading() -> None:
    shared = Child("shared", 1)
    first = Child("first", 2)
    first.other = shared
    second = Loose(shared)
    loaded = _round_trip([first, second, shared])
    assert loaded[0].other is loaded[1].value is loaded[2]


def test_an_entry_with_reference_cycles_is_written_as_pickle_does(
    tmp_path: Path,
) -> None:
    """An object in a cycle would need itself before it is made from its values."""
    child = Child("child", 1)
    loose = Loose(child)
    child.other = loose
    loose.me = loose  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(RecursionError):
        _dumps([child, loose])
    entry = tmp_path / "entry.pkl"
    parse_cache._write_entry(entry, [child, loose])  # pyright: ignore[reportPrivateUsage]
    assert not list(tmp_path.glob("*.tmp"))
    loaded = pickle.loads(entry.read_bytes())
    assert loaded[0].other is loaded[1]
    assert loaded[1].value is loaded[0]
    assert loaded[1].me is loaded[1]


def test_enums_and_own_reductions_are_written_as_pickle_does() -> None:
    Reduced.reduced = 0
    stated = Stated()
    stated.value = 5
    loaded = _round_trip([Colour.RED, Reduced(3), stated])
    assert loaded[0] is Colour.RED
    assert Reduced.reduced == 1 and loaded[1].value == 3
    assert loaded[2].value == (5, True)
    for cls in (Colour, Reduced, Stated):
        assert parse_cache._compact_class(cls) is None  # pyright: ignore[reportPrivateUsage]


def test_classes_of_other_packages_are_written_as_pickle_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parse_cache, "_PACKAGES", ("xknxeditor.",))
    assert parse_cache._compact_class(Base) is None  # pyright: ignore[reportPrivateUsage]
    assert "BUILD" in _opcodes(_dumps(Child("child", 1)))


def test_a_class_whose_slots_changed_is_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Values must not go into other slots, nor a new slot stay without a value: such a pickle
    is not read (the cache parses again)."""
    data = _dumps(Child("child", 1))
    monkeypatch.setitem(parse_cache._slots, Child, ("name", "added"))  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(pickle.UnpicklingError, match="Child has changed"):
        pickle.loads(data)


def test_an_entry_written_with_other_slots_is_parsed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xml = (_FIXTURES / "fan_coil_application.xml").read_bytes()
    version = detect_version(xml)
    knx = parse_cache.cached_to_ir(xml, version, tmp_path)
    parsed: list[bytes] = []

    def parse(xml_bytes: bytes, version: str) -> object:
        parsed.append(xml_bytes)
        return to_ir(xml_bytes, version)

    monkeypatch.setattr(parse_cache, "to_ir", parse)
    assert parse_cache.cached_to_ir(xml, version, tmp_path) == knx
    assert not parsed
    monkeypatch.setitem(parse_cache._slots, Knx, ("project",))  # pyright: ignore[reportPrivateUsage]
    assert parse_cache.cached_to_ir(xml, version, tmp_path) == knx
    assert parsed == [xml]


def _texts(root: object) -> Iterator[str]:
    """The texts in the slots of the objects reachable from ``root``."""
    seen: set[int] = set()
    stack: list[Any] = [root]
    while stack:
        item = stack.pop()
        if id(item) in seen or isinstance(item, (str, int, float, bytes, Enum)):
            continue
        seen.add(id(item))
        if isinstance(item, list):
            stack.extend(item)
            continue
        for name in parse_cache._slot_names(type(item)):  # pyright: ignore[reportPrivateUsage]
            value = getattr(item, name, None)
            if isinstance(value, str):
                yield value
            else:
                stack.append(value)


def test_equal_texts_are_one_object_after_the_conversion_and_the_cache(
    tmp_path: Path,
) -> None:
    xml = (_FIXTURES / "fan_coil_application.xml").read_bytes()
    version = detect_version(xml)
    parsed = list(_texts(to_ir(xml, version)))
    assert len(parsed) > len(set(parsed))  # the application repeats texts
    assert len({id(text) for text in parsed}) == len(set(parsed))

    parse_cache.cached_to_ir(xml, version, tmp_path)
    cached = list(_texts(parse_cache.cached_to_ir(xml, version, tmp_path)))
    assert cached == parsed
    assert len({id(text) for text in cached}) == len(set(cached))


def _same_graph(first: object, second: object) -> None:
    """``first`` and ``second`` are graphs of the same types and values, with the same objects
    shared."""
    pairs: dict[int, int] = {}
    stack: list[tuple[Any, Any, str]] = [(first, second, "root")]
    while stack:
        a, b, where = stack.pop()
        assert type(a) is type(b), where
        if a is None or isinstance(
            a, (str, int, float, bytes, Enum, frozenset, set, type)
        ):
            assert a == b, where
            continue
        assert pairs.setdefault(id(a), id(b)) == id(b), f"sharing differs at {where}"
        if isinstance(a, (list, tuple)):
            stack.extend((x, y, f"{where}[]") for x, y in zip(a, b, strict=True))
        elif isinstance(a, dict):
            assert list(a) == list(b), where
            stack.extend((a[key], b[key], f"{where}[{key!r}]") for key in a)
        else:
            for name in parse_cache._slot_names(type(a)):  # pyright: ignore[reportPrivateUsage]
                assert hasattr(a, name) == hasattr(b, name), f"{where}.{name}"
                if hasattr(a, name):
                    stack.append(
                        (getattr(a, name), getattr(b, name), f"{where}.{name}")
                    )
            if hasattr(a, "__dict__"):
                stack.append((vars(a), vars(b), f"{where}.__dict__"))


@pytest.fixture
def tree_code() -> Iterator[None]:
    parse_cache.set_tree_code("compact test")
    try:
        yield
    finally:
        parse_cache.set_tree_code(None)


def _ui(app: Application) -> tuple[object, ...]:
    program, tree = app.resolved()
    ui = DynamicUI(program, tree_builder=tree)
    return (
        ui.ui(),
        ui.active_parameter_ref_ids(),
        ui.active_parameter_driven_com_object_ref_ids(),
        ui.instantiated_com_object_ref_ids(),
        ui.encode_to_memory(),
    )


@pytest.mark.usefixtures("tree_code")
@pytest.mark.parametrize("xml_path", _APPLICATIONS, ids=lambda path: path.stem)
def test_an_application_from_the_cache_is_the_parsed_one(
    tmp_path: Path, xml_path: Path
) -> None:
    """Parsed, cached with its trees and read back: the same IR and trees as a parse without
    the cache, and the same user interface, group objects and memory."""
    xml = xml_path.read_bytes()
    match = re.search(rb'RefId="(M-[0-9A-F]{4})"', xml)
    manufacturer = match.group(1).decode() if match else "M-00FA"
    parse_application_xml(xml, manufacturer, cache_dir=tmp_path)  # parsed, trees stored
    (entry,) = tmp_path.glob("ir_*.pkl")
    with entry.open("rb") as file:
        knx, code, trees = pickle.load(file)
    assert code == "compact test"

    fresh = to_ir(xml, detect_version(xml))
    _same_graph(knx, fresh)
    assert knx == fresh
    assert trees.keys() == {program.id for program in _programs(fresh)}
    # The trees as pickle writes them (they hold ids of objects of the process that built
    # them, as keys only).
    built = (
        fresh,
        {program.id: DynamicTreeBuilder(program) for program in _programs(fresh)},
    )
    _same_graph(_round_trip(built), pickle.loads(pickle.dumps(built)))

    cached = parse_application_xml(xml, manufacturer, cache_dir=tmp_path)
    uncached = parse_application_xml(xml, manufacturer)
    assert [_ui(app) for app in cached] == [_ui(app) for app in uncached]


def _programs(knx: Any) -> Iterator[Any]:
    for manufacturer in knx.manufacturer_data.manufacturer:
        if manufacturer.application_programs is not None:
            yield from manufacturer.application_programs.application_program
