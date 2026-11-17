import asyncio
import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import PropertyValueRead, PropertyValueResponse

from xknxeditor.download import DeviceProgrammer
from xknxeditor.vendor.mdt.firmware import (
    FirmwareCatalog,
    FirmwareInfo,
    FirmwareRelease,
    bundled_catalog,
    import_catalog,
    installed_version,
    load_catalog,
    read_bsl_info,
    read_firmware,
    version_key,
)


@pytest.mark.parametrize(
    "tail,version,expected",
    [
        ("0012", None, "1.2"),
        ("002a", None, "2.10"),
        ("0b2a", None, "2.10-beta"),
        ("ff00", "0883", "1.2.3"),
        ("ffb0", "0883", "1.2.3-beta"),
        ("ff00", "f7fe", "30.31.62"),
        ("ff00", "ffff", None),
        ("0005", None, None),
        ("ff00", None, None),
        ("ff00", "08", None),
    ],
)
def test_installed_version_formats(tail, version, expected):
    assert (
        installed_version(
            bytes(8) + bytes.fromhex(tail), bytes.fromhex(version) if version else None
        )
        == expected
    )
    assert installed_version(None, b"\x08\x83") is None
    assert installed_version(b"\x00", b"\x08\x83") is None


class VersionReader(DeviceProgrammer):
    async def read_bsl_info_packet(self, sequence):
        raise ManagementConnectionError("Identity not readable")


class PropertyTransport:
    def __init__(
        self, *, manufacturer=b"\x00\x83", missing=None, cancel=False, legacy=False
    ):
        self.calls = []
        self.values = {
            12: manufacturer,
            15: bytes(8) + (b"\x00\x12" if legacy else b"\xff\x00"),
            25: b"\x08\x83",
            78: bytes.fromhex("000102030405"),
        }
        self.missing = missing
        self.cancel = cancel

    async def request(self, payload, response_type):
        self.calls.append(payload)
        assert type(payload) is PropertyValueRead
        assert response_type is PropertyValueResponse
        assert payload.object_index == 0
        assert payload.count == payload.start_index == 1
        if payload.property_id == self.missing:
            if self.cancel:
                raise asyncio.CancelledError
            raise ManagementConnectionError("Not readable")
        data = self.values[payload.property_id]
        return Telegram(
            destination_address=IndividualAddress("1.1.1"),
            payload=PropertyValueResponse(
                object_index=0,
                property_id=payload.property_id,
                count=1,
                start_index=1,
                data=data,
            ),
        )


@pytest.mark.parametrize("legacy", [False, True])
async def test_real_programmer_sends_only_ordinary_reads(legacy):
    transport = PropertyTransport(legacy=legacy)
    info = await read_firmware(VersionReader(transport))
    assert info.version == ("1.2" if legacy else "1.2.3")
    assert [p.property_id for p in transport.calls] == (
        [12, 15, 78] if legacy else [12, 15, 25, 78]
    )
    assert info.hardware_type == bytes.fromhex("000102030405")


async def test_wrong_manufacturer_stops_before_version_queries():
    transport = PropertyTransport(manufacturer=b"\x00\x07")
    with pytest.raises(ValueError, match="MDT"):
        await read_firmware(VersionReader(transport))
    assert [p.property_id for p in transport.calls] == [12]


@pytest.mark.parametrize("missing", [15, 25, 78])
async def test_missing_property_has_no_invented_value_and_cancellation_propagates(
    missing,
):
    info = await read_firmware(VersionReader(PropertyTransport(missing=missing)))
    assert info.version == ("1.2.3" if missing == 78 else None)
    with pytest.raises(asyncio.CancelledError):
        await read_firmware(
            VersionReader(PropertyTransport(missing=missing, cancel=True))
        )


def archive(entries):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buffer.getvalue()


