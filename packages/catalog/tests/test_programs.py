"""A hardware program resolves to its application independently of catalog items."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from xknxeditor.catalog import CatalogService
from xknxeditor.catalog.core.hardware import FactoryData

_FIXTURE = (
    Path(__file__).parents[3]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)


def test_program_application_id_without_catalog_item(tmp_path: Path) -> None:
    db = tmp_path / "cat.xknxcatalog"
    svc = CatalogService(db)
    svc.import_knxprod(_FIXTURE.read_bytes())
    product = svc.list_products()[0]
    program = product.hardware2program_ref_id
    assert program is not None
    assert svc.get_program_application_id(program) == product.application_id

    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM catalog_section_products")
    assert svc.list_products() == []
    assert svc.get_program_application_id(program) == product.application_id
    assert svc.get_program_application_id("M-0000_H-unknown_HP-0000-00-0000") is None


def test_coupler_capabilities(tmp_path: Path) -> None:
    from sqlalchemy.orm import Session as _Session

    from xknxeditor.catalog.models import HardwareProgramCouplerCapability

    service = CatalogService(tmp_path / "cat.xknxcatalog")
    service.import_knxprod(_FIXTURE.read_bytes())
    program = service.list_products()[0].hardware2program_ref_id
    assert program is not None
    assert service.coupler_capabilities(program) == set()
    with _Session(service._engine) as db:  # pyright: ignore[reportPrivateUsage]
        db.add(
            HardwareProgramCouplerCapability(
                hardware_program_id=program, capability="SecurityProxy"
            )
        )
        db.commit()
    assert service.coupler_capabilities(program) == {"SecurityProxy"}


def test_factory_image_from_the_hardware_program(tmp_path: Path) -> None:
    import base64
    import re
    import zipfile

    service = CatalogService(tmp_path / "cat.xknxcatalog")
    service.import_knxprod(_FIXTURE.read_bytes())
    program = service.list_products()[0].hardware2program_ref_id
    assert program is not None
    assert service.get_factory_image(program) is None
    assert service.get_factory_image("M-0000_H-unknown_HP-0000-00-0000") is None

    source = service.get_program_source(program)
    assert source is not None
    stored = Path(source[0])
    with zipfile.ZipFile(stored) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    hardware = entries["M-0008/Hardware.xml"].decode("utf-8")
    image = base64.b64encode(b"\x01\x02\x03").decode()
    stamp = base64.b64encode(b"\x04\x05").decode()

    def write(attributes: str) -> None:
        entries["M-0008/Hardware.xml"] = re.sub(
            rf'(<Hardware2Program\b[^>]*\bId="{re.escape(program)}")',
            rf"\1 {attributes}",
            hardware,
        ).encode("utf-8")
        with zipfile.ZipFile(stored, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)

    write(f'LoadedImage="{image}"')
    assert service.get_factory_image(program) == FactoryData(b"\x01\x02\x03")
    # The stamp of the factory state comes with the image.
    write(f'LoadedImage="{image}" CheckSums="{stamp}"')
    assert service.get_factory_image(program) == FactoryData(
        b"\x01\x02\x03", b"\x04\x05"
    )
    # A stamp without an image is of no use.
    write(f'CheckSums="{stamp}"')
    assert service.get_factory_image(program) is None
    write(f'LoadedImage="{image}"')

    stored.unlink()
    assert service.get_factory_image(program) is None


def test_ip_config_of_the_application(tmp_path: Path) -> None:
    import re
    import zipfile

    service = CatalogService(tmp_path / "cat.xknxcatalog")
    service.import_knxprod(_FIXTURE.read_bytes())
    product = service.list_products()[0]
    program = product.hardware2program_ref_id
    assert program is not None
    # Without the attribute the tool sets the IP configuration.
    assert service.get_ip_config(program) == "Tool"
    assert service.get_ip_config("M-0000_H-unknown_HP-0000-00-0000") is None

    source = service.get_program_source(program)
    assert source is not None
    stored = Path(source[0])
    with zipfile.ZipFile(stored) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    name = next(n for n in entries if n.endswith(f"{product.application_id}.xml"))
    entries[name] = re.sub(
        rb"(<ApplicationProgram\b)", rb'\1 IPConfig="Custom"', entries[name], count=1
    )
    with zipfile.ZipFile(stored, "w") as archive:
        for entry, data in entries.items():
            archive.writestr(entry, data)
    assert service.get_ip_config(program) == "Custom"
