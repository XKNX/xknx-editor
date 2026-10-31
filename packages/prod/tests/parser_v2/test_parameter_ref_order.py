"""Several active ParameterRefs of one parameter: a download applies them one after the other,
the refs without a numeric Tag by ref number first, then the others by tag, so the last one
sets the value (a converted Siemens application shows P-90 as R-5100, Tag 5100, and keeps
R-90, Tag 90, in a hidden block)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknxeditor.prod.parser_v2.encode import parameter_ref_order, resolve_param_values

APP = "M-00FD_A-001E-11-72B7"


def test_untagged_refs_come_first_by_number_then_tagged_ones_by_tag() -> None:
    refs = [
        (f"{APP}_P-90_R-5100", "5100"),
        (f"{APP}_P-90_R-90", "90"),
        (f"{APP}_P-90_R-12", None),
        (f"{APP}_P-90_R-3", ""),
        (f"{APP}_P-90_R-7", "x"),
    ]
    ordered = sorted(refs, key=lambda ref: parameter_ref_order(*ref))
    assert [ref for ref, _ in ordered] == [
        f"{APP}_P-90_R-3",
        f"{APP}_P-90_R-7",
        f"{APP}_P-90_R-12",
        f"{APP}_P-90_R-90",
        f"{APP}_P-90_R-5100",
    ]


def _state(values: dict[str, str], active: set[str]) -> Any:
    return SimpleNamespace(
        active_param_refs=lambda: active,
        get=values.get,
        relative_param_values=lambda: [],
    )


def test_the_last_active_ref_sets_the_value() -> None:
    hidden, shown, other = (
        f"{APP}_P-90_R-90",
        f"{APP}_P-90_R-5100",
        f"{APP}_P-91_R-91",
    )
    idx = cast(
        Any,
        SimpleNamespace(
            parameter_refs={
                hidden: SimpleNamespace(ref_id=f"{APP}_P-90", tag="90"),
                shown: SimpleNamespace(ref_id=f"{APP}_P-90", tag="5100"),
                other: SimpleNamespace(ref_id=f"{APP}_P-91", tag="91"),
            },
            parameters={},
        ),
    )
    values = {hidden: "0", shown: "1", other: "5"}
    both = resolve_param_values(idx, _state(values, {hidden, shown, other}))
    assert both == {f"{APP}_P-90": "1", f"{APP}_P-91": "5"}
    # An inactive ref does not count, wherever it comes.
    only_hidden = resolve_param_values(idx, _state(values, {hidden}))
    assert only_hidden == {f"{APP}_P-90": "0"}


def test_a_module_instance_takes_the_last_ref_too() -> None:
    from xknxeditor.prod.parser_v2.encode import (
        _build_instance_overrides,  # pyright: ignore[reportPrivateUsage]
    )

    md = f"{APP}_MD-1"
    first, last = f"{md}_P-1_R-1", f"{md}_P-1_R-2"
    idx = cast(
        Any,
        SimpleNamespace(
            parameter_refs={
                last: SimpleNamespace(ref_id=f"{md}_P-1", tag=None),
                first: SimpleNamespace(ref_id=f"{md}_P-1", tag=None),
            },
            parameters={f"{md}_P-1": SimpleNamespace(id=f"{md}_P-1")},
        ),
    )
    # Stored in the other order: the ref number decides, not where the value is.
    module = cast(Any, SimpleNamespace(param_ref_id_to_value={last: "2", first: "1"}))
    assert _build_instance_overrides(module, idx) == {f"{md}_P-1": "2"}
