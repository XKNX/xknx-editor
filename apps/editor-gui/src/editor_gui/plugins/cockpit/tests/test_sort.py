from __future__ import annotations

from editor_gui.plugins.cockpit.service import CockpitRow, sort_rows
from editor_gui.plugins.health.service import Severity


def _row(
    node_id: int,
    address: str,
    name: str = "",
    product: str = "",
    *,
    loaded: int = 0,
    issues: int = 0,
) -> CockpitRow:
    return CockpitRow(
        node_id=node_id,
        individual_address=address,
        name=name,
        product_name=product,
        order_number="",
        loaded_count=loaded,
        issues=[(Severity.INFO, "x")] * issues,
    )


def _addresses(rows: list[CockpitRow]) -> list[str]:
    return [r.individual_address for r in rows]


def test_address_sorts_numerically_with_unaddressed_last() -> None:
    rows = [
        _row(1, "1.0.49"),
        _row(2, ""),
        _row(3, "1.0.5"),
        _row(4, "1.1.1"),
        _row(5, "0.2.10"),
    ]
    assert _addresses(sort_rows(rows, 0)) == ["0.2.10", "1.0.5", "1.0.49", "1.1.1", ""]
    assert _addresses(sort_rows(rows, 0, descending=True)) == [
        "",
        "1.1.1",
        "1.0.49",
        "1.0.5",
        "0.2.10",
    ]


def test_text_columns_sort_case_insensitively_ties_by_address() -> None:
    rows = [
        _row(1, "1.0.9", "beta", "Switch"),
        _row(2, "1.0.10", "Alpha", "dimmer"),
        _row(3, "1.0.2", "beta", "switch"),
    ]
    assert _addresses(sort_rows(rows, 1)) == ["1.0.10", "1.0.2", "1.0.9"]
    assert _addresses(sort_rows(rows, 2)) == ["1.0.10", "1.0.2", "1.0.9"]


def test_loaded_and_status_sort_by_count() -> None:
    rows = [
        _row(1, "1.0.1", loaded=5, issues=2),
        _row(2, "1.0.2", loaded=-1, issues=0),
        _row(3, "1.0.3", loaded=2, issues=1),
    ]
    assert _addresses(sort_rows(rows, 3)) == ["1.0.2", "1.0.3", "1.0.1"]
    assert _addresses(sort_rows(rows, 4, descending=True)) == [
        "1.0.1",
        "1.0.3",
        "1.0.2",
    ]
