"""Line coupler filter table built from a project's plain XML."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from xknxeditor.proj.core.coupler_filter import (
    FILTER_BYTES,
    ProjectLinks,
    coupler_settings,
    expected_config_bytes,
    filter_groups,
    filter_image,
    ga_int,
    ga_text,
    image_groups,
    links_from_knxproj,
)

NS = "http://knx.org/xml/project/23"


def _links() -> ProjectLinks:
    links = ProjectLinks()
    links.device_line = {
        "1.1.0": (1, 1),
        "1.1.5": (1, 1),
        "1.1.6": (1, 1),
        "1.0.3": (1, 0),
    }
    links.device_groups = {
        "1.1.0": {ga_int("9/9/9")},  # the coupler's own links never count
        "1.1.5": {ga_int("1/2/3"), ga_int("2/0/1"), ga_int("14/0/1")},
        "1.1.6": {ga_int("1/2/3")},
        "1.0.3": {ga_int("1/2/3"), ga_int("4/0/0")},
    }
    links.unfiltered = {ga_int("5/5/5")}
    return links


def test_ga_round_trip() -> None:
    assert ga_int("1/2/3") == (1 << 11) | (2 << 8) | 3
    assert ga_text(ga_int("13/7/255")) == "13/7/255"
    assert ga_int("1/515") == (1 << 11) | 515


def test_ets_rule_takes_every_group_linked_on_the_line() -> None:
    got = {ga_text(g) for g in filter_groups(_links(), "1.1.0")}
    # 1/2/3 and 2/0/1 linked on line 1.1; 5/5/5 unfiltered; 14/0/1 is outside the table;
    # 4/0/0 only outside the line; 9/9/9 only on the coupler itself
    assert got == {"1/2/3", "2/0/1", "5/5/5"}


def test_partner_rule_needs_a_link_across_the_coupler() -> None:
    got = {ga_text(g) for g in filter_groups(_links(), "1.1.0", partner=True)}
    assert got == {"1/2/3", "5/5/5"}


def test_not_a_coupler_address() -> None:
    with pytest.raises(ValueError):
        filter_groups(_links(), "1.1.5")


@pytest.mark.parametrize("lsb_first", [True, False])
def test_image_round_trip(lsb_first: bool) -> None:
    groups = {0, 7, 8, ga_int("1/2/3"), ga_int("13/7/255")}
    image = filter_image(groups | {ga_int("14/0/0")}, lsb_first=lsb_first)
    assert len(image) == FILTER_BYTES
    assert image_groups(image, lsb_first=lsb_first) == groups


def test_image_bit_order() -> None:
    assert filter_image({0})[0] == 0x01
    assert filter_image({0}, lsb_first=False)[0] == 0x80
    assert filter_image({9})[1] == 0x02


APP = """<?xml version="1.0"?>
<KNX xmlns="http://knx.org/xml/project/23"><ManufacturerData><Manufacturer RefId="M-0001">
<ApplicationPrograms><ApplicationProgram Id="M-0001_A-0001-21-51F4" MaskVersion="MV-0912">
<Static>
<ParameterTypes>
<ParameterType Id="T-grp" Name="t_LKgrpDef"><TypeRestriction Base="Value" SizeInBit="2">
<Enumeration Text="transmit all (for testing only)" Value="1"/><Enumeration Text="block" Value="2"/>
<Enumeration Text="filter" Value="3"/></TypeRestriction></ParameterType>
<ParameterType Id="T-ack" Name="t_LKackEn"><TypeRestriction Base="Value" SizeInBit="1">
<Enumeration Text="always" Value="0"/><Enumeration Text="only if let through" Value="1"/>
</TypeRestriction></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="M-0001_A-0001-21-51F4_P-3" Name="LKgrpConf.SMgrpDef" ParameterType="T-grp"
 Text="Group telegrams line-&gt;main line" Value="3"><Memory CodeSegment="S" Offset="28" BitOffset="2"/></Parameter>
<Parameter Id="M-0001_A-0001-21-51F4_P-2" Name="LKgrpConf.MSgrpDef" ParameterType="T-grp"
 Text="Group telegrams main line-&gt;line" Value="3"><Memory CodeSegment="S" Offset="28" BitOffset="6"/></Parameter>
<Parameter Id="M-0001_A-0001-21-51F4_P-11" Name="LKdef.mainLnAck" ParameterType="T-ack"
 Text="Telegram confirmation on main line" Value="1"><Memory CodeSegment="S" Offset="27" BitOffset="4"/></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="M-0001_A-0001-21-51F4_P-3_R-3" RefId="M-0001_A-0001-21-51F4_P-3"/>
