"""A search of a device's settings seeds UIs with the same values again and again: within the
search each is seeded once (and only read), what the calculations make of the same values is
computed once, and a seeding whose second build would start from the state the first one
started from comes back with the first one, evaluated already. None of it changes the result."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from test_module_decode_rounds import _EXTRA, _FIRST, _FIRST_2, _SECOND, _SWITCH, _XML

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.recover import seed_dynamic_ui


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _segments(application: Application, **values: str) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id -> value)."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values.items():
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


def _read(application: Application) -> dict[str, str]:
    """The values decoded from a device with both channels' settings set."""
    segments = _segments(
        application, **{_SWITCH: "1", _FIRST: "5", _SECOND: "7", _FIRST_2: "4"}
    )
    values, _unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}
    )
    return values


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """The evaluations of UIs from now on."""
    count = [0]
    build = DynamicUI._build_ui  # pyright: ignore[reportPrivateUsage]

    def counted(self: DynamicUI, *, capture_active: bool) -> None:
        count[0] += 1
        build(self, capture_active=capture_active)

    monkeypatch.setattr(DynamicUI, "_build_ui", counted)
    yield count


def _same_ui(a: DynamicUI, b: DynamicUI) -> None:
    assert a.ui() == b.ui()
    assert a.active_parameter_ref_ids() == b.active_parameter_ref_ids()
    assert a.get_module_instances() == b.get_module_instances()
    assert a.encode_to_memory() == b.encode_to_memory()


def test_a_seeding_starting_where_the_first_build_did_comes_back_evaluated(
    application: Application, builds: list[int]
) -> None:
    # The values of both module instances: the second build would start from the state the
    # first one started from.
    values = _read(application)
    builds[0] = 0
    evaluated = seed_dynamic_ui(application, values, evaluated=True)
    assert evaluated is not None
    assert builds[0] == 1
    evaluated.ui()
    assert builds[0] == 1
    plain = seed_dynamic_ui(application, values)
    assert plain is not None
    plain.ui()
    assert builds[0] == 3
    _same_ui(evaluated, plain)


def test_instances_without_values_need_no_second_build(
    application: Application, builds: list[int]
) -> None:
    # No value of a module instance: the first build makes the instances, the second would
    # start with them holding nothing, which the evaluation makes alike where it reaches them.
    seeded = seed_dynamic_ui(application, {_EXTRA: "1"}, evaluated=True)
    assert seeded is not None
    assert builds[0] == 1
    seeded.ui()
    assert builds[0] == 1
    plain = seed_dynamic_ui(application, {_EXTRA: "1"})
    assert plain is not None
    plain.ui()
    assert builds[0] == 3
    _same_ui(seeded, plain)


