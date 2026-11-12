"""The pre-flight classification must split each changed byte so a difference count is
never scary without context: a byte whose changed bit carries a real configured value is
a genuine change, while a byte only reset to the application default (device holds an older
value) or a device-managed runtime byte is benign.

The classification is pure (``editor_gui.programming``), shared by the GUI result window and
the MCP evaluate tool; these tests exercise it directly (no imgui context needed).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

from editor_gui.plugins.project.ui.preflight_result import (
    _CAT_CONFIG,
    _CAT_DEFAULT,
    _CAT_MATCH,
    _CAT_RUNTIME,
    PreflightResultWindow,
)
from editor_gui.programming import (
    classify_preflight,
    parameter_driven_bits,
    preflight_segment_counts,
    runtime_managed_addresses,
)
from xknxeditor.download.preflight import (
    PreflightReport,
    PropertyDiff,
    SegmentDiff,
    memory_key,
)

if TYPE_CHECKING:
    from editor_gui.device import Device


def _window(runtime: set[int] | None = None, driven: dict[int, int] | None = None):
    w = PreflightResultWindow()
    w._runtime = frozenset(runtime or set())
    w._driven = dict(driven or {})
    return w


def test_driven_bit_change_is_config() -> None:
    # bit 6 (0x40) differs and is driven by an active parameter -> real change.
    w = _window(driven={0x100: 0x40})
    seg = SegmentDiff(address=0x100, current=b"\x00", planned=b"\x40")
    assert w._segment_category(seg) == _CAT_CONFIG
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (1, 0, 0)


def test_stale_bit_on_written_byte_is_default() -> None:
    # The 1.1.9 case: byte written for a neighbour (bit 6 driven), but the only
    # differing bit is bit 5 (0x20), which no active parameter drives. The device
    # holds 0x74, our image writes 0x54 -> reset to the application default: benign.
    w = _window(driven={0x100: 0x40})
    seg = SegmentDiff(address=0x100, current=b"\x74", planned=b"\x54")
    assert w._segment_category(seg) == _CAT_DEFAULT
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (0, 1, 0)


def test_runtime_address_is_runtime() -> None:
    # A device-managed address stays runtime even if a bit differs.
    w = _window(runtime={0x100})
    seg = SegmentDiff(address=0x100, current=b"\x00", planned=b"\xff")
    assert w._segment_category(seg) == _CAT_RUNTIME
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (0, 0, 1)


def test_mixed_segment_reports_config() -> None:
    # One config byte + one default byte -> the segment is a real change overall,
    # but the counts break it down for the summary buckets.
    w = _window(driven={0x200: 0x40})
    seg = SegmentDiff(address=0x200, current=b"\x00\x74", planned=b"\x40\x54")
    assert w._segment_category(seg) == _CAT_CONFIG
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (1, 1, 0)


def test_unchanged_segment_is_match() -> None:
    w = _window()
    seg = SegmentDiff(address=0x300, current=b"\x11", planned=b"\x11")
    assert w._segment_category(seg) == _CAT_MATCH
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (0, 0, 0)


def test_classify_report_totals_and_locations() -> None:
    # A config segment, a benign default-only segment, and a property write (always
    # configured). config_locations counts the config segment plus the property, not the
    # default-only one.
    report = PreflightReport(
        segments=(
            SegmentDiff(address=0x100, current=b"\x00", planned=b"\x40"),  # config
            SegmentDiff(
                address=0x200, current=b"\x74", planned=b"\x54"
            ),  # default only
        ),
        properties=(
            PropertyDiff(
                object_index=0, property_id=7, current=b"\x00", planned=b"\x01"
            ),
        ),
    )
    classes = classify_preflight(report, frozenset(), {0x100: 0x40})
    assert classes.config_bytes == 2  # one driven byte + the property write
    assert classes.default_bytes == 1
    assert classes.runtime_bytes == 0
    assert classes.config_locations == 2  # config segment + property
    assert classes.actionable is True


def test_device_written_address_counts_as_runtime() -> None:
    # The 0x45c0 case: a byte the firmware rewrites at runtime (observed unstable during
    # the preview) is device-managed even though a driven bit differs -> it must not be
    # counted as config, so config_bytes==0 honestly means "in sync".
    report = PreflightReport(
        segments=(SegmentDiff(address=0x45C0, current=b"\x00", planned=b"\x40"),),
        properties=(),
        device_written=frozenset({0x45C0}),
    )
    classes = classify_preflight(report, frozenset(), {0x45C0: 0x40})
    assert classes.config_bytes == 0
    assert classes.runtime_bytes == 1
    assert classes.actionable is False


def test_a_procedure_step_property_is_device_managed() -> None:
    # A Berker 8014xxx0: the procedure writes object 5 property 51 with 01 and later 02 (a
    # download handshake), so an unchanged device never holds either value.
    steps = tuple(
        PropertyDiff(
            object_index=5,
            property_id=51,
            current=b"\x00",
            planned=planned,
            procedure_step=True,
        )
        for planned in (b"\x01", b"\x02")
    )
    report = PreflightReport(segments=(), properties=steps)
    classes = classify_preflight(report, frozenset(), {})
    assert (classes.config_bytes, classes.runtime_bytes) == (0, 2)
    assert classes.config_locations == 0
    assert classes.actionable is False
    assert "(procedure step)" in report.summary()
    assert PreflightResultWindow._property_category(steps[0]) == _CAT_RUNTIME
    setting = PropertyDiff(
        object_index=5, property_id=52, current=b"\x00", planned=b"\x01"
    )
    assert PreflightResultWindow._property_category(setting) == _CAT_CONFIG
    same = PropertyDiff(
        object_index=5, property_id=52, current=b"\x01", planned=b"\x01"
    )
    assert PreflightResultWindow._property_category(same) == _CAT_MATCH


def test_a_procedure_step_in_memory_is_device_managed() -> None:
    # A Hager TXA/TYF on MV-07B0: the procedure writes 0x0080 with 01 at the start and 02 at
    # the end, so the device holds 02 and the first write always plans a change.
    step = SegmentDiff(
        address=0x80, current=b"\x02", planned=b"\x01", procedure_step=True
    )
    w = _window(driven={0x80: 0xFF})
    assert w._segment_category(step) == _CAT_RUNTIME
    assert preflight_segment_counts(step, w._runtime, w._driven) == (0, 0, 1)
    report = PreflightReport(segments=(step,), properties=())
    classes = classify_preflight(report, frozenset(), {0x80: 0xFF})
    assert (classes.config_bytes, classes.runtime_bytes, classes.actionable) == (
        0,
        1,
        False,
    )
    assert "memory 0x0080: 1/1 changed (procedure step)" in report.summary()


def test_group_table_bytes_are_config() -> None:
    # The 1.0.47 case: the device's address table holds one group address, the project four.
    # No parameter writes these bytes, and still they are the change a download makes.
    w = _window()
    seg = SegmentDiff(
        address=0x5B04,
        current=bytes.fromhex("0001182000010001"),
        planned=bytes.fromhex("0004001200160017"),
        table_bytes=frozenset(range(8)),
    )
    assert w._segment_category(seg) == _CAT_CONFIG
    report = PreflightReport(segments=(seg,), properties=())
    classes = classify_preflight(report, w._runtime, w._driven)
    assert (classes.config_bytes, classes.default_bytes) == (seg.changed_bytes, 0)
    assert classes.config_locations == 1


def test_an_octet_the_download_corrects_is_device_managed() -> None:
    # A group object table whose product data counts 200 objects where the application has
    # 125: after a full download the device holds 200, a partial download writes 125. The
    # count is in the table, and still no setting changes it.
    w = _window()
    seg = SegmentDiff(
        address=0x43FF,
        current=bytes.fromhex("c80102"),
        planned=bytes.fromhex("7d0105"),
        table_bytes=frozenset(range(3)),
        corrected_bytes=frozenset({0}),
    )
    assert preflight_segment_counts(seg, w._runtime, w._driven) == (1, 0, 1)


def test_a_user_memory_byte_takes_the_user_memory_maps() -> None:
    # The user memory is an address space of its own: what a parameter drives or the device
    # writes at 0x100 of the standard memory says nothing about 0x100 of the user memory.
    seg = SegmentDiff(
        address=0x100, current=b"\x00\x00", planned=b"\x40\xff", user_memory=True
    )
    assert preflight_segment_counts(seg, {0x101}, {0x100: 0x40}) == (0, 2, 0)
    runtime = {memory_key(0x101, user=True)}
    driven = {memory_key(0x100, user=True): 0x40}
    assert preflight_segment_counts(seg, runtime, driven) == (1, 0, 1)


def test_the_parameter_maps_key_a_user_memory_segment_apart() -> None:
    def written_bit_mask() -> dict[str, bytes]:
        return {"AS-0100": b"\x40", "AS-U0100": b"\x01"}

    def memory_param_map() -> dict[str, dict[int, tuple[str, int]]]:
        return {"AS-0100": {0: ("P-1", 0)}, "AS-U0100": {0: ("P-2", 0)}}

    def segment_base_addrs() -> dict[str, int]:
        return {"AS-0100": 0x100, "AS-U0100": 0x100}

    code = SimpleNamespace(
        absolute_segment=[
            SimpleNamespace(id="AS-0100", user_memory=False),
            SimpleNamespace(id="AS-U0100", user_memory=True),
        ]
    )
    device = cast(
        "Device",
        SimpleNamespace(
            dynamic_ui=SimpleNamespace(
                written_bit_mask=written_bit_mask,
                memory_param_map=memory_param_map,
                segment_base_addrs=segment_base_addrs,
            ),
            app=SimpleNamespace(
                program=SimpleNamespace(static=SimpleNamespace(code=code))
            ),
        ),
    )
    assert parameter_driven_bits(device) == {
        0x100: 0x40,
        memory_key(0x100, user=True): 0x01,
    }
    with patch(
        "xknxeditor.prod.parser_v2.application_indexer.ApplicationIndexer",
        lambda _program: SimpleNamespace(
            parameters={
                "P-1": SimpleNamespace(access=SimpleNamespace(name="NONE")),
                "P-2": SimpleNamespace(access=SimpleNamespace(name="NONE")),
            }
        ),
    ):
        assert runtime_managed_addresses(device) == {
            0x100,
            memory_key(0x100, user=True),
        }


def test_a_user_memory_segment_is_labelled_apart_from_standard_memory() -> None:
    standard = SegmentDiff(address=0x100, current=b"\x00", planned=b"\x01")
    user = SegmentDiff(
        address=0x100, current=b"\x00", planned=b"\x01", user_memory=True
    )
    assert PreflightResultWindow._segment_location(standard) == "Memory 0x0100"
    assert PreflightResultWindow._segment_location(user) == "User memory 0x0100"
    report = PreflightReport(segments=(standard, user), properties=())
    assert "  memory 0x0100: 1/1 changed" in report.summary()
    assert "  user memory 0x0100: 1/1 changed" in report.summary()
    w = _window()
    w._label = "1.1.1"
    w._scope = "all"
    exported = w._export_text(report)
    assert "## memory 0x0100 (1B) changed=1" in exported
    assert "## user memory 0x0100 (1B) changed=1" in exported


def test_copied_and_exported_texts_name_device_scope_and_bytes() -> None:
    from editor_gui.plugins.project.strings import S

    w = PreflightResultWindow()
    report = PreflightReport(
        segments=(SegmentDiff(address=0x100, current=b"", planned=b""),),
        properties=(),
    )
    w.submit_result("1.1.5  Dimmer", S.SCOPE_PARAMETERS, report)
    with w._lock:
        assert w._pending is not None
        w._label, w._scope, w._report = w._pending[:3]
    first = w._clipboard_text().splitlines()[0]
    assert (
        first == f"1.1.5  Dimmer  {S.PREFLIGHT_SCOPE.format(scope=S.SCOPE_PARAMETERS)}"
    )
    # English labels for what the device holds and what a download would write.
    lines = w._export_text(report).splitlines()
    assert "device   : 01" in lines
    assert "generated: 02" in lines


def test_export_saves_where_the_file_dialog_says(tmp_path, monkeypatch) -> None:
    from editor_gui import file_dialogs
    from editor_gui.plugins.project.strings import S

    asked: list[tuple[str, str, list[str]]] = []
    target = tmp_path / "result"

    class Dialog:
        def ready(self, timeout: int = 0) -> bool:
            return True

        def kill(self) -> bool:
            return True

        def result(self) -> str:
            return str(target)

    def save_file(title: str, default_path: str = "", filters=None, *_args):
        asked.append((title, default_path, list(filters or [])))
        return Dialog()

    # The dialog every export uses: the system's, or in the browser UI the server file + download.
    monkeypatch.setattr(file_dialogs, "save_file", save_file)
    w = PreflightResultWindow()
    w._label = "1.1.5  Dimmer: Kitchen"
    report = PreflightReport(
        segments=(SegmentDiff(address=0x100, current=b"", planned=b""),),
        properties=(),
    )
    w._start_export(report)
    ((title, default, filters),) = asked
    assert title == S.PREFLIGHT_EXPORT_TITLE
    assert default.startswith("1.1.5_Dimmer_Kitchen-") and default.endswith(".txt")
    assert filters == [S.PREFLIGHT_TEXT_FILES, "*.txt", S.PREFLIGHT_ALL_FILES, "*"]
    w._poll_export()
    # Saved with the text suffix, and the dialog is done.
    assert "generated: 01" in (tmp_path / "result.txt").read_text(encoding="utf-8")
    assert w._save_dialog is None and w._export_error is None