def metadata_archive():
    return archive(
        [
            (
                "Databases/firmware_cache.xml",
                b"""<ArrayOfFirmwareCache><FirmwareCache><FilenameFW>test.hex</FilenameFW><Info>1.2.3</Info><DeviceInfo>Test.01</DeviceInfo><SpecificProduct>none</SpecificProduct><HWSWKey>AQIDBA==</HWSWKey><LastModified>2025-01-01T00:00:00Z</LastModified><Source>Public</Source><IsBlacklisted>false</IsBlacklisted></FirmwareCache></ArrayOfFirmwareCache>""",
            ),
            ("Databases/blacklist_cache.xml", b"<ArrayOfFirmwareCache/>"),
            ("test.hex", b"firmware binary, never copied"),
        ]
    )


def test_import_keeps_only_metadata_and_requires_blacklist():
    raw = metadata_archive()
    catalog = import_catalog(raw, "test.zip")
    assert len(catalog.releases) == 1
    assert load_catalog(catalog.dump()) == catalog
    assert b"firmware binary" not in catalog.dump()
    assert (
        import_catalog(archive([("Firmware.zip", raw)]), "test.etsapp").releases
        == catalog.releases
    )
    for bad in (
        b"<ArrayOfFirmwareCache/>",
        archive([("Firmware.zip", archive([("Firmware.zip", raw)]))]),
        archive([("Databases/firmware_cache.xml", b"<ArrayOfFirmwareCache/>")]),
    ):
        with pytest.raises(ValueError):
            import_catalog(bad, "bad")


def test_catalog_matching_blacklist_and_numeric_version_order():
    base = FirmwareRelease(
        "a.hex",
        "2.9",
        "Test.01",
        "none",
        "01020304",
        "2025-01-01T00:00:00Z",
        "Public",
        False,
    )
    newer = replace(base, filename="b.hex", version="2.10")
    catalog = FirmwareCatalog(
        "test",
        (
            base,
            newer,
            replace(base, filename="dev.hex", source="Dev"),
            replace(base, filename="special.hex", specific_product="Other"),
            replace(base, filename="blocked.hex", version="2.8"),
            replace(base, filename="renamed.hex", version="2.8", blacklisted=True),
        ),
    )
    assert catalog.for_product("Test.01") == (base, newer)
    assert catalog.for_product("Test.02") == ()
    assert sorted(["2.10", "2.9", "2.10-beta", "2.10.0"], key=version_key) == [
        "2.9",
        "2.10-beta",
        "2.10",
        "2.10.0",
    ]


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"source": 3, "releases": []},
        {"source": "bad", "releases": [None]},
    ],
)
def test_invalid_stored_catalog(raw):
    with pytest.raises(ValueError):
        load_catalog(json.dumps(raw).encode())


def test_oversize_and_duplicate_metadata(monkeypatch):
    from xknxeditor.vendor.mdt import firmware

    raw = metadata_archive()
    with ZipFile(BytesIO(raw)) as z:
        entries = [(name, z.read(name)) for name in z.namelist()]
    with pytest.warns(UserWarning, match="Duplicate"):
        duplicate = archive([*entries, entries[0]])
    with pytest.raises(ValueError, match="duplicate"):
        import_catalog(duplicate, "duplicate")
    monkeypatch.setattr(firmware, "MAX_CATALOG_SIZE", 10)
    with pytest.raises(ValueError, match="oversized"):
        import_catalog(raw, "large")


def test_bundled_snapshot_and_optional_original_parity():
    catalog = bundled_catalog()
    assert len(catalog.releases) == 195
    assert len([r for r in catalog.releases if r.source == "Public"]) == 194
    assert catalog.latest_modified == "2025-11-04T15:45:19+01:00"
    assert load_catalog(catalog.dump()) == catalog
    assert catalog.for_product("AKD-0201.02")
    source = (
        Path(__file__).parents[3]
        / ".references/mdtdca/MDT_Firmware_Update_1.3.0.5.etsapp"
    )
    if source.exists():
        assert (
            import_catalog(source.read_bytes(), source.name).releases
            == catalog.releases
        )


