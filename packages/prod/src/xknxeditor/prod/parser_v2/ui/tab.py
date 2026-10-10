from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xknxeditor.prod.parser_v2.ui import UiNode


@dataclass(frozen=True, slots=True)
class UiTab:
    children: tuple[UiNode, ...]
    id: str | None = None
    name: str | None = None
    text: str | None = None
    number: str | None = None
    icon: str | None = None
    # A ChannelIndependentBlock: its blocks belong to no channel.
    independent: bool = False
