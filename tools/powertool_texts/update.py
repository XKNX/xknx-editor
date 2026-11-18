"""Update gettext catalogs from the independently authored PowerTool text map."""

from __future__ import annotations

import json
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po, write_po


def main() -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / "packages/vendor/src/xknxeditor/vendor/powertool"
    )
    mapping = json.loads((root / "text-map.json").read_text(encoding="utf-8"))
    messages = {
        text
        for nodes in mapping.values()
        for spec in nodes.values()
        for text in (
            spec["label"],
            *spec.get("options", []),
            *spec.get("subsystems", []),
        )
        if text
    }
    template = Catalog(
        project="XKNX Editor",
        copyright_holder="XKNX Editor contributors",
    )
    for message in sorted(messages):
        template.add(message)
    for path in sorted((root / "locales").glob("*/LC_MESSAGES/powertool.po")):
        with path.open("rb") as stream:
            catalog = read_po(stream, locale=path.parents[1].name)
        catalog.update(template, no_fuzzy_matching=True, update_creation_date=False)
        with path.open("wb") as stream:
            write_po(stream, catalog, width=88, sort_output=True, ignore_obsolete=True)


if __name__ == "__main__":
    main()
