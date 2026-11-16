"""PowerTool devices another manufacturer sells (ABB sells Busch-Jaeger devices, their
application ids end in ``-O0007``): the device runs the original manufacturer's firmware, so its
program ids carry that manufacturer (the product data writes ``0007610821`` for an ABB
``M-0002_A-6108-23-...-O0007``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from xknxeditor.vendor.powertool import native, prepare
from xknxeditor.vendor.powertool.prepare import (
    firmware_manufacturer,
    is_powertool_application,
    profile_for,
    program_identity,
)


def _app(
    app_id: str,
    number: int,
    version: int,
    *,
    original: str | None = None,
    kind: str = "ApplicationProgram",
) -> Any:
    return SimpleNamespace(
        id=app_id,
        manufacturer_id=app_id[:6],
        program=SimpleNamespace(
            application_number=number,
            application_version=version,
            mask_version="MV-07B0",
            program_type=kind,
            original_manufacturer=original,
        ),
    )


@pytest.fixture
def parameters(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values: dict[str, str] = {}
    monkeypatch.setattr(prepare, "product_parameters", lambda _app: values)
    monkeypatch.setattr(native, "product_parameters", lambda _app: values)
    return values


def test_firmware_manufacturer_is_the_original_one() -> None:
    oem = _app("M-0002_A-6108-21-39FA-O0007", 0x6108, 0x21, original="M-0007")
    own = _app("M-0007_A-6108-21-B505", 0x6108, 0x21)
    assert firmware_manufacturer(oem) == 0x0007
    assert firmware_manufacturer(own) == 0x0007
    assert firmware_manufacturer(_app("M-0002_A-6108-20-2048", 0x6108, 0x20)) == 0x0002
    assert program_identity(oem) == (0x6108, 0x21)
    assert program_identity(own) == (0x6108, 0x21)
    assert program_identity(_app("M-0002_A-6108", 0x6108, 0x21)) is None
    assert is_powertool_application(cast("Any", oem))
    # An ABB application of its own firmware is not one of the Busch-Jaeger 6108 variants.
    assert not is_powertool_application(_app("M-0002_A-6108-21-39FA", 0x6108, 0x21))


def test_oem_6108_push_button_has_its_variant(parameters: dict[str, str]) -> None:
    parameters.update(
        {"PTDatabaseCode": "3", "ProductType": "TasterBA1f", "BAUBVersion": "0.1.0"}
    )
    app = _app("M-0002_A-6108-21-39FA-O0007", 0x6108, 0x21, original="M-0007")
    pei = _app(
        "M-0002_A-6108-31-E8BB-O0007",
        0x6108,
        0x31,
        original="M-0007",
        kind="PeiProgram",
    )
    assert profile_for(app, pei) == "01"
    # The secondary program of another manufacturer's file does not belong to it.
    other = _app("M-0007_A-6108-31-C3AE", 0x6108, 0x31, kind="PeiProgram")
    with pytest.raises(ValueError, match="inconsistent"):
        profile_for(app, other)


def test_oem_family_product_writes_the_original_manufacturers_ids(
    parameters: dict[str, str],
) -> None:
    parameters.update(
        {"PTDatabaseCode": "3", "ProductType": "ObjektRTR", "BAUBVersion": "0.1.0"}
    )
    app = _app("M-0002_A-6108-40-18C6-O0007", 0x6108, 0x40, original="M-0007")
    # The PEI program of the same file names no original manufacturer: one device, one
    # firmware, so it carries the application's.
    pei = _app("M-0002_A-6108-50-9B90", 0x6108, 0x50, kind="PeiProgram")
    profile = native.product_profile(app, pei)
    assert profile == "ObjektRTR"
    config = native.for_product(native.default(profile), app, pei)
    assert getattr(config, "program_ids", None) == ("0007610840", "0007610850")
    # An ABB device of ABB's own firmware is not one of the Busch-Jaeger profiles.
    with pytest.raises(ValueError, match="identity"):
        native.product_profile(_app("M-0002_A-6108-40-18C6", 0x6108, 0x40), pei)
