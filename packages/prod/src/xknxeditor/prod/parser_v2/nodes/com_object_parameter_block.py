from __future__ import annotations

from xknxeditor.namespaces.intermediate.access_t import Access
from xknxeditor.namespaces.intermediate.application_program_channel_t import (
    ComObjectParameterBlock,
)

from .._name import apply_text_args, fill_name
from ..context import EvalContext
from ..ui import UiNode
from ..ui.parameter_block import UiParameterBlock
from .base import DynamicNode


class ComObjectParameterBlockNode(DynamicNode):
    """A grouped box of parameters (dynamic-XML ParameterBlock)."""

    __slots__ = ("_children", "_elem", "_heading_text")

    def __init__(
        self,
        elem: ComObjectParameterBlock,
        children: list[DynamicNode | None],
        heading_text: str | None = None,
    ) -> None:
        self._elem = elem
        self._children = children
        # The resolved Text of the block's heading parameter (its ``ParamRefId``), if that parameter
        # carries one. The importer labels the block with this Text (e.g. a channel-prefixed "A: Drive") in
        # preference to the block's own Name (which may be a generic template like "Jalousie X: …").
        self._heading_text = heading_text

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        # The block's heading parameter is active while the block is shown.
        if self._elem.param_ref_id:
            ctx.mark_active_param(self._elem.param_ref_id)
        explorer = ctx.explore
        hidden = self._elem.access == Access.NONE
        if explorer is not None:
            heading = (
                ctx.get_text(self._elem.id)
                or self._heading_text
                or self._elem.text
                or self._elem.name
            )
            explorer.enter_section(
                fill_name(apply_text_args(heading or "", ctx.text_args()), ""),
                hidden=hidden,
            )
        tree = ctx.object_tree if self._elem.show_in_com_object_tree else None
        if tree is not None:
            tree.enter("Folder", ctx.qualify(self._elem.id))
        try:
            items = [u for c in self._children if c for u in c.eval(ctx)]
        finally:
            if explorer is not None:
                explorer.leave_section(hidden=hidden)
            if tree is not None:
                tree.leave()
        arg_defaults = ctx.text_args()
        text_ref = self._elem.text_parameter_ref_id
        name_value = ctx.get(text_ref) if text_ref else None
        template = (
            ctx.get_text(self._elem.id)
            or self._heading_text
            or self._elem.text
            or self._elem.name
        )
        raw_text = apply_text_args(template or "", arg_defaults)
        text = fill_name(raw_text, name_value or "") or None
        text_indent = len(raw_text) - len(raw_text.lstrip(" ")) if text else 0
        rows = self._elem.rows.row if self._elem.rows else []
        cols = self._elem.columns.column if self._elem.columns else []

        def label(text: str | None, ref: str | None) -> str:
            value = ctx.get(ref) if ref else None
            return fill_name(
                apply_text_args(text or "", arg_defaults),
                value or "",
                keep_whitespace=True,
            )

        row_labels = tuple(label(r.text, r.text_parameter_ref_id) for r in rows)
        column_headers = tuple(label(c.text, c.text_parameter_ref_id) for c in cols)
        return [
            UiParameterBlock(
                id=self._elem.id,
                name=self._elem.name,
                text=text,
                inline=self._elem.inline,
                layout=self._elem.layout,
                children=tuple(items),
                row_labels=row_labels,
                column_headers=column_headers,
                hidden=self._elem.access == Access.NONE,
                help_context=self._elem.help_context,
                column_widths=tuple(c.width for c in cols),
                collapsed_rows=frozenset(
                    i for i, r in enumerate(rows, start=1) if r.collapse_if_empty
                ),
                cell=self._elem.cell,
                icon=self._elem.icon,
                param_ref_id=self._elem.param_ref_id,
                text_indent=text_indent,
            )
        ]
