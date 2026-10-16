"""The effective KNX Data Secure state of a group address."""

from types import SimpleNamespace
from typing import cast

from xknxeditor.proj.core.ga_security import exports_key, is_secured
from xknxeditor.proj.models import GroupAddress


def test_on_and_off_decide_alone() -> None:
    assert is_secured("On", [1, 2], set())
    assert not is_secured("Off", [1], {1})


def test_auto_needs_every_linked_device_commissioned_securely() -> None:
    for mode in (None, "Auto"):
        assert is_secured(mode, [1, 2], {1, 2, 3})
        assert not is_secured(mode, [1, 2], {1})
        assert not is_secured(mode, [], {1})  # unlinked


def _ga(mode: str | None, linked: bool) -> GroupAddress:
    return cast(
        "GroupAddress", SimpleNamespace(security_mode=mode, links=[1] if linked else [])
    )


def test_a_plain_group_address_is_exported_without_its_key() -> None:
    assert exports_key(_ga("On", True), secured=True)
    # Auto in the clear because of its links, or switched off: no key.
    assert not exports_key(_ga(None, True), secured=False)
    assert not exports_key(_ga("Off", False), secured=False)
    # An unlinked Auto one keeps its key.
    assert exports_key(_ga("Auto", False), secured=False)
