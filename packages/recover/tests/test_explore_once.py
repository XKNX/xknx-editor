"""The explore pass that reads selectors from the hidden settings with fixed values: run once
for the UIs a search explores alike, and stopped by a cancel while it runs."""

from __future__ import annotations

from typing import Any

import pytest
from test_fixed_datapoint_types import _COUNT, _application, _device, _type

from xknxeditor.prod.parser_v2.context import Explorer
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.recover import parameters
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters


def _search(
    monkeypatch: pytest.MonkeyPatch, cancel: Any = None
) -> tuple[parameters.RecoveredParameters, list[str]]:
    """The fast search of a device whose datapoints all have the first type, and how each
    explore pass ended ("started", then "done" for a pass that finished)."""
    application = _application()
    explore = DynamicUI.explore_parameters
    passes: list[str] = []

    def counted(self: DynamicUI, *args: Any, **kwargs: Any) -> Any:
        passes.append("started")
        result = explore(self, *args, **kwargs)
        passes.append("done")
        return result

    monkeypatch.setattr(DynamicUI, "explore_parameters", counted)
    types = dict.fromkeys(range(1, _COUNT + 1), 1)
    result = recover_parameters(
        application,
        _device(application, types),
        object_sizes=dict.fromkeys(types, "1 Bit"),
        effort=RecoveryEffort.FAST,
        cancel=cancel(passes) if cancel is not None else None,
    )
    return result, passes


def test_a_search_explores_the_application_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No repeat counts with another value: every UI of the search is explored alike.
    result, passes = _search(monkeypatch)
    assert result.complete
    assert {result.values[_type(channel)] for channel in range(1, _COUNT + 1)} == {"1"}
    assert passes == ["started", "done"]


def test_a_cancel_while_exploring_ends_the_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cancel comes once the explore pass has started (a pass of a large application takes
    # longer than the rest of a step): it is heard inside the pass, which does not finish.
    monkeypatch.setattr(Explorer, "CHECK_EVERY", 1)
    result, passes = _search(monkeypatch, lambda passes: lambda: bool(passes))
    assert passes == ["started"]
    assert not result.complete
    assert result.limited


def test_a_cancelled_explore_pass_is_not_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    application = _application()
    ui = application.dynamic_ui()
    assert ui is not None
    ui.ui()
    monkeypatch.setattr(Explorer, "CHECK_EVERY", 1)
    cache = parameters._SearchCache(application)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(parameters._SEARCH, "cache", cache, raising=False)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(parameters._Cancelled):  # pyright: ignore[reportPrivateUsage]
        parameters._explored(application, ui, lambda: True)  # pyright: ignore[reportPrivateUsage]
    assert cache.explored == []
    places = parameters._explored(application, ui)  # pyright: ignore[reportPrivateUsage]
    assert places == [(p.parameter.ref_id, p.gates) for p in ui.explore_parameters()]
    assert len(cache.explored) == 1
