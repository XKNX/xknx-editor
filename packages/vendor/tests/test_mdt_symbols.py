import struct
from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

import pytest

from xknxeditor.vendor.mdt import decode_symbol, encode_symbol, profile_for_application
from xknxeditor.vendor.mdt.symbols import Symbol, read_symbols


def test_threshold_orientation_and_checksum() -> None:
    pixels = bytearray(b"\xff" * 4096)
    pixels[0] = 127
    pixels[64] = 128
    pixels[7 * 64 + 1] = 0
    pixels[63 * 64 + 63] = 0
    data = encode_symbol(bytes(pixels))
    assert data[0] == 1
    assert data[8] == 128
    assert data[511] == 128
    assert encode_symbol(decode_symbol(data)) == data
    with pytest.raises(ValueError, match="checksum"):
        decode_symbol(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(ValueError):
        encode_symbol(b"")


def test_profiles_and_stored_pairs() -> None:
    profile = profile_for_application("M-0083_A-0054-14-0D63")
    assert profile is not None and profile.slots == 20
    assert profile_for_application("M-0083_A-0054-FF-0000") is None
    assert profile_for_application("M-0007_A-0054-14-0D63") is None
    symbol = Symbol(19, b"\xff" * 4096)
    entries = symbol.entries(profile)
    assert read_symbols(entries, profile) == (symbol,)
    with pytest.raises(ValueError):
        replace(symbol, slot=20).entries(profile)
    entries.pop("Symbol_19_bmp")
    with pytest.raises(ValueError, match="incomplete"):
        read_symbols(entries, profile)


def test_original_symbol_libraries() -> None:
    root = Path(__file__).resolve().parents[3] / ".references" / "mdtdca"
    names = (
        "MDT_DCA_BE_01_Push_Button_II_Smart_v4_5_2_0.etsapp",
        "MDT_DCA_BE_01_Central_Operation_Unit_Smart_v13.etsapp",
    )
    if not all((root / name).exists() for name in names):
        pytest.skip("Optional original symbol libraries are not installed")
    checked = 0
    for name in names:
        with ZipFile(root / name) as archive:
            for member in archive.namelist():
                if not member.endswith(".data"):
                    continue
                data = archive.read(member)
                assert encode_symbol(decode_symbol(data)) == data
                if member.endswith("Symbol_999.data"):
                    continue
                bmp = archive.read(member[:-5] + ".bmp")
                offset = struct.unpack_from("<I", bmp, 10)[0]
                height = struct.unpack_from("<i", bmp, 22)[0]
                pixels = bytes(
                    bmp[54 + bmp[offset + (63 - y if height > 0 else y) * 64 + x] * 4]
                    for y in range(64)
                    for x in range(64)
                )
                assert encode_symbol(pixels) == data, member
                checked += 1
    assert checked == 164


@pytest.mark.parametrize(
    ("application", "roles", "first_custom"),
    [
        (
            "M-0083_A-018B-31-369F",
            (
                "special",
                "special",
                "automatic",
                "comfort",
                "standby",
                "night",
                "frost_protection",
                "heat_protection",
                "temperature",
            ),
            2,
        ),
        (
            "M-0083_A-0054-14-0D63",
            (
                None,
                None,
                None,
                None,
                None,
                "temperature",
                "comfort",
                "standby",
                "night",
                "frost_protection",
                "heat_protection",
                "cooling",
                "heating",
            ),
            0,
        ),
    ],
)
def test_functional_symbol_roles_and_protected_slots(application, roles, first_custom):
    profile = profile_for_application(application)
    assert profile is not None
    assert profile.symbol_roles == roles
    assert len(profile.symbol_roles) < profile.slots
    for slot in range(profile.slots):
        symbol = Symbol(slot, b"\xff" * 4096)
        if slot < first_custom:
            with pytest.raises(ValueError, match="outside"):
                symbol.entries(profile)
        else:
            assert read_symbols(symbol.entries(profile), profile) == (symbol,)


def test_image_rgb_average_ignores_alpha_and_exports_bmp() -> None:
    from io import BytesIO

    from PIL import Image

    from xknxeditor.vendor.mdt.symbols import import_symbol_image

    stream = BytesIO()
    Image.new("RGBA", (64, 64), (255, 0, 0, 0)).save(stream, format="PNG")
    assert import_symbol_image(stream.getvalue()) == bytes([85]) * 4096
    profile = profile_for_application("M-0083_A-018B-31-369F")
    assert profile is not None
    symbol = Symbol(2, bytes(range(256)) * 16)
    entries = symbol.entries(profile)
    assert entries["Symbol_2_bmp"].startswith(b"BM")
    assert read_symbols(entries, profile) == (symbol,)
    entries["Symbol_2_data"] = encode_symbol(bytes(v ^ 255 for v in symbol.grayscale))
    assert read_symbols(entries, profile)[0].data == entries["Symbol_2_data"]
    entries["Symbol_2_data"] = encode_symbol(bytes(4096))
    with pytest.raises(ValueError, match="inconsistent"):
        read_symbols(entries, profile)


def test_original_project_custom_symbols() -> None:
    root = Path(__file__).resolve().parents[3] / ".references" / "mdtdca"
    source = root / "~mdtdca.knxproj"
    if not source.exists():
        pytest.skip("Optional original symbol project is not installed")
    profile = profile_for_application("M-0083_A-018B-31-369F")
    assert profile is not None
    with ZipFile(source) as archive:
        entries = {
            Path(name).stem.split("-", 1)[1]: archive.read(name)
            for name in archive.namelist()
            if "/M0083-A0041/4355-Symbol_" in name
        }
    original = dict(entries)
    symbols = read_symbols(entries, profile)
    assert [s.slot for s in symbols] == [2, 3, 16, 19, 33]
    for symbol in symbols:
        assert symbol.data == original[f"Symbol_{symbol.slot}_data"]
        assert read_symbols(symbol.entries(profile), profile) == (symbol,)
    assert entries == original


@pytest.mark.parametrize("image_format", ["PNG", "BMP", "DATA"])
def test_import_formats_and_grayscale_export(image_format):
    from io import BytesIO

    from PIL import Image, PngImagePlugin

    from xknxeditor.vendor.mdt.symbols import export_symbol_image, import_symbol_image

    pixels = bytes([127, 128]) * 2048
    if image_format == "DATA":
        data = encode_symbol(pixels)
        expected = bytes([0, 255]) * 2048
    else:
        picture = Image.frombytes("L", (64, 64), pixels)
        stream = BytesIO()
        if image_format == "PNG":
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Description", "")
            picture.save(stream, format="PNG", pnginfo=metadata)
            padding = 514 - len(stream.getvalue())
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Description", "x" * padding)
            stream = BytesIO()
            picture.save(stream, format="PNG", pnginfo=metadata)
            assert len(stream.getvalue()) == 514
        else:
            picture.save(stream, format=image_format)
        data = stream.getvalue()
        expected = pixels
    actual = import_symbol_image(data)
    assert actual == expected
    assert decode_symbol(encode_symbol(actual)) == bytes([0, 255]) * 2048
    with Image.open(BytesIO(export_symbol_image(actual))) as exported:
        assert exported.format == "PNG"
        assert exported.size == (64, 64)
        assert exported.tobytes() == expected


def test_import_rejects_wrong_dimensions_formats_and_checksum():
    from io import BytesIO

    from PIL import Image

    from xknxeditor.vendor.mdt.symbols import import_symbol_image

    for size in ((63, 64), (64, 65)):
        stream = BytesIO()
        Image.new("RGB", size, "white").save(stream, format="PNG")
        with pytest.raises(ValueError, match="64 by 64"):
            import_symbol_image(stream.getvalue())
    stream = BytesIO()
    Image.new("RGB", (64, 64), "white").save(stream, format="JPEG")
    with pytest.raises(ValueError, match="Invalid symbol image"):
        import_symbol_image(stream.getvalue())
    data = encode_symbol(bytes(4096))
    with pytest.raises(ValueError, match="checksum"):
        import_symbol_image(data[:-1] + bytes([data[-1] ^ 1]))


@pytest.mark.parametrize(
    "application", ["M-0083_A-018B-31-369F", "M-0083_A-0054-14-0D63"]
)
def test_complete_zip_library_roundtrip_inversion_and_numbering(application):
    from io import BytesIO

    from xknxeditor.vendor.mdt.symbols import (
        export_symbol_library,
        import_symbol_library,
    )

    profile = profile_for_application(application)
    payload = {
        slot: encode_symbol(bytes((i + slot) % 256 for i in range(4096)))
        for slot in range(profile.first_custom_slot, profile.slots)
    }
    archive = export_symbol_library(payload, profile)
    assert archive == export_symbol_library(payload, profile)
    with ZipFile(BytesIO(archive)) as source:
        assert source.namelist() == [f"Symbol_{slot + 1}.bmp" for slot in payload]
        assert all(source.read(name).startswith(b"BM") for name in source.namelist())
    imported = import_symbol_library(archive, profile)
    assert {s.slot: s.data for s in imported} == payload
    inverted = import_symbol_library(archive, profile, invert=True)
    assert all(
        s.data[:512] == bytes(v ^ 255 for v in payload[s.slot][:512]) for s in inverted
    )
    incomplete = dict(payload)
    incomplete.pop(profile.slots - 1)
    with pytest.raises(ValueError, match="No pixels"):
        export_symbol_library(incomplete, profile)


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "nested", "png", "size", "invalid"]
)
def test_zip_library_rejects_incomplete_or_invalid_input(fault):
    from io import BytesIO

    from xknxeditor.vendor.mdt.symbols import (
        MAX_LIBRARY_IMAGE_SIZE,
        export_symbol_library,
        import_symbol_library,
    )

    profile = profile_for_application("M-0083_A-0054-14-0D63")
    original = export_symbol_library(
        {slot: encode_symbol(bytes(4096)) for slot in range(20)}, profile
    )
    stream = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(stream, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "Symbol_20.bmp":
                if fault == "missing":
                    continue
                if fault == "nested":
                    name = "nested/" + name
                if fault == "duplicate":
                    target.writestr(name.upper(), data)
                if fault == "png":
                    data = b"\x89PNG\r\n\x1a\n"
                if fault == "size":
                    data = b"BM" + bytes(MAX_LIBRARY_IMAGE_SIZE)
            target.writestr(name, data)
        target.writestr("../ignored.txt", b"ignored")
    with pytest.raises(ValueError):
        import_symbol_library(
            b"not a zip" if fault == "invalid" else stream.getvalue(), profile
        )


def test_zip_never_accepts_a_packed_symbol_disguised_as_bitmap():
    from io import BytesIO

    from xknxeditor.vendor.mdt.symbols import (
        export_symbol_library,
        import_symbol_library,
        symbol_crc,
    )

    profile = profile_for_application("M-0083_A-0054-14-0D63")
    pixels = b"BM" + bytes(510)
    disguised = pixels + symbol_crc(pixels)
    original = export_symbol_library(
        dict.fromkeys(range(20), encode_symbol(bytes(4096))), profile
    )
    stream = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(stream, "w") as target:
        for name in source.namelist():
            target.writestr(
                name, disguised if name == "Symbol_20.bmp" else source.read(name)
            )
    with pytest.raises(ValueError, match="BMP"):
        import_symbol_library(stream.getvalue(), profile)
