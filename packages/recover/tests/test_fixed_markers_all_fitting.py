"""Where no byte of a hidden setting with a fixed value differs from the device, the search
for the selectors those values name looks for no placement of them: there is no branch to
correct."""

from __future__ import annotations

from typing import Any

import pytest
from test_fixed_datapoint_types import _COUNT, _application, _device

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application
from xknxeditor.prod.parser_v2.dynamic import DynamicUI


def _named(
    application: Application, types: dict[int, int], monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], int]:
    """What the fixed values name for a device with the datapoint ``types``, and how often
    the placements were looked for."""
    segments = _device(application, types)
    values, _unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, {}, fixed={}
    )
    ui = application.dynamic_ui()
    assert ui is not None
    fixed = parameters._fixed_values(application, ui)  # pyright: ignore[reportPrivateUsage]
    looked = [0]
    explore = DynamicUI.explore_parameters

    def counted(self: DynamicUI, *args: Any) -> object:
        looked[0] += 1
        return explore(self, *args)

    monkeypatch.setattr(DynamicUI, "explore_parameters", counted)
    named = parameters._infer_from_fixed(  # pyright: ignore[reportPrivateUsage]
        application, segments, None, None, values, set(), {}, fixed
    )
    monkeypatch.setattr(DynamicUI, "explore_parameters", explore)
    return named, looked[0]


@pytest.mark.parametrize("union", [False, True])
def test_markers_that_all_fit_are_not_looked_for(
    union: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application(union=union)
    named, looked = _named(
        application, dict.fromkeys(range(1, _COUNT + 1), 0), monkeypatch
    )
    assert named == {}
    assert looked == 0


@pytest.mark.parametrize("union", [False, True])
def test_a_marker_that_differs_is_looked_for(
    union: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application(union=union)
    types = dict.fromkeys(range(1, _COUNT + 1), 0)
    types[5] = 1
    named, looked = _named(application, types, monkeypatch)
    assert looked == 1
    assert named
