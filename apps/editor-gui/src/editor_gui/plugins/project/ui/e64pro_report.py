"""Shared cached diagnostics displayed without starting emergency-light tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugin_blob_readers.dali_backup.e64pro import ecg_names
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.e64pro import cells, table
from editor_gui.widgets.flow import button_width, same_line_if_fits

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from editor_gui.plugins.project.ui.dali_commissioning import DaliRunner
    from xknxeditor.dali import MdtDaliCommissioner
    from xknxeditor.dali.report import DiagnosticReport


@dataclass
class ReportView:
    busy: bool = False
    error: str = ""
    save: file_dialogs.SaveFileDialog | None = None
    export_data: bytes = b""

    def render(self, device: Device, project: ProjectService, run: DaliRunner) -> None:
        if self.save is not None and self.save.ready():
            path = self.save.result()
            self.save = None
            if path:
                try:
                    Path(path).write_bytes(self.export_data)
                except OSError as exc:
                    self.error = str(exc)
            self.export_data = b""
        imgui.begin_disabled(self.busy)
        if imgui.button(S.E64_READ_REPORT):
            self.busy = True
            self.error = ""
            generation = project.generation

            pending: list[DiagnosticReport] = []

            async def read(commissioner: MdtDaliCommissioner) -> object:
                try:
                    report = await commissioner.read_report()
                    pending.append(report)
                    return report
                except Exception as exc:
                    self.error = str(exc)
                    raise

            def ended() -> None:
                self.busy = False
                if pending:
                    try:
                        project.store_dali_report(device, 0, generation, pending[0])
                    except ValueError as exc:
                        self.error = str(exc)

            try:
                run(device, read, 0, S.E64_READ_REPORT, ended, False)
            except Exception as exc:
                self.busy = False
                self.error = str(exc)
        imgui.end_disabled()
        report = project.dali_report(device)
        if self.error:
            imgui.text_wrapped(self.error)
        if report is None:
            imgui.text_wrapped(S.E64_NO_REPORT)
            return
        same_line_if_fits(button_width(S.E64_EXPORT_XML))
        imgui.begin_disabled(self.save is not None)
        if imgui.button(S.E64_EXPORT_XML):
            self.export_data = report.to_xml(ecg_names(device))
            self.save = file_dialogs.save_file(
                S.E64_EXPORT_XML,
                default_path=f"dali-report-{device.individual_address}.xml",
                filters=["XML", "*.xml"],
            )
        imgui.end_disabled()
        imgui.text_unformatted(report.read_at)
        names = ecg_names(device)
        summary = report.summary
        with table(
            "summary",
            [S.E64_TYPE, S.E64_COUNT, S.E64_ERRORS, S.E64_FAILURE_RATE],
            height=145,
            min_width=430,
        ) as show:
            if show:
                for name, count, errors, rate in (
                    (
                        S.E64_LAMPS,
                        summary.lamps,
                        summary.lamp_errors,
                        summary.lamp_failure_rate,
                    ),
                    (
                        S.E64_ECG,
                        summary.ecgs,
                        summary.ecg_errors,
                        summary.ecg_failure_rate,
                    ),
                    (
                        S.E64_CONVERTERS,
                        summary.converters,
                        summary.converter_errors,
                        summary.converter_failure_rate,
                    ),
                ):
                    cells([name, str(count), str(errors), f"{rate} %"])
        imgui.text_unformatted(
            f"{S.E64_TOTAL_FAILURE_RATE}: {summary.total_failure_rate} %"
        )
        with table(
            "tests",
            [
                S.E64_ECG,
                S.E64_DATE,
                S.E64_MODE,
                S.E64_RESULT,
                S.E64_ERRORS,
                S.E64_STATUS,
                S.E64_EMERGENCY_STATUS,
                S.E64_EMERGENCY_MODE,
                S.E64_EMERGENCY_FAILURE,
            ],
            min_width=860,
        ) as show:
            if show:
                for test in report.tests:
                    failures = [
                        label
                        for flag, label in (
                            (test.fail_converter, S.E64_CONVERTERS),
                            (test.fail_duration, S.E64_DURATION),
                            (test.fail_battery, S.E64_BATTERY),
                            (test.fail_lamp, S.E64_LAMPS),
                            (test.fail_delay, S.E64_DELAY),
                            (test.fail_test, S.E64_TEST),
                        )
                        if flag
                    ]
                    cells(
                        [
                            f"{test.ecg_index + 1} {names.get(test.ecg_index, '')}",
                            f"{test.date} {test.time}",
                            test.mode,
                            test.result,
                            ", ".join(failures),
                            S.E64_CONVERTER_STATES.get(
                                test.machine_state, str(test.machine_state)
                            ),
                            f"0x{test.emergency_status:02X}",
                            f"0x{test.emergency_mode:02X}",
                            f"0x{test.emergency_failure:02X}",
                        ]
                    )
                    if test.pending_function or test.pending_duration:
                        imgui.text_unformatted(
                            f"{'FT ' if test.pending_function else ''}{'DT ' if test.pending_duration else ''}: {S.E64_TEST_PENDING}"
                        )
