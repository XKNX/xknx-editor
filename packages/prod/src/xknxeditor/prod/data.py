"""Parse a KNX XML and return the unified IR.

The per-version `files.vXX` models are just a parsing step: they get converted straight to the
`intermediate` (IR) form and dropped, so the rest of the package only ever sees IR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xknxeditor.namespaces import load_xml
from xknxeditor.namespaces.adapters.convert import Context, convert

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.knx import Knx


def to_ir(xml_bytes: bytes, version: str) -> Knx:
    """Read `xml_bytes` via the per-version model and convert to IR."""
    from xsdata.exceptions import ParserError

    from xknxeditor.namespaces.intermediate.knx import Knx as _Knx

    from .heal import drop_channel_when_items, heal_application_xml

    # Non-conformant legacy products can omit attributes the binding requires; heal before binding.
    healed = heal_application_xml(xml_bytes)
    try:
        model = load_xml(healed, version)
    except ParserError as exc:
        # Items in a when of a channel's choose, where no schema allows them, are left out.
        kept = (
            drop_channel_when_items(healed) if "ChannelChooseWhen" in str(exc) else None
        )
        if kept is None:
            raise
        model = load_xml(kept, version)
    return convert(Context(version=version), model, _Knx)
