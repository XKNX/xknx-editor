"""Which project devices a failed device check points to."""

from __future__ import annotations

from collections.abc import Iterable

from editor_gui.device import Device
from xknxeditor.download.errors import CompareMismatch
from xknxeditor.download.merge import iter_load_controls
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp


def compare_mismatch(error: BaseException | None) -> CompareMismatch | None:
    """The property compare mismatch ``error`` is, or was caused by."""
    while error is not None:
        if isinstance(error, CompareMismatch) and error.read is not None:
            return error
        error = error.__cause__
    return None


def matching_devices(
    mismatch: CompareMismatch, devices: Iterable[Device]
) -> list[Device]:
    """Devices whose application accepts the value the device reported (in any branch of a
    ``choose`` too: which one runs depends on the device's parameters)."""
    found: list[Device] = []
    for device in devices:
        app = getattr(device, "app", None)
        procedures = app.load_procedures if app is not None else None
        if procedures is None:
            continue
        if any(
            _accepts(control, mismatch)
            for procedure in procedures.load_procedure
            for control in iter_load_controls(procedure.choice)
        ):
            found.append(device)
    return found


def _accepts(control: object, mismatch: CompareMismatch) -> bool:
    if (
        not isinstance(control, LdCtrlCompareProp)
        or control.prop_id != mismatch.property_id
        or mismatch.read is None
    ):
        return False
    if control.obj_idx is not None:
        if control.obj_idx != mismatch.object_index:
            return False
    elif not (mismatch.object_index == 0 and control.obj_type == 0):
        return False
    expected = control.inline_data
    mask = control.mask or b""
    mask += b"\xff" * max(0, len(expected) - len(mask))
    length = min(len(expected), len(mismatch.read))
    if not length:
        return False
    return all(
        (read ^ value) & bits == 0
        for read, value, bits in zip(
            mismatch.read[:length], expected[:length], mask[:length], strict=True
        )
    )
