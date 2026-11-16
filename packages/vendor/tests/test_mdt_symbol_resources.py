from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from xknxeditor.vendor.mdt import encode_symbol, profile_for_application
from xknxeditor.vendor.mdt.symbol_resources import prepare_default_library
from xknxeditor.vendor.mdt.symbols import read_symbol_library


def _archive(
    *,
    app_id="M0083-A00D1",
    application="M-0083_A-0054-14-0000",
    missing=False,
    corrupt=False,
    duplicate=False,
):
    result = BytesIO()
    with ZipFile(result, "w") as z:
        z.writestr(
            "AddInManifest.xml",
            f'<Knxapp AppId="{app_id}" AddInAssembly="code.dll"><DeviceConfiguration><ApplicationProgram Id="{application}" /></DeviceConfiguration></Knxapp>',
        )
        z.writestr("code.dll", b"executable")
        z.writestr("help/readme.txt", b"unneeded")
        for slot in range(19 if missing else 20):
            data = encode_symbol(bytes([slot % 2 * 255]) * 4096)
            if corrupt and slot == 5:
                data = data[:-1] + bytes([data[-1] ^ 1])
            z.writestr(f"images/Symbol_{slot}.data", data)
        if duplicate:
            with pytest.warns(UserWarning, match="Duplicate"):
                z.writestr("images/Symbol_0.data", encode_symbol(bytes(4096)))
    return result.getvalue()


def test_only_validated_pixels_and_identity_are_retained():
    app = "M-0083_A-0054-14-0D63"
    profile = profile_for_application(app)
    raw = _archive()
    packed = prepare_default_library(raw, app)
    assert prepare_default_library(packed, app) == packed
    with ZipFile(BytesIO(packed)) as z:
        assert len(z.namelist()) == 21
        assert b"AddInAssembly" not in z.read("AddInManifest.xml")
        with ZipFile(BytesIO(raw)) as original:
            for slot in range(20):
                name = f"images/Symbol_{slot}.data"
                assert z.read(name) == original.read(name)
    assert (
        len(
            read_symbol_library(
                packed, profile, new_library=False, second_generation=False
            )
        )
        == 20
    )


@pytest.mark.parametrize(
    "options",
    [
        {"app_id": "M0083-A0041"},
        {"application": "M-0083_A-0054-13-0000"},
        {"missing": True},
        {"corrupt": True},
        {"duplicate": True},
    ],
)
def test_invalid_library_is_rejected(options):
    with pytest.raises(ValueError):
        prepare_default_library(_archive(**options), "M-0083_A-0054-14-0D63")


@pytest.mark.parametrize(
    "filename,application",
    [
        ("MDT_DCA_BE_01_Push_Button_II_Smart_v4_5_2_0.etsapp", "M-0083_A-008A-25-83B3"),
        (
            "MDT_DCA_BE_01_Central_Operation_Unit_Smart_v13.etsapp",
            "M-0083_A-0054-14-0D63",
        ),
    ],
)
def test_original_app_libraries_remain_byte_exact(filename, application):
    root = Path(__file__).resolve().parents[3] / ".references/mdtdca"
    source = root / filename
    if not source.exists():
        pytest.skip("Optional original symbol app not installed")
    raw = source.read_bytes()
    packed = prepare_default_library(raw, application)
    profile = profile_for_application(application)
    assert len(packed) < 50_000
    for newer in (False, True):
        for second in (False, True):
            assert read_symbol_library(
                packed, profile, new_library=newer, second_generation=second
            ) == read_symbol_library(
                raw, profile, new_library=newer, second_generation=second
            )
