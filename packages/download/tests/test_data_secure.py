"""Tests for KNX Data Secure point-to-point programming (Tool Key access).

The vector tests reproduce the worked examples in KNX Standard v3.0.0, 3/3/7
Annex C (C.1.1-C.1.4) byte-for-byte, so the CCM construction is pinned to the
authoritative reference and cannot drift.
"""

from __future__ import annotations

import pytest
from xknx.cemi.cemi_frame import CEMILData
from xknx.secure.data_secure_asdu import SecureData
from xknx.telegram import Telegram
from xknx.telegram.address import IndividualAddress
from xknx.telegram.apci import MemoryRead, SecureAPDU
from xknx.telegram.tpci import TDataConnected

from xknxeditor.download.data_secure import (
    DATA_SCF,
    SYNC_REQ_SCF,
    SYNC_RES_SCF,
    DeviceSecurity,
    SecureManagement,
    SecureProgrammingError,
    ToolKeyCemiSecure,
    _BlockFields,
    _decrypt,
    _encrypt,
)

# 3/3/7 Annex C security parameters.
TOOL_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
DEVICE = IndividualAddress("15.15.0")  # FF00h
TOOL = IndividualAddress("15.15.103")  # FF67h


def _cemi(
    source: IndividualAddress,
    destination: IndividualAddress,
    *,
    sequence_number: int = 0,
    payload: object = None,
) -> CEMILData:
    """Build a connection-oriented CEMILData, like the transport layer would."""
    return CEMILData.init_from_telegram(
        telegram=Telegram(
            source_address=source,
            destination_address=destination,
            tpci=TDataConnected(sequence_number=sequence_number),
            payload=payload,  # type: ignore[arg-type]
        ),
        src_addr=source,
    )


def test_scf_values_match_reference() -> None:
    # 3/3/7 Annex C: SCF 90h (S-A_Data), 92h (S-A_Sync.req), 93h (S-A_Sync.res).
    assert DATA_SCF.to_knx() == bytes.fromhex("90")
    assert SYNC_REQ_SCF.to_knx() == bytes.fromhex("92")
    assert SYNC_RES_SCF.to_knx() == bytes.fromhex("93")


def test_annex_c_1_1_s_a_data() -> None:
    # C.1.1: SA=FF67 DA=FF00 SCF=90 SeqNr=4, connectionless (TPCI/APCI 03F1).
    telegram = Telegram(source_address=TOOL, destination_address=DEVICE)
    fields = _BlockFields.from_telegram(telegram, source=TOOL)
    plain = bytes.fromhex("03D705351001202122232425262728292A2B2C2D2E2F")
    cipher, mac = _encrypt(
        key=TOOL_KEY,
        fields=fields,
        nonce=(4).to_bytes(6, "big"),
        additional_data=DATA_SCF.to_knx(),
        payload=plain,
    )
    assert (
        cipher + mac
    ).hex() == "6767242a2308ca76a11774214ee4cf5d94909f743d050d8fc168"


def test_annex_c_1_2_s_a_data_response() -> None:
    # C.1.2: SA=FF00 DA=FF67 SCF=90 SeqNr=3, connectionless.
    telegram = Telegram(source_address=DEVICE, destination_address=TOOL)
    fields = _BlockFields.from_telegram(telegram, source=DEVICE)
    plain = bytes.fromhex("03D605351001202122232425262728292A2B2C2D2E2F")
    cipher, mac = _encrypt(
        key=TOOL_KEY,
        fields=fields,
        nonce=(3).to_bytes(6, "big"),
        additional_data=DATA_SCF.to_knx(),
        payload=plain,
    )
    assert (
        cipher + mac
    ).hex() == "706f533105503557cb2b24f1dd341b60b7e017ecd6b06849a72b"


def test_annex_c_1_3_s_a_sync_req() -> None:
    # C.1.3: SCF=92 SeqNrLocal=1 Serial=0 Challenge=3, connection-oriented
    # (TPCI/APCI 43F1). A = SCF | KNX Serial Number, P = Challenge.
    fields = _BlockFields(_cemi(TOOL, DEVICE, sequence_number=0))
    cipher, mac = _encrypt(
        key=TOOL_KEY,
        fields=fields,
        nonce=(1).to_bytes(6, "big"),
        additional_data=SYNC_REQ_SCF.to_knx() + b"\x00" * 6,
        payload=(3).to_bytes(6, "big"),
    )
    assert (cipher + mac).hex() == "c1cf4506f09bd79fab55"