def test_populated_import_blacklist_excludes_public_release():
    with ZipFile(BytesIO(metadata_archive())) as z:
        public = z.read("Databases/firmware_cache.xml")
    blocked = public.replace(b"<IsBlacklisted>false", b"<IsBlacklisted>true")
    data = archive(
        [
            ("Databases/firmware_cache.xml", public),
            ("Databases/blacklist_cache.xml", blocked),
        ]
    )
    catalog = import_catalog(data, "blocked.zip")
    assert len(catalog.releases) == 2
    assert catalog.for_product("Test.01") == ()
    assert load_catalog(catalog.dump()).for_product("Test.01") == ()


def test_led_controller_catalog_keeps_firmware_branches_separate():
    variants = bundled_catalog().variants_for_product("AKD-0424R.02")
    assert {key: {r.version for r in entries} for key, entries in variants} == {
        "83000340": {"3.15"},
        "83000440": {"4.10"},
        "83001440": {"4.10"},
        "83000540": {"5.9"},
        "83001540": {"5.9"},
    }


def test_variant_grouping_preserves_blacklist_and_version_order():
    entry = bundled_catalog().for_product("AKD-0424R.02")[0]
    older = replace(entry, filename="old.hex", version="2.9")
    newer = replace(entry, filename="new.hex", version="2.10")
    blocked = replace(entry, filename="blocked.hex", version="3.0")
    catalog = FirmwareCatalog(
        "test",
        (
            newer,
            older,
            blocked,
            replace(blocked, blacklisted=True),
        ),
    )
    assert catalog.variants_for_product(entry.product) == (
        (entry.hardware_key, (older, newer)),
    )


def bsl_info(key="83000440"):
    return bytes.fromhex("0100000c" + key) + bytes(20)


def packets(data, chunk=7):
    chunks = [data[i : i + chunk] for i in range(0, len(data), chunk)]
    return [
        bytes((0x91, len(chunks), i, len(part))) + part for i, part in enumerate(chunks)
    ]


