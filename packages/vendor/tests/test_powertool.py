"""Byte-exact originals and native model boundaries."""

import json
from dataclasses import replace

import pytest

from packages.vendor.tests.powertool_fixtures import archive_cases, open_archive
from xknxeditor.vendor.powertool import (
    Association,
    ChannelConfig,
    HostProfile,
    compile_image,
    default_config,
    from_json_bytes,
    to_json_bytes,
)

CASES = archive_cases("original-images.zip", manifest=False)


@pytest.mark.parametrize("case", CASES)
def test_original_pid(case: str) -> None:
    with open_archive("original-images.zip") as archive:
        config = from_json_bytes(archive.read(case + "/config.json"))
        host = json.loads(archive.read(case + "/host.json"))
        associations = [Association(**a) for a in host.pop("associations")]
        assert compile_image(
            config, HostProfile(**host), associations
        ).pid == archive.read(case + "/image.pid")
        assert from_json_bytes(to_json_bytes(config)) == config


def test_configuration_rejects_unmodelled_profiles() -> None:
    with pytest.raises(ValueError):
        replace(default_config("01"), schema_version=2)
    with pytest.raises(ValueError):
        ChannelConfig("unknown")
    with pytest.raises(ValueError):
        from_json_bytes(b'{"format":1,"format":2}')
    with pytest.raises(ValueError):
        compile_image(default_config("01"), flags={"absent": 1})


def test_allocation_holes_are_not_visible_number_offsets() -> None:
    cfg = replace(
        default_config("02"),
        channels=(
            ChannelConfig("hvac_button"),
            ChannelConfig("switch_button"),
            ChannelConfig("switch_rocker"),
            ChannelConfig("inactive"),
        ),
    )
    image = compile_image(cfg)
    assert any(o.index != o.number + 1 for o in image.objects if o.visible)
    assert (0, 0) in image.allocation[1:]
