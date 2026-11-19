"""Repeater detection resolves the actual hardware-to-program relationship."""

import zipfile

import pytest

from editor_gui.plugins.catalog.service import CatalogService
from packages.vendor.tests.powertool_fixtures import open_archive
from xknxeditor.prod import parse_hardware_xml


def test_actual_hardware_repeater_flag(tmp_path, monkeypatch):
    source = tmp_path / "product.knxprod"
    with (
        open_archive("all-profile-products.zip") as original,
        zipfile.ZipFile(source, "w") as output,
    ):
        for name in original.namelist():
            if name.startswith("smarttouch/"):
                output.writestr(name.removeprefix("smarttouch/"), original.read(name))
        hardware = parse_hardware_xml(original.read("smarttouch/M-0007/Hardware.xml"))
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    monkeypatch.setattr(catalog, "get_program_source", lambda _: (source, "M-0007"))
    for hardware_id, programs in hardware.hardware_to_program.items():
        for program in programs:
            assert (
                catalog.hardware_is_powerline_repeater(program)
                == hardware.hardware[hardware_id].raw.is_power_line_repeater
            )
    with pytest.raises(ValueError, match="program is missing"):
        catalog.hardware_is_powerline_repeater("missing")
    monkeypatch.setattr(catalog, "get_program_source", lambda _: None)
    with pytest.raises(ValueError, match="data is missing"):
        catalog.hardware_is_powerline_repeater("missing")


def test_the_rf_capabilities_of_a_hardware_program(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from enum import Enum
    from types import SimpleNamespace

    import xknxeditor.prod as prod
    import xknxeditor.prod.archive as archive

    class Capability(Enum):
        READY_FAST_SLOW = "ReadyFastSlow"

    hardware = SimpleNamespace(
        programs={
            "P": SimpleNamespace(
                raw=SimpleNamespace(
                    rftx_capabilities=Capability.READY_FAST_SLOW, rfrx_capabilities=None
                )
            )
        }
    )
    opened = []
    monkeypatch.setattr(
        archive,
        "Archive",
        lambda path, require_catalog: nullcontext(
            SimpleNamespace(get_hardware_xml=lambda manufacturer: opened.append(path))
        ),
    )
    monkeypatch.setattr(prod, "parse_hardware_xml", lambda xml: hardware)
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    monkeypatch.setattr(
        catalog, "get_program_source", lambda _: ("product.knxprod", "M-0001")
    )
    assert catalog.rf_capabilities("P") == ("ReadyFastSlow", None)
    # cached: the archive is read once
    assert catalog.rf_capabilities("P") == ("ReadyFastSlow", None)
    assert opened == ["product.knxprod"]
    # a program the archive lacks, and one without a source
    assert catalog.rf_capabilities("Q") == (None, None)
    monkeypatch.setattr(catalog, "get_program_source", lambda _: None)
    assert catalog.rf_capabilities("R") == (None, None)
