"""Health panel: a scrollable, grouped list of actionable project checks that navigate on click.

Each finding has the icon of its kind (error, warning, hint) in the kind's colour; the kinds can be
shown or hidden with a checkbox each, which also counts them.
"""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.health.service import Finding, HealthService, Severity
from editor_gui.plugins.health.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.dpi import px
from editor_gui.widgets.flow import same_line_if_fits
from editor_gui.widgets.icon_button import ICON_ERROR, ICON_INFO, ICON_WARNING

# The status color (see ``status_color``) of each severity.
SEVERITY_KIND: dict[Severity, str] = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "info",
}
SEVERITY_ICON: dict[Severity, str] = {
    Severity.ERROR: ICON_ERROR,
    Severity.WARNING: ICON_WARNING,
    Severity.INFO: ICON_INFO,
}
# Errors first, then warnings, then hints.
_ORDER = (Severity.ERROR, Severity.WARNING, Severity.INFO)
# The gap between a finding's icon and its text.
_ICON_GAP = 4.0


def severity_label(severity: Severity) -> str:
    """The name of a kind of findings, as its filter shows it."""
    return {
        Severity.ERROR: S.HEALTH_KIND_ERRORS,
        Severity.WARNING: S.HEALTH_KIND_WARNINGS,
        Severity.INFO: S.HEALTH_KIND_HINTS,
    }[severity]


def severity_icon(severity: Severity) -> None:
    """The icon of ``severity`` in its colour, centred in a slot as wide as the widest of the
    icons, so the texts after them start at the same place."""
    icon = SEVERITY_ICON[severity]
    slot = max(imgui.calc_text_size(i).x for i in SEVERITY_ICON.values())
    x = imgui.get_cursor_pos_x()
    imgui.set_cursor_pos_x(x + (slot - imgui.calc_text_size(icon).x) / 2)
    imgui.text_colored(status_color(SEVERITY_KIND[severity]), icon)
    imgui.same_line(0.0, 0.0)
    imgui.set_cursor_pos_x(x + slot + px(_ICON_GAP))


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
        # The kinds of findings shown (all, until a checkbox hides one).
        self.shown: set[Severity] = set(_ORDER)

    def render(self) -> None:
        if not self._is_open():
            imgui.text_disabled(S.HEALTH_EMPTY)
            return
        findings = self._service.findings()
        self._render_filters(findings)
        imgui.separator()
        if not findings:
            imgui.text_disabled(S.HEALTH_ALL_GOOD)
            return
        shown = [f for f in findings if f.severity in self.shown]
        if not shown:
            imgui.text_disabled(S.HEALTH_NONE_SHOWN)
            return
        # Stable within a kind.
        rank = {severity: i for i, severity in enumerate(_ORDER)}
        for i, f in enumerate(sorted(shown, key=lambda f: rank[f.severity])):
            self._render_row(i, f)

    def _render_filters(self, findings: list[Finding]) -> None:
        """A checkbox per kind of findings, with its icon and how many there are; a click on the
        icon or the text toggles it too."""
        spacing = imgui.get_style().item_inner_spacing.x
        for index, severity in enumerate(_ORDER):
            count = sum(1 for f in findings if f.severity is severity)
            text = f"{severity_label(severity)} ({count})"
            if index:
                same_line_if_fits(
                    imgui.get_frame_height()
                    + spacing
                    + imgui.calc_text_size(SEVERITY_ICON[severity]).x
                    + px(_ICON_GAP)
                    + imgui.calc_text_size(text).x,
                    px(16.0),
                )
            imgui.begin_group()
            changed, on = checkbox(f"##health_{severity.value}", severity in self.shown)
            imgui.same_line(0.0, spacing)
            severity_icon(severity)
            imgui.text(text)
            imgui.end_group()
            if not changed and imgui.is_item_clicked():
                changed, on = True, severity not in self.shown
            if changed:
                if on:
                    self.shown.add(severity)
                else:
                    self.shown.discard(severity)

    def _render_row(self, index: int, f: Finding) -> None:
        severity_icon(f.severity)
        # Clickable when it can navigate to a device or a group address; else a plain line.
        if f.device_node_id is not None:
            if imgui.selectable(f"{f.message}##health{index}", False)[0]:
                self._on_navigate(f.device_node_id)
        elif f.ga_id is not None:
            if imgui.selectable(f"{f.message}##health{index}", False)[0]:
                self._on_navigate_ga(f.ga_id)
        else:
            imgui.text_wrapped(f.message)
