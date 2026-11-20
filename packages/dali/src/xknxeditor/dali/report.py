"""Read-only gateway diagnostics and emergency-light test results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from .connection import DaliCommissioningError, MdtDaliConnection
from .properties import PropertyType
from .structs import EcgProp


@dataclass(frozen=True)
class FailureSummary:
    lamps: int
    ecgs: int
    converters: int
    lamp_errors: int
    ecg_errors: int
    converter_errors: int
    lamp_failure_rate: int
    ecg_failure_rate: int
    converter_failure_rate: int
    total_failure_rate: int

    @classmethod
    def decode(cls, raw: bytes) -> FailureSummary:
        if len(raw) != 10:
            raise DaliCommissioningError("Invalid DALI failure summary length")
        return cls(
            *(v if v <= 64 else 0 for v in raw[:6]),
            *(round(v * 100 / 255) for v in raw[6:]),
        )


@dataclass(frozen=True)
class EmergencyTest:
    slot: int
    ecg_index: int
    date: str
    time: str
    mode: str
    result: str
    fail_converter: bool
    fail_duration: bool
    fail_battery: bool
    fail_lamp: bool
    fail_delay: bool
    fail_test: bool
    machine_state: int
    pending_function: bool
    pending_duration: bool
    emergency_status: int
    emergency_mode: int
    emergency_failure: int

    @classmethod
    def decode(
        cls, slot: int, ecg_index: int, raw: bytes, status: bytes
    ) -> EmergencyTest:
        if len(raw) != 10 or len(status) != 5:
            raise DaliCommissioningError("Invalid DALI emergency-test record length")
        mode = {1: "FT", 2: "DT", 4: "BT"}.get(raw[1], "?")
        result = "N/A"
        if raw[0] != 255 and mode != "?":
            result = (
                f"{round(raw[0] * 100 / 254)} %"
                if mode in ("FT", "BT")
                else f"{raw[0] * 2} min"
            )
        failures = 0 if mode == "BT" else raw[2]
        date = f"{2000 + raw[9]:04}-{min(raw[8], 12):02}-{raw[7] & 31:02}"
        time = f"{min(raw[4], 24):02}:{raw[5] if raw[5] <= 59 else 0:02}:{raw[6] if raw[6] <= 59 else 0:02}"
        return cls(
            slot,
            ecg_index,
            date,
            time,
            mode,
            result,
            bool(failures & 1),
            bool(failures & 2),
            bool(failures & 4),
            bool(failures & 8),
            bool(failures & (16 if mode == "FT" else 32)),
            bool(failures & (64 if mode == "FT" else 128)),
            status[0] & 15,
            bool(status[0] & 64),
            bool(status[0] & 128),
            status[1],
            status[2],
            status[3],
        )


@dataclass(frozen=True)
class DiagnosticReport:
    read_at: str
    summary: FailureSummary
    tests: tuple[EmergencyTest, ...]

    def to_xml(self, names: dict[int, str] | None = None) -> bytes:
        root = ET.Element(
            "Report",
            {
                "create-date": self.read_at[:10],
                "create-time": self.read_at[11:],
                "read-at": self.read_at,
            },
        )
        summary = ET.SubElement(root, "Summary")
        from dataclasses import asdict

        summary.attrib.update(
            {k.replace("_", "-"): str(v) for k, v in asdict(self.summary).items()}
        )
        results = ET.SubElement(root, "TestResults")
        for test in self.tests:
            values = {
                "index": str(test.slot),
                "ecg-index": str(test.ecg_index + 1),
                "ecg-name": (names or {}).get(test.ecg_index, ""),
                "date": test.date,
                "time": test.time,
                "mode": test.mode,
                "result": test.result,
            }
            values.update(
                {
                    k.replace("_", "-"): str(v)
                    for k, v in asdict(test).items()
                    if k.startswith("fail_")
                }
            )
            ET.SubElement(results, "Test", values)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)


async def read_report(connection: MdtDaliConnection) -> DiagnosticReport:
    summary = FailureSummary.decode(
        await connection.read_table(PropertyType.GENERAL_FAILURE, 1)
    )
    props = await connection.read_table(PropertyType.EVG_PROP, 64)
    results = await connection.read_table(PropertyType.TEST_RESULT, 64)
    statuses = await connection.read_table(PropertyType.CONVERTER_STATUS, 64)
    if len(props) != 256 or len(results) != 640 or len(statuses) != 320:
        raise DaliCommissioningError("Incomplete DALI diagnostic tables")
    tests: list[EmergencyTest] = []
    for slot in range(64):
        prop = EcgProp.decode(props[slot * 4 : slot * 4 + 4])
        if prop.ecg_type in (1, 129) and prop.ets_ecg_index < 64:
            tests.append(
                EmergencyTest.decode(
                    slot,
                    prop.ets_ecg_index,
                    results[slot * 10 : slot * 10 + 10],
                    statuses[slot * 5 : slot * 5 + 5],
                )
            )
    return DiagnosticReport(
        datetime.now(UTC).isoformat(timespec="seconds"), summary, tuple(tests)
    )
