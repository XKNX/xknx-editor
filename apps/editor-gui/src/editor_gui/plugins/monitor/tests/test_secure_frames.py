"""Secured group telegrams are shown decrypted with the group address's key."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from xknx.cemi import CEMIFrame, CEMILData, CEMIMessageCode
from xknx.dpt import DPTBinary
from xknx.secure.data_secure import DataSecure
from xknx.telegram import GroupAddress, IndividualAddress, Telegram
from xknx.telegram.apci import GroupValueWrite

from editor_gui.plugins.monitor import frames

_KEY = bytes(range(16))
_ADDRESS = GroupAddress("1/0/1")


def _secured_frame() -> bytes:
    data_secure = DataSecure(
        group_key_table={_ADDRESS: _KEY},
        individual_address_table={},
    )
    ldata = CEMILData.init_from_telegram(
        Telegram(destination_address=_ADDRESS, payload=GroupValueWrite(DPTBinary(1))),
        src_addr=IndividualAddress("1.1.5"),
    )
    secured = data_secure.outgoing_cemi(ldata)
    return CEMIFrame(code=CEMIMessageCode.L_DATA_IND, data=secured).to_knx()


@pytest.fixture(autouse=True)
def _keys() -> Iterator[None]:
    yield
    frames.secure_group_keys = None


def test_a_secured_telegram_is_decrypted() -> None:
    raw = _secured_frame()
    frame = frames.decode_cemi(raw)
    assert frame is not None and frame.data_secure and not frame.decrypted
    assert frame.type_name == "SecureService"
    frames.secure_group_keys = lambda: {_ADDRESS.raw: _KEY}
    frame = frames.decode_cemi(raw)
    assert frame is not None and frame.data_secure and frame.decrypted
    assert isinstance(frame.apci, GroupValueWrite)
    assert frame.type_name == "GroupValueWrite"
    assert frame.value_payload == DPTBinary(1)


def test_a_wrong_key_keeps_it_secured() -> None:
    frames.secure_group_keys = lambda: {_ADDRESS.raw: bytes(16)}
    frame = frames.decode_cemi(_secured_frame())
    assert frame is not None and not frame.decrypted