<ParameterRef Id="M-0001_A-0001-21-51F4_P-2_R-2" RefId="M-0001_A-0001-21-51F4_P-2"/>
<ParameterRef Id="M-0001_A-0001-21-51F4_P-11_R-11" RefId="M-0001_A-0001-21-51F4_P-11"/>
</ParameterRefs>
</Static></ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""

PROJECT = """<?xml version="1.0"?>
<KNX xmlns="http://knx.org/xml/project/23"><Project Id="P-0001"><Installations><Installation>
<Topology><Area Address="1"><Line Address="0">
<DeviceInstance Id="P-0001-0_DI-2" Address="3"><ComObjectInstanceRefs>
<ComObjectInstanceRef RefId="O-1_R-1" Links="GA-1 GA-3"/></ComObjectInstanceRefs></DeviceInstance>
</Line><Line Address="1">
<DeviceInstance Id="P-0001-0_DI-1" Address="0"
 Hardware2ProgramRefId="M-0001_H-hp.5F00147-1_HP-0001-21-51F4">
<ParameterInstanceRefs>
<ParameterInstanceRef RefId="M-0001_A-0001-21-51F4_P-3_R-3" Value="1"/>
<ParameterInstanceRef RefId="M-0001_A-0001-21-51F4_P-11_R-11" Value="0"/>
</ParameterInstanceRefs></DeviceInstance>
<DeviceInstance Id="P-0001-0_DI-3" Address="5"><ComObjectInstanceRefs>
<ComObjectInstanceRef RefId="O-1_R-1" Links="GA-1"/>
<ComObjectInstanceRef RefId="O-2_R-2" Links="GA-2"/></ComObjectInstanceRefs></DeviceInstance>
<DeviceInstance Id="P-0001-0_DI-4"/>
</Line></Area></Topology>
<GroupAddresses><GroupRanges><GroupRange><GroupRange>
<GroupAddress Id="P-0001-0_GA-1" Address="2563" Name="Shared"/>
<GroupAddress Id="P-0001-0_GA-2" Address="4097" Name="Only on 1.1"/>
<GroupAddress Id="P-0001-0_GA-3" Address="8192" Name="Only on 1.0"/>
<GroupAddress Id="P-0001-0_GA-4" Address="10240" Name="Pass" Unfiltered="true"/>
</GroupRange></GroupRange></GroupRanges></GroupAddresses>
</Installation></Installations></Project></KNX>"""


@pytest.fixture
def knxproj(tmp_path: Path) -> Path:
    path = tmp_path / "t.knxproj"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("P-0001/0.xml", PROJECT)
        z.writestr("M-0001/M-0001_A-0001-21-51F4.xml", APP)
    return path


def test_links_from_knxproj(knxproj: Path) -> None:
    links = links_from_knxproj(str(knxproj))
    assert links.device_line["1.1.5"] == (1, 1)
    assert "1.1.None" not in links.device_line  # unaddressed device left out
    assert links.device_groups["1.1.5"] == {2563, 4097}
    assert links.unfiltered == {10240}
    assert {ga_text(g) for g in filter_groups(links, "1.1.0")} == {
        "1/2/3",
        "2/0/1",
        "5/0/0",
    }
    assert {ga_text(g) for g in filter_groups(links, "1.1.0", partner=True)} == {
        "1/2/3",
        "5/0/0",
    }


def test_protected_project_refused(tmp_path: Path) -> None:
    path = tmp_path / "p.knxproj"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("P-0001.zip", b"x")
    with pytest.raises(ValueError):
        links_from_knxproj(str(path))


def test_coupler_settings_and_bytes(knxproj: Path) -> None:
    settings = {s.name: s for s in coupler_settings(str(knxproj), "1.1.0")}
    assert settings["LKgrpConf.SMgrpDef"].value == 1  # overridden: transmit all
    assert settings["LKgrpConf.MSgrpDef"].value == 3  # default: filter
    assert settings["LKgrpConf.SMgrpDef"].choices[1].startswith("transmit all")
    assert settings["LKgrpConf.SMgrpDef"].address == 0x011C
    assert settings["LKgrpConf.SMgrpDef"].mask == 0x30
    assert settings["LKgrpConf.MSgrpDef"].mask == 0x03
    assert settings["LKdef.mainLnAck"].mask == 0x08
    expected = expected_config_bytes(settings.values())
    assert expected[0x011C] == (0x13, 0x33)
    assert expected[0x011B] == (0x00, 0x08)


def test_coupler_settings_unknown_device(knxproj: Path) -> None:
    with pytest.raises(ValueError):
        coupler_settings(str(knxproj), "1.1.9")
