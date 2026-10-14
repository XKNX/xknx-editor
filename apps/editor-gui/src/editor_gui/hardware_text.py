"""Display texts for a product's hardware (DIN rail mounting, bus current)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from editor_gui.regional import regional_format

if TYPE_CHECKING:
    from xknxeditor.catalog import ProductSummary

# Width of one DIN rail module in millimetres.
_MODULE_WIDTH_MM = 18.0


def _number_text(value: float) -> str:
    """``value`` with at most one decimal, in the regional decimal format."""
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", regional_format().decimal)


def rail_text(product: ProductSummary | None, yes: str, no: str) -> str:
    """``142.3 mm (8M)`` for a DIN rail device with a known width; ``-`` when unknown."""
    if product is None or product.is_rail_mounted is None:
        return "-"
    if not product.is_rail_mounted:
        return no
    width = product.width_mm
    if not width:
        return yes
    modules = max(1, round(width / _MODULE_WIDTH_MM))
    return f"{_number_text(width)} mm ({modules}M)"


def bus_current_text(product: ProductSummary | None) -> str:
    if product is None or product.bus_current is None:
        return "-"
    return f"{_number_text(product.bus_current)} mA"