def test_annex_c_1_4_s_a_sync_res() -> None:
    # C.1.4: SCF=93 SeqNrRemote=3 SeqNrLocal=4 Random=AA*6, connection-oriented.
    # Nonce is the Random value; A = SCF, P = SeqNrRemote | SeqNrLocal.
    fields = _BlockFields(_cemi(DEVICE, TOOL, sequence_number=0))
    cipher, mac = _encrypt(
        key=TOOL_KEY,
        fields=fields,
        nonce=bytes.fromhex("AAAAAAAAAAAA"),
        additional_data=SYNC_RES_SCF.to_knx(),
        payload=(3).to_bytes(6, "big") + (4).to_bytes(6, "big"),
    )
    assert (cipher + mac).hex() == "9c023ad25e146470693e638d5b70cac4"


def test_device_security_normalizes_key() -> None:
    assert DeviceSecurity(DEVICE, TOOL_KEY).tool_key == TOOL_KEY
    assert DeviceSecurity(DEVICE, TOOL_KEY.hex()).tool_key == TOOL_KEY
    with pytest.raises(SecureProgrammingError, match="16 octets"):
        DeviceSecurity(DEVICE, b"\x00" * 15)
    with pytest.raises(SecureProgrammingError, match="hex"):
        DeviceSecurity(DEVICE, "zz")


def _device_sync_response(
    sync_request: CEMILData, *, sending: int, tool_expected: int
) -> CEMILData:
    """Emulate a device answering an S-A_Sync request with the same Tool Key.

    Verifies the request, then builds the S-A_Sync response frame the tool must
    accept, so a passing round trip proves both directions are consistent.
    """
    payload = sync_request.payload
    assert isinstance(payload, SecureAPDU)
    challenge = _decrypt(
        key=TOOL_KEY,
        fields=_BlockFields(sync_request),
        nonce=payload.secured_data.sequence_number_bytes,
        additional_data=payload.scf.to_knx() + b"\x00" * 6,
        cipher=payload.secured_data.secured_apdu[6:],
        mac=payload.secured_data.message_authentication_code,
    )
    random = bytes.fromhex("AAAAAAAAAAAA")
    response = _cemi(DEVICE, TOOL, sequence_number=0)
    cipher, mac = _encrypt(
        key=TOOL_KEY,
        fields=_BlockFields(response),
        nonce=random,
        additional_data=SYNC_RES_SCF.to_knx(),
        payload=sending.to_bytes(6, "big") + tool_expected.to_bytes(6, "big"),
    )
    challenge_xor_random = bytes(a ^ b for a, b in zip(challenge, random, strict=True))
    response.payload = SecureAPDU(
        scf=SYNC_RES_SCF,
        secured_data=SecureData(
            sequence_number_bytes=challenge_xor_random,
            secured_apdu=cipher,
            message_authentication_code=mac,
        ),
    )
    return response


def _device_secured_response(
    payload: MemoryRead, *, sequence_number: int, key: bytes = TOOL_KEY
) -> CEMILData:
    """Emulate a device sending a secured S-A_Data response frame."""
    response = _cemi(DEVICE, TOOL, sequence_number=0)
    cipher, mac = _encrypt(
        key=key,
        fields=_BlockFields(response),
        nonce=sequence_number.to_bytes(6, "big"),
        additional_data=DATA_SCF.to_knx(),
        payload=bytes(payload.to_knx()),
    )
    response.payload = SecureAPDU(
        scf=DATA_SCF,
        secured_data=SecureData(
            sequence_number_bytes=sequence_number.to_bytes(6, "big"),
            secured_apdu=cipher,
            message_authentication_code=mac,
        ),
    )
    return response


def test_sync_then_data_round_trip() -> None:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    management.arm_sync()

    # A frame to the device becomes the S-A_Sync request; it stays armed (so a
    # transport retransmission replays the same request) until the response.
    request = management.build_sync_request_cemi(_cemi(TOOL, DEVICE, sequence_number=0))
    assert management.sync_armed
    response = _device_sync_response(request, sending=0x100, tool_expected=0x200)
    assert management.decode_sync_response_cemi(response) is True
    assert management.synchronized
    assert not management.sync_armed

    # The first secured data frame uses the SeqNr the device returned.
    wrapped = management.wrap_cemi(
        _cemi(
            TOOL, DEVICE, sequence_number=1, payload=MemoryRead(address=0x10, count=1)
        )
    )
    assert isinstance(wrapped.payload, SecureAPDU)
    assert (
        int.from_bytes(wrapped.payload.secured_data.sequence_number_bytes, "big")
        == 0x200
    )

    # A device response secured with a higher SeqNr decrypts back to the APDU.
    device_frame = _device_secured_response(
        MemoryRead(address=0x10, count=1), sequence_number=0x100
    )
    unwrapped = management.unwrap_cemi(device_frame)
    assert isinstance(unwrapped.payload, MemoryRead)
    assert unwrapped.payload.address == 0x10


