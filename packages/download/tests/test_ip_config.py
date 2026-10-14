"""IP settings a full download writes to the KNXnet/IP parameter object."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from xknxeditor.download.image import DownloadImage
from xknxeditor.download.ip_config import (
    IpDeviceConfig,
    applies,
    ip_config_controls,
    with_ip_config_controls,
)
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.application_program_ipconfig_t import (
    ApplicationProgramIpconfig,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_prop_t import (
    LdCtrlLoadImageProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.prod import Application

from .test_extended_properties import ExtendedDevice

# The IP interface of the recording: 1.1.1 with the additional addresses 1.1.239 down to 1.1.224.
_RECORDED = IpDeviceConfig(
    individual_address=0x1101,
    friendly_name="OpenKNX: IP-Interface (Dev)",
    additional_addresses=tuple(range(239, 223, -1)),
)
# (property, element count, data) as a recorded download of this configuration wrote them.
_RECORDED_WRITES = [
    (51, 1, "0000"),
    (66, 1, "e000170c"),
    (67, 1, "20"),
    (76, 30, "4f70656e4b4e583a2049502d496e74657266616365202844657629000000"),
    (55, 1, "04"),
    (53, 16, "11ef11ee11ed11ec11eb11ea11e911e811e711e611e511e411e311e211e111e0"),
]


def _application(
    *,
    additional: int = 16,
    mask: str = "MV-07B0",
    ipconfig: ApplicationProgramIpconfig = ApplicationProgramIpconfig.TOOL,
) -> Application:
    return cast(
        "Application",
        SimpleNamespace(
            manufacturer_id="M-00FA",
            program=SimpleNamespace(
                additional_addresses_count=additional,
                mask_version=mask,
                ipconfig=ipconfig,
                pei_type=0,
                application_number=0xA502,
                application_version=0x13,
                is_secure_enabled=False,
                static=SimpleNamespace(
                    options=SimpleNamespace(supports_extended_property_services=True)
                ),
            ),
        ),
    )


def _writes(controls: list[object]) -> list[tuple[int, int, str]]:
    return [
        (c.prop_id, c.count, (c.inline_data or b"").hex())
        for c in controls
        if isinstance(c, LdCtrlWriteProp)
    ]


def test_controls_match_the_recorded_download() -> None:
    controls = ip_config_controls(_application(), _RECORDED)
    mac = controls[0]
    assert isinstance(mac, LdCtrlLoadImageProp)
    assert (mac.obj_type, mac.prop_id, mac.count) == (11, 64, 1)
    assert all(
        c.obj_type == 11 and c.verify and c.start_element == 1
        for c in controls[1:]
        if isinstance(c, LdCtrlWriteProp)
    )
    assert _writes(controls) == _RECORDED_WRITES


def test_fixed_address_writes_ip_mask_and_gateway() -> None:
    config = IpDeviceConfig(
        individual_address=0x1101,
        fixed_address=True,
        ip_address="192.168.1.20",
        subnet_mask="255.255.255.0",
        default_gateway="192.168.1.1",
        routing_multicast_address="224.0.23.13",
    )
    writes = _writes(ip_config_controls(_application(additional=2), config))
    assert (66, 1, "e000170d") in writes
    assert (55, 1, "01") in writes
    assert writes[-4:] == [
        (60, 1, "c0a80114"),
        (61, 1, "ffffff00"),
        (62, 1, "c0a80101"),
        (53, 2, "ffffffff"),
    ]


def test_friendly_name_is_latin1_and_transliterated() -> None:
    def name(text: str) -> str:
        config = IpDeviceConfig(individual_address=0x1101, friendly_name=text)
        writes = _writes(ip_config_controls(_application(additional=0), config))
        return next(bytes.fromhex(data) for pid, _, data in writes if pid == 76).decode(
            "latin-1"
        )

    assert name("  Küche  ") == "Küche".ljust(30, "\x00")
    assert name("Zone Řez") == "Zone Rez".ljust(30, "\x00")
    assert name("x" * 40) == "x" * 30


def test_custom_ip_configuration_is_not_managed() -> None:
    assert applies(_application())
    assert not applies(_application(ipconfig=ApplicationProgramIpconfig.CUSTOM))


def test_controls_go_before_the_last_restart() -> None:
    first, last = LdCtrlRestart(), LdCtrlRestart()
    controls = with_ip_config_controls(_application(), [first, last], _RECORDED)
    assert controls[0] is first and controls[-1] is last
    assert isinstance(controls[1], LdCtrlLoadImageProp)


def test_line_coupler_controls_follow_its_object_load() -> None:
    load = LdCtrlLoad(obj_type=6)
    coupler_write = LdCtrlWriteProp(
        obj_type=6, prop_id=52, verify=True, inline_data=b"\x00"
    )
    restart = LdCtrlRestart()
    controls = with_ip_config_controls(
        _application(mask="MV-091A"), [load, coupler_write, restart], _RECORDED
    )
    assert controls[:2] == [load, coupler_write]
    assert isinstance(controls[2], LdCtrlLoadImageProp)
    assert controls[-1] is restart


async def test_download_writes_settings_and_reads_the_mac_address() -> None:
    device = ExtendedDevice()
    mac = bytes.fromhex("e8f60ae0623d")
    device.ext_values[(11, 1, 64)] = mac
    application = _application()
    controls = with_ip_config_controls(application, [LdCtrlRestart()], _RECORDED)
    runner = LoadProcedureRunner(
        application,
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device, max_apdu_length=50),
        controls=controls,
    )
    await runner.run()

    for pid, _, data in _RECORDED_WRITES:
        assert device.ext_values[(11, 1, pid)].hex() == data
    assert runner.captured_property(11, 64) == mac
    assert device.restarted
    assert not any(
        type(p).__name__ == "PropertyExtDescriptionRead" for p in device.sent
    )
