from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.parameter_block_layout_t import (
    ParameterBlockLayout,
)

if TYPE_CHECKING:
    from xknxeditor.prod.parser_v2.ui import UiNode


@dataclass(frozen=True, slots=True)
class UiParameterBlock:
    id: str
    children: tuple[UiNode, ...]
    name: str | None = None
    text: str | None = None
    inline: bool = False
    layout: ParameterBlockLayout = ParameterBlockLayout.LIST
    row_labels: tuple[str, ...] = ()
    column_headers: tuple[str, ...] = ()
    read_only: bool = False
    # Not shown in the dialog; its parameters stay active.
    hidden: bool = False
    help_context: str | None = None
    column_widths: tuple[str, ...] = ()  # Column Width as declared, e.g. "45%"
    collapsed_rows: frozenset[int] = frozenset()  # 1-based rows with CollapseIfEmpty
    cell: str | None = None  # "row,col" inside a parent GRID/TABLE block
    icon: str | None = None
