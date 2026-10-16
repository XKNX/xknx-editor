"""KNX IP Secure configuration written to the KNXnet/IP Parameter Object (object type 11)."""

from __future__ import annotations

import base64

from xknxeditor.download.ip_secure import (
    EMPTY_PASSWORD_HASH,
    IpSecureConfig,
    device_authentication_code,
    ip_secure_controls,
    password_hash,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp

_BACKBONE = bytes(range(16))


def test_derivations_match_the_standard() -> None:
    # The hash of the empty password is the well-known value of 3/8/9 2.3.1.4.3.
    assert password_hash("") == EMPTY_PASSWORD_HASH
    assert len(device_authentication_code("secret")) == 16
    # The two salts differ, so the same passphrase gives different results.
    assert password_hash("abc") != device_authentication_code("abc")


def test_derivations_match_the_hashes_ets_stores() -> None:
    # A project stores these hashes base64-encoded next to the passphrases (xknxproject's public
    # "xknx_test_project"): the same derivations.
    assert device_authentication_code("authcode") == base64.b64decode(
        "2W7vedSbsgBROAkSj5XhEg=="
    )
    assert password_hash("test1234") == base64.b64decode("hVH0H6qL7jwBo8nJHdnx7g==")


def _writes(controls: list[object]) -> dict[int, tuple[int, bytes]]:
    return {
        c.prop_id: (c.count, c.inline_data or b"")
        for c in controls
        if isinstance(c, LdCtrlWriteProp)
    }


def test_backbone_key_and_latencies() -> None:
    writes = _writes(ip_secure_controls(IpSecureConfig(backbone_key=_BACKBONE)))
    assert writes[91] == (1, _BACKBONE)
    assert writes[95] == (1, (2000).to_bytes(2, "big"))
    assert writes[96] == (1, bytes([0x1A]))
    assert 92 not in writes and 93 not in writes


def test_password_hashes_and_tunnelling_users() -> None:
    config = IpSecureConfig(
        device_authentication_passphrase="dev",
        user_passwords={1: "mgmt", 3: "user3"},
        tunnelling_users=[(3, 2), (1, 0)],  # user 1 (management) is dropped
    )
    controls = ip_secure_controls(config)
    writes = _writes(controls)
    assert writes[92] == (1, device_authentication_code("dev"))
    count, table = writes[93]
    assert count == 3 and len(table) == 48
    assert table[0:16] == password_hash("mgmt")
    assert table[16:32] != EMPTY_PASSWORD_HASH  # user 2 unset: no usable password
    assert table[32:48] == password_hash("user3")
    assert writes[97] == (1, bytes([3, 2]))


def test_empty_config_writes_only_defaults() -> None:
    writes = _writes(ip_secure_controls(IpSecureConfig()))
    assert set(writes) == {95, 96}