def test_wrap_before_sync_raises() -> None:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    with pytest.raises(SecureProgrammingError, match="not synchronized"):
        management.wrap_cemi(
            _cemi(TOOL, DEVICE, payload=MemoryRead(address=0x10, count=1))
        )


def test_unwrap_rejects_replayed_sequence_number() -> None:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    management.arm_sync()
    request = management.build_sync_request_cemi(_cemi(TOOL, DEVICE, sequence_number=0))
    response = _device_sync_response(request, sending=0x100, tool_expected=0x200)
    management.decode_sync_response_cemi(response)

    # last valid remote is sending-1 = 0xFF; a frame at 0xFF must be rejected.
    device_frame = _device_secured_response(
        MemoryRead(address=0x10, count=1), sequence_number=0xFF
    )
    with pytest.raises(SecureProgrammingError, match="sequence number too low"):
        management.unwrap_cemi(device_frame)


def test_hook_passes_through_foreign_and_control_frames() -> None:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    hook = ToolKeyCemiSecure(management, DEVICE)

    # A frame to another device is untouched.
    other = IndividualAddress("1.1.1")
    to_other = _cemi(TOOL, other, payload=MemoryRead(address=0x10, count=1))
    assert hook.outgoing_cemi(to_other) is to_other

    # A control frame to the device (no APDU) is untouched.
    control = _cemi(TOOL, DEVICE)
    assert hook.outgoing_cemi(control) is control


def test_hook_rejects_plaintext_response_from_device() -> None:
    # In a secure session, a plaintext APDU from the target is a fault or a spoofed response a bus
    # attacker injected to satisfy a pending request; the hook must reject it (DataSecureError ->
    # xknx drops the frame), never pass unauthenticated data through.
    from xknx.exceptions import DataSecureError
    from xknx.telegram.apci import MemoryResponse

    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    hook = ToolKeyCemiSecure(management, DEVICE)

    plaintext = _cemi(DEVICE, TOOL, payload=MemoryResponse(address=0x10, data=b"\x00"))
    with pytest.raises(DataSecureError):
        hook.received_cemi(plaintext)

    # A control frame from the device (no APDU) still passes through unchanged.
    control = _cemi(DEVICE, TOOL)
    assert hook.received_cemi(control) is control


def test_hook_wraps_and_unwraps_over_session() -> None:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    hook = ToolKeyCemiSecure(management, DEVICE)

    # Sync via the hook: the armed session turns the outgoing frame into the req.
    management.arm_sync()
    request = hook.outgoing_cemi(
        _cemi(TOOL, DEVICE, sequence_number=0, payload=MemoryRead(address=0, count=1))
    )
    assert isinstance(request.payload, SecureAPDU)
    assert request.payload.scf.service is SYNC_REQ_SCF.service
    response = _device_sync_response(request, sending=0x100, tool_expected=0x200)
    hook.received_cemi(response)
    assert management.synchronized

    # Now a plain outgoing frame is wrapped as S-A_Data.
    wrapped = hook.outgoing_cemi(
        _cemi(
            TOOL, DEVICE, sequence_number=1, payload=MemoryRead(address=0x10, count=1)
        )
    )
    assert isinstance(wrapped.payload, SecureAPDU)
    assert wrapped.payload.scf.service is DATA_SCF.service

    # A secured device frame is unwrapped back to the plain APDU.
    device_frame = _device_secured_response(
        MemoryRead(address=0x10, count=1), sequence_number=0x100
    )
    unwrapped = hook.received_cemi(device_frame)
    assert isinstance(unwrapped.payload, MemoryRead)


def test_sync_request_replays_identical_on_retransmission() -> None:
    # A missing T_ACK makes xknx resend the same trigger frame; the hook must
    # replay the identical Secure APDU, not build a fresh one or wrap as data.
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    management.arm_sync()
    first = management.build_sync_request_cemi(_cemi(TOOL, DEVICE, sequence_number=0))
    second = management.build_sync_request_cemi(_cemi(TOOL, DEVICE, sequence_number=0))
    assert isinstance(first.payload, SecureAPDU)
    assert isinstance(second.payload, SecureAPDU)
    assert first.payload.secured_data.to_knx() == second.payload.secured_data.to_knx()


def test_decode_rejects_zero_remote_sequence_number() -> None:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    management.arm_sync()
    request = management.build_sync_request_cemi(_cemi(TOOL, DEVICE, sequence_number=0))
    # SeqNrRemote 0 would make last_valid_remote negative and admit a replay.
    response = _device_sync_response(request, sending=0, tool_expected=0x200)
    with pytest.raises(SecureProgrammingError, match="sequence number 0"):
        management.decode_sync_response_cemi(response)