@pytest.mark.parametrize("chunk", [1, 7, 10, 28])
async def test_identity_reassembly(chunk):
    reader = SimpleNamespace(
        read_bsl_info_packet=AsyncMock(side_effect=packets(bsl_info(), chunk))
    )
    assert await read_bsl_info(reader) == bsl_info()
    assert [
        call.args[0] for call in reader.read_bsl_info_packet.await_args_list
    ] == list(range((28 + chunk - 1) // chunk))


@pytest.mark.parametrize(
    "responses",
    [
        [b""],
        [b"\xff"],
        [bytes.fromhex("91010000")],
        [bytes.fromhex("91010002aa")],
        [bytes.fromhex("91010101aa")],
        [bytes.fromhex("90010001aa")],
        [bytes.fromhex("91000001aa")],
        [bytes.fromhex("911d0001aa")],
        [bytes.fromhex("91010001aa")],
        packets(bsl_info()[:-1]),
        packets(bsl_info() + b"x"),
        packets(b"\xff" + bsl_info()[1:]),
        packets(bsl_info()[:3] + b"\x0b" + bsl_info()[4:]),
        [packets(bsl_info())[0], bytes.fromhex("91030101aa")],
    ],
)
async def test_malformed_identity_never_supplies_a_hardware_key(responses):
    reader = VersionReader(PropertyTransport())
    reader.read_bsl_info_packet = AsyncMock(side_effect=responses)
    info = await read_firmware(reader)
    assert info.version == "1.2.3"
    assert info.hardware_key is None and info.bsl_info is None
    assert info.identity_error


async def test_identity_cancellation_propagates():
    reader = VersionReader(PropertyTransport())
    reader.read_bsl_info_packet = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await read_firmware(reader)


@pytest.mark.parametrize(
    "key,latest",
    [
        ("83000340", "3.15"),
        ("83000440", "4.10"),
        ("83001440", "4.10"),
        ("83000540", "5.9"),
        ("83001540", "5.9"),
    ],
)
def test_actual_key_selects_compatible_branch(key, latest):
    info = FirmwareInfo(131, "4.10", None, None, None, bsl_info(key))
    releases = bundled_catalog().compatible_releases("AKD-0424R.02", info)
    assert {r.version for r in releases} == {latest}
    assert all(r.hardware_key == key for r in releases)
    assert (
        bundled_catalog().compatible_releases(
            "AKD-0424R.02", replace(info, bsl_info=None)
        )
        == ()
    )
    assert (
        bundled_catalog().compatible_releases(
            "AKD-0424R.02", replace(info, manufacturer_id=7)
        )
        == ()
    )


def test_key_matching_uses_specific_product_and_hardware_constraints():
    base = bundled_catalog().for_product("AKD-0424R.02")[0]
    info = FirmwareInfo(131, "1.0", None, None, None, bsl_info(base.hardware_key))
    catalog = FirmwareCatalog("test", (base,))
    assert catalog.compatible_releases("Different display name", info) == (base,)
    specific = replace(base, specific_product="SCN-P360D4.03")
    catalog = FirmwareCatalog("test", (specific,))
    assert catalog.compatible_releases("SCN-P360D4.03", info) == ()
    matching = replace(info, hardware_type=bytes.fromhex("000000000314"))
    assert catalog.compatible_releases("SCN-P360D4.03", matching) == (specific,)
    assert catalog.compatible_releases("SCN-P360K4.03", matching) == ()
    assert (
        catalog.compatible_releases(
            "SCN-P360D4.03",
            replace(matching, hardware_type=bytes.fromhex("00000000031b")),
        )
        == ()
    )
    catalog = FirmwareCatalog(
        "test", (base, replace(base, filename="renamed.hex", blacklisted=True))
    )
    assert catalog.compatible_releases(base.product, info) == ()


def test_version_order_handles_missing_revision_and_beta():
    assert version_key("4.10") == version_key("4.10.0")
    assert version_key("4.10.0") < version_key("4.10.0.0")
    assert version_key("4.10-beta") < version_key("4.10")
    assert version_key("4.10-alpha") < version_key("4.10-beta")


async def test_identity_ignores_reply_padding_after_declared_data():
    responses = [packet + b"\x00\x00" for packet in packets(bsl_info())]
    reader = SimpleNamespace(read_bsl_info_packet=AsyncMock(side_effect=responses))
    assert await read_bsl_info(reader) == bsl_info()


def test_blacklist_membership_applies_even_without_the_cached_flag():
    with ZipFile(BytesIO(metadata_archive())) as z:
        public = z.read("Databases/firmware_cache.xml")
    data = archive(
        [
            ("Databases/firmware_cache.xml", public),
            (
                "Databases/blacklist_cache.xml",
                public.replace(b"test.hex", b"other.hex"),
            ),
        ]
    )
    assert import_catalog(data, "blacklist.zip").public_releases() == ()


def test_bootloader_fields_from_live_controller_capture():
    from xknxeditor.vendor.mdt.firmware import BootloaderInfo, decode_bootloader_info

    captured = bytes.fromhex("00010032830004400200000000440000000000006ea0000000980000")
    assert decode_bootloader_info(captured) == BootloaderInfo(
        version_code=0x0100,
        crc_type_code=0,
        max_telegram_length=50,
        encryption_type_code=2,
    )
    info = FirmwareInfo(131, "4.10", None, None, None, captured)
    assert info.bootloader == decode_bootloader_info(captured)
    unknown_codes = bytearray(captured)
    unknown_codes[2] = 0xAB
    unknown_codes[8] = 0xCD
    assert decode_bootloader_info(bytes(unknown_codes)) == BootloaderInfo(
        256, 171, 50, 205
    )
    for invalid in (
        None,
        b"",
        captured[:-1],
        captured + b"x",
        b"\xff" + captured[1:],
        captured[:3] + b"\x0b" + captured[4:],
    ):
        assert decode_bootloader_info(invalid) is None
