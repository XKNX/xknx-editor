"""The binding of the KNX XML (see xknxeditor.namespaces.binding) gives what xsdata gives for every
XML the tests hold: application programs, a product's hardware, catalog and master data."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from xknxeditor.namespaces import detect_version, get_model_class
from xknxeditor.namespaces.binding import Binder
from xknxeditor.namespaces.schema import _parser  # pyright: ignore[reportPrivateUsage]
from xknxeditor.prod.heal import heal_application_xml

_FIXTURES = Path(__file__).parent / "fixtures"
_PRODUCT = _FIXTURES / "gira_2gang_button_interface.knxprod"


def _documents() -> list[tuple[str, bytes]]:
    documents = [
        (path.name, path.read_bytes()) for path in sorted(_FIXTURES.glob("*.xml"))
    ]
    with zipfile.ZipFile(_PRODUCT) as product:
        documents += [
            (name, product.read(name))
            for name in product.namelist()
            if name.lower().endswith(".xml")
        ]
    return documents


@pytest.mark.parametrize(("name", "xml"), _documents(), ids=lambda item: str(item)[:40])
def test_the_binding_gives_what_xsdata_gives(name: str, xml: bytes) -> None:
    xml = heal_application_xml(xml)
    model = get_model_class(detect_version(xml))
    assert Binder().parse(xml, model) == _parser.from_bytes(xml, model), name
