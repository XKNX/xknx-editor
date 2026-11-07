"""Health panel: a scrollable, grouped list of actionable project checks that navigate on click."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.health.service import Finding, HealthService, Severity
from editor_gui.plugins.health.strings import S
from editor_gui.theme import status_color

# The status color (see ``status_color``) of each severity.
SEVERITY_KIND: dict[Severity, str] = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "info",
}
_SEVERITY_ICON: dict[Severity, str] = {
    Severity.ERROR: "[!]",
    Severity.WARNING: "[!]",
    Severity.INFO: "[i]",
}


class HealthPanel:
    def __init__(
        self,
        service: HealthService,
        on_navigate: Callable[[int], None],
        on_navigate_ga: Callable[[int], None],
        is_open: Callable[[], bool],
    ) -> None:
        self._service = service
        self._on_navigate = on_navigate
        self._on_navigate_ga = on_navigate_ga
        self._is_open = is_open

    def render(self) -> None:
        if not self._is_open():
            imgui.text_disabled(S.HEALTH_EMPTY)
            return
        findings = self._service.findings()
        errors = sum(1 for f in findings if f.severity is Severity.ERROR)
        warnings = sum(1 for f in findings if f.severity is Severity.WARNING)
        imgui.text_disabled(S.HEALTH_SUMMARY.format(errors=errors, warnings=warnings))
        imgui.separator()
        if not findings:
            imgui.text_disabled(S.HEALTH_ALL_GOOD)
            return
        # Errors first, then warnings, then info; stable within a group.
        order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        for i, f in enumerate(sorted(findings, key=lambda f: order[f.severity])):
            self._render_row(i, f)

    def _render_row(self, index: int, f: Finding) -> None:
        color = status_color(SEVERITY_KIND[f.severity])
        icon = _SEVERITY_ICON[f.severity]
        imgui.push_style_color(imgui.Col_.text, color)
        imgui.text(icon)
        imgui.pop_style_color()
        imgui.same_line()
        # Clickable when it can navigate to a device or a group address; else a plain line.
        if f.device_node_id is not None:
            if imgui.selectable(f"{f.message}##health{index}", False)[0]:
                self._on_navigate(f.device_node_id)
        elif f.ga_id is not None:
            if imgui.selectable(f"{f.message}##health{index}", False)[0]:
                self._on_navigate_ga(f.ga_id)
        else:
            imgui.text_wrapped(f.message)
