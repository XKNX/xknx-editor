"""A copy of a parameter state (a change's rollback point, a simulation) is independent of the
original, but shares the static defaults, which are replaced and never changed in place."""

from __future__ import annotations

import copy

from xknxeditor.prod.parser_v2.state import GlobalState


def test_a_copy_shares_the_defaults_only() -> None:
    defaults = {"P-1_R-1": "0", "P-2_R-2": "5"}
    state = GlobalState(values={"P-1_R-1": "1"}, param_ref_defaults=defaults)
    child = state.module_child("M-1", param_ref_defaults={"P-3_R-3": "7"})
    child.set("P-3_R-3", "8")

    clone = copy.deepcopy(state)
    clone.set("P-1_R-1", "2")
    clone_child = clone.module_child("M-1")
    clone_child.set("P-3_R-3", "9")

    assert state.get("P-1_R-1") == "1"
    assert child.get("P-3_R-3") == "8"
    assert clone.get("P-1_R-1") == "2"
    assert clone_child is not child
    assert clone_child.get("P-3_R-3") == "9"
    assert clone.get("P-2_R-2") == "5"
    assert clone._param_ref_defaults is defaults  # pyright: ignore[reportPrivateUsage]
