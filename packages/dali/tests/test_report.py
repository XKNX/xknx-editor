from xml.etree import ElementTree as ET

import pytest
from _dali_fakes import FakeProgrammer

from xknxeditor.dali.commissioning import MdtDaliCommissioner
from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.properties import PropertyType
from xknxeditor.dali.report import DiagnosticReport, EmergencyTest, FailureSummary


def test_summary_counts_and_scaling():
    result = FailureSummary.decode(bytes([64, 3, 2, 255, 1, 0, 255, 128, 0, 64]))
    assert (result.lamps, result.ecgs, result.converters, result.lamp_errors) == (
        64,
        3,
        2,
        0,
    )
    assert (
        result.lamp_failure_rate,
        result.ecg_failure_rate,
        result.total_failure_rate,
    ) == (100, 50, 25)
    with pytest.raises(DaliCommissioningError):
        FailureSummary.decode(b"short")


@pytest.mark.parametrize(
    ("mode", "result", "failures"),
    [(1, "50 %", True), (2, "254 min", True), (4, "50 %", False)],
)
def test_test_record_modes_flags_and_pending_bits(mode, result, failures):
    test = EmergencyTest.decode(
        7,
        12,
        bytes([127, mode, 255, 0, 16, 23, 45, 20, 11, 26]),
        bytes([0xC7, 1, 2, 3, 0]),
    )
    assert (test.date, test.time, test.result) == ("2026-11-20", "16:23:45", result)
    assert (
        test.fail_converter is failures
        and test.fail_delay is failures
        and test.fail_test is failures
    )
    assert test.machine_state == 7 and test.pending_function and test.pending_duration
    assert (test.emergency_status, test.emergency_mode, test.emergency_failure) == (
        1,
        2,
        3,
    )
    manual = EmergencyTest.decode(
        0, 0, bytes([255, 1, 0, 0, 0, 0, 0, 0, 0, 0]), bytes([0x31, 0, 0, 0, 0])
    )
    assert manual.result == "N/A"
    assert not manual.pending_function and not manual.pending_duration


def test_report_xml_uses_correct_delay_failure_and_name():
    test = EmergencyTest.decode(
        7, 12, bytes([254, 1, 16, 0, 16, 23, 45, 20, 11, 26]), bytes(5)
    )
    report = DiagnosticReport(
        "2026-11-20T16:25:00+00:00", FailureSummary.decode(bytes(10)), (test,)
    )
    item = ET.fromstring(report.to_xml({12: "Exit & hall"})).find("TestResults/Test")
    assert item.get("ecg-index") == "13" and item.get("ecg-name") == "Exit & hall"
    assert item.get("fail-delay") == "True" and item.get("fail-converter") == "False"


async def test_report_reads_tables_and_never_writes_or_starts_tests():
    programmer = FakeProgrammer()
    programmer.set_read(PropertyType.GENERAL_FAILURE, bytes(10))
    programmer.set_read(
        PropertyType.EVG_PROP,
        bytes([1, 0, 16, 5])
        + bytes([129, 0, 16, 63])
        + bytes([1, 0, 16, 255])
        + bytes(4 * 61),
    )
    programmer.set_read(
        PropertyType.TEST_RESULT, bytes([127, 1, 16, 0, 16, 23, 45, 20, 11, 26]) * 64
    )
    programmer.set_read(PropertyType.CONVERTER_STATUS, bytes([0xC7, 1, 2, 3, 0]) * 64)
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, object_index_base=7)
    )
    report = await commissioner.read_report()
    assert [t.ecg_index for t in report.tests] == [5, 63]
    assert programmer.writes == []
    programmer.set_read(PropertyType.TEST_RESULT, b"")
    with pytest.raises(DaliCommissioningError):
        await commissioner.read_report()


async def test_partial_record_is_rejected_instead_of_truncated():
    programmer = FakeProgrammer()
    programmer.set_read(PropertyType.GENERAL_FAILURE, bytes(11))
    commissioner = MdtDaliCommissioner(MdtDaliConnection(programmer))
    with pytest.raises(DaliCommissioningError, match="partial element"):
        await commissioner.read_report()


@pytest.mark.parametrize("group", [0, 16, 64, 80, 32, 96, 255])
@pytest.mark.parametrize("duplicate_group", [None, 16, 32, 255])
async def test_report_only_labels_uniquely_assigned_converters(group, duplicate_group):
    programmer = FakeProgrammer()
    programmer.set_read(PropertyType.GENERAL_FAILURE, bytes(10))
    props = bytearray(bytes([255, 0, 255, 255]) * 64)
    props[28:32] = bytes([1, 0, group, 12])
    if duplicate_group is not None:
        props[36:40] = bytes([6, 0, duplicate_group, 12])
    programmer.set_read(PropertyType.EVG_PROP, bytes(props))
    programmer.set_read(
        PropertyType.TEST_RESULT, bytes([127, 1, 16, 0, 16, 23, 45, 20, 11, 26]) * 64
    )
    programmer.set_read(PropertyType.CONVERTER_STATUS, bytes(5 * 64))
    commissioner = MdtDaliCommissioner(
        MdtDaliConnection(programmer, object_index_base=7)
    )
    report = await commissioner.read_report()
    assert [(test.slot, test.ecg_index) for test in report.tests] == (
        [(7, 12)] if group in (0, 16, 64, 80) and duplicate_group != 16 else []
    )
    assert programmer.writes == []
