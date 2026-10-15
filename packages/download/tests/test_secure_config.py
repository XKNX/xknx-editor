"""The KNX Data Secure configuration a secured download writes into the Security Interface Object."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from xknx.telegram import Telegram
from xknx.telegram.apci import (
    APCI,
    FunctionPropertyCommand,
    PropertyExtValueWriteCon,
    PropertyValueWrite,
)

from xknxeditor.download.image import DownloadImage
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.secure_config import (
    GroupSecurity,
    commissioning_controls,
    plain_controls,
    security_tables,
    table_controls,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.prod import Application

from .test_extended_properties import ExtendedDevice

_KEY_A = bytes(range(16))
_KEY_B = bytes(range(16, 32))
_TOOL_KEY = bytes(range(32, 48))
_FDSK = bytes(range(48, 64))


def _tables():
    tables = security_tables(
        [0x0801, 0x0802, 0x0900],
        [(0x0801, 0), (0x0802, 3), (0x0900, 5), (0x0900, 6)],
        GroupSecurity(
            keys={0x0900: _KEY_B, 0x0802: _KEY_A, 0x0A00: _KEY_A},
            senders=[(0x1105, 7), (0x1101, 0), (0x1105, 9)],
        ),
    )
    assert tables is not None
    return tables


def test_tables_follow_the_address_table() -> None:
    tables = _tables()
    # Address table indices from 1, in index order; a key of an address the device does not
    # hold is left out.
    assert tables.group_keys == ((2, _KEY_A), (3, _KEY_B))
    assert tables.individual_addresses == ((0x1101, 0), (0x1105, 9))
    assert tables.go_flags == bytes([0, 0, 0, 3, 0, 3, 3])


def test_no_tables_without_a_secured_group_address() -> None:
    security = GroupSecurity(keys={0x0A00: _KEY_A})
    assert security_tables([0x0801], [(0x0801, 0)], security) is None


def test_table_controls_order() -> None:
    controls = table_controls(_tables())
    kinds = [type(c).__name__ for c in controls]
    assert kinds[:2] == ["LdCtrlUnload", "LdCtrlLoad"]
    load_completed = kinds.index("LdCtrlLoadCompleted")
    writes = [c for c in controls if isinstance(c, LdCtrlWriteProp)]
    before = [
        c.prop_id for c in controls[:load_completed] if isinstance(c, LdCtrlWriteProp)
    ]
    assert before == [54, 54, 54, 53, 53, 53]
    assert all(c.obj_type == 17 and c.verify for c in writes)
    clears = [(c.prop_id, c.inline_data) for c in writes if c.start_element == 0]
    assert clears == [(54, b"\x00\x00"), (53, b"\x00\x00"), (61, b"\x00\x00")]
    keys = [c.inline_data for c in writes if c.prop_id == 53 and c.start_element]
    assert keys == [b"\x00\x02" + _KEY_A, b"\x00\x03" + _KEY_B]
    addresses = [c.inline_data for c in writes if c.prop_id == 54 and c.start_element]
    assert addresses == [
        bytes.fromhex("1101000000000000"),
        bytes.fromhex("1105000000000009"),
    ]
    assert all(
        isinstance(c, (LdCtrlUnload, LdCtrlLoad, LdCtrlLoadCompleted, LdCtrlWriteProp))
        for c in controls
    )


class _SecurityDevice(ExtendedDevice):
    """Records the property writes (by object type, extended or not) and function property
    commands in order."""

    def __init__(self) -> None:
        super().__init__()
        self.object_types[7] = 17
        self.writes: list[tuple[int, int, int, int, bytes]] = []
        self.commands: list[tuple[int, bytes]] = []

    async def request(self, payload: APCI, expected: type[APCI] | None) -> Telegram:
        if isinstance(payload, PropertyExtValueWriteCon):
            self.writes.append(
                (
                    payload.interface_object_type,
                    payload.property_id,
                    payload.start_index,
                    payload.nr_of_elem,
                    payload.data,
                )
            )
        if isinstance(payload, PropertyValueWrite) and payload.property_id != 5:
            self.writes.append(
                (
                    self.object_types[payload.object_index],
                    payload.property_id,
                    payload.start_index,
                    payload.count,
                    payload.data,
                )
            )
        if isinstance(payload, FunctionPropertyCommand):
            self.commands.append((payload.property_id, payload.data))
        return await super().request(payload, expected)


class _Manager:
    """A secured connection manager: tells which key the device uses and takes the change."""

    def __init__(
        self,
        device: _SecurityDevice,
        active_key: bytes,
        device_sequence_number: int | None = None,
    ) -> None:
        self.device = device
        self.active_key = active_key
        self.device_sequence_number = device_sequence_number
        self.changes: list[bytes] = []

    async def open(self) -> _SecurityDevice:
        return self.device

    async def close(self) -> None:
        pass

    def change_tool_key(self, key: bytes) -> None:
        self.changes.append(key)
        self.active_key = key


def _application() -> Application:
    return cast(
        "Application",
        SimpleNamespace(
            manufacturer_id="M-00FA",
            program=SimpleNamespace(
                mask_version="MV-07B0",
                pei_type=0,
                application_number=0xA502,
                application_version=0x13,
                is_secure_enabled=True,
                static=SimpleNamespace(
                    options=SimpleNamespace(supports_extended_property_services=True)
                ),
            ),
        ),
    )


async def _run(
    active_key: bytes, device_sequence_number: int | None = None
) -> tuple[_SecurityDevice, _Manager]:
    device = _SecurityDevice()
    manager = _Manager(device, active_key, device_sequence_number)
    controls = [
        *commissioning_controls(_TOOL_KEY),
        *table_controls(_tables()),
        LdCtrlRestart(),
    ]
    runner = LoadProcedureRunner(
        _application(),
        DownloadImage(segments=(), properties=()),
        connection_manager=manager,
        max_apdu_length=50,
        controls=controls,
        object_types=device.object_types,
    )
    await runner.run()
    return device, manager


async def test_commissioning_writes_the_tool_key_with_the_factory_key() -> None:
    device, manager = await _run(_FDSK)
    assert device.commands[0] == (51, b"\x00\x00\x01")
    assert device.writes[0] == (17, 56, 1, 1, _TOOL_KEY)
    assert manager.changes == [_TOOL_KEY]
    group_keys = [w for w in device.writes if w[1] == 53]
    assert group_keys == [
        (17, 53, 0, 1, b"\x00\x00"),
        (17, 53, 1, 1, b"\x00\x02" + _KEY_A),
        (17, 53, 2, 1, b"\x00\x03" + _KEY_B),
    ]
    assert [w for w in device.writes if w[1] == 61] == [
        (17, 61, 0, 1, b"\x00\x00"),
        (17, 61, 1, 7, bytes([0, 0, 0, 3, 0, 3, 3])),
    ]
    assert device.restarted


async def test_a_device_with_its_tool_key_gets_only_the_tables() -> None:
    device, manager = await _run(_TOOL_KEY)
    assert manager.changes == []
    assert all(w[1] != 56 for w in device.writes)
    assert any(w[1] == 53 for w in device.writes)


def test_p2p_partners_join_the_address_table() -> None:
    tables = security_tables(
        [0x0801, 0x0900],
        [(0x0801, 0), (0x0900, 2)],
        GroupSecurity(
            keys={0x0801: _KEY_A, 0x0900: _KEY_B},
            senders=[(0x1101, 5)],
            p2p=[(0x1109, _TOOL_KEY, 0)],
        ),
    )
    assert tables is not None
    # The IA table holds the sender and the P2P partner, sorted.
    assert tables.individual_addresses == ((0x1101, 5), (0x1109, 0))
    # The P2P key points at the partner's index in that table (1-based) with roles 0.
    assert tables.p2p_keys == ((2, _TOOL_KEY, 0),)
    # Every object on a secured group address gets authentication + confidentiality.
    assert tables.go_flags == bytes([0x03, 0x00, 0x03])


def test_p2p_only_device_has_tables() -> None:
    tables = security_tables([], [], GroupSecurity(keys={}, p2p=[(0x1109, _KEY_A, 0)]))
    assert tables is not None
    assert tables.group_keys == ()
    assert tables.individual_addresses == ((0x1109, 0),)
    assert tables.p2p_keys == ((1, _KEY_A, 0),)
    controls = table_controls(tables)
    writes = [c for c in controls if isinstance(c, LdCtrlWriteProp)]
    p2p_writes = [c for c in writes if c.prop_id == 52]
    assert [c.inline_data for c in p2p_writes] == [
        b"\x00\x00",
        b"\x00\x01" + _KEY_A + b"\x00\x00",
    ]


def test_security_tables_must_fit_the_device() -> None:
    import pytest

    from xknxeditor.download.errors import ImageError
    from xknxeditor.download.image import (
        _validate_security_capacity,  # pyright: ignore[reportPrivateUsage]
    )

    def application(group_keys: int) -> Application:
        return cast(
            "Application",
            SimpleNamespace(
                program=SimpleNamespace(
                    max_security_group_key_table_entries=group_keys,
                    max_security_individual_address_entries=0,
                    max_security_p2_pkey_table_entries=0,
                )
            ),
        )

    tables = _tables()  # two group keys
    _validate_security_capacity(application(2), tables)
    _validate_security_capacity(application(0), tables)  # not declared: not checked
    with pytest.raises(ImageError, match="Group Key Table has 2 entries"):
        _validate_security_capacity(application(1), tables)


async def test_initial_configuration_sets_the_sequence_number() -> None:
    from xknxeditor.download.data_secure import sequence_number_timestamp

    # A factory-fresh device counts from a small value: the first configuration sets it to the
    # current timestamp, before the Tool Key (3/5/3 1.5.4.1).
    before = sequence_number_timestamp()
    device, _manager = await _run(_FDSK, device_sequence_number=5)
    sequence = [w for w in device.writes if w[1] == 59]
    assert len(sequence) == 1
    assert int.from_bytes(sequence[0][4], "big") >= before
    assert device.writes.index(sequence[0]) < next(
        i for i, w in enumerate(device.writes) if w[1] == 56
    )


async def test_a_current_sequence_number_is_kept() -> None:
    from xknxeditor.download.data_secure import sequence_number_timestamp

    device, _manager = await _run(
        _FDSK, device_sequence_number=sequence_number_timestamp() + 10_000
    )
    assert all(w[1] != 59 for w in device.writes)
    # Not on a device that already uses its Tool Key either.
    device, _manager = await _run(_TOOL_KEY, device_sequence_number=5)
    assert all(w[1] != 59 for w in device.writes)


async def test_switching_to_plain_empties_the_tables_and_ends_the_security_mode() -> None:
    device = _SecurityDevice()
    runner = LoadProcedureRunner(
        _application(),
        DownloadImage(segments=(), properties=()),
        connection_manager=_Manager(device, _TOOL_KEY),
        max_apdu_length=50,
        controls=[*plain_controls(p2p=True), LdCtrlRestart()],
        object_types=device.object_types,
    )
    await runner.run()
    cleared = {w[1] for w in device.writes if w[2] == 0 and w[4] == bytes(2)}
    assert cleared == {52, 53, 54, 61}
    assert device.commands == [(51, bytes(3))]
    # Without a Point-to-point Key Table, that one is left alone.
    assert all(
        getattr(c, "prop_id", None) != 52 for c in plain_controls(p2p=False)
    )
