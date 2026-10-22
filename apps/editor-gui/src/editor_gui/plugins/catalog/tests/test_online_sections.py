"""The catalog sections of a manufacturer's online index: products filed into nested sections,
their names in the app's language, and the tree the online catalog shows them in."""

from __future__ import annotations

import json

from editor_gui.plugins.catalog.online_catalog import (
    download_languages,
    parse_catalog_items,
)
from editor_gui.plugins.catalog.ui.panel import section_tree


def _section(
    section_id: str,
    name: str,
    german: str,
    entries: list[str],
    children: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "Id": section_id,
        "Name": name,
        "DefaultLanguage": "en-US",
        "ChildSections": children or [],
        "IndexFileEntrieIds": entries,
        "Translations": [
            {"Language": "en-US", "Text": name},
            {"Language": "de-DE", "Text": german},
        ],
    }


def _entry(entry_id: str, name: str) -> dict[str, object]:
    return {"Id": entry_id, "CatalogItemName": name, "OrderNumber": entry_id.upper()}


_INDEX = json.dumps(
    {
        "ManufacturerId": 131,
        "ManufacturerName": "Maker",
        "Entries": [
            _entry("dim", "Dimmer"),
            _entry("switch", "Switch"),
            _entry("led", "LED indicator"),
            _entry("loose", "Loose"),
        ],
        "Sections": [
            _section(
                "S-ACT",
                "Actuators",
                "Aktoren",
                [],
                [
                    _section("S-SW", "Switch actuators", "Schaltaktoren", ["switch"]),
                    # Listed twice, and an entry the index no longer has.
                    _section(
                        "S-DIM",
                        "Dimming actuators",
                        "Dimmaktoren",
                        ["dim", "dim", "old"],
                    ),
                ],
            ),
            _section("S-VIS", "Indicator", "Anzeige", ["led"]),
        ],
    }
).encode()


def test_products_carry_their_sections_from_the_top_down() -> None:
    items = {i.id: i for i in parse_catalog_items(_INDEX)}
    assert [s.id for s in items["dim"].sections] == ["S-ACT", "S-DIM"]
    assert [s.id for s in items["led"].sections] == ["S-VIS"]
    assert items["loose"].sections == ()


def test_section_names_follow_the_language() -> None:
    section = parse_catalog_items(_INDEX)[0].sections[0]
    assert section.name_in("de") == "Aktoren"
    assert section.name_in("de-DE") == "Aktoren"
    assert section.name_in("en") == "Actuators"
    # No Dutch name: the default one.
    assert section.name_in("nl") == "Actuators"


def test_the_tree_files_products_into_sorted_sections() -> None:
    tree = section_tree(parse_catalog_items(_INDEX), "de")
    top = list(tree.children.values())
    assert [n.section.name_in("de") for n in top if n.section] == ["Aktoren", "Anzeige"]
    below = list(top[0].children.values())
    assert [n.section.name_in("de") for n in below if n.section] == [
        "Dimmaktoren",
        "Schaltaktoren",
    ]
    assert [i.id for i in below[0].items] == ["dim"]
    assert [i.id for i in tree.items] == ["loose"]


def test_products_download_in_the_editor_language_first() -> None:
    others = ["es-ES", "el-GR", "zh-CN", "ja-JP"]
    assert download_languages("de") == ["de-DE", "en-US", "nl-NL", *others]
    assert download_languages("nl") == ["nl-NL", "en-US", "de-DE", *others]
    # A language the editor has no catalog language for: English first.
    assert download_languages("fr") == ["en-US", "de-DE", "nl-NL", *others]
