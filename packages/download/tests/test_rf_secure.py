"""Tests for the domain and individual address of a KNX RF device with KNX Data Secure: system
broadcast S-A_Sync with its serial number, then the secured address writes."""

from __future__ import annotations

import secrets
from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIMessageCode
from xknx.cemi.flags import CEMIFlags, CEMIPriority
from xknx.exceptions import ManagementConnectionError
from xknx.secure.data_secure_asdu import (
    SecureData,
    SecurityAlgorithmIdentifier,
    SecurityALService,
    SecurityControlField,
)
from xknx.telegram import GroupAddress, IndividualAddress, apci
from xknx.telegram.tpci import TDataBroadcast

import xknxeditor.download.rf_commissioning as rf
from xknxeditor.download import data_secure
from xknxeditor.download.data_secure import DeviceSecurity, SecureProgrammingError
from xknxeditor.download.errors import AddressInUseError, SerialNumberNotFoundError

DOMAIN_ADDRESS = bytes.fromhex("00fa12345678")
SERIAL = bytes.fromhex("00837aa2039c")
FDSK = bytes(range(16))
TOOL_KEY = bytes(range(16, 32))
_NEW = IndividualAddress("1.2.20")
_DEVICE = IndividualAddress("15.15.255")


def _scf(service: SecurityALService) -> SecurityControlField:
    return SecurityControlField(
        tool_access=True,
        algorithm=SecurityAlgorithmIdentifier.CCM_ENCRYPTION,
        system_broadcast=True,
        service=service,
    )


class _SecureRFDevice:
    """A KNX RF device with KNX Data Secure, as an interface sees it: it answers a system
    broadcast S-A_Sync with its serial number and decrypts the secured frames to it."""

    def __init__(self, xknx: XKNX, key: bytes) -> None:
        self.xknx = xknx
        self.key = key
        self.answers = True
        self.sequence = 0x1000
        self.received: list[apci.APCI] = []
        self.frames: list[CEMILData] = []

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        data = cemi.data
        assert isinstance(data, CEMILData)
        self.frames.append(data)
        # a system broadcast: the System Broadcast flag 0
        assert data.flags.system_broadcast
        self.xknx.cemi_handler.handle_cemi_frame(
            CEMIFrame(code=CEMIMessageCode.L_DATA_CON, data=data)
        )
        payload = data.payload
        assert isinstance(payload, apci.SecureAPDU)
        assert payload.scf.system_broadcast
        if payload.scf.service is SecurityALService.S_A_SYNC_REQ:
            self._answer_sync(data, payload)
        else:
            self.received.append(self._decrypt(data, payload))

    def _answer_sync(self, request: CEMILData, payload: apci.SecureAPDU) -> None:
        serial, cipher = (
            payload.secured_data.secured_apdu[:6],
            payload.secured_data.secured_apdu[6:],
        )
        assert serial == SERIAL
        try:
            challenge = data_secure._decrypt(  # pyright: ignore[reportPrivateUsage]
                key=self.key,
                fields=data_secure._BlockFields(request),  # pyright: ignore[reportPrivateUsage]
                nonce=payload.secured_data.sequence_number_bytes,
                additional_data=payload.scf.to_knx() + serial,
                cipher=cipher,
                mac=payload.secured_data.message_authentication_code,
            )
        except SecureProgrammingError:
            return  # another key: no answer
        if not self.answers:
            return
        response = CEMILData(
            flags=CEMIFlags(priority=CEMIPriority.SYSTEM, system_broadcast=True),
            src_addr=_DEVICE,
            dst_addr=GroupAddress(0),
            tpci=TDataBroadcast(),
            payload=apci.IndividualAddressRead(),
        )
        random = secrets.token_bytes(6)
        scf = _scf(SecurityALService.S_A_SYNC_RES)
        plain = self.sequence.to_bytes(6, "big") + (0x2000).to_bytes(6, "big")
        cipher, mac = data_secure._encrypt(  # pyright: ignore[reportPrivateUsage]
            key=self.key,
            fields=data_secure._BlockFields(response),  # pyright: ignore[reportPrivateUsage]
            nonce=random,
            additional_data=scf.to_knx(),
            payload=plain,
        )
        response.payload = apci.SecureAPDU(
            scf=scf,
            secured_data=SecureData(
                sequence_number_bytes=bytes(
                    a ^ b for a, b in zip(challenge, random, strict=True)
                ),
                secured_apdu=cipher,
                message_authentication_code=mac,
            ),
        )
        self.xknx.cemi_handler.handle_cemi_frame(
            CEMIFrame(code=CEMIMessageCode.L_DATA_IND, data=response)
        )

    def _decrypt(self, data: CEMILData, payload: apci.SecureAPDU) -> apci.APCI:
        assert payload.scf.service is SecurityALService.S_A_DATA
        plain = data_secure._decrypt(  # pyright: ignore[reportPrivateUsage]
            key=self.key,
            fields=data_secure._BlockFields(data),  # pyright: ignore[reportPrivateUsage]
            nonce=payload.secured_data.sequence_number_bytes,
            additional_data=payload.scf.to_knx(),
            cipher=payload.secured_data.secured_apdu,
            mac=payload.secured_data.message_authentication_code,
        )
        return apci.APCI.from_knx(plain)


