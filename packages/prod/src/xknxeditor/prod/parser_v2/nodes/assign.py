from __future__ import annotations

from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate import Assign

from ..context import EvalContext
from ..ui import UiNode
from .base import DynamicNode

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..state import ParameterState


class AssignNode(DynamicNode):
    """Writes a value into a parameter (stored like any other value) when it starts applying
    and whenever its source changes (see :meth:`ParameterState.run_assign`); state-only,
    renders nothing.

    It reads nothing but its source's value, so a block or Choose holding it is taken from
    the memo like one without (see EvalMemo): what it writes depends on what earlier
    evaluations left, so taking it again does it again (see :meth:`redo`)."""

    pure = True

    def __init__(self, elem: Assign):
        self._elem = elem

    @property
    def target(self) -> str:
        """The param-ref it writes."""
        return self._elem.target_param_ref_ref

    @property
    def spec(self) -> tuple[int, str, str | None, str | None]:
        """What :meth:`redo` applies: its key (the id of its element), its target, its
        source and its fixed value (see :meth:`ParameterState.apply_assign`)."""
        elem = self._elem
        return (
            id(elem),
            elem.target_param_ref_ref,
            elem.source_param_ref_ref,
            elem.value,
        )

    @property
    def source(self) -> str | None:
        """The param-ref whose value it writes, None for a fixed value."""
        elem = self._elem
        return elem.source_param_ref_ref if elem.value is None else None

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        scope = ctx.unrecorded_scope()
        if scope is not None:
            # As below, in one call (a pass of a large application runs thousands).
            elem = self._elem
            scope.apply_assign(
                id(elem),
                elem.target_param_ref_ref,
                elem.source_param_ref_ref,
                elem.value,
                marks=True,
            )
            return []
        # The Assign applies, also while its source has no value.
        key = id(self._elem)
        ctx.note_assign(key)
        target = self._elem.target_param_ref_ref
        # An assigned parameter is active like a shown one, and so is its source.
        ctx.mark_active_param(target)
        if self._elem.source_param_ref_ref is not None:
            ctx.mark_active_param(self._elem.source_param_ref_ref)
        if self._elem.value is not None:
            # The last Assign of the pass decides where the target's value comes from.
            ctx.mark_assigned_constant(target)
            ctx.run_assign(key, target, self._elem.value, from_source=False)
        elif self._elem.source_param_ref_ref is not None:
            ctx.mark_assigned_from(target, self._elem.source_param_ref_ref)
            value = ctx.get(self._elem.source_param_ref_ref)
            ctx.run_assign(key, target, value, from_source=True)
        ctx.note_assign_node(self)
        return []

    def written(
        self, scope: ParameterState, get: Callable[[str], str | None]
    ) -> str | None:
        """What :meth:`redo` would write into its target in ``scope`` now, its source's value
        read with ``get``; None when it writes nothing."""
        elem = self._elem
        if elem.value is not None:
            value, from_source = elem.value, False
        elif elem.source_param_ref_ref is not None:
            value, from_source = get(elem.source_param_ref_ref), True
        else:
            return None
        if value is None or not scope.assign_writes(
            id(elem), elem.target_param_ref_ref, value, from_source=from_source
        ):
            return None
        return value

    def redo(self, scope: ParameterState) -> None:
        """What :meth:`eval` does in ``scope`` but the marks, as in a pass that explores
        nothing, the evaluation taken from the memo with its source's value read the same."""
        elem = self._elem
        scope.apply_assign(
            id(elem),
            elem.target_param_ref_ref,
            elem.source_param_ref_ref,
            elem.value,
            marks=False,
        )
