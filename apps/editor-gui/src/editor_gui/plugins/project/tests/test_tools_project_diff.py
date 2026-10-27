"""The Project Diff tab lists the parameters and group objects of a changed device under it,
collapsed, with their values in the open and in the other project."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import tools
from editor_gui.plugins.project.ui.project_diff import (
    DetailRow,
    DiffEntry,
    DiffJob,
    DiffOutcome,
    DiffResult,
)
from editor_gui.plugins.project.ui.tools import ToolsPanel


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _panel() -> ToolsPanel:
    panel = object.__new__(ToolsPanel)
    panel._diff_other_name = "old.xknx"  # pyright: ignore[reportPrivateUsage]
    panel.show_backups = None
    return panel


def _job_panel(job: DiffJob) -> ToolsPanel:
    panel = _panel()
    panel._diff_result = None  # pyright: ignore[reportPrivateUsage]
    panel._diff_error = ""  # pyright: ignore[reportPrivateUsage]
    panel._diff_password_path = None  # pyright: ignore[reportPrivateUsage]
    panel._diff_password_focus = False  # pyright: ignore[reportPrivateUsage]
    panel._diff_job = None  # pyright: ignore[reportPrivateUsage]
    panel._diff_job_path = ""  # pyright: ignore[reportPrivateUsage]
    panel._on_diff_projects = lambda _path, _password: job  # pyright: ignore[reportPrivateUsage]
    return panel


@pytest.mark.usefixtures("context")
def test_a_running_comparison_shows_its_progress_and_can_be_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = DiffJob()
    panel = _job_panel(job)
    panel._run_diff("old.xknx", None)  # pyright: ignore[reportPrivateUsage]
    bars: list[str] = []
    monkeypatch.setattr(
        tools.imgui, "progress_bar", lambda _fraction, _size, text: bars.append(text)
    )
    pressed = [False]
    monkeypatch.setattr(tools.imgui, "button", lambda _label: pressed[0])

    def frame() -> None:
        imgui.new_frame()
        imgui.begin("Tools")
        panel._poll_diff_job()  # pyright: ignore[reportPrivateUsage]
        if panel._diff_job is not None:  # pyright: ignore[reportPrivateUsage]
            panel._render_diff_progress(panel._diff_job)  # pyright: ignore[reportPrivateUsage]
        imgui.end()
        imgui.end_frame()

    frame()
    job.progress(1, 3)
    frame()
    assert bars == [
        S.TOOLS_DIFF_READING,
        S.TOOLS_DIFF_PROGRESS.format(done=1, total=3),
    ]
    pressed[0] = True
    frame()
    assert job.cancel_requested
    # The worker ends it as stopped: nothing is shown.
    job.future.set_result(DiffOutcome(None, canceled=True))
    frame()
    assert panel._diff_job is None  # pyright: ignore[reportPrivateUsage]
    assert panel._diff_result is None  # pyright: ignore[reportPrivateUsage]
    assert panel._diff_other_name == ""  # pyright: ignore[reportPrivateUsage]


def test_the_outcome_of_a_comparison_is_shown_once_it_ended() -> None:
    job = DiffJob()
    panel = _job_panel(job)
    panel._run_diff("old.xknx", None)  # pyright: ignore[reportPrivateUsage]
    panel._poll_diff_job()  # pyright: ignore[reportPrivateUsage]
    assert panel._diff_job is job  # pyright: ignore[reportPrivateUsage]
    result = DiffResult(devices=[], group_addresses=[])
    job.future.set_result(DiffOutcome(result))
    panel._poll_diff_job()  # pyright: ignore[reportPrivateUsage]
    assert panel._diff_job is None  # pyright: ignore[reportPrivateUsage]
    assert panel._diff_result is result  # pyright: ignore[reportPrivateUsage]


def test_a_comparison_asked_from_elsewhere_shows_the_tab() -> None:
    job = DiffJob()
    panel = _job_panel(job)
    started: list[tuple[str, str | None]] = []

    def start(path: str, password: str | None) -> DiffJob:
        started.append((path, password))
        return job

    panel._on_diff_projects = start  # pyright: ignore[reportPrivateUsage]
    panel._select_diff = False  # pyright: ignore[reportPrivateUsage]
    panel.open_diff("backup.zip", "Backup of 2026-10-27 12:00", "secret")
    assert panel._select_diff  # pyright: ignore[reportPrivateUsage]
    assert panel._diff_other_name == "Backup of 2026-10-27 12:00"  # pyright: ignore[reportPrivateUsage]
    assert started == [("backup.zip", "secret")]

    # One still running goes on.
    panel._select_diff = False  # pyright: ignore[reportPrivateUsage]
    panel.open_diff("other.zip", "Other", None)
    assert panel._select_diff  # pyright: ignore[reportPrivateUsage]
    assert started == [("backup.zip", "secret")]
    assert panel._diff_other_name == "Backup of 2026-10-27 12:00"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("context")
def test_the_backups_are_shown_from_the_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = _job_panel(DiffJob())
    panel._diff_dialog = None  # pyright: ignore[reportPrivateUsage]
    panel._diff_other_name = ""  # pyright: ignore[reportPrivateUsage]
    shown: list[bool] = []
    panel.show_backups = lambda: shown.append(True)
    monkeypatch.setattr(
        tools.imgui, "button", lambda label: label == S.TOOLS_DIFF_BACKUPS
    )
    imgui.new_frame()
    imgui.begin("Tools")
    panel._render_diff_tab()  # pyright: ignore[reportPrivateUsage]
    imgui.end()
    imgui.end_frame()
    assert shown == [True]


@pytest.mark.usefixtures("context")
def test_what_it_was_compared_with_is_a_line_above_the_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = _job_panel(DiffJob())
    panel._diff_dialog = None  # pyright: ignore[reportPrivateUsage]
    panel._diff_other_name = "Backup of 2026-10-27 12:00"  # pyright: ignore[reportPrivateUsage]
    panel._diff_result = DiffResult(devices=[], group_addresses=[])  # pyright: ignore[reportPrivateUsage]
    lines: list[str] = []
    monkeypatch.setattr(tools.imgui, "text_wrapped", lines.append)
    imgui.new_frame()
    imgui.begin("Tools")
    panel._render_diff_tab()  # pyright: ignore[reportPrivateUsage]
    imgui.end()
    imgui.end_frame()
    assert S.TOOLS_DIFF_COMPARED_WITH.format(name="Backup of 2026-10-27 12:00") in lines


def _frame(panel: ToolsPanel, result: DiffResult) -> None:
    imgui.new_frame()
    imgui.begin("Tools")
    panel._render_diff_section(S.TOOLS_DIFF_DEVICES, result.devices)  # pyright: ignore[reportPrivateUsage]
    imgui.end()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_a_changed_device_lists_its_parameters_and_group_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (
        DetailRow("parameter", "Channel A > Mode", "changed", "On", "Off"),
        DetailRow("group_object", "Switch", "changed", "1.001 -> 1/0/1", ""),
    )
    result = DiffResult(
        devices=[DiffEntry("1.1.5", "changed", "name 'A' -> 'B'", rows)],
        group_addresses=[],
    )
    panel = _panel()
    shown: list[str] = []
    real_text_wrapped = imgui.text_wrapped

    def text_wrapped(text: str) -> None:
        shown.append(text)
        real_text_wrapped(text)

    monkeypatch.setattr(tools.imgui, "text_wrapped", text_wrapped)
    _frame(panel, result)
    # Collapsed at first: only the device line.
    assert shown == ["1.1.5  name 'A' -> 'B'"]
    monkeypatch.setattr(tools.imgui, "tree_node", lambda *_a: True)
    monkeypatch.setattr(tools.imgui, "tree_pop", lambda: None)
    shown.clear()
    _frame(panel, result)
    assert shown == [
        "1.1.5  name 'A' -> 'B'",
        "Channel A > Mode",
        "On",
        "Off",
        "Switch",
        "1.001 -> 1/0/1",
        "-",
    ]