def _bus(
    monkeypatch: pytest.MonkeyPatch,
    key: bytes,
    reads: list[IndividualAddress | None],
) -> tuple[XKNX, _SecureRFDevice]:
    xknx = XKNX()
    device = _SecureRFDevice(xknx, key)
    xknx.knxip_interface = cast(Any, device)
    monkeypatch.setattr(rf, "_SYNC_TIMEOUT_S", 0.2)
    monkeypatch.setattr(rf, "_SERIAL_NUMBER_VERIFY_DELAY_S", 0)
    monkeypatch.setattr(rf, "_ADDRESS_ADOPTION_DELAY_S", 0)

    async def serial_read(xknx: XKNX, serial: bytes) -> IndividualAddress | None:
        assert serial == SERIAL
        return reads.pop(0)

    monkeypatch.setattr(rf, "_serial_number_read", serial_read)
    return xknx, device


def _security(*keys: bytes) -> DeviceSecurity:
    return DeviceSecurity(_DEVICE, keys[0], keys[1:])


async def test_the_addresses_are_written_secured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xknx, device = _bus(monkeypatch, FDSK, [_DEVICE, _NEW])
    previous = xknx.cemi_handler.data_secure
    # the Tool Key does not work, the FDSK does: a device in its factory state
    await rf.secure_addresses_by_serial_number(
        xknx, SERIAL, _NEW, _security(TOOL_KEY, FDSK), domain_address=DOMAIN_ADDRESS
    )
    assert device.received == [
        apci.DomainAddressSerialNumberWrite(
            serial=SERIAL, domain_address=DOMAIN_ADDRESS
        ),
        apci.IndividualAddressSerialWrite(address=_NEW, serial=SERIAL),
    ]
    # two synchronisations, one per key, then two secured frames with rising numbers
    assert len(device.frames) == 4
    numbers = [
        int.from_bytes(frame.payload.secured_data.sequence_number_bytes, "big")  # type: ignore[union-attr]
        for frame in device.frames[2:]
    ]
    assert numbers == [0x2000, 0x2001]
    assert xknx.cemi_handler.data_secure is previous


async def test_the_address_it_has_is_not_written_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xknx, device = _bus(monkeypatch, TOOL_KEY, [_NEW])
    await rf.secure_addresses_by_serial_number(xknx, SERIAL, _NEW, _security(TOOL_KEY))
    # no domain address given: only the synchronisation
    assert device.received == []


async def test_an_address_in_use_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    xknx, device = _bus(monkeypatch, TOOL_KEY, [_DEVICE])

    async def in_use() -> bool:
        return True

    with pytest.raises(AddressInUseError):
        await rf.secure_addresses_by_serial_number(
            xknx,
            SERIAL,
            _NEW,
            _security(TOOL_KEY),
            domain_address=DOMAIN_ADDRESS,
            address_in_use=in_use,
        )
    assert device.received == [
        apci.DomainAddressSerialNumberWrite(
            serial=SERIAL, domain_address=DOMAIN_ADDRESS
        )
    ]


async def test_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    # no key the device answers to
    xknx, device = _bus(monkeypatch, FDSK, [])
    device.answers = False
    with pytest.raises(SecureProgrammingError, match="Tool Key or FDSK"):
        await rf.secure_addresses_by_serial_number(xknx, SERIAL, _NEW, _security(FDSK))
    assert xknx.cemi_handler.data_secure is None
    # not found by serial number after the domain address
    xknx, _ = _bus(monkeypatch, FDSK, [None])
    with pytest.raises(SerialNumberNotFoundError):
        await rf.secure_addresses_by_serial_number(
            xknx, SERIAL, _NEW, _security(FDSK), domain_address=DOMAIN_ADDRESS
        )
    # the individual address not taken
    xknx, _ = _bus(monkeypatch, FDSK, [_DEVICE, _DEVICE])
    with pytest.raises(ManagementConnectionError, match=r"answers at 15\.15\.255"):
        await rf.secure_addresses_by_serial_number(xknx, SERIAL, _NEW, _security(FDSK))


async def test_other_frames_go_to_the_securer_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _Previous:
        def outgoing_cemi(self, cemi_data: CEMILData) -> CEMILData:
            calls.append("out")
            return cemi_data

        def received_cemi(self, cemi_data: CEMILData) -> CEMILData:
            calls.append("in")
            return cemi_data

    management = data_secure.SecureManagement(
        device=_security(FDSK), tool_address=IndividualAddress(0), serial_number=SERIAL
    )
    securer = rf._BroadcastSecurer(management, _Previous())  # pyright: ignore[reportPrivateUsage]
    group = CEMILData(
        src_addr=_DEVICE,
        dst_addr=GroupAddress("1/2/3"),
        tpci=TDataBroadcast(),
        payload=apci.GroupValueRead(),
    )
    assert securer.outgoing_cemi(group) is group
    assert securer.received_cemi(group) is group
    assert calls == ["out", "in"]
    alone = rf._BroadcastSecurer(management, None)  # pyright: ignore[reportPrivateUsage]
    assert alone.outgoing_cemi(group) is group
    assert alone.received_cemi(group) is group
    # a secured frame to the broadcast address the session cannot decrypt
    broken = CEMILData(
        src_addr=_DEVICE,
        dst_addr=GroupAddress(0),
        tpci=TDataBroadcast(),
        payload=apci.SecureAPDU(
            scf=_scf(SecurityALService.S_A_DATA),
            secured_data=SecureData(
                sequence_number_bytes=bytes(6),
                secured_apdu=b"\x00",
                message_authentication_code=bytes(4),
            ),
        ),
    )
    from xknx.exceptions import DataSecureError

    with pytest.raises(DataSecureError):
        alone.received_cemi(broken)