def test_a_seeding_starting_otherwise_is_evaluated_again(
    application: Application,
    builds: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A second build that would not start alike (here: every layout told apart) evaluates
    # the UI seeded with the instances the first build made.
    monkeypatch.setattr(
        DynamicUI, "layouts_alike", staticmethod(lambda _first, _second: False)
    )
    seeded = seed_dynamic_ui(application, {_EXTRA: "1"}, evaluated=True)
    assert seeded is not None
    assert builds[0] == 1
    seeded.ui()
    assert builds[0] == 2
    plain = seed_dynamic_ui(application, {_EXTRA: "1"})
    assert plain is not None
    _same_ui(seeded, plain)


def test_within_a_search_the_same_values_give_the_same_ui(
    application: Application,
) -> None:
    values = _read(application)
    assert parameters._seeded(  # pyright: ignore[reportPrivateUsage]
        application, values, None
    ) is not parameters._seeded(  # pyright: ignore[reportPrivateUsage]
        application, values, None
    )
    cache = parameters._SearchCache(application)  # pyright: ignore[reportPrivateUsage]
    parameters._SEARCH.cache = cache  # pyright: ignore[reportPrivateUsage]
    try:
        first = parameters._seeded(  # pyright: ignore[reportPrivateUsage]
            application, values, None
        )
        assert first is parameters._seeded(  # pyright: ignore[reportPrivateUsage]
            application, values, None
        )
        other = parameters._seeded(  # pyright: ignore[reportPrivateUsage]
            application, {**values, _EXTRA: "1"}, None
        )
        assert other is not first
        # What the calculations make of the values is computed once, handed out as a copy.
        calculated = parameters._with_calculated(  # pyright: ignore[reportPrivateUsage]
            application, values, None
        )
        calculated["changed"] = "1"
        again = parameters._with_calculated(  # pyright: ignore[reportPrivateUsage]
            application, values, None
        )
        assert "changed" not in again
    finally:
        parameters._SEARCH.cache = None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("settings", "linked"),
    [
        ({_SWITCH: "1", _FIRST: "5", _SECOND: "7", _FIRST_2: "4"}, {1}),
        ({_EXTRA: "1"}, {2}),
        ({_FIRST: "5"}, {1, 2}),
    ],
)
def test_the_caches_do_not_change_the_result(
    application: Application,
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, str],
    linked: set[int],
) -> None:
    segments = _segments(application, **settings)
    cached = recover_parameters(application, segments, linked_objects=linked)
    monkeypatch.setattr(parameters, "_search_cache", lambda _application: None)
    assert recover_parameters(application, segments, linked_objects=linked) == cached


def test_a_decode_with_fixed_values_decodes_with_the_first_build(
    application: Application,
    builds: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fixed values are looked up in the UI as seeded, not evaluated; the decode itself
    # takes the first build, which evaluates alike, instead of evaluating that UI again.
    segments = _segments(
        application, **{_SWITCH: "1", _FIRST: "5", _SECOND: "7", _FIRST_2: "4"}
    )
    inferred = {_EXTRA: "1"}
    fixed = {f"{_EXTRA}-none": "0"}
    builds[0] = 0
    decoded = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, inferred, fixed=fixed
    )
    reused = builds[0]
    seed_for_lookups = parameters._seeded_for_lookups  # pyright: ignore[reportPrivateUsage]

    def without_alike(*args: object) -> tuple[object, None]:
        return seed_for_lookups(*args)[0], None  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(parameters, "_seeded_for_lookups", without_alike)
    builds[0] = 0
    assert (
        parameters._decode(  # pyright: ignore[reportPrivateUsage]
            application, segments, None, None, inferred, fixed=fixed
        )
        == decoded
    )
    assert builds[0] == reused + 1


def test_the_objects_of_a_ui_are_gathered_once(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = seed_dynamic_ui(application, {_SWITCH: "1"}, evaluated=True)
    assert ui is not None
    plain = parameters._shown_objects(ui)  # pyright: ignore[reportPrivateUsage]
    gather = parameters._objects_of  # pyright: ignore[reportPrivateUsage]
    gathered: list[object] = []

    def counted(nodes: list[object]) -> list[tuple[int, str]]:
        gathered.append(nodes)
        return gather(nodes)

    monkeypatch.setattr(parameters, "_objects_of", counted)
    previous = getattr(parameters._SEARCH, "cache", None)  # pyright: ignore[reportPrivateUsage]
    parameters._SEARCH.cache = parameters._SearchCache(application)  # pyright: ignore[reportPrivateUsage]
    try:
        sizes = dict.fromkeys(plain, "1 Bit")
        assert parameters._shown_objects(ui) == plain  # pyright: ignore[reportPrivateUsage]
        assert parameters._shown_objects(ui, sizes) == plain  # pyright: ignore[reportPrivateUsage]
        assert parameters._shown_objects(ui, {n: "1 Byte" for n in plain}) == set()  # pyright: ignore[reportPrivateUsage]
    finally:
        parameters._SEARCH.cache = previous  # pyright: ignore[reportPrivateUsage]
    assert gathered == [ui.ui()]