def test_unwrap_rejects_bad_apdu_prefix() -> None:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    management.arm_sync()
    request = management.build_sync_request_cemi(_cemi(TOOL, DEVICE, sequence_number=0))
    management.decode_sync_response_cemi(
        _device_sync_response(request, sending=0x100, tool_expected=0x200)
    )
    # Encrypt a plaintext whose first octet has non-zero reserved bits.
    response = _cemi(DEVICE, TOOL, sequence_number=0)
    cipher, mac = _encrypt(
        key=TOOL_KEY,
        fields=_BlockFields(response),
        nonce=(0x100).to_bytes(6, "big"),
        additional_data=DATA_SCF.to_knx(),
        payload=b"\xfc\x00",
    )
    response.payload = SecureAPDU(
        scf=DATA_SCF,
        secured_data=SecureData(
            sequence_number_bytes=(0x100).to_bytes(6, "big"),
            secured_apdu=cipher,
            message_authentication_code=mac,
        ),
    )
    with pytest.raises(SecureProgrammingError, match="reserved prefix"):
        management.unwrap_cemi(response)


def test_wrap_rejects_exhausted_sequence_number() -> None:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    management.arm_sync()
    request = management.build_sync_request_cemi(_cemi(TOOL, DEVICE, sequence_number=0))
    management.decode_sync_response_cemi(
        _device_sync_response(request, sending=0x100, tool_expected=0x200)
    )
    # Drive the 48-bit Sequence Number Sending past its maximum.
    management._next_sequence_number = 0xFFFFFFFFFFFF + 1
    with pytest.raises(SecureProgrammingError, match="exhausted"):
        management.wrap_cemi(
            _cemi(TOOL, DEVICE, payload=MemoryRead(address=0x10, count=1))
        )


def test_hook_delegates_foreign_traffic_to_previous_securer() -> None:
    class _Recorder:
        def __init__(self) -> None:
            self.out: list[CEMILData] = []
            self.inc: list[CEMILData] = []

        def outgoing_cemi(self, cemi_data: CEMILData) -> CEMILData:
            self.out.append(cemi_data)
            return cemi_data

        def received_cemi(self, cemi_data: CEMILData) -> CEMILData:
            self.inc.append(cemi_data)
            return cemi_data

    previous = _Recorder()
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    hook = ToolKeyCemiSecure(management, DEVICE, previous=previous)

    other = IndividualAddress("1.1.1")
    out = _cemi(TOOL, other, payload=MemoryRead(address=0x10, count=1))
    inc = _cemi(other, TOOL, payload=MemoryRead(address=0x10, count=1))
    hook.outgoing_cemi(out)
    hook.received_cemi(inc)
    assert previous.out == [out]
    assert previous.inc == [inc]


def _synchronized() -> SecureManagement:
    management = SecureManagement(
        device=DeviceSecurity(DEVICE, TOOL_KEY), tool_address=TOOL
    )
    management.arm_sync()
    request = management.build_sync_request_cemi(_cemi(TOOL, DEVICE, sequence_number=0))
    management.decode_sync_response_cemi(
        _device_sync_response(request, sending=0x100, tool_expected=0x200)
    )
    return management


def test_tool_key_change_takes_effect_with_the_device_answer() -> None:
    management = _synchronized()
    new_key = bytes(range(16, 32))
    management.change_tool_key(new_key)
    # The Tool Key write itself (and a retransmission of it) still uses the current key.
    management.wrap_cemi(
        _cemi(TOOL, DEVICE, sequence_number=1, payload=MemoryRead(address=0, count=1))
    )
    assert management.active_key == TOOL_KEY
    answer = _device_secured_response(
        MemoryRead(address=0x10, count=1), sequence_number=0x100, key=new_key
    )
    assert isinstance(management.unwrap_cemi(answer).payload, MemoryRead)
    assert management.active_key == new_key


def test_an_answer_with_the_old_key_keeps_it() -> None:
    management = _synchronized()
    management.change_tool_key(bytes(range(16, 32)))
    answer = _device_secured_response(
        MemoryRead(address=0x10, count=1), sequence_number=0x100
    )
    assert isinstance(management.unwrap_cemi(answer).payload, MemoryRead)
    assert management.active_key == TOOL_KEY


def test_device_security_alternative_keys() -> None:
    other = bytes(range(16, 32))
    security = DeviceSecurity(DEVICE, TOOL_KEY, [other, TOOL_KEY, other.hex()])
    assert security.keys == (TOOL_KEY, other)
