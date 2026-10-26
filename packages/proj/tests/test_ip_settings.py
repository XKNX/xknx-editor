"""The IP settings of an IP device: reading an address, changing how it gets one, its addresses,
one undo step per change (also after reopening), and what else a change marks."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.ip_settings import known_address, parse_ipv4
from xknxeditor.proj.models import Device


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("192.168.1.10", "192.168.1.10"),
        (" 10.0.0.1 ", "10.0.0.1"),
        ("192.168.1", "192.168.0.1"),
        ("10.1", "10.0.0.1"),
        ("1", "0.0.0.1"),
        ("0x0a.0.0.010", "10.0.0.8"),
        ("255.255.255.255", "255.255.255.255"),
    ],
)
def test_addresses_read_like_the_usual_parsers(text: str, expected: str) -> None:
    assert parse_ipv4(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "1.2.3.", "300.1.1.1", "1.2.3.4.5", "1.2.3.256", "a.b.c.d", "0x", "::1", "09"],
)
def test_unreadable_addresses_are_rejected(text: str) -> None:
    with pytest.raises(ValueError, match="invalid IP address"):
        parse_ipv4(text)


def test_unknown_addresses() -> None:
    assert known_address(None) is None
    assert known_address("0.0.0.0") is None
    assert known_address("255.255.255.255") is None
    assert known_address("192.168.1.10") == "192.168.1.10"


def _project(tmp_path: Path) -> tuple[ProjectService, str, int]:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx", "P-0C01")
    area = next(a for a in svc.topology(pid, 0).areas if a.address == 1)
    segment = next(ln for ln in area.lines if ln.address == 1).segments[0].id
    device = svc.add_device(pid, segment, "P-1", address=5)
    svc.set_device_commissioning(
        pid, device, mac_address="70:B3:D5:DC:8E:11", medium_config_loaded=True
    )
    return svc, pid, device


def _device(svc: ProjectService, pid: str, device: int) -> Device:
    found = svc._state(pid).session.get(Device, device)  # type: ignore[attr-defined]
    assert found is not None
    return found


def test_a_fixed_address_and_back(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    assert svc.set_device_ip_config(
        pid,
        device,
        assign="Fixed",
        ip_address="192.168.1.10",
        subnet_mask="255.255.255.0",
        default_gateway="192.168.1",
    )
    stored = _device(svc, pid, device)
    assert stored.ip_config == {
        "MACAddress": "70:B3:D5:DC:8E:11",
        "Assign": "Fixed",
        "IPAddress": "192.168.1.10",
        "SubnetMask": "255.255.255.0",
        "DefaultGateway": "192.168.0.1",
    }
    # A fixed address has to be downloaded.
    assert stored.medium_config_loaded is False
    # Back to automatic: the addresses stay.
    assert svc.set_device_ip_config(pid, device, assign="Auto")
    assert stored.ip_config is not None
    assert stored.ip_config["Assign"] == "Auto"
    assert stored.ip_config["IPAddress"] == "192.168.1.10"


def test_automatic_or_custom_needs_no_download(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    svc.set_device_ip_config(pid, device, ip_address="10.0.0.7")
    assert _device(svc, pid, device).medium_config_loaded is True
    svc.set_device_ip_config(pid, device, assign="Fixed", tool_managed=False)
    assert _device(svc, pid, device).medium_config_loaded is True


def test_nothing_changes_without_a_difference(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    svc.set_device_ip_config(pid, device, ip_address="10.0.0.7")
    entries = len(svc.history(pid))
    assert not svc.set_device_ip_config(pid, device, ip_address="10.0.0.7")
    assert not svc.set_device_ip_config(pid, device, assign="Auto")
    assert len(svc.history(pid)) == entries


def test_unreadable_input_changes_nothing(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    with pytest.raises(ValueError, match="invalid IP address"):
        svc.set_device_ip_config(pid, device, ip_address="1.2.3.")
    with pytest.raises(ValueError, match="unknown IP assignment"):
        svc.set_device_ip_config(pid, device, assign="Dhcp")
    assert _device(svc, pid, device).ip_config == {"MACAddress": "70:B3:D5:DC:8E:11"}


def test_each_change_is_one_undo_step_and_stamps_last_modified(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    stored = _device(svc, pid, device)
    svc.set_device_ip_config(pid, device, assign="Fixed", ip_address="10.0.0.7")
    stored.last_modified = None
    assert svc.undo(pid)
    assert stored.ip_config == {"MACAddress": "70:B3:D5:DC:8E:11"}
    assert stored.medium_config_loaded is True
    # Undoing it changes the configuration too.
    assert stored.last_modified
    assert svc.redo(pid)
    assert stored.ip_config is not None
    assert stored.ip_config["IPAddress"] == "10.0.0.7"
    assert stored.medium_config_loaded is False


def test_undo_after_reopening(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    svc.set_device_ip_config(pid, device, assign="Fixed", ip_address="10.0.0.7")
    svc.close(pid)
    pid = svc.open(tmp_path / "p.xknx")
    assert svc.undo(pid)
    assert _device(svc, pid, device).ip_config == {"MACAddress": "70:B3:D5:DC:8E:11"}
    assert svc.redo(pid)
    stored = _device(svc, pid, device).ip_config
    assert stored is not None
    assert stored["Assign"] == "Fixed"


def test_a_download_keeps_the_address_it_found(tmp_path: Path) -> None:
    svc, pid, device = _project(tmp_path)
    stored = _device(svc, pid, device)
    stored.last_modified = None
    svc.set_device_commissioning(pid, device, ip_address="10.0.0.7", download=True)
    assert stored.ip_config == {
        "MACAddress": "70:B3:D5:DC:8E:11",
        "IPAddress": "10.0.0.7",
    }
    # A download leaves "Last Modified" as it was.
    assert stored.last_modified is None
    assert svc.undo(pid)
    assert stored.ip_config == {"MACAddress": "70:B3:D5:DC:8E:11"}
