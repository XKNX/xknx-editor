import struct

import pytest

from xknxeditor.vendor.mdt.visucontrol import (
    Configuration,
    create_configuration,
    pack_json,
)


def configuration() -> Configuration:
    database = {
        "versions": {"dca_api": "1", "database": "3.0.0"},
        "items": {
            "objects_list": {
                "1": {
                    "id_driver": "knx",
                    "type": "dtp_1",
                    "typeformat": "1",
                    "name": "Light",
                    "command": "1",
                    "status": "2",
                    "flags": (1 << 2) | (1 << 10) | (1 << 3) | (1 << 5),
                }
            }
        },
        "elements": {
            "objects_list": {
                "1": {"@class": "psoe_switch", "function_map": {"switch": "1"}}
            }
        },
    }
    texts = {
        "psoe_switch": {
            "switch": {"command": {"en": "Switch"}, "status": {"en": "Feedback"}}
        }
    }
    return create_configuration(
        (pack_json(database), b"scheduler", b"scenes"),
        b"##01" + pack_json(texts),
        {"name": "VC", "rooms": []},
    )


def test_container_roundtrip_objects_and_flags() -> None:
    config = configuration()
    data = config.write()
    assert Configuration.read(data).write() == data
    objects = config.objects()
    assert [(o.number, o.flags, o.function_text) for o in objects] == [
        (1, "CW", "Switch"),
        (2, "CT", "Feedback"),
    ]
    assert objects[1].name == "Light Status"
    assert config.version == 2048


@pytest.mark.parametrize(
    "change", ["truncated", "length", "manufacturer", "api", "duplicate"]
)
def test_invalid_containers_fail_before_import(change: str) -> None:
    config = configuration()
    data = config.write()
    if change == "truncated":
        data = data[:-1]
    elif change == "length":
        data = struct.pack("<I", 0xFFFFFFFF) + data[4:]
    elif change == "manufacturer":
        data = data.replace(b"##FF0083", b"##FF0007")
    else:
        db = config.database
        if change == "api":
            db["versions"]["dca_api"] = "2"
        else:
            db["items"]["objects_list"]["1"]["status"] = "1"
        parts = (config.parts[0], pack_json(db), *config.parts[2:])
        data = b"".join(struct.pack("<I", len(p)) + p for p in parts)
    with pytest.raises(ValueError):
        Configuration.read(data)


def test_version_bytes_use_five_five_six_bit_packing() -> None:
    original = configuration()
    version = {"device": {"major": "1", "minor": "200", "revision": "129"}}
    config = Configuration((pack_json(version), *original.parts[1:]))
    assert config.version == (1 << 11) | (8 << 6) | 1
    assert Configuration.read(config.write()).parts[0] == config.parts[0]


def test_missing_api_and_compression_trailing_data_are_rejected() -> None:
    original = configuration()
    db = original.database
    del db["versions"]["dca_api"]
    with pytest.raises(ValueError, match="API"):
        Configuration(
            (original.parts[0], pack_json(db), *original.parts[2:])
        ).validate()
    parts = (original.parts[0], original.parts[1] + b"trailing", *original.parts[2:])
    with pytest.raises(ValueError, match="length"):
        Configuration(parts).validate()
